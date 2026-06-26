"""
定时扫描任务：每 SCAN_INTERVAL 秒扫描店铺，检测补货并通知 QQ 群。
"""
import logging
import re
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    GPT_BATCH_INTERVAL,
    GPT_CATEGORIES,
    GPT_INSTANT_PRICE_THRESHOLD,
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

# GPT 分类价格 >= 阈值的补货/新品缓冲区，每 GPT_BATCH_INTERVAL 秒统一发送
_gpt_pending_restocked: dict[str, object] = {}
_gpt_pending_new: dict[str, object] = {}


def set_bot_client(client) -> None:
    global _bot_client
    _bot_client = client


def _parse_price(price_str: str) -> float:
    """从价格字符串提取数值，解析失败返回 inf（不触发即时通知）。"""
    m = re.search(r"[\d.]+", price_str or "")
    return float(m.group()) if m else float("inf")


async def scan_and_notify(first_run: bool = False) -> None:
    """扫描商店库存，有补货时发群消息。

    first_run=True 时只建立快照，不发通知（避免把全量商品误报为补货）。
    静默时段（00:00-09:00）跳过扫描，9 点后首次扫描可捕获整夜的补货。
    """
    if not first_run and _in_quiet_hours():
        logger.debug("静默时段，跳过扫描")
        return

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

    restocked, new_products = state.diff_states(old_state, current_products)
    restocked = [p for p in restocked if p.category not in NOTIFY_EXCLUDE_CATEGORIES]
    new_products = [p for p in new_products if p.category not in NOTIFY_EXCLUDE_CATEGORIES]
    state.save_state(current_products)

    in_stock_count = sum(1 for p in current_products.values() if p.in_stock)
    logger.info(
        f"扫描完成：共 {len(current_products)} 个商品，"
        f"有货 {in_stock_count} 个，补货 {len(restocked)} 个，新品 {len(new_products)} 个"
    )

    # GPT 分类：低价立即通知，高价缓冲到批量任务
    def _is_gpt_instant(p) -> bool:
        return p.category in GPT_CATEGORIES and _parse_price(p.price) < GPT_INSTANT_PRICE_THRESHOLD

    def _is_gpt_batch(p) -> bool:
        return p.category in GPT_CATEGORIES and _parse_price(p.price) >= GPT_INSTANT_PRICE_THRESHOLD

    instant_restocked = [p for p in restocked if p.category not in GPT_CATEGORIES or _is_gpt_instant(p)]
    instant_new = [p for p in new_products if p.category not in GPT_CATEGORIES or _is_gpt_instant(p)]

    for p in restocked:
        if _is_gpt_batch(p):
            _gpt_pending_restocked[p.id] = p
    for p in new_products:
        if _is_gpt_batch(p):
            _gpt_pending_new[p.id] = p

    if _bot_client is not None:
        if instant_restocked:
            await _bot_client.send_restock_notice(instant_restocked)
        if instant_new:
            await _bot_client.send_new_product_notice(instant_new)


async def flush_gpt_pending() -> None:
    """每 GPT_BATCH_INTERVAL 秒发送一次 GPT 分类的缓冲补货/新品通知。"""
    if _in_quiet_hours():
        return

    restocked = list(_gpt_pending_restocked.values())
    new_products = list(_gpt_pending_new.values())
    _gpt_pending_restocked.clear()
    _gpt_pending_new.clear()

    if not restocked and not new_products:
        return

    logger.info(f"GPT 批量通知：补货 {len(restocked)} 个，新品 {len(new_products)} 个")
    if _bot_client is not None:
        if restocked:
            await _bot_client.send_restock_notice(restocked)
        if new_products:
            await _bot_client.send_new_product_notice(new_products)


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
        flush_gpt_pending,
        trigger="interval",
        seconds=GPT_BATCH_INTERVAL,
        id="gpt_batch_notify",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler
