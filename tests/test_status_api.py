import json
import threading
from urllib.request import Request, urlopen

from api import server
from shop.models import Product
from storage import state


def test_save_state_persists_stock_count(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(state, "_STATE_FILE", state_file)

    state.save_state(
        {
            "g28zpj": Product(
                id="g28zpj",
                title="GPT Plus",
                url="https://example.test/item/g28zpj",
                category="GPT",
                category_id=1,
                price="21.50",
                stock_count=7,
                in_stock=True,
                description="private details",
                description_html="<p>private details</p>",
                cover_url="https://cdn.example/cover.png",
                detail_image_urls=("https://cdn.example/detail.png",),
            )
        }
    )

    snapshot = json.loads(state_file.read_text(encoding="utf-8"))
    assert snapshot["products"]["g28zpj"]["stock_count"] == 7
    assert snapshot["products"]["g28zpj"]["description_html"] == "<p>private details</p>"
    assert snapshot["products"]["g28zpj"]["cover_url"] == "https://cdn.example/cover.png"
    assert snapshot["products"]["g28zpj"]["detail_image_urls"] == [
        "https://cdn.example/detail.png"
    ]


def test_build_status_payload_exposes_only_public_fields(monkeypatch):
    monkeypatch.setattr(
        server.state,
        "load_snapshot",
        lambda: {
            "last_scan": "2026-07-21T18:00:00",
            "products": {
                "available": {
                    "title": "Available",
                    "price": "21.50",
                    "stock_count": 7,
                    "in_stock": True,
                    "listed": True,
                    "description": "must stay private",
                    "url": "https://example.test/private",
                },
                "delisted": {
                    "title": "Delisted",
                    "price": "37.00",
                    "stock_count": 13,
                    "in_stock": True,
                    "listed": False,
                },
            },
        },
    )

    payload = server.build_status_payload()

    assert payload == {
        "schema_version": 1,
        "updated_at": "2026-07-21T18:00:00",
        "products": [
            {
                "id": "available",
                "title": "Available",
                "price": "21.50",
                "stock_count": 7,
                "in_stock": True,
                "listed": True,
            },
            {
                "id": "delisted",
                "title": "Delisted",
                "price": "37.00",
                "stock_count": 13,
                "in_stock": False,
                "listed": False,
            },
        ],
    }


def test_status_endpoint_returns_json_cache_and_cors_headers(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_status_payload",
        lambda: {"schema_version": 1, "updated_at": None, "products": []},
    )
    httpd = server.create_status_server("127.0.0.1", 0, "https://shop.example")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = httpd.server_address
        request = Request(
            f"http://{host}:{port}/api/v1/catalog/status",
            headers={"Origin": "https://shop.example"},
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/json; charset=utf-8"
            assert response.headers["Cache-Control"] == "public, max-age=30"
            assert response.headers["Access-Control-Allow-Origin"] == "https://shop.example"
            assert json.load(response) == {
                "schema_version": 1,
                "updated_at": None,
                "products": [],
            }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
