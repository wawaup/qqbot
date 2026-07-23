"""Load inventory from shop-core instead of scraping LDXP directly."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import SHOP_CORE_BASE_URL, SHOP_CORE_INTERNAL_TOKEN, SHOP_CORE_TIMEOUT_SECONDS
from shop.models import Product

logger = logging.getLogger(__name__)


class ShopCoreError(RuntimeError):
    """shop-core inventory request failed."""


def _headers() -> dict[str, str]:
    return {
        "X-Internal-Token": SHOP_CORE_INTERNAL_TOKEN,
        "Accept": "application/json",
    }


def _to_product(product_id: str, item: dict[str, Any]) -> Product:
    detail_image_urls = item.get("detail_image_urls") or []
    if not isinstance(detail_image_urls, list):
        detail_image_urls = []
    stock_count = int(item.get("stock_count") or 0)
    listed = bool(item.get("listed", True))
    in_stock = listed and bool(item.get("in_stock", stock_count > 0))
    return Product(
        id=product_id,
        title=str(item.get("title") or product_id),
        url=str(item.get("url") or ""),
        category=str(item.get("category") or "其他"),
        category_id=item.get("category_id") if isinstance(item.get("category_id"), int) else None,
        in_stock=in_stock,
        price=str(item.get("price") or ""),
        stock_count=stock_count if listed else 0,
        description=str(item.get("description") or ""),
        description_html=str(item.get("description_html") or ""),
        cover_url=str(item.get("cover_url") or ""),
        detail_image_urls=tuple(str(url) for url in detail_image_urls if isinstance(url, str)),
    )


async def fetch_inventory_products() -> dict[str, Product]:
    """Return currently listed products from shop-core inventory snapshot.

    Delisted rows are omitted so existing merge-state semantics still mark them
    listed=False on save_state().
    """
    if not SHOP_CORE_BASE_URL:
        raise ShopCoreError("SHOP_CORE_BASE_URL 未配置")
    if not SHOP_CORE_INTERNAL_TOKEN:
        raise ShopCoreError("SHOP_CORE_INTERNAL_TOKEN 未配置")

    url = f"{SHOP_CORE_BASE_URL.rstrip('/')}/api/internal/inventory/snapshot"
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=_headers())
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        raise ShopCoreError("shop-core snapshot 响应格式错误")
    raw_products = payload.get("products")
    if not isinstance(raw_products, dict):
        raise ShopCoreError("shop-core snapshot 缺少 products")

    products: dict[str, Product] = {}
    for product_id, item in raw_products.items():
        if not isinstance(product_id, str) or not isinstance(item, dict):
            continue
        if not bool(item.get("listed", True)):
            continue
        products[product_id] = _to_product(product_id, item)
    logger.info("shop-core inventory loaded: %s listed products", len(products))
    return products


async def fetch_status_payload() -> dict[str, Any]:
    """Proxy-friendly public status payload from shop-core."""
    if not SHOP_CORE_BASE_URL:
        raise ShopCoreError("SHOP_CORE_BASE_URL 未配置")
    url = f"{SHOP_CORE_BASE_URL.rstrip('/')}/api/v1/catalog/status"
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ShopCoreError("shop-core status 契约不匹配")
    return payload


def _require_core() -> str:
    if not SHOP_CORE_BASE_URL:
        raise ShopCoreError("SHOP_CORE_BASE_URL 未配置")
    if not SHOP_CORE_INTERNAL_TOKEN:
        raise ShopCoreError("SHOP_CORE_INTERNAL_TOKEN 未配置")
    return SHOP_CORE_BASE_URL.rstrip("/")


def fetch_review_queue_sync(*, pending_only: bool = True) -> list[dict[str, Any]]:
    """Sync helper for menu building (review skill is mostly sync around storage)."""
    base = _require_core()
    url = f"{base}/api/internal/review-queue"
    with httpx.Client(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = client.get(
            url,
            headers=_headers(),
            params={"pending_only": str(pending_only).lower()},
        )
        response.raise_for_status()
        payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else []


async def fetch_review_queue(*, pending_only: bool = True) -> list[dict[str, Any]]:
    base = _require_core()
    url = f"{base}/api/internal/review-queue"
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.get(
            url,
            headers=_headers(),
            params={"pending_only": str(pending_only).lower()},
        )
        response.raise_for_status()
        payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else []


async def fetch_publish_context(product_id: str) -> dict[str, Any]:
    base = _require_core()
    url = f"{base}/api/internal/publish-context/{product_id}"
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=_headers())
        if response.status_code == 404:
            raise ShopCoreError(f"商品不存在：{product_id}")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ShopCoreError("publish-context 响应格式错误")
    return payload


async def publish_product(product_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Call shop-core atomic publish. Raises ShopCoreError with detail on 4xx."""
    base = _require_core()
    url = f"{base}/api/internal/products/{product_id}/publish"
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=_headers(), json=body)
        if response.status_code >= 400:
            detail = response.text
            try:
                data = response.json()
                if isinstance(data, dict) and data.get("detail"):
                    detail = str(data["detail"])
            except Exception:
                pass
            raise ShopCoreError(f"publish 失败 ({response.status_code}): {detail}")
        payload = response.json()
    if not isinstance(payload, dict):
        raise ShopCoreError("publish 响应格式错误")
    return payload


async def record_review_decision(
    product_id: str,
    *,
    status: str,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist human review outcome into shop-core review_queue (DB audit)."""
    base = _require_core()
    url = f"{base}/api/internal/review-queue/{product_id}/decision"
    body = {"status": status, "decision": decision or {}}
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=_headers(), json=body)
        if response.status_code >= 400:
            detail = response.text
            try:
                data = response.json()
                if isinstance(data, dict) and data.get("detail"):
                    detail = str(data["detail"])
            except Exception:
                pass
            raise ShopCoreError(f"record decision 失败 ({response.status_code}): {detail}")
        payload = response.json()
    if not isinstance(payload, dict):
        raise ShopCoreError("record decision 响应格式错误")
    return payload


async def fetch_inventory_events(*, since: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Inventory change events from shop-core (new|relisted|restocked)."""
    base = _require_core()
    url = f"{base}/api/internal/inventory/events"
    params: dict[str, Any] = {"limit": limit}
    if since:
        params["since"] = since
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=_headers(), params=params)
        response.raise_for_status()
        payload = response.json()
    events = payload.get("events") if isinstance(payload, dict) else None
    return events if isinstance(events, list) else []


async def fetch_content_change_events(*, since: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Description/content change events from shop-core worker."""
    base = _require_core()
    url = f"{base}/api/internal/content/changes"
    params: dict[str, Any] = {"limit": limit}
    if since:
        params["since"] = since
    async with httpx.AsyncClient(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=_headers(), params=params)
        response.raise_for_status()
        payload = response.json()
    events = payload.get("events") if isinstance(payload, dict) else None
    return events if isinstance(events, list) else []


def product_from_inventory_event(
    event: dict[str, Any],
    products: dict[str, Product] | None = None,
) -> Product | None:
    """Map a core inventory event to a Product for notifications."""
    product_id = str(event.get("product_id") or "").strip()
    if not product_id:
        return None
    if products and product_id in products:
        return products[product_id]
    stock_count = int(event.get("stock_count") or 0)
    return Product(
        id=product_id,
        title=str(event.get("title") or product_id),
        url=str(event.get("url") or ""),
        category=str(event.get("category") or "其他"),
        category_id=event.get("category_id") if isinstance(event.get("category_id"), int) else None,
        in_stock=stock_count > 0,
        price=str(event.get("price") or ""),
        stock_count=stock_count,
        description=str(event.get("description") or ""),
        description_html=str(event.get("description_html") or ""),
        cover_url=str(event.get("cover_url") or ""),
    )


def fetch_catalog_products_sync() -> list[dict[str, Any]]:
    """Public catalog product rows (includes hidden / not-yet-published)."""
    if not SHOP_CORE_BASE_URL:
        raise ShopCoreError("SHOP_CORE_BASE_URL 未配置")
    url = f"{SHOP_CORE_BASE_URL.rstrip('/')}/api/catalog/products"
    with httpx.Client(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    products = payload.get("products") if isinstance(payload, dict) else None
    return products if isinstance(products, list) else []
