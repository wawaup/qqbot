from __future__ import annotations

from shop.core_client import product_from_inventory_event
from shop.models import Product
from storage import event_cursors
from scheduler.tasks import _partition_core_inventory_events


def test_filter_and_advance_inventory_cursors(tmp_path, monkeypatch):
    monkeypatch.setattr(event_cursors, "_FILE", tmp_path / "event_cursors.json")
    monkeypatch.setattr(event_cursors, "get_blob_payload", lambda key: None)
    monkeypatch.setattr(event_cursors, "put_blob_payload", lambda *a, **k: False)

    events = [
        {"id": 3, "event_type": "restocked", "product_id": "a", "created_at": "2026-01-03T00:00:00+00:00"},
        {"id": 1, "event_type": "new", "product_id": "b", "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "event_type": "relisted", "product_id": "c", "created_at": "2026-01-02T00:00:00+00:00"},
    ]
    fresh = event_cursors.filter_new_inventory_events(events)
    assert [e["id"] for e in fresh] == [1, 2, 3]

    event_cursors.advance_inventory(fresh)
    cursors = event_cursors.load()
    assert cursors["inventory_last_id"] == 3
    assert cursors["inventory_since"] == "2026-01-03T00:00:00+00:00"

    # Already processed ids are dropped
    again = event_cursors.filter_new_inventory_events(events)
    assert again == []


def test_partition_core_inventory_events_last_wins():
    products = {
        "a": Product(id="a", title="A", url="u", category="c", in_stock=True, price="1", stock_count=1),
        "b": Product(id="b", title="B", url="u", category="c", in_stock=True, price="2", stock_count=2),
        "gone": Product(id="gone", title="G", url="u", category="c", in_stock=False, price="0", stock_count=0),
    }
    events = [
        {"id": 1, "event_type": "new", "product_id": "a", "stock_count": 1, "title": "A"},
        {"id": 2, "event_type": "restocked", "product_id": "a", "stock_count": 1, "title": "A"},
        {"id": 3, "event_type": "relisted", "product_id": "b", "stock_count": 2, "title": "B"},
        {"id": 4, "event_type": "new", "product_id": "missing", "stock_count": 9, "title": "X"},
        {"id": 5, "event_type": "restocked", "product_id": "gone", "stock_count": 3, "title": "G"},
    ]
    new_p, relisted, restocked = _partition_core_inventory_events(events, products)
    assert [p.id for p in new_p] == []
    assert [p.id for p in restocked] == ["a"]
    assert [p.id for p in relisted] == ["b"]


def test_product_from_inventory_event_prefers_snapshot():
    products = {
        "x": Product(
            id="x",
            title="From snapshot",
            url="https://x",
            category="官方",
            category_id=1,
            in_stock=True,
            price="9",
            stock_count=3,
        )
    }
    product = product_from_inventory_event(
        {"product_id": "x", "title": "From event", "stock_count": 1, "price": "1"},
        products,
    )
    assert product is not None
    assert product.title == "From snapshot"
    assert product.price == "9"
