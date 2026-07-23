"""上新审核会话持久化（单店主串行队列 + 当前会话）。

优先 shop-core app_blobs；失败时回退本地 review_sessions.json（勿提交 git）。
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage.core_blobs import QQBOT_REVIEW_SESSIONS, get_blob_payload, put_blob_payload

_FILE = Path("review_sessions.json")
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict[str, Any]:
    return {
        "updated_at": None,
        "current": None,
        "queue": [],
        "history": [],
        "last_menu": [],
        "deferred_ids": [],
    }


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("current", None)
    data.setdefault("queue", [])
    data.setdefault("history", [])
    data.setdefault("last_menu", [])
    data.setdefault("deferred_ids", [])
    return data


def load() -> dict[str, Any]:
    with _LOCK:
        remote = get_blob_payload(QQBOT_REVIEW_SESSIONS)
        if isinstance(remote, dict):
            return _normalize(dict(remote))

        if not _FILE.exists():
            return _empty()
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _empty()
        if not isinstance(data, dict):
            return _empty()
        return _normalize(data)


def save(data: dict[str, Any]) -> None:
    with _LOCK:
        payload = deepcopy(data)
        payload["updated_at"] = _now()
        if put_blob_payload(QQBOT_REVIEW_SESSIONS, payload, kind="runtime"):
            return
        _FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def get_current() -> dict[str, Any] | None:
    current = load().get("current")
    return current if isinstance(current, dict) else None


def set_current(session: dict[str, Any] | None) -> None:
    data = load()
    data["current"] = session
    save(data)


def clear_current(*, archive: bool = True) -> dict[str, Any] | None:
    data = load()
    current = data.get("current") if isinstance(data.get("current"), dict) else None
    if current and archive:
        history = data.get("history")
        if not isinstance(history, list):
            history = []
        history.append({**current, "archived_at": _now()})
        data["history"] = history[-50:]
    data["current"] = None
    save(data)
    return current


def enqueue(session: dict[str, Any]) -> int:
    data = load()
    queue = data.get("queue")
    if not isinstance(queue, list):
        queue = []
    queue.append(session)
    data["queue"] = queue
    save(data)
    return len(queue)


def pop_queue() -> dict[str, Any] | None:
    data = load()
    queue = data.get("queue")
    if not isinstance(queue, list) or not queue:
        return None
    item = queue.pop(0)
    data["queue"] = queue
    save(data)
    return item if isinstance(item, dict) else None


def queue_length() -> int:
    queue = load().get("queue")
    return len(queue) if isinstance(queue, list) else 0


def set_last_menu(items: list[dict[str, Any]]) -> None:
    data = load()
    data["last_menu"] = items
    save(data)


def get_last_menu() -> list[dict[str, Any]]:
    menu = load().get("last_menu")
    return menu if isinstance(menu, list) else []


def get_deferred_ids() -> set[str]:
    ids = load().get("deferred_ids")
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids if x}


def mark_deferred(product_id: str) -> None:
    data = load()
    ids = data.get("deferred_ids")
    if not isinstance(ids, list):
        ids = []
    if product_id not in ids:
        ids.append(product_id)
    data["deferred_ids"] = ids
    save(data)


def clear_deferred(product_id: str) -> None:
    data = load()
    ids = data.get("deferred_ids")
    if not isinstance(ids, list):
        return
    data["deferred_ids"] = [x for x in ids if x != product_id]
    save(data)
