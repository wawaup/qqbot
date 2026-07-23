from __future__ import annotations

import pytest

from storage.core_blobs import BlobWriteError, put_blob_payload


def test_put_blob_fail_closed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import storage.core_blobs as core_blobs

    monkeypatch.setattr(core_blobs, "core_blobs_enabled", lambda: True)
    monkeypatch.setattr(core_blobs, "blob_fail_closed", lambda: True)
    monkeypatch.setattr(core_blobs, "SHOP_CORE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(core_blobs, "SHOP_CORE_INTERNAL_TOKEN", "token")
    monkeypatch.setattr(core_blobs, "SHOP_CORE_TIMEOUT_SECONDS", 0.1)

    with pytest.raises(BlobWriteError):
        put_blob_payload("qqbot-inventory-state", {"products": {}}, kind="runtime")


def test_put_blob_allows_false_when_not_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import storage.core_blobs as core_blobs

    monkeypatch.setattr(core_blobs, "core_blobs_enabled", lambda: True)
    monkeypatch.setattr(core_blobs, "blob_fail_closed", lambda: False)
    monkeypatch.setattr(core_blobs, "SHOP_CORE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(core_blobs, "SHOP_CORE_INTERNAL_TOKEN", "token")
    monkeypatch.setattr(core_blobs, "SHOP_CORE_TIMEOUT_SECONDS", 0.1)

    assert put_blob_payload("qqbot-inventory-state", {"products": {}}, kind="runtime") is False