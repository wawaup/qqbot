"""
定时扫描任务：每 SCAN_INTERVAL 秒扫描店铺，检测上新/上架/补货并通知 QQ 群。
"""
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    NOTIFY_COOLDOWN,
    NOTIFY_EXCLUDE_CATEGORIES,
    PRICE_DROP_MIN_DELTA,
    PRICE_DROP_RESTOCK_BLOCK_SECONDS,
    SCAN_INTERVAL,
    SHOP_URL,
    TWITTER_ENABLED,
    TWITTER_INCLUDE_REPLIES,
    TWITTER_INCLUDE_RETWEETS,
    TWITTER_SCAN_INTERVAL,
    TWITTER_TOPIC_FILTER,
    TWITTER_USERNAME,
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

# 显著降价后短时间内拦截群补货，防止降价+补货被薅
_price_drop_block: dict[str, datetime] = {}


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


def _mark_price_drop_block(drops: list) -> None:
    now = datetime.now(CST)
    for item in drops:
        product = item[0] if isinstance(item, tuple) else item
        _price_drop_block[product.id] = now


def _is_restock_blocked(product_id: str) -> bool:
    last = _price_drop_block.get(product_id)
    if last is None:
        return False
    return (datetime.now(CST) - last).total_seconds() < PRICE_DROP_RESTOCK_BLOCK_SECONDS


def _filter_restock_after_price_drop(products: list) -> list:
    """降价达到门槛后的窗口期内，拦截该商品的群补货通知。"""
    kept: list = []
    blocked: list = []
    for p in products:
        if _is_restock_blocked(p.id):
            blocked.append(p)
        else:
            kept.append(p)
    if blocked:
        logger.info(
            f"降价后拦截补货群通知：{len(blocked)} 个商品"
            f"（{PRICE_DROP_RESTOCK_BLOCK_SECONDS}s 内）"
        )
    return kept


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

    first_run=True 时只建立快照，不发通知（避免把全量商品误报为补货，
    也避免进程重启期间的库存变化被当成一次性批量通知刷屏）。
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
    price_drops = state.diff_price_drops(
        old_state, current_products, min_delta=PRICE_DROP_MIN_DELTA
    )
    _mark_price_drop_block(price_drops)

    def _exclude(products: list) -> list:
        return [p for p in products if p.category_id not in NOTIFY_EXCLUDE_CATEGORIES]

    new_products = _exclude(new_products)
    relisted_products = _exclude(relisted_products)
    restocked_products = _filter_restock_after_price_drop(_exclude(restocked_products))

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
        f"新品 {len(new_products)} 个，上架 {len(relisted_products)} 个，"
        f"补货 {len(restocked_products)} 个，降价 {len(price_drops)} 个"
    )

    # 降价只私聊店主，不受群静默时段影响
    if _bot_client is not None and price_drops:
        await _bot_client.send_price_drop_notice(price_drops)

    # 静默时段只更新快照和冷却状态，不发群通知；事件缓冲起来，09:00 由 daily_digest job 统一汇总
    if _in_quiet_hours():
        logger.debug("静默时段，跳过群通知，缓冲事件")
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
        if event_type == "restocked" and _is_restock_blocked(product.id):
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


async def scan_tweets_and_notify(first_run: bool = False) -> None:
    """拉取 Twitter 时间线，有新帖则连同文字和图片转发到群。

    first_run=True 时只记下当前已有帖子 id，不转发（避免启动时把时间线刷屏）。
    不受店铺静默时段影响：店主发推频率低，且多是主动公告。
    """
    if not TWITTER_ENABLED or not TWITTER_USERNAME:
        return

    from storage import twitter_state
    from twitter.classifier import is_worth_forwarding, preview_text
    from twitter.fetcher import fetch_timeline, pick_new_threads

    logger.info(f"开始拉取 @{TWITTER_USERNAME} 的推文...")
    try:
        tweets = await fetch_timeline(TWITTER_USERNAME)
    except Exception as e:
        logger.error(f"拉取推文失败: {e}")
        return

    old_ids = twitter_state.load_seen_ids()
    fetched_ids = [t.id for t in tweets]

    if first_run or not old_ids:
        twitter_state.save_seen_ids(
            twitter_state.merge_seen(old_ids, fetched_ids),
            username=TWITTER_USERNAME,
        )
        logger.info(f"推文快照建立：记下 {len(fetched_ids)} 条，启动后的新帖才会转发")
        return

    new_threads = pick_new_threads(
        tweets,
        set(old_ids),
        TWITTER_USERNAME,
        include_replies=TWITTER_INCLUDE_REPLIES,
        include_retweets=TWITTER_INCLUDE_RETWEETS,
    )

    failed_ids: set[str] = set()
    if TWITTER_TOPIC_FILTER:
        kept = []
        skipped = 0
        for thread in new_threads:
            preview = preview_text(thread)
            verdict = await is_worth_forwarding(preview)
            if verdict is True:
                kept.append(thread)
            elif verdict is False:
                skipped += 1
                logger.info(f"资讯过滤：跳过 {thread[0].id} {preview[:40]!r}")
            else:
                failed_ids.update(t.id for t in thread)
                logger.warning(f"资讯过滤失败，下轮重试 {thread[0].id}")
        if skipped:
            logger.info(f"资讯过滤：跳过 {skipped} 组闲聊/引流帖")
        new_threads = kept

    logger.info(
        f"推文扫描完成：时间线 {len(tweets)} 条，待转发 {len(new_threads)} 组"
        + (f"（含串推）" if any(len(th) > 1 for th in new_threads) else "")
    )

    if _bot_client is not None:
        for thread in new_threads:
            await _bot_client.send_tweet_notice(thread)
            await asyncio.sleep(0.5)

    save_ids = [tid for tid in fetched_ids if tid not in failed_ids]
    twitter_state.save_seen_ids(
        twitter_state.merge_seen(old_ids, save_ids),
        username=TWITTER_USERNAME,
    )



_CPA_AUTH_DIR = Path("/opt/cliproxyapi/auths")
_CPA_ALERT_STATE = Path("/root/qqbot/cpa_alert_state.json")
_EGRESS_HEALTH = Path("/root/.config/mihomo/antigravity-egress-health.json")
# 同一件事 12 小时内只提醒一次；问题消失后再出现会重新提醒。
_CPA_ALERT_COOLDOWN = timedelta(hours=12)
# 令牌过期后给 CPA 留时间自己刷新，节点刚恢复时常要等一会儿。
_CPA_TOKEN_GRACE = timedelta(minutes=30)
# 出口检查每 30 分钟跑一次，超过这个时间没更新说明定时器停了。
_EGRESS_STALE = timedelta(minutes=90)


def _cpa_alert_state() -> dict:
    try:
        data = json.loads(_CPA_ALERT_STATE.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_cpa_alert_state(data: dict) -> None:
    _CPA_ALERT_STATE.write_text(json.dumps(data, ensure_ascii=False))


def _cpa_alert_items(now: datetime) -> list[tuple[str, str]]:
    """只列需要人工处理的事：换订阅、重新登录、出口检查停了。"""
    items: list[tuple[str, str]] = []
    node_bad = False
    try:
        health = json.loads(_EGRESS_HEALTH.read_text())
    except Exception:
        health = None
    if not isinstance(health, dict):
        items.append(("egress:missing", "- 出口检查没有结果，换线定时器可能没在跑（antigravity-egress-failover.timer）"))
    else:
        try:
            updated = datetime.fromisoformat(str(health.get("updated_at")))
            if now - updated > _EGRESS_STALE:
                minutes = int((now - updated).total_seconds() // 60)
                items.append(("egress:stale", f"- 出口检查已经 {minutes} 分钟没跑了（antigravity-egress-failover.timer）"))
        except ValueError:
            pass
        slots = health.get("slots") or {}
        exhausted = [str(s.get("email")) for s in slots.values() if isinstance(s, dict) and s.get("status") == "exhausted"]
        if health.get("subscription_dead") or exhausted:
            node_bad = True
            reasons = []
            if health.get("subscription_dead"):
                reasons.append(f"美国节点能解析的只剩 {health.get('us_resolvable')}/{health.get('us_nodes')}")
            if exhausted:
                reasons.append("、".join(exhausted) + " 换遍候选节点都不通")
            items.append((
                "egress:subscription",
                "- 节点大面积不可用（" + "；".join(reasons) + "）。请从订阅网页下载新的 Clash 配置，"
                "在 shop-tool 里运行 deploy/aliyun-relay/push_subscription.sh，或把文件交给 Claude。"
                "节点恢复后令牌一般会自己刷新，不用重新登录。",
            ))
    if _CPA_AUTH_DIR.is_dir():
        for path in sorted(_CPA_AUTH_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception as e:
                logger.warning("CPA 凭证读取失败 %s: %s", path.name, e)
                continue
            email = str(data.get("email") or path.stem)
            if data.get("disabled"):
                items.append((email + ":disabled", f"- {email} 在 CPA 里被禁用了，需要重新登录或启用"))
                continue
            try:
                exp = datetime.fromisoformat(str(data.get("expired") or ""))
            except ValueError:
                continue
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=CST)
            # 节点坏了刷不了令牌，归到上面换订阅那条，不让你白白重新登录。
            if node_bad or now - exp < _CPA_TOKEN_GRACE:
                continue
            when = exp.astimezone(CST).strftime("%m-%d %H:%M")
            items.append((email + ":relogin", f"- {email} 访问令牌 {when} 过期后一直没刷新上，节点是通的，需要在 CPA 重新登录这个号"))
    return items


async def scan_cpa_auth_alerts() -> None:
    """需要人工处理时私聊店主。每 30 分钟查一次，同一件事 12 小时内只发一次。"""
    if _bot_client is None:
        return
    now = datetime.now(CST)
    items = _cpa_alert_items(now)
    state = _cpa_alert_state()
    notified = state.get("notified") if isinstance(state.get("notified"), dict) else {}
    current = {key for key, _line in items}
    # 已经解决的事从记录里去掉，下次再出现会重新提醒。
    notified = {key: at for key, at in notified.items() if key in current}
    due = []
    for key, _line in items:
        try:
            last = datetime.fromisoformat(str(notified.get(key)))
        except ValueError:
            last = None
        if last is None or now - last >= _CPA_ALERT_COOLDOWN:
            due.append(key)
    if not due:
        _save_cpa_alert_state({"notified": notified})
        return
    text = "# CPA 需要处理\n\n" + "\n".join(line for _key, line in items)
    try:
        await _bot_client.send_cpa_alert(text)
    except Exception as e:
        logger.error("CPA 告警发送失败: %s", e)
        return
    for key in current:
        notified[key] = now.isoformat()
    _save_cpa_alert_state({"notified": notified})
    logger.info("CPA 告警已私聊，%s 条", len(items))


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
    scheduler.add_job(
        scan_cpa_auth_alerts,
        trigger="interval",
        seconds=1800,
        id="cpa_auth_alert",
        replace_existing=True,
        max_instances=1,
    )
    if TWITTER_ENABLED and TWITTER_USERNAME:
        scheduler.add_job(
            scan_tweets_and_notify,
            trigger="interval",
            seconds=TWITTER_SCAN_INTERVAL,
            id="twitter_scan",
            replace_existing=True,
            max_instances=1,
        )
    return scheduler
