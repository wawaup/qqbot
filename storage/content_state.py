import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bot.warranty import analyze_warranty_title_change
from storage.core_blobs import QQBOT_CONTENT_STATE, get_blob_payload, put_blob_payload

_CONTENT_STATE_FILE = Path("content_state.json")


@dataclass(frozen=True)
class ContentChange:
    product_id: str
    title: str
    url: str
    changed_fields: tuple[str, ...]
    previous_title: str = ""
    warranty_shortened: bool = False
    warranty_summary: str = ""
    suppressible: bool = False  # 仅上架时间话术、可降噪


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
            "category": str(product.get("category", "")),
            "description_sha256": _description_hash(description_html),
            "cover_url": str(product.get("cover_url", "")),
            "detail_image_urls": [str(url) for url in detail_image_urls],
        }
    return snapshot


def load_snapshot() -> dict[str, dict]:
    remote = get_blob_payload(QQBOT_CONTENT_STATE)
    if isinstance(remote, dict):
        # Preferred envelope: {schema_version, checked_at, products}
        if isinstance(remote.get("products"), dict):
            return remote["products"]
        # Bare product map fallback
        if remote and all(isinstance(v, dict) for v in remote.values()):
            return remote  # type: ignore[return-value]

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
    if put_blob_payload(QQBOT_CONTENT_STATE, data, kind="runtime"):
        return
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
        previous_title = ""
        warranty_shortened = False
        warranty_summary = ""
        suppressible = False
        if old is None:
            changed_fields = ("new_product",)
        else:
            fields = []
            previous_title = str(old.get("title") or "")
            new_title = str(product.get("title") or "")
            if previous_title != new_title:
                fields.append("title")
                analysis = analyze_warranty_title_change(
                    previous_title,
                    new_title,
                    category=str(product.get("category") or old.get("category") or ""),
                )
                if analysis is not None:
                    warranty_summary = analysis.summary
                    warranty_shortened = analysis.shortened
                    # 成品号标题仅上架时间话术、质保未变：若没有其它字段变化，可降噪
                    if analysis.listing_time_only and not analysis.shortened:
                        suppressible = True
            if old.get("description_sha256") != product.get("description_sha256"):
                fields.append("description")
                suppressible = False
            if old.get("cover_url") != product.get("cover_url"):
                fields.append("cover")
                suppressible = False
            if old.get("detail_image_urls") != product.get("detail_image_urls"):
                fields.append("detail_images")
                suppressible = False
            changed_fields = tuple(fields)
            # 仅标题上的上架时间话术噪音：不进入待处理列表
            if suppressible and changed_fields == ("title",):
                continue
        if changed_fields:
            changes.append(
                ContentChange(
                    product_id=product_id,
                    title=product.get("title", ""),
                    url=product.get("url", ""),
                    changed_fields=changed_fields,
                    previous_title=previous_title,
                    warranty_shortened=warranty_shortened,
                    warranty_summary=warranty_summary,
                    suppressible=suppressible,
                )
            )
    # 质保缩短的优先排前面
    changes.sort(key=lambda c: (0 if c.warranty_shortened else 1, c.product_id))
    return changes
