from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shop.models import Product


def format_price(price) -> str:
    """展示价格：整数不带小数；有小数只保留 1 位（如 21.50 → 21.5，22.00 → 22）。"""
    if price is None:
        return ""
    text = str(price).strip()
    if not text:
        return ""
    raw = text
    for token in ("元", "￥", "¥", "r", "R", "CNY", "cny"):
        raw = raw.replace(token, "")
    raw = raw.strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return text
    quantized = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return str(int(quantized))
    return format(quantized, "f")


def _price_label(p: "Product") -> str:
    price = format_price(getattr(p, "price", ""))
    return f"{price}r · " if price else ""


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
        for i, p in enumerate(items, 1):
            block.append(_item_line(i, p))
        parts.append("\n".join(block))

    return "\n\n".join(parts)


def filter_category_products(
    products: dict[str, "Product"],
    category_ids: list[int],
) -> list:
    id_set = set(category_ids)
    return [p for p in products.values() if p.in_stock and p.category_id in id_set]


def format_category_products(items: list["Product"], label: str) -> str:
    if not items:
        return f"## 【{label}】\n\n暂时没有有货商品，补货时会通知～"

    lines = [f"# 【{label}】有货商品"]
    for i, p in enumerate(items, 1):
        lines.append(_item_line(i, p))
    return "\n".join(lines)


def format_search_results(query: str, products: list["Product"]) -> str:
    lines = [f"# 🔍「{query}」有货商品"]
    for i, p in enumerate(products, 1):
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


_CONTENT_CHANGE_LABELS = {
    "new_product": "新增商品",
    "title": "商品标题",
    "description": "商品说明",
    "cover": "商品封面",
    "detail_images": "详情图片",
}


def format_content_change_notice(changes: list) -> str:
    urgent = [c for c in changes if getattr(c, "warranty_shortened", False)]
    normal = [c for c in changes if not getattr(c, "warranty_shortened", False)]

    lines = ["# 📝 商品资料变化待处理"]
    if urgent:
        lines.append(
            f"⚠️ **成品号质保缩短 {len(urgent)} 件（重要，请优先处理）**；"
            f"其余资料变化 {len(normal)} 件。"
        )
        lines.append("私聊发 `待审清单` 可按序号逐个审核。")
    else:
        lines.append(
            f"发现 {len(changes)} 个商品的说明或图片来源发生变化，"
            "可私聊发 `待审清单` 按序号审核。"
        )

    shown = 0
    for change in urgent + normal:
        if shown >= 12:
            break
        labels = "、".join(
            _CONTENT_CHANGE_LABELS.get(field, field) for field in change.changed_fields
        )
        header = change.title
        if getattr(change, "warranty_shortened", False):
            header = f"⚠️ {header}"
        lines.extend((f"\n## {header}", f"变化：{labels}"))
        if getattr(change, "warranty_summary", ""):
            lines.append(f"质保：{change.warranty_summary}")
        if getattr(change, "previous_title", "") and "title" in change.changed_fields:
            lines.append(f"原标题：{change.previous_title}")
        lines.append(change.url)
        shown += 1
    if len(changes) > shown:
        lines.append(f"\n另有 {len(changes) - shown} 个商品发生变化。私聊 `待审清单` 查看全部。")
    return "\n".join(lines)
