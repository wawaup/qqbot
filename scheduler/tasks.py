"""
定时扫描任务：每 SCAN_INTERVAL 秒扫描店铺，检测上新/上架/补货并通知 QQ 群。
"""
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    NOTIFY_COOLDOWN,
    NOTIFY_EXCLUDE_CATEGORIES,
    SCAN_INTERVAL,
    SHOP_URL,
)
from shop import scraper
from shop.models import Product
from storage import state

CST = timezone(timedelta(hours=8))
QUIET_START = 0   # 00:00
QUIET_END   = 9   # 09:00，不含（即 09:00 起正常发）


def _in_quiet_hours() -> bool:
    hour = datetime.now(CST).hour
    return QUIET_START <= hour < QUIET_END

logger = logging.getLogger(__name__)

_bot_client = None

# 通知冷却：记录每个商品 ID 上次进入通知队列的时间
# 同一 goods_key 无论触发的是新品/上架/补货中的哪一种，都共用这份冷却
_notify_cooldown: dict[str, datetime] = {}

# 静默时段（00:00-09:00）检测到的事件先缓冲在这里，09:00 由 daily_digest job 统一发送
# key: product.id，同一商品多次触发时后写覆盖先写，天然去重
_quiet_buffer: dict[str, tuple] = {}


def _is_on_cooldown(product_id: str) -> bool:
    last = _notify_cooldown.get(product_id)
    if last is None:
        return False
    return (datetime.now(CST) - last).total_seconds() < NOTIFY_COOLDOWN


def _mark_notified(products: list) -> None:
    now = datetime.now(CST)
    for p in products:
        _notify_cooldown[p.id] = now


def _filter_cooldown(products: list) -> list:
    """过滤掉仍在冷却期内的商品，返回可以通知的商品列表。"""
    return [p for p in products if not _is_on_cooldown(p.id)]


def _buffer_quiet_events(new_products: list, relisted_products: list, restocked_products: list) -> None:
    """静默时段不发通知，但把事件记下来，09:00 由 daily_digest job 统一汇总发送。"""
    for p in new_products:
        _quiet_buffer[p.id] = ("new", p)
    for p in relisted_products:
        _quiet_buffer[p.id] = ("relisted", p)
    for p in restocked_products:
        _quiet_buffer[p.id] = ("restocked", p)


def set_bot_client(client) -> None:
    global _bot_client
    _bot_client = client


async def scan_and_notify(first_run: bool = False) -> None:
    """扫描商店库存，有上新/上架/补货时发群消息。

    first_run=True 时只建立快照，不发通知（避免把全量商品误报为补货）。
    静默时段（00:00-09:00）仍然扫描并更新快照、正常做冷却标记，只跳过发送通知，
    确保用户 @查询 时拿到的是最新库存数据，也不会漏发静默时段检测到的事件。
    """
    logger.info("开始扫描商店库存...")
    try:
        current_products = await scraper.scan_all(SHOP_URL)
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        return

    old_state = state.load_state()

    if first_run or not old_state:
        logger.info(f"初始快照建立：共 {len(current_products)} 个商品")
        state.save_state(current_products)
        return

    new_products, relisted_products, restocked_products = state.diff_states(old_state, current_products)

    def _exclude(products: list) -> list:
        return [p for p in products if p.category_id not in NOTIFY_EXCLUDE_CATEGORIES]

    new_products = _exclude(new_products)
    relisted_products = _exclude(relisted_products)
    restocked_products = _exclude(restocked_products)

    state.save_state(current_products)

    in_stock_count = sum(1 for p in current_products.values() if p.in_stock)

    # 冷却过滤：同一商品 NOTIFY_COOLDOWN 秒内只进入通知队列一次
    before_counts = (len(new_products), len(relisted_products), len(restocked_products))
    new_products = _filter_cooldown(new_products)
    relisted_products = _filter_cooldown(relisted_products)
    restocked_products = _filter_cooldown(restocked_products)
    cooled = sum(before_counts) - len(new_products) - len(relisted_products) - len(restocked_products)
    if cooled:
        logger.info(f"冷却过滤：跳过 {cooled} 个商品（{NOTIFY_COOLDOWN}s 内已通知过）")
    _mark_notified(new_products + relisted_products + restocked_products)

    logger.info(
        f"扫描完成：共 {len(current_products)} 个商品，有货 {in_stock_count} 个，"
        f"新品 {len(new_products)} 个，上架 {len(relisted_products)} 个，补货 {len(restocked_products)} 个"
    )

    # 静默时段只更新快照和冷却状态，不发通知；事件缓冲起来，09:00 由 daily_digest job 统一汇总
    if _in_quiet_hours():
        logger.debug("静默时段，跳过通知，缓冲事件")
        _buffer_quiet_events(new_products, relisted_products, restocked_products)
        return

    # 非静默时段的重新上架不通知、也不缓冲，直接忽略

    if _bot_client is not None:
        if new_products:
            await _bot_client.send_new_product_notice(new_products)
        if restocked_products:
            await _bot_client.send_restock_notice(restocked_products)


def _revalidate_buffered_events(buffered: list[tuple[str, "Product"]]) -> list[tuple[str, "Product"]]:
    """按最新快照重新校验缓冲事件：商品已下架/缺货/被下架商品清除的一律丢弃，
    并用最新数据（价格等）刷新商品信息，避免汇总里出现失效商品或过期信息。
    """
    current_state = state.load_state()
    events = []
    for event_type, product in buffered:
        entry = current_state.get(product.id)
        if entry is None or not entry.get("listed", True) or not entry.get("in_stock", False):
            continue
        events.append((event_type, Product(
            id=product.id,
            title=entry["title"],
            url=entry["url"],
            category=entry["category"],
            category_id=entry.get("category_id"),
            in_stock=entry["in_stock"],
            price=entry.get("price", ""),
            description=entry.get("description", ""),
        )))
    return events


async def send_daily_digest() -> None:
    """09:00 静默时段结束时触发，汇总发送这段时间缓冲的新品/上架/补货事件。

    发送前用最新快照重新校验，过滤掉已经失效（被下架、删除或缺货）的商品，
    避免把一整晚的变化里已经不存在的商品也发出来。
    """
    global _quiet_buffer
    if not _quiet_buffer:
        return
    buffered = list(_quiet_buffer.values())
    _quiet_buffer = {}

    events = _revalidate_buffered_events(buffered)
    if not events:
        logger.info("每日汇总：缓冲的商品均已失效或缺货，跳过发送")
        return

    if _bot_client is not None:
        await _bot_client.send_daily_digest(events)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scan_and_notify,
        trigger="interval",
        seconds=SCAN_INTERVAL,
        id="shop_scan",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        send_daily_digest,
        trigger="cron",
        hour=QUIET_END,
        minute=0,
        timezone=CST,
        id="daily_digest",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler
