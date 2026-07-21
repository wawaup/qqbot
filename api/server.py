import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from storage import state

logger = logging.getLogger(__name__)


def build_status_payload() -> dict:
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


def create_status_server(
    host: str, port: int, allowed_origin: str
) -> ThreadingHTTPServer:
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
            if allowed_origin == "*":
                self.send_header("Access-Control-Allow-Origin", "*")
            elif origin == allowed_origin:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

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
