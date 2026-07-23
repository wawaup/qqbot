import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from config import (
    INVENTORY_SOURCE,
    REVIEW_API_ENABLED,
    REVIEW_API_TOKEN,
    SHOP_CORE_BASE_URL,
    SHOP_CORE_TIMEOUT_SECONDS,
    STATUS_API_PROXY_CORE,
)
from storage import state

logger = logging.getLogger(__name__)

_bot_client = None
_bot_loop: asyncio.AbstractEventLoop | None = None


def set_bot_client(client) -> None:
    """由 main/scheduler 注入，供审核 API 主动私信。"""
    global _bot_client, _bot_loop
    _bot_client = client
    try:
        _bot_loop = asyncio.get_running_loop()
    except RuntimeError:
        _bot_loop = None


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


def _run_coro(coro):
    """在 HTTP 工作线程中跑 async 审核逻辑；若 bot 主 loop 可用则挂到其上。"""
    loop = _bot_loop
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=120)
    return asyncio.run(coro)


def create_status_server(
    host: str, port: int, allowed_origin: str
) -> ThreadingHTTPServer:
    allowed_origins = frozenset(
        origin.strip() for origin in allowed_origin.split(",") if origin.strip()
    )

    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/healthz":
                self._write_json(200, {"status": "ok"}, cache_control="no-store")
                return
            if path == "/api/v1/catalog/status":
                self._write_json(
                    200,
                    build_status_payload(),
                    cache_control="public, max-age=30",
                )
                return
            if path == "/api/v1/review/status" and REVIEW_API_ENABLED:
                if not self._review_authorized():
                    return
                from storage import review_sessions

                current = review_sessions.get_current()
                self._write_json(
                    200,
                    {
                        "current": current,
                        "queue_length": review_sessions.queue_length(),
                    },
                    cache_control="no-store",
                )
                return
            self._write_json(404, {"error": "not_found"}, cache_control="no-store")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if not REVIEW_API_ENABLED:
                self._write_json(404, {"error": "not_found"}, cache_control="no-store")
                return
            if path not in {"/api/v1/review/start", "/api/v1/review/test"}:
                self._write_json(404, {"error": "not_found"}, cache_control="no-store")
                return
            if not self._review_authorized():
                return

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                body = {}
            if not isinstance(body, dict):
                body = {}

            # 也支持 query ?product_id=
            qs = parse_qs(parsed.query)
            product_id = str(body.get("product_id") or (qs.get("product_id") or [""])[0]).strip()
            notify = bool(body.get("notify", True))

            try:
                from bot import review as review_skill

                if path == "/api/v1/review/test" or body.get("test"):
                    session, message = _run_coro(review_skill.start_test_review())
                else:
                    if not product_id:
                        self._write_json(
                            400,
                            {"error": "product_id_required", "hint": "POST {\"product_id\":\"g28zpj\"}"},
                            cache_control="no-store",
                        )
                        return
                    session, message = _run_coro(
                        review_skill.start_review(product_id, source="api")
                    )
            except KeyError as exc:
                self._write_json(404, {"error": "product_not_found", "detail": str(exc)}, cache_control="no-store")
                return
            except Exception as exc:
                logger.exception("review api failed")
                self._write_json(500, {"error": "review_failed", "detail": str(exc)}, cache_control="no-store")
                return

            notified = False
            if notify and _bot_client is not None and hasattr(_bot_client, "push_c2c_to_owners"):
                try:
                    notified = bool(_run_coro(_bot_client.push_c2c_to_owners(message)))
                except Exception as exc:
                    logger.warning("review notify failed: %s", exc)

            self._write_json(
                200,
                {
                    "ok": True,
                    "notified": notified,
                    "session_id": session.get("id"),
                    "product_id": (session.get("product") or {}).get("id"),
                    "message_preview": message[:500],
                    "message": message,
                },
                cache_control="no-store",
            )

        def _review_authorized(self) -> bool:
            if not REVIEW_API_TOKEN:
                # 未配置 token 时仅允许本机，降低误暴露风险
                client = self.client_address[0] if self.client_address else ""
                if client not in {"127.0.0.1", "::1", "localhost"}:
                    self._write_json(403, {"error": "forbidden_remote_without_token"}, cache_control="no-store")
                    return False
                return True
            token = self.headers.get("X-Review-Token") or ""
            if token != REVIEW_API_TOKEN:
                self._write_json(401, {"error": "unauthorized"}, cache_control="no-store")
                return False
            return True

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
    if REVIEW_API_ENABLED:
        logger.info(
            "上新审核测试接口：POST /api/v1/review/test 与 POST /api/v1/review/start"
        )
    return httpd, thread
