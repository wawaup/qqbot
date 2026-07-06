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


def set_bot_client(client) -> None:
    global _bot_client
    _bot_client = client


async def scan_and_notify(first_run: bool = False) -> None:
    """扫描商店库存，有上新/上架/补货时发群消息。

    first_run=True 时只建立快照，不发通知（避免把全量商品误报为补货）。
    静默时段（00:00-09:00）仍然扫描并更新快照、正常做冷却标记，只跳过发送通知，
    确保用户 @查询 时拿到的是最新库存数据。
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

    # 静默时段只更新快照和冷却状态，不发通知
    if _in_quiet_hours():
        logger.debug("静默时段，跳过通知")
        return

    if _bot_client is not None:
        if new_products:
            await _bot_client.send_new_product_notice(new_products)
        if relisted_products:
            await _bot_client.send_relisted_notice(relisted_products)
        if restocked_products:
            await _bot_client.send_restock_notice(restocked_products)


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
    return scheduler
