import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import (
    INVENTORY_SOURCE,
    SHOP_CORE_BASE_URL,
    SHOP_CORE_TIMEOUT_SECONDS,
    STATUS_API_PROXY_CORE,
)
from storage import state

logger = logging.getLogger(__name__)


def build_status_payload_from_state() -> dict:
    snapshot = state.load_snapshot()
    public_products = []
    for product_id, product in sorted(snapshot["products"].items()):
        listed = bool(product.get("listed", True))
        public_products.append(
            {
                "id": product_id,
                "title": product.get("title", ""),
                "price": str(product.get("price", "")),
                "stock_count": int(product.get("stock_count", 0)),
                "in_stock": listed and bool(product.get("in_stock", False)),
                "listed": listed,
            }
        )
    return {
        "schema_version": 1,
        "updated_at": snapshot["last_scan"],
        "products": public_products,
    }


def build_status_payload_from_core() -> dict:
    if not SHOP_CORE_BASE_URL:
        raise RuntimeError("SHOP_CORE_BASE_URL 未配置")
    url = f"{SHOP_CORE_BASE_URL.rstrip('/')}/api/v1/catalog/status"
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=SHOP_CORE_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("shop-core status 契约不匹配")
    if not isinstance(payload.get("products"), list):
        raise RuntimeError("shop-core status 缺少 products")
    return payload


def build_status_payload() -> dict:
    """Prefer shop-core when inventory is sourced from core; fall back to local state."""
    use_core = STATUS_API_PROXY_CORE and INVENTORY_SOURCE in {"shop-core", "core"} and bool(SHOP_CORE_BASE_URL)
    if use_core:
        try:
            return build_status_payload_from_core()
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as error:
            logger.warning("proxy status from shop-core failed, fallback to local state: %s", error)
    return build_status_payload_from_state()


def create_status_server(
    host: str, port: int, allowed_origin: str
) -> ThreadingHTTPServer:
    allowed_origins = frozenset(
        origin.strip() for origin in allowed_origin.split(",") if origin.strip()
    )

    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._write_json(200, {"status": "ok"}, cache_control="no-store")
                return
            if self.path == "/api/v1/catalog/status":
                self._write_json(
                    200,
                    build_status_payload(),
                    cache_control="public, max-age=30",
                )
                return
            self._write_json(404, {"error": "not_found"}, cache_control="no-store")

        def _write_json(self, status: int, payload: dict, cache_control: str) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            origin = self.headers.get("Origin")
            if "*" in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", "*")
            elif origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("status api client disconnected before response completed")

        def log_message(self, format: str, *args) -> None:
            logger.debug("status api: " + format, *args)

    return ThreadingHTTPServer((host, port), StatusHandler)


def start_status_server(
    host: str, port: int, allowed_origin: str
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = create_status_server(host, port, allowed_origin)
    thread = threading.Thread(
        target=httpd.serve_forever,
        name="qqbot-status-api",
        daemon=True,
    )
    thread.start()
    logger.info("商品状态接口已启动：http://%s:%s", host, httpd.server_port)
    return httpd, thread
