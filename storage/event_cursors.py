"""Cursor for shop-core inventory/content event consumption.

Stored in app_blobs when core is configured; local file is cache only.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from storage.core_blobs import QQBOT_EVENT_CURSORS, get_blob_payload, put_blob_payload

_FILE = Path("event_cursors.json")


def _empty() -> dict[str, Any]:
    return {
        "inventory_since": None,
        "inventory_last_id": 0,
        "content_since": None,
        "content_last_id": 0,
    }


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    base = _empty()
    base.update({k: data.get(k) for k in base if k in data})
    try:
        base["inventory_last_id"] = int(base.get("inventory_last_id") or 0)
    except (TypeError, ValueError):
        base["inventory_last_id"] = 0
    try:
        base["content_last_id"] = int(base.get("content_last_id") or 0)
    except (TypeError, ValueError):
        base["content_last_id"] = 0
    return base


def load() -> dict[str, Any]:
    remote = get_blob_payload(QQBOT_EVENT_CURSORS)
    if isinstance(remote, dict):
        return _normalize(remote)
    if not _FILE.exists():
        return _empty()
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    return _normalize(data)


def save(data: dict[str, Any]) -> None:
    payload = _normalize(deepcopy(data))
    # put_blob raises when fail-closed and core write fails.
    put_blob_payload(QQBOT_EVENT_CURSORS, payload, kind="runtime")
    _FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def advance_inventory(events: list[dict[str, Any]], cursors: dict[str, Any] | None = None) -> dict[str, Any]:
    """Update inventory cursor from a batch of events (already filtered/processed)."""
    state = _normalize(cursors or load())
    last_id = int(state.get("inventory_last_id") or 0)
    last_since = state.get("inventory_since")
    for event in events:
        try:
            event_id = int(event.get("id") or 0)
        except (TypeError, ValueError):
            event_id = 0
        if event_id > last_id:
            last_id = event_id
        created = event.get("created_at")
        # Keep the newest timestamp string (ISO-8601 sorts lexicographically).
        if isinstance(created, str) and created and (not last_since or created > str(last_since)):
            last_since = created
    state["inventory_last_id"] = last_id
    if last_since:
        state["inventory_since"] = last_since
    save(state)
    return state


def filter_new_inventory_events(events: list[dict[str, Any]], cursors: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Keep only events with id greater than last processed id."""
    state = _normalize(cursors or load())
    last_id = int(state.get("inventory_last_id") or 0)
    fresh: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            event_id = int(event.get("id") or 0)
        except (TypeError, ValueError):
            event_id = 0
        if event_id > last_id:
            fresh.append(event)
    # core may return desc when since is null; sort asc for stable processing
    fresh.sort(key=lambda e: int(e.get("id") or 0))
    return fresh
