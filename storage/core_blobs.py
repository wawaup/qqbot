"""Read/write operational blobs via shop-core (preferred over local JSON files).

When SHOP_CORE_BASE_URL + token are set, state/config is stored in DB.
Local files remain as offline fallback / bootstrap only.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import SHOP_CORE_BASE_URL, SHOP_CORE_INTERNAL_TOKEN, SHOP_CORE_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

BOT_KEYWORDS = "bot-keywords"
BOT_CATEGORY_COMMANDS = "bot-category-commands"
QQBOT_INVENTORY_STATE = "qqbot-inventory-state"
QQBOT_CONTENT_STATE = "qqbot-content-state"
QQBOT_REVIEW_SESSIONS = "qqbot-review-sessions"


def core_blobs_enabled() -> bool:
    return bool(SHOP_CORE_BASE_URL and SHOP_CORE_INTERNAL_TOKEN)


def _headers() -> dict[str, str]:
    return {
        "X-Internal-Token": SHOP_CORE_INTERNAL_TOKEN,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return SHOP_CORE_BASE_URL.rstrip("/")


def get_blob_payload(key: str) -> Any | None:
    """Return payload or None if missing / core unavailable."""
    if not core_blobs_enabled():
        return None
    url = f"{_base()}/api/internal/blobs/{key}"
    try:
        with httpx.Client(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=_headers())
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and "payload" in data:
            return data["payload"]
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("get blob %s failed: %s", key, exc)
        return None


def put_blob_payload(key: str, payload: Any, *, kind: str = "runtime") -> bool:
    """Write payload to core. Returns True on success."""
    if not core_blobs_enabled():
        return False
    url = f"{_base()}/api/internal/blobs/{key}"
    try:
        with httpx.Client(timeout=SHOP_CORE_TIMEOUT_SECONDS) as client:
            response = client.put(
                url,
                headers=_headers(),
                json={"kind": kind, "payload": payload},
            )
            response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("put blob %s failed: %s", key, exc)
        return False
