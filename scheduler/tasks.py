"""
定时扫描任务：每 SCAN_INTERVAL 秒扫描店铺，检测补货并通知 QQ 群。
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import SCAN_INTERVAL, SHOP_URL
from shop import scraper
from storage import state

logger = logging.getLogger(__name__)

_bot_client = None


def set_bot_client(client) -> None:
    global _bot_client
    _bot_client = client


async def scan_and_notify(first_run: bool = False) -> None:
    """扫描商店库存，有补货时发群消息。

    first_run=True 时只建立快照，不发通知（避免把全量商品误报为补货）。
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

    newly_in_stock = state.diff_states(old_state, current_products)
    state.save_state(current_products)

    in_stock_count = sum(1 for p in current_products.values() if p.in_stock)
    logger.info(
        f"扫描完成：共 {len(current_products)} 个商品，"
        f"有货 {in_stock_count} 个，新补货 {len(newly_in_stock)} 个"
    )

    if newly_in_stock and _bot_client is not None:
        await _bot_client.send_restock_notice(newly_in_stock)


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
