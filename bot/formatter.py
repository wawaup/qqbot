"""
消息格式化：将商品数据转成 QQ 群消息文本。
"""
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shop.models import Product


def _item_line(i: int, p: "Product") -> str:
    price = f" {p.price}r" if p.price else ""
    return f"{i}.{price} {p.title}\n   {p.url}\n"


def format_product_menu(products: dict[str, "Product"]) -> str:
    """生成全量商品清单（分类展示，仅含有货）。"""
    by_category: dict[str, list] = defaultdict(list)
    for p in products.values():
        if p.in_stock:
            by_category[p.category].append(p)

    if not by_category:
        return "当前没有有货商品，请稍后再查询～"

    lines = ["📋 商品清单"]
    for cat_name, items in by_category.items():
        lines.append(f"\n【{cat_name}】")
        for i, p in enumerate(items, 1):
            lines.append(_item_line(i, p))

    return "\n".join(lines).rstrip()


def format_category_products(
    products: dict[str, "Product"],
    categories: list[str],
    label: str,
) -> str:
    """生成指定分类的有货商品列表（含价格）。"""
    cat_set = set(categories)
    items = [
        p for p in products.values()
        if p.in_stock and p.category in cat_set
    ]

    if not items:
        return f"【{label}】暂时没有有货商品，补货时会通知～"

    lines = [f"【{label}】有货商品"]
    for i, p in enumerate(items, 1):
        lines.append(_item_line(i, p))
    return "\n".join(lines)


def format_restock_notice(products: list["Product"]) -> str:
    """生成补货通知消息。"""
    lines = ["🔔 补货通知"]
    for p in products:
        price = f" {p.price}r" if p.price else ""
        lines.append(f"\n{price} {p.title}\n   {p.url}\n")
    return "\n".join(lines)
