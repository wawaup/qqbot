"""
状态持久化：维护上次扫描的商品快照，计算新上架商品。
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
        return data.get("products", {})
    except (json.JSONDecodeError, KeyError):
        return {}


def save_state(products: dict[str, "Product"]) -> None:
    """保存当前扫描结果到快照文件。"""
    data = {
        "last_scan": datetime.now().isoformat(timespec="seconds"),
        "products": {
            pid: {
                "title": p.title,
                "url": p.url,
                "category": p.category,
                "in_stock": p.in_stock,
                "price": p.price,
            }
            for pid, p in products.items()
        },
    }
    _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def diff_states(
    old: dict[str, dict], new: dict[str, "Product"]
) -> list["Product"]:
    """返回本次新上架（之前缺货或不存在，现在有货）的商品列表。"""
    newly_in_stock = []
    for pid, product in new.items():
        if not product.in_stock:
            continue
        prev = old.get(pid)
        if prev is None or not prev.get("in_stock", True):
            newly_in_stock.append(product)
    return newly_in_stock
