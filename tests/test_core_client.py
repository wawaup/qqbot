from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from shop import core_client
from shop.models import Product


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_fetch_inventory_products_filters_delisted(monkeypatch):
    monkeypatch.setattr(core_client, "SHOP_CORE_BASE_URL", "http://core.test")
    monkeypatch.setattr(core_client, "SHOP_CORE_INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(core_client, "SHOP_CORE_TIMEOUT_SECONDS", 5.0)

    payload = {
        "last_scan": "2026-07-23T00:00:00+00:00",
        "products": {
            "listed": {
                "title": "On shelf",
                "url": "https://example/item/listed",
                "category": "GPT",
                "category_id": 1,
                "in_stock": True,
                "price": "10.00",
                "stock_count": 2,
                "description": "plain",
                "description_html": "<p>plain</p>",
                "cover_url": "https://cdn/cover.png",
                "detail_image_urls": ["https://cdn/d1.png"],
                "listed": True,
            },
            "gone": {
                "title": "Delisted",
                "url": "https://example/item/gone",
                "category": "GPT",
                "category_id": 1,
                "in_stock": False,
                "price": "1.00",
                "stock_count": 0,
                "description": "",
                "description_html": "",
                "cover_url": "",
                "detail_image_urls": [],
                "listed": False,
            },
        },
    }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            assert url.endswith("/api/internal/inventory/snapshot")
            assert headers["X-Internal-Token"] == "secret"
            return _FakeResponse(payload)

    monkeypatch.setattr(core_client.httpx, "AsyncClient", _FakeClient)

    products = await core_client.fetch_inventory_products()
    assert set(products) == {"listed"}
    product = products["listed"]
    assert isinstance(product, Product)
    assert product.in_stock is True
    assert product.description_html == "<p>plain</p>"
    assert product.detail_image_urls == ("https://cdn/d1.png",)


@pytest.mark.asyncio
async def test_fetch_inventory_products_requires_config(monkeypatch):
    monkeypatch.setattr(core_client, "SHOP_CORE_BASE_URL", "")
    monkeypatch.setattr(core_client, "SHOP_CORE_INTERNAL_TOKEN", "")
    with pytest.raises(core_client.ShopCoreError):
        await core_client.fetch_inventory_products()
