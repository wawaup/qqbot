from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shop.models import Product


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
