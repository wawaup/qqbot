from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shop.models import Product
    from twitter.models import Tweet


def _price_sort_key(p: "Product") -> float:
    try:
        return float(p.price)
    except (TypeError, ValueError):
        return float("inf")


def sort_by_price(items: list) -> list:
    return sorted(items, key=_price_sort_key)


def _price_label(p: "Product") -> str:
    return f"{p.price}r · " if p.price else ""


def _item_line(i: int, p: "Product") -> str:
    return f"{i}. **{_price_label(p)}{p.title}**\n   {p.url}"


def _notice_line(p: "Product") -> str:
    return f"\n**{_price_label(p)}{p.title}**\n   {p.url}"


def format_product_menu(products: dict[str, "Product"]) -> str:
    by_category: dict[str, list] = defaultdict(list)
    for p in products.values():
        if p.in_stock:
            by_category[p.category].append(p)

    if not by_category:
        return "当前没有有货商品，请稍后再查询～"

    parts = ["# 📋 商品清单"]
    for cat_name, items in by_category.items():
        block = [f"## {cat_name}"]
        for i, p in enumerate(sort_by_price(items), 1):
            block.append(_item_line(i, p))
        parts.append("\n".join(block))

    return "\n\n".join(parts)


def filter_category_products(
    products: dict[str, "Product"],
    category_ids: list[int],
) -> list:
    id_set = set(category_ids)
    return sort_by_price(
        [p for p in products.values() if p.in_stock and p.category_id in id_set]
    )


def format_category_products(items: list["Product"], label: str) -> str:
    if not items:
        return f"## 【{label}】\n\n暂时没有有货商品，补货时会通知～"

    lines = [f"# 【{label}】有货商品"]
    for i, p in enumerate(sort_by_price(items), 1):
        lines.append(_item_line(i, p))
    return "\n".join(lines)


def format_search_results(query: str, products: list["Product"]) -> str:
    lines = [f"# 🔍「{query}」有货商品"]
    for i, p in enumerate(sort_by_price(products), 1):
        lines.append(_item_line(i, p))
    return "\n".join(lines)


def format_product_detail(p: "Product") -> str:
    lines = [f"# {p.title}", f"\n{_price_label(p)}".rstrip(" ·")]
    if p.description:
        desc = p.description
        if len(desc) > 400:
            desc = desc[:400] + "...更多详情见下方链接"
        lines.append(f"\n{desc}")
    lines.append(f"\n🔗 {p.url}")
    return "\n".join(lines)


_DIGEST_LABELS = {
    "new": "🆕 新品",
    "relisted": "🔄 上架",
    "restocked": "🔔 补货",
}


def format_daily_digest(events: list[tuple[str, "Product"]]) -> str:
    by_type: dict[str, list] = defaultdict(list)
    for event_type, p in events:
        by_type[event_type].append(p)

    parts = ["# 📊 00:00-09:00 静默时段商品动态"]
    for event_type in ("new", "relisted", "restocked"):
        products = by_type.get(event_type)
        if not products:
            continue
        block = [f"## {_DIGEST_LABELS[event_type]}"]
        for p in products:
            block.append(_notice_line(p))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def format_restock_notice(products: list["Product"]) -> str:
    lines = ["# 🔔 补货通知"]
    for p in products:
        lines.append(_notice_line(p))
    return "\n".join(lines)


def format_new_product_notice(products: list["Product"]) -> str:
    lines = ["# 🆕 新品上架"]
    for p in products:
        lines.append(_notice_line(p))
    return "\n".join(lines)


_TWEET_TEXT_LIMIT = 800
_REPLY_TEXT_LIMIT = 400
_QUOTE_TEXT_LIMIT = 200

_HASHTAG_RE = re.compile(r"#[^\s#]+")
_CST = timezone(timedelta(hours=8))


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _strip_hashtags(text: str) -> str:
    """去掉 #tag，避免 QQ Markdown 把它们当成标题。"""
    text = _HASHTAG_RE.sub("", text or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _format_tweet_time(ts: int) -> str:
    if not ts:
        return ""
    dt = datetime.fromtimestamp(ts, _CST)
    return f"{dt.year}年{dt.month}月{dt.day}日 {dt.hour:02d}:{dt.minute:02d}"


def _heading(label: str, ts: int = 0) -> str:
    """emoji 小标题加粗，时间不加粗。"""
    time_str = _format_tweet_time(ts)
    if time_str:
        return f"**{label}** {time_str}"
    return f"**{label}**"


def _quote_block(tweet: "Tweet") -> str | None:
    if not tweet.quote_handle:
        return None
    quote = _clip(_strip_hashtags(tweet.quote_text or ""), _QUOTE_TEXT_LIMIT).replace("\n", "\n> ")
    return f"> 引用 @{tweet.quote_handle}：\n> {quote}"


def format_tweet_notice(
    tweet: "Tweet",
    display_name: str,
    thread: list["Tweet"] | None = None,
) -> str:
    items = list(thread) if thread else [tweet]
    root = items[0]
    if root.is_retweet:
        title = _heading(f"🔁 转发了 @{root.author_handle}", root.created_timestamp)
    else:
        title = _heading(f"🌐 {display_name}", root.created_timestamp)

    body = _clip(_strip_hashtags(root.text), _TWEET_TEXT_LIMIT) or "（无文字）"
    parts = [title, "", body]
    quote = _quote_block(root)
    if quote:
        parts.append(f"\n{quote}")

    for reply in items[1:]:
        parts.append("")
        parts.append(_heading("💬 追加评论", reply.created_timestamp))
        parts.append(_clip(_strip_hashtags(reply.text), _REPLY_TEXT_LIMIT) or "（无文字）")
        reply_quote = _quote_block(reply)
        if reply_quote:
            parts.append(reply_quote)

    if root.has_video:
        parts.append("\n🎬 含视频，点链接查看")

    parts.append("")
    parts.append(_heading("🔗 原贴链接"))
    parts.append(root.url)
    return "\n".join(parts)
