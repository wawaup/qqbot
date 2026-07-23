"""
定时扫描任务：每 SCAN_INTERVAL 秒扫描店铺，检测上新/上架/补货并通知 QQ 群。
"""
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    CONTENT_CHECK_INTERVAL,
    INVENTORY_SOURCE,
    NOTIFY_COOLDOWN,
    NOTIFY_EXCLUDE_CATEGORIES,
    SCAN_INTERVAL,
    SHOP_URL,
)
from shop import scraper
from shop.core_client import (
    ShopCoreError,
    fetch_inventory_events,
    fetch_inventory_products,
)
from shop.models import Product
from storage import content_state, event_cursors, state

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


def _exclude_notify_categories(products: list) -> list:
    return [p for p in products if p.category_id not in NOTIFY_EXCLUDE_CATEGORIES]


def _partition_core_inventory_events(
    events: list[dict],
    products: dict[str, Product],
) -> tuple[list[Product], list[Product], list[Product]]:
    """Map core inventory events → (new, relisted, restocked). Last event type wins per product.

    Only products present in the current listed snapshot are eligible — event rows
    alone must not resurrect delisted SKUs for notifications.
    """
    by_id: dict[str, tuple[str, Product]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in {"new", "relisted", "restocked"}:
            continue
        product_id = str(event.get("product_id") or "").strip()
        if not product_id or product_id not in products:
            continue
        product = products[product_id]
        if not product.in_stock:
            continue
        by_id[product.id] = (event_type, product)

    new_products: list[Product] = []
    relisted_products: list[Product] = []
    restocked_products: list[Product] = []
    for event_type, product in by_id.values():
        if event_type == "new":
            new_products.append(product)
        elif event_type == "relisted":
            relisted_products.append(product)
        else:
            restocked_products.append(product)
    return new_products, relisted_products, restocked_products


async def _diff_from_core_events(
    current_products: dict[str, Product],
    *,
    first_run: bool,
) -> tuple[list[Product], list[Product], list[Product], str, list[dict]]:
    """Prefer shop-core inventory events; fall back to local merge-state diff.

    Returns (new, relisted, restocked, mode, fresh_events_to_ack).
    Caller must advance the event cursor only after notify/buffer succeeds so a
    crash mid-notify does not drop events.
    """
    cursors = event_cursors.load()
    if first_run or not cursors.get("inventory_since"):
        # Bootstrap cursor without notifying historical backlog.
        try:
            bootstrap_events = await fetch_inventory_events(since=None, limit=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bootstrap inventory events failed: %s", exc)
            bootstrap_events = []
        if bootstrap_events:
            event_cursors.advance_inventory(bootstrap_events, cursors)
        else:
            # No events yet — stamp empty cursor so next runs poll with since=now path via last_id=0
            event_cursors.save({**cursors, "inventory_since": datetime.now(CST).isoformat(timespec="seconds")})
        return [], [], [], "core-events-bootstrap", []

    since = cursors.get("inventory_since")
    try:
        raw_events = await fetch_inventory_events(
            since=str(since) if since else None,
            limit=200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("core inventory events 不可用，回退本地 diff: %s", exc)
        old_state = state.load_state()
        return (*state.diff_states(old_state, current_products), "local-diff-fallback", [])

    fresh = event_cursors.filter_new_inventory_events(raw_events, cursors)
    new_products, relisted_products, restocked_products = _partition_core_inventory_events(
        fresh, current_products
    )
    return new_products, relisted_products, restocked_products, "core-events", fresh


async def scan_and_notify(first_run: bool = False) -> None:
    """扫描商店库存，有上新/上架/补货时发群消息。

    first_run=True 时只建立快照，不发通知（避免把全量商品误报为补货，
    也避免进程重启期间的库存变化被当成一次性批量通知刷屏）。
    静默时段（00:00-09:00）仍然扫描并更新快照、正常做冷却标记，只跳过发送通知，
    确保用户 @查询 时拿到的是最新库存数据，也不会漏发静默时段检测到的事件。

    shop-core 源：通知分类优先消费 core inventory events；本地 state 仅作查询缓存与
    events 不可用时的回退 diff 基线。
    """
    logger.info("开始扫描商店库存... source=%s", INVENTORY_SOURCE)
    use_core = INVENTORY_SOURCE in {"shop-core", "core"}
    try:
        if use_core:
            current_products = await fetch_inventory_products()
        else:
            current_products = await scraper.scan_all(SHOP_URL)
    except ShopCoreError as e:
        logger.error(f"shop-core 库存读取失败: {e}")
        return
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        return

    old_state = state.load_state()
    events_to_ack: list[dict] = []

    if first_run or not old_state:
        logger.info(f"初始快照建立：共 {len(current_products)} 个商品")
        state.save_state(current_products)
        if use_core:
            await _diff_from_core_events(current_products, first_run=True)
        return

    if use_core:
        (
            new_products,
            relisted_products,
            restocked_products,
            diff_mode,
            events_to_ack,
        ) = await _diff_from_core_events(current_products, first_run=False)
    else:
        new_products, relisted_products, restocked_products = state.diff_states(
            old_state, current_products
        )
        diff_mode = "local-diff"

    new_products = _exclude_notify_categories(new_products)
    relisted_products = _exclude_notify_categories(relisted_products)
    restocked_products = _exclude_notify_categories(restocked_products)

    # Query cache / offline baseline — not the notify authority when using core events.
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
        f"扫描完成 mode={diff_mode}：共 {len(current_products)} 个商品，有货 {in_stock_count} 个，"
        f"新品 {len(new_products)} 个，上架 {len(relisted_products)} 个，补货 {len(restocked_products)} 个"
    )

    notify_ok = True
    try:
        # 静默时段只更新快照和冷却状态，不发通知；事件缓冲起来，09:00 由 daily_digest job 统一汇总
        if _in_quiet_hours():
            logger.debug("静默时段，跳过通知，缓冲事件")
            _buffer_quiet_events(new_products, relisted_products, restocked_products)
        elif _bot_client is not None:
            # 非静默时段的重新上架不通知、也不缓冲，直接忽略
            if new_products:
                await _bot_client.send_new_product_notice(new_products)
                # Auto-start C2C publish review for owners (serial queue inside review skill).
                try:
                    from bot import review as review_skill

                    for product in new_products:
                        try:
                            _session, msg = await review_skill.start_publish_review(
                                product.id,
                                source="inventory_new",
                            )
                            await _bot_client.push_c2c_to_owners(
                                f"新品待上架审核 `{product.id}`\n\n{msg}"
                            )
                        except Exception as review_exc:  # noqa: BLE001
                            logger.warning(
                                "auto publish review failed for %s: %s",
                                product.id,
                                review_exc,
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auto publish review hook failed: %s", exc)
            if restocked_products:
                await _bot_client.send_restock_notice(restocked_products)
    except Exception as exc:  # noqa: BLE001
        notify_ok = False
        logger.error("库存通知发送失败，不推进 event cursor: %s", exc)

    # Ack core events only after notify/buffer path finishes successfully.
    # Delisted/out-of-stock events still get acked (empty notify lists) so the
    # cursor does not stall on historical noise.
    if notify_ok and events_to_ack:
        try:
            event_cursors.advance_inventory(events_to_ack)
        except Exception as exc:  # noqa: BLE001
            logger.error("推进 inventory event cursor 失败: %s", exc)


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
            stock_count=entry.get("stock_count", 0),
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


async def check_catalog_content_changes() -> None:
    """定期比较商品标题、说明、封面及详情图 URL，只在变化时通知。"""
    current = content_state.build_snapshot(state.load_state())
    previous = content_state.load_snapshot()
    if not previous:
        content_state.save_snapshot(current)
        logger.info("商品资料基线已建立：共 %s 个商品", len(current))
        return

    changes = content_state.diff_snapshots(previous, current)
    if not changes:
        logger.info("商品资料检查完成：无变化")
        return
    if _bot_client is None:
        logger.warning("发现 %s 个商品资料变化，但机器人尚未就绪", len(changes))
        return

    sent = await _bot_client.send_content_change_notice(changes)
    if sent:
        content_state.save_snapshot(current)
        logger.info("商品资料变化已通知：%s 个商品", len(changes))
    else:
        logger.warning("商品资料变化通知未发送，保留差异供下次重试")


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_catalog_content_changes,
        trigger="interval",
        seconds=CONTENT_CHECK_INTERVAL,
        id="catalog_content_check",
        replace_existing=True,
        max_instances=1,
    )
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
