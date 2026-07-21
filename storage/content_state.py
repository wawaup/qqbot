import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_CONTENT_STATE_FILE = Path("content_state.json")


@dataclass(frozen=True)
class ContentChange:
    product_id: str
    title: str
    url: str
    changed_fields: tuple[str, ...]


def _description_hash(description_html: str) -> str:
    return hashlib.sha256(description_html.encode("utf-8")).hexdigest()


def build_snapshot(products: dict[str, dict]) -> dict[str, dict]:
    snapshot = {}
    for product_id, product in products.items():
        detail_image_urls = product.get("detail_image_urls", [])
        if not isinstance(detail_image_urls, list):
            detail_image_urls = []
        description_html = product.get("description_html", "")
        if not isinstance(description_html, str):
            description_html = ""
        snapshot[product_id] = {
            "title": str(product.get("title", "")),
            "url": str(product.get("url", "")),
            "description_sha256": _description_hash(description_html),
            "cover_url": str(product.get("cover_url", "")),
            "detail_image_urls": [str(url) for url in detail_image_urls],
        }
    return snapshot


def load_snapshot() -> dict[str, dict]:
    if not _CONTENT_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_CONTENT_STATE_FILE.read_text(encoding="utf-8"))
        products = data.get("products", {})
        return products if isinstance(products, dict) else {}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def save_snapshot(products: dict[str, dict]) -> None:
    data = {
        "schema_version": 1,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "products": products,
    }
    temporary_file = _CONTENT_STATE_FILE.with_name(f".{_CONTENT_STATE_FILE.name}.tmp")
    temporary_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(_CONTENT_STATE_FILE)


def diff_snapshots(
    previous: dict[str, dict], current: dict[str, dict]
) -> list[ContentChange]:
    changes = []
    for product_id, product in sorted(current.items()):
        old = previous.get(product_id)
        if old is None:
            changed_fields = ("new_product",)
        else:
            fields = []
            if old.get("title") != product.get("title"):
                fields.append("title")
            if old.get("description_sha256") != product.get("description_sha256"):
                fields.append("description")
            if old.get("cover_url") != product.get("cover_url"):
                fields.append("cover")
            if old.get("detail_image_urls") != product.get("detail_image_urls"):
                fields.append("detail_images")
            changed_fields = tuple(fields)
        if changed_fields:
            changes.append(
                ContentChange(
                    product_id=product_id,
                    title=product.get("title", ""),
                    url=product.get("url", ""),
                    changed_fields=changed_fields,
                )
            )
    return changes
