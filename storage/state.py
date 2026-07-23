"""
状态持久化：维护上次扫描的商品快照，计算新上架/重新上架/补货商品。

优先写入 shop-core app_blobs；失败或未配置时回退本地 state.json（勿提交 git）。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from storage.core_blobs import QQBOT_INVENTORY_STATE, get_blob_payload, put_blob_payload

if TYPE_CHECKING:
    from shop.models import Product

_STATE_FILE = Path("state.json")


def _normalize_products(products: dict) -> dict:
    if not isinstance(products, dict):
        return {}
    for entry in products.values():
        if not isinstance(entry, dict):
            continue
        entry.setdefault("listed", True)
        entry.setdefault("category_id", None)
        entry.setdefault("stock_count", 0)
        entry.setdefault("description_html", "")
        entry.setdefault("cover_url", "")
        entry.setdefault("detail_image_urls", [])
        if not isinstance(entry.get("description"), str):
            entry["description"] = ""
    return products


def load_snapshot() -> dict:
    """加载完整快照，同时兼容旧版状态文件。"""
    remote = get_blob_payload(QQBOT_INVENTORY_STATE)
    if isinstance(remote, dict) and ("products" in remote or "last_scan" in remote):
        products = _normalize_products(remote.get("products") or {})
        return {"last_scan": remote.get("last_scan"), "products": products}

    if not _STATE_FILE.exists():
        return {"last_scan": None, "products": {}}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        products = _normalize_products(data.get("products", {}))
        return {"last_scan": data.get("last_scan"), "products": products}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {"last_scan": None, "products": {}}


def load_state() -> dict[str, dict]:
    """加载上次快照，返回 {product_id: {...}} 字典。"""
    return load_snapshot()["products"]


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
            "stock_count": p.stock_count,
            "description": p.description,
            "description_html": p.description_html,
            "cover_url": p.cover_url,
            "detail_image_urls": list(p.detail_image_urls),
            "listed": True,
        }
    for pid, entry in merged.items():
        if pid not in products:
            entry["listed"] = False
            entry["in_stock"] = False
            entry["stock_count"] = 0

    data = {
        "last_scan": datetime.now().isoformat(timespec="seconds"),
        "products": merged,
    }
    if not put_blob_payload(QQBOT_INVENTORY_STATE, data, kind="runtime"):
        temporary_file = _STATE_FILE.with_name(f".{_STATE_FILE.name}.tmp")
        temporary_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_file.replace(_STATE_FILE)


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
