"""
状态持久化：维护上次扫描的商品快照，计算新上架/重新上架/补货商品。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shop.models import Product

_STATE_FILE = Path("state.json")


def load_state() -> dict[str, dict]:
    """加载上次快照，返回 {product_id: {...}} 字典。"""
    if not _STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        products = data.get("products", {})
        for entry in products.values():
            entry.setdefault("listed", True)  # 兼容旧格式：老数据都是当时在架的商品
            entry.setdefault("category_id", None)
            entry.setdefault("market_price", "")
            entry.setdefault("stock_count", 0)
            entry.setdefault("description", "")
            entry.setdefault("image", "")
        return products
    except (json.JSONDecodeError, KeyError):
        return {}


def save_state(products: dict[str, "Product"]) -> None:
    """合并保存当前扫描结果：本次出现的商品刷新字段并标记在架，
    本次消失的旧商品保留记录、仅标记下架，不删除。
    """
    old = load_state()

    merged: dict[str, dict] = dict(old)
    for pid, p in products.items():
        merged[pid] = {
            "title": p.title,
            "url": p.url,
            "category": p.category,
            "category_id": p.category_id,
            "in_stock": p.in_stock,
            "price": p.price,
            "market_price": p.market_price,
            "stock_count": p.stock_count,
            "description": p.description,
            "image": p.image,
            "listed": True,
        }
    for pid, entry in merged.items():
        if pid not in products:
            entry["listed"] = False

    data = {
        "last_scan": datetime.now().isoformat(timespec="seconds"),
        "products": merged,
    }
    _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def diff_states(
    old: dict[str, dict], new: dict[str, "Product"]
) -> tuple[list["Product"], list["Product"], list["Product"]]:
    """返回 (新品列表, 重新上架列表, 补货列表)，三者互斥。

    - 新品：goods_key 之前完全没出现过，现在在架且有货
    - 上架：goods_key 之前出现过但已下架（listed=False），现在在架且有货
    - 补货：goods_key 之前在架但缺货，现在在架且有货
    """
    new_products = []
    relisted_products = []
    restocked_products = []
    for pid, product in new.items():
        if not product.in_stock:
            continue
        prev = old.get(pid)
        if prev is None:
            new_products.append(product)
        elif not prev.get("listed", True):
            relisted_products.append(product)
        elif not prev.get("in_stock", True):
            restocked_products.append(product)
    return new_products, relisted_products, restocked_products
