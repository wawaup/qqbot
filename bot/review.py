"""上新 / 官方订阅商品卡片人工审核 Skill。

流程：
1. 从 state.json 取商品 → Grok 生成展示标题 + 操作步骤草稿
2. 私聊推送「原标题 vs 建议标题」与步骤，等待店主确认
3. 简短肯定 → 提交；「不用调整」→ 标题保留原文；修改意见 → 重拟后再审
4. 商品详情 HTML 不经模型改写，确认时按来源直接同步
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.formatter import format_price as _format_price
from bot.llm import GrokError, chat_messages, grok_configured
from config import (
    GROK_REVIEW_MAX_TOKENS,
    NAVIGATOR_ROOT,
    OWNER_USER_OPENIDS,
    REVIEW_APPLY_ENABLED,
)
from storage import review_sessions
from storage.state import load_state

logger = logging.getLogger(__name__)

APPROVE_RE = re.compile(
    r"^(可以|可|行|好|好的|确认|同意|没问题|通过|提交|就这样|就这样吧|ok|okay|yes|y|lgtm)[.!。！\s]*$",
    re.I,
)
# 「不用调整 / 省略这次更改」= 本轮完成：不改展示，标记完成并从待审移除
SKIP_COMPLETE_RE = re.compile(
    r"^(不用调整|不用改|不改|省略|省略这次|省略本次|跳过|本次不改|这次不改|"
    r"保留原标题|原标题|标题不用改|标题不用调整|标题可以不改|"
    r"标题保留|保持原标题|用原标题|无需更改|无需修改)[.!。！\s]*$",
    re.I,
)
# 暂不改 = 仍留在待审清单，下次再处理
DEFER_RE = re.compile(
    r"^(暂不改|暂时不改|先不改|下次再改|稍后再说|稍后处理|defer|later)[.!。！\s]*$",
    re.I,
)
CANCEL_RE = re.compile(
    r"^(取消|算了|不要|放弃|停止|stop|cancel)[.!。！\s]*$",
    re.I,
)
STATUS_RE = re.compile(r"^(审核状态|当前审核|review\s*status|status)$", re.I)
HELP_RE = re.compile(r"^(审核帮助|审核说明|review\s*help)$", re.I)
LIST_RE = re.compile(
    r"^(待审清单|审核清单|待审核|需要审核|审核列表|list\s*review|pending)[.!。！\s]*$",
    re.I,
)
SELECT_RE = re.compile(
    r"^(?:审核|选择|选|开始)?\s*(?:第\s*)?(\d{1,3})\s*(?:号|个|项)?[.!。！\s]*$",
    re.I,
)
START_RE = re.compile(
    r"^(?:审核|上新审核|review)\s+(?:测试|test|([A-Za-z0-9_-]{4,32}))\s*$",
    re.I,
)
START_TEST_RE = re.compile(r"^(?:审核测试|review\s*test)$", re.I)

# 导航站 review-queue 中视为仍待处理的状态
_PENDING_QUEUE_STATUSES = {
    "pending_manual_enrichment",
    "pending_human_review",
    "needs_review",
    "source_changed",
    "pending",
}

DRAFT_SYSTEM = """你是商品上架整理助手，服务「曼波导购」导航站。
根据店铺原始标题、详情，以及系统提供的可选项（品牌类目、已有档位、登录方式），生成上架草稿。

规则：
1. 只输出一个合法 JSON 对象，不要 Markdown 代码围栏，不要解释。
2. 不要编造原文没有的承诺、质保天数、价格、渠道事实。
3. proposed_title / short_title：面向用户；原标题足够好可与原文相同。
4. surface 只能是 prepared-accounts / official-membership / other。
5. usage.steps：只提取用户可执行动作；没有则空数组。
6. 网址必须写成 Markdown 链接且只能来自原文。
7. 官方订阅：target_category 只能来自可见品牌列表；fulfillment_mode 只能是
   self-service-redemption / assisted-topup / choice-at-purchase。
8. membership_guess：只能 attach 已有 tier_id，或 create_tier（new_tier 结构完整）。
   禁止编造不存在的 tier_id。
9. 成品号：prepared_guess.login_method 与 offer_group_id 必须来自提供的 login_guides；
   无法匹配时 surface 仍可为 prepared-accounts，但 blockers 写明需新建登录教程。
10. issues / blockers 用中文短句。

输出 JSON schema：
{
  "proposed_title": "string",
  "short_title": "string",
  "keep_original_title_recommended": false,
  "title_reason": "string",
  "surface": "prepared-accounts|official-membership|other",
  "surface_reason": "string",
  "target_category": "gpt-official-recharge|grok-official-recharge|cursor|claude|null",
  "fulfillment_mode": "string",
  "usage": {
    "heading": "操作步骤",
    "render": "timeline",
    "layout": "vertical",
    "intro_markdown": "string",
    "steps": [{"id": "step-01", "title": "动宾短语", "body_markdown": "说明", "image_asset": null}]
  },
  "usage_notes": "string",
  "plan_presentation": {
    "title": "string", "tagline": "string", "billingPeriod": "月",
    "membershipTypeOrder": 10, "ctaLabel": "购买", "includedLabel": "包含：",
    "features": ["string"], "officialPrice": "string", "recommended": false
  },
  "membership_guess": {
    "mode": "attach_option|create_tier",
    "tier_group_id": "string",
    "tier_id": "string",
    "option_label": "1 个月",
    "channel": "string",
    "new_tier": null
  },
  "prepared_guess": {
    "login_method": "string",
    "offer_group_id": "string",
    "delivery_category": "未接码|已接码",
    "warranty_label": "质保首登"
  },
  "issues": ["需店主确认"],
  "blockers": ["无法自动上架的原因"]
}
"""

OFFICIAL_SURFACE = "official-membership"
PREPARED_SURFACE = "prepared-accounts"
STEP_SURFACE = "await_surface"
STEP_OFFICIAL = "await_official_fields"
STEP_PREPARED = "await_prepared_fields"
STEP_COPY = "await_copy"
STEP_FINAL = "await_final"

SURFACE_RE = re.compile(r"^(官方|官方订阅|成品号|成品|跳过|other|其他)[.!。！\s]*$", re.I)
PUBLISH_RE = re.compile(r"^(发布|上架|可以发布|确认发布)[.!。！\s]*$", re.I)
OFFICIAL_CAT_RE = re.compile(
    r"^(?:类目|品牌|分类)\s*(gpt|claude|cursor|grok|gpt-official-recharge|grok-official-recharge|claude|cursor)\s*$",
    re.I,
)
ATTACH_TIER_RE = re.compile(r"^(?:挂到|档位)\s*([A-Za-z0-9_-]+)\s*$", re.I)
LOGIN_RE = re.compile(r"^(?:登录|登录方式)\s*([A-Za-z0-9_-]+)\s*$", re.I)
GROUP_RE = re.compile(r"^(?:分组|offer|offer_group)\s*([A-Za-z0-9_-]+)\s*$", re.I)
WARRANTY_RE = re.compile(r"^(?:质保)\s*(.+)$", re.I)
FULFILL_RE = re.compile(r"^(?:履约)\s*(卡密|代充|可选|自助|人工|self-service-redemption|assisted-topup|choice-at-purchase)\s*$", re.I)
CATEGORY_ALIASES = {
    "gpt": "gpt-official-recharge",
    "gpt-official-recharge": "gpt-official-recharge",
    "claude": "claude",
    "cursor": "cursor",
    "grok": "grok-official-recharge",
    "grok-official-recharge": "grok-official-recharge",
}
FULFILL_ALIASES = {
    "卡密": "self-service-redemption",
    "自助": "self-service-redemption",
    "代充": "assisted-topup",
    "人工": "assisted-topup",
    "可选": "choice-at-purchase",
    "self-service-redemption": "self-service-redemption",
    "assisted-topup": "assisted-topup",
    "choice-at-purchase": "choice-at-purchase",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _product_from_state(product_id: str) -> dict[str, Any] | None:
    products = load_state()
    raw = products.get(product_id)
    if not isinstance(raw, dict):
        return None
    return {
        "id": product_id,
        "title": str(raw.get("title") or product_id),
        "url": str(raw.get("url") or ""),
        "category": str(raw.get("category") or ""),
        "category_id": raw.get("category_id"),
        "price": str(raw.get("price") or ""),
        "in_stock": bool(raw.get("in_stock", False)),
        "listed": bool(raw.get("listed", True)),
        "description": str(raw.get("description") or ""),
        "description_html": str(raw.get("description_html") or ""),
        "cover_url": str(raw.get("cover_url") or ""),
        "detail_image_urls": list(raw.get("detail_image_urls") or []),
    }


def _guess_surface(title: str, category: str) -> str:
    text = f"{title} {category}".lower()
    # 成品号优先：避免「Plus 成品」被官方关键词截胡
    if any(k in text for k in ("成品", "账号", "邮箱交付", "接码", "成品号")):
        return "prepared-accounts"
    if any(
        k in text
        for k in (
            "官方",
            "代充",
            "充值",
            "直充",
            "卡密",
            "cdk",
            "订阅",
            "年卡",
            "月卡",
            "pro",
            "plus",
        )
    ):
        return "official-membership"
    return "other"


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _normalize_draft(raw: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    original = product["title"]
    proposed = str(raw.get("proposed_title") or original).strip() or original
    short_title = str(raw.get("short_title") or proposed).strip() or proposed
    usage_in = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    steps_in = usage_in.get("steps") if isinstance(usage_in.get("steps"), list) else []
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps_in, start=1):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        body = str(step.get("body_markdown") or step.get("body") or "").strip()
        if not title and not body:
            continue
        step_id = str(step.get("id") or f"step-{index:02d}").strip()
        steps.append(
            {
                "id": step_id,
                "title": title or f"步骤{index}",
                "body_markdown": body or title,
                "image_asset": step.get("image_asset"),
            }
        )
    surface = str(raw.get("surface") or "").strip()
    if surface not in {"prepared-accounts", "official-membership", "other"}:
        surface = _guess_surface(original, product.get("category", ""))
    issues = raw.get("issues") if isinstance(raw.get("issues"), list) else []
    blockers = raw.get("blockers") if isinstance(raw.get("blockers"), list) else []
    membership = raw.get("membership_guess") if isinstance(raw.get("membership_guess"), dict) else {}
    prepared = raw.get("prepared_guess") if isinstance(raw.get("prepared_guess"), dict) else {}
    plan = raw.get("plan_presentation") if isinstance(raw.get("plan_presentation"), dict) else {}
    target_category = raw.get("target_category")
    target_category = str(target_category).strip() if target_category else None
    fulfillment = str(raw.get("fulfillment_mode") or "").strip() or None
    return {
        "proposed_title": proposed,
        "short_title": short_title,
        "keep_original_title_recommended": bool(raw.get("keep_original_title_recommended")),
        "title_reason": str(raw.get("title_reason") or "").strip(),
        "surface": surface,
        "surface_reason": str(raw.get("surface_reason") or "").strip(),
        "target_category": target_category,
        "fulfillment_mode": fulfillment,
        "usage": {
            "heading": str(usage_in.get("heading") or "操作步骤"),
            "render": "timeline",
            "layout": str(usage_in.get("layout") or "vertical"),
            "intro_markdown": str(usage_in.get("intro_markdown") or "").strip(),
            "steps": steps,
        },
        "usage_notes": str(raw.get("usage_notes") or "").strip(),
        "plan_presentation": plan,
        "membership_guess": membership,
        "prepared_guess": prepared,
        "issues": [str(i) for i in issues if str(i).strip()],
        "blockers": [str(i) for i in blockers if str(i).strip()],
        "title_kept_original": proposed == original,
    }


async def generate_draft(
    product: dict[str, Any],
    *,
    feedback: str | None = None,
    previous_draft: dict[str, Any] | None = None,
    publish_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not grok_configured():
        raise GrokError("Grok 未配置，无法生成审核草稿")

    payload = {
        "product_id": product["id"],
        "original_title": product["title"],
        "source_category_name": product.get("category") or "",
        "price": product.get("price") or "",
        "description_markdown": (product.get("description") or "")[:6000],
        "description_html_excerpt": (product.get("description_html") or "")[:4000],
        "purchase_url": product.get("url") or "",
    }
    user_parts = [
        "请为以下商品生成导航站上架草稿 JSON：",
        json.dumps(payload, ensure_ascii=False),
    ]
    if publish_context:
        # Compact context for the model: categories, tier ids, login guides.
        compact = {
            "visible_official_categories": publish_context.get("visible_official_categories") or [],
            "membership_tiers": publish_context.get("membership_tiers") or {},
            "login_guides": publish_context.get("login_guides") or [],
            "official_fulfillment_modes": publish_context.get("official_fulfillment_modes") or [],
        }
        user_parts.append("系统可选项（禁止编造列表外 id）：")
        user_parts.append(json.dumps(compact, ensure_ascii=False)[:12000])
    if previous_draft:
        user_parts.append("上一版草稿：")
        user_parts.append(json.dumps(previous_draft, ensure_ascii=False))
    if feedback:
        user_parts.append(f"店主修改意见：{feedback}")
        user_parts.append("请按意见修改后重新输出完整 JSON。")

    text = await chat_messages(
        [
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        system=DRAFT_SYSTEM,
        max_tokens=GROK_REVIEW_MAX_TOKENS,
    )
    try:
        data = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError as exc:
        logger.error("审核草稿 JSON 解析失败: %s | raw=%s", exc, text[:400])
        raise GrokError("模型返回的草稿不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise GrokError("模型草稿必须是 JSON 对象")
    draft = _normalize_draft(data, product)
    # 若模型/启发式给出 other，但 core 已有 surface，优先信任 core
    if publish_context and draft.get("surface") == "other":
        core_product = publish_context.get("product") if isinstance(publish_context, dict) else None
        core_surface = str((core_product or {}).get("catalog_surface") or "")
        if core_surface in {PREPARED_SURFACE, OFFICIAL_SURFACE}:
            draft["surface"] = core_surface
            draft["surface_reason"] = (draft.get("surface_reason") or "") + "（沿用 core catalog_surface）"
    return draft


def classify_owner_reply(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return "empty"
    if CANCEL_RE.match(content):
        return "cancel"
    if DEFER_RE.match(content):
        return "defer"
    if SKIP_COMPLETE_RE.match(content):
        return "skip_complete"
    if APPROVE_RE.match(content):
        return "approve"
    if STATUS_RE.match(content):
        return "status"
    if HELP_RE.match(content):
        return "help"
    if LIST_RE.match(content):
        return "list"
    return "revise"


def _navigator_root() -> Path | None:
    root = Path(NAVIGATOR_ROOT) if NAVIGATOR_ROOT else None
    if root and root.is_dir():
        return root
    return None


def _queue_item_from_raw(data: dict[str, Any], *, fallback_id: str = "", source: str) -> dict[str, Any] | None:
    """Normalize shop-core / navigator review-queue rows into menu source items."""
    status = str(data.get("status") or "")
    if status and status not in _PENDING_QUEUE_STATUSES:
        if status.startswith("approved") or status in {
            "done",
            "completed",
            "skipped",
            "omitted",
            "published",
            "published_via_qqbot",
        }:
            return None
        # 其它未知状态：仍展示，避免漏审
    product_id = str(data.get("product_id") or fallback_id or "")
    if not product_id:
        return None
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    changed = data.get("changed_fields")
    if not isinstance(changed, list):
        changed = payload.get("changed_fields") if isinstance(payload.get("changed_fields"), list) else []
    detected_at = str(
        data.get("detected_at")
        or payload.get("detected_at")
        or data.get("updated_at")
        or ""
    )
    title = str(data.get("title") or data.get("display_title") or payload.get("title") or "")
    return {
        "product_id": product_id,
        "status": status or "pending",
        "changed_fields": list(changed or []),
        "detected_at": detected_at,
        "title": title,
        "source": source,
    }


def _load_review_queue_items() -> list[dict[str, Any]]:
    """Prefer shop-core review-queue; fall back to navigator JSON files."""
    items: list[dict[str, Any]] = []

    try:
        from shop.core_client import fetch_review_queue_sync

        core_items = fetch_review_queue_sync(pending_only=True)
        for raw in core_items:
            if not isinstance(raw, dict):
                continue
            item = _queue_item_from_raw(raw, source="shop-core")
            if item:
                items.append(item)
        if items:
            return items
    except Exception as exc:  # noqa: BLE001 — core optional for menu
        logger.info("shop-core review-queue unavailable, fallback navigator: %s", exc)

    root = _navigator_root()
    if root is None:
        return []
    qdir = root / "data" / "review-queue"
    if not qdir.is_dir():
        return []
    for path in sorted(qdir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        item = _queue_item_from_raw(data, fallback_id=path.stem, source="review-queue")
        if item:
            items.append(item)
    return items


def _load_catalog_pending() -> list[dict[str, Any]]:
    root = _navigator_root()
    if root is None:
        return []
    index_path = root / "data" / "catalog" / "index.json"
    if not index_path.exists():
        return []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    products = index.get("products") if isinstance(index, dict) else None
    if not isinstance(products, list):
        return []
    pending: list[dict[str, Any]] = []
    for row in products:
        if not isinstance(row, dict):
            continue
        status = str(row.get("editorial_status") or "")
        if status not in {"needs_review", "source_changed", "pending"}:
            continue
        surface = str(row.get("catalog_surface") or "")
        if surface == "hidden":
            continue
        pending.append(
            {
                "product_id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "status": status,
                "surface": surface,
                "source": "catalog-index",
            }
        )
    return pending


def build_pending_menu() -> list[dict[str, Any]]:
    """合并 review-queue + catalog 待审，编号后缓存到 last_menu。"""
    state_products = load_state()
    by_id: dict[str, dict[str, Any]] = {}

    for item in _load_review_queue_items():
        pid = item["product_id"]
        if not pid:
            continue
        st = state_products.get(pid) if isinstance(state_products.get(pid), dict) else {}
        by_id[pid] = {
            "product_id": pid,
            "title": str(item.get("title") or st.get("title") or pid),
            "price": str(st.get("price") or ""),
            "status": item.get("status") or "pending",
            "changed_fields": item.get("changed_fields") or [],
            "detected_at": item.get("detected_at") or "",
            "source": item.get("source") or "review-queue",
            "priority": 0 if "title" in (item.get("changed_fields") or []) else 1,
        }

    for item in _load_catalog_pending():
        pid = item["product_id"]
        if not pid:
            continue
        if pid in by_id:
            continue
        st = state_products.get(pid) if isinstance(state_products.get(pid), dict) else {}
        by_id[pid] = {
            "product_id": pid,
            "title": str(item.get("title") or st.get("title") or pid),
            "price": str(st.get("price") or ""),
            "status": item.get("status") or "needs_review",
            "changed_fields": [],
            "detected_at": "",
            "source": "catalog-index",
            "priority": 2,
        }

    deferred = review_sessions.get_deferred_ids()
    rows = list(by_id.values())
    # 暂缓的靠后；有 title 变化的靠前
    rows.sort(
        key=lambda r: (
            1 if r["product_id"] in deferred else 0,
            r.get("priority", 9),
            r.get("detected_at") or "",
            r["product_id"],
        )
    )
    menu: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        menu.append(
            {
                "index": index,
                "product_id": row["product_id"],
                "title": row["title"],
                "price": row.get("price") or "",
                "status": row.get("status") or "",
                "changed_fields": row.get("changed_fields") or [],
                "deferred": row["product_id"] in deferred,
                "source": row.get("source") or "",
            }
        )
    review_sessions.set_last_menu(menu)
    return menu


def format_pending_menu(menu: list[dict[str, Any]] | None = None) -> str:
    items = menu if menu is not None else build_pending_menu()
    if not items:
        return (
            "当前没有待审核商品。\n"
            "也可直接 `审核 商品id` 或 `审核测试` 手动开审。"
        )
    lines = [
        f"# 待审核商品（共 {len(items)}）",
        "回复序号开始审核，例如 `1` 或 `审核 1`。",
        "审核中：`可以`=提交　`不用调整/省略`=完成且不改　`暂不改`=仍留清单　`取消`=退出本轮",
        "",
    ]
    # QQ 消息长度有限，最多列 30 条
    for item in items[:30]:
        mark = "⏸" if item.get("deferred") else "•"
        price = _format_price(item.get("price"))
        price_bit = f" {price}r" if price else ""
        fields = item.get("changed_fields") or []
        field_bit = f" 〔{'/'.join(fields)}〕" if fields else ""
        title = item.get("title") or item.get("product_id")
        if len(title) > 40:
            title = title[:38] + "…"
        lines.append(
            f"{item['index']}. {mark} `{item['product_id']}`{price_bit} {title}{field_bit}"
        )
    if len(items) > 30:
        lines.append(f"\n…另有 {len(items) - 30} 个未列出，可 `审核 商品id` 直接开审。")
    deferred_n = sum(1 for i in items if i.get("deferred"))
    if deferred_n:
        lines.append(f"\n⏸ = 你标过「暂不改」的 {deferred_n} 个，仍在清单中。")
    return "\n".join(lines)


def resolve_menu_selection(text: str) -> str | None:
    """从序号解析 product_id；无效返回 None。"""
    content = (text or "").strip()
    match = SELECT_RE.match(content)
    if not match:
        return None
    index = int(match.group(1))
    menu = review_sessions.get_last_menu()
    if not menu:
        menu = build_pending_menu()
    for item in menu:
        if int(item.get("index") or 0) == index:
            return str(item.get("product_id") or "")
    return None


def _surface_label(surface: str) -> str:
    return {
        "prepared-accounts": "成品号",
        "official-membership": "官方订阅/充值",
        "other": "其他",
    }.get(surface, surface or "其他")


def format_review_message(session: dict[str, Any]) -> str:
    """Render the current wizard step for C2C."""
    product = session["product"]
    draft = session.get("draft") or {}
    step = session.get("step") or STEP_COPY
    original = product["title"]
    proposed = draft.get("proposed_title") or original
    surface = draft.get("surface") or "other"
    lines = [
        f"## 上新审核 · {product['id']}",
        f"步骤：{step}　价格：{_format_price(product.get('price')) or '—'}",
        f"链接：{product.get('url') or '—'}",
        f"类型：{_surface_label(surface)}"
        + (f"（{draft.get('surface_reason')}）" if draft.get("surface_reason") else ""),
        "",
    ]

    if step == STEP_SURFACE:
        lines.extend(
            [
                "### 请确认上架通道",
                f"- 模型预判：{_surface_label(surface)}",
                "- 回复 `官方` / `成品号` / `跳过`（不发布）",
                "- 或发修改意见让我重判",
            ]
        )
    elif step == STEP_OFFICIAL:
        mg = draft.get("membership_guess") or {}
        lines.extend(
            [
                "### 官方订阅字段",
                f"- 品牌类目：`{draft.get('target_category') or '—'}`",
                f"- 履约：`{draft.get('fulfillment_mode') or '—'}`",
                f"- 档位模式：`{mg.get('mode') or '—'}` tier=`{mg.get('tier_id') or '—'}`",
                f"- 选项：{mg.get('option_label') or '—'} / {mg.get('channel') or '—'}",
                "",
                "可改：`类目 gpt|claude|cursor|grok`　`挂到 <tier_id>`　`履约 卡密|代充|可选`",
                "确认无误回 `可以` 进入文案确认。",
            ]
        )
    elif step == STEP_PREPARED:
        pg = draft.get("prepared_guess") or {}
        lines.extend(
            [
                "### 成品号字段",
                f"- 登录方式：`{pg.get('login_method') or '—'}`",
                f"- 分组：`{pg.get('offer_group_id') or '—'}`",
                f"- 质保：{pg.get('warranty_label') or '—'}　接码：{pg.get('delivery_category') or '—'}",
                "",
                "可改：`登录 <login_method>`　`分组 <offer_group_id>`　`质保 ...`",
                "若需**新登录教程**请回 `跳过`（阻塞，人工补 guide 后再审）。",
                "确认无误回 `可以`。",
            ]
        )
    elif step == STEP_COPY:
        title_same = original.strip() == str(proposed).strip()
        lines.extend(
            [
                "### 标题对比",
                f"- 原标题：{original}",
                f"- 建议标题：{proposed}",
                f"- 短标题：{draft.get('short_title') or proposed}",
            ]
        )
        if title_same:
            lines.append("- 结论：建议与原标题一致")
        if draft.get("title_reason"):
            lines.append(f"- 说明：{draft['title_reason']}")
        usage = draft.get("usage") or {}
        steps = usage.get("steps") or []
        lines.extend(["", "### 操作步骤"])
        if not steps:
            lines.append("（未提取到明确步骤，确认后将提交空步骤）")
        else:
            for index, usage_step in enumerate(steps, start=1):
                lines.append(f"{index}. **{usage_step.get('title', '')}**")
                body = str(usage_step.get("body_markdown") or "").strip()
                if body and body != usage_step.get("title"):
                    lines.append(f"   {body}")
        lines.extend(
            [
                "",
                "### 请回复",
                "- `可以`：进入终稿",
                "- `不用调整`：标题用原文并进入终稿",
                "- `暂不改`：退出本轮，仍留待审清单",
                "- 修改意见：重拟文案",
            ]
        )
    else:  # STEP_FINAL or legacy
        lines.extend(
            [
                "### 终稿清单",
                f"- surface：`{surface}`",
                f"- 标题：{effective_title(session)}",
                f"- 短标题：{draft.get('short_title') or effective_title(session)}",
            ]
        )
        if surface == OFFICIAL_SURFACE:
            mg = draft.get("membership_guess") or {}
            lines.extend(
                [
                    f"- 类目：`{draft.get('target_category')}`",
                    f"- 履约：`{draft.get('fulfillment_mode')}`",
                    f"- 档位：{mg.get('mode')} / {mg.get('tier_id') or (mg.get('new_tier') or {}).get('id')}",
                    f"- 选项：{mg.get('option_label')} {mg.get('channel') or ''}",
                ]
            )
        elif surface == PREPARED_SURFACE:
            pg = draft.get("prepared_guess") or {}
            lines.extend(
                [
                    f"- 登录：`{pg.get('login_method')}`",
                    f"- 分组：`{pg.get('offer_group_id')}`",
                    f"- 质保：{pg.get('warranty_label')} / {pg.get('delivery_category')}",
                ]
            )
        lines.extend(
            [
                "",
                "回复 `发布` 或 `可以` → 写入 shop-core 上架。",
                "`取消` 退出；改字段可说「回退」或具体修改意见。",
            ]
        )

    issues = draft.get("issues") or []
    blockers = draft.get("blockers") or []
    if issues:
        lines.extend(["", "### 待你留意"])
        for issue in issues[:6]:
            lines.append(f"- {issue}")
    if blockers:
        lines.extend(["", "### 阻塞点"])
        for item in blockers[:6]:
            lines.append(f"- {item}")
    lines.append(f"\n轮次：{session.get('round', 1)}")
    return "\n".join(lines)


def format_help() -> str:
    return (
        "## 上新审核用法\n"
        "1. `待审清单`：列出待审商品（带序号）\n"
        "2. 回复 `1` / `审核 1`：进入该商品审核\n"
        "3. 或 `审核 g28zpj` / `审核测试` 直接开审\n"
        "4. `审核状态`：当前会话\n"
        "\n"
        "审核中：\n"
        "- `可以`：提交草稿并完成\n"
        "- `不用调整` / `省略`：完成且不改，移出待审\n"
        "- `暂不改`：仍留清单，下次再改\n"
        "- 修改意见：重拟后再确认\n"
        "\n"
        "规则：标题/步骤需确认；详情按来源同步；仅 OWNER 可触发。\n"
        "特例：成品号质保天数缩短会强制通知；仅上架时间话术变化可忽略。"
    )


def format_status(session: dict[str, Any] | None) -> str:
    qlen = review_sessions.queue_length()
    menu_n = len(review_sessions.get_last_menu() or [])
    if not session:
        return (
            f"当前没有进行中的审核。会话队列：{qlen}；"
            f"最近清单缓存：{menu_n} 条。\n"
            "发 `待审清单` 查看，或 `审核 商品id` 开始。"
        )
    product = session.get("product") or {}
    return (
        f"进行中：{product.get('id')} · {product.get('title', '')}\n"
        f"状态：{session.get('status')}　轮次：{session.get('round', 1)}\n"
        f"会话队列：{qlen}\n"
        "可回复 `可以` / `不用调整` / `暂不改` / 修改意见 / `取消`。"
    )


def _mark_queue_status(product_id: str, status: str, **extra: Any) -> list[str]:
    """更新 navigator review-queue 状态；返回改动的路径。"""
    paths: list[str] = []
    root = _navigator_root()
    if root is None:
        return paths
    queue_path = root / "data" / "review-queue" / f"{product_id}.json"
    if not queue_path.exists():
        return paths
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        queue = {"product_id": product_id}
    if not isinstance(queue, dict):
        queue = {"product_id": product_id}
    queue["status"] = status
    queue["updated_at"] = _now()
    for key, value in extra.items():
        queue[key] = value
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths.append(str(queue_path))
    return paths


def _initial_step(draft: dict[str, Any]) -> str:
    surface = draft.get("surface")
    if surface in {OFFICIAL_SURFACE, PREPARED_SURFACE, "other"}:
        return STEP_SURFACE
    return STEP_SURFACE


def _new_session(
    product: dict[str, Any],
    draft: dict[str, Any],
    *,
    source: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "status": "awaiting_confirmation",
        "step": _initial_step(draft),
        "source": source,
        "created_at": _now(),
        "updated_at": _now(),
        "round": 1,
        "product": product,
        "draft": draft,
        "context": context or {},
        "final_title": draft["proposed_title"],
    }


def build_publish_body(session: dict[str, Any]) -> dict[str, Any]:
    """Assemble shop-core publish payload from session draft."""
    product = session["product"]
    draft = session.get("draft") or {}
    surface = draft.get("surface")
    title = effective_title(session)
    body: dict[str, Any] = {
        "display_title": title,
        "catalog_surface": surface,
        "catalog_visible": True,
        "short_title": draft.get("short_title") or title,
        "usage": draft.get("usage") or {"heading": "操作步骤", "steps": []},
        "resolve_review": True,
        "generated_by": "qqbot-c2c-review",
    }
    if surface == OFFICIAL_SURFACE:
        mg = draft.get("membership_guess") or {}
        mode = str(mg.get("mode") or "attach_option")
        membership: dict[str, Any] = {
            "mode": mode,
            "category_id": draft.get("target_category"),
            "tier_group_id": mg.get("tier_group_id"),
            "tier_id": mg.get("tier_id"),
            "option": {
                "productId": product["id"],
                "label": mg.get("option_label") or "查看详情",
            },
        }
        if mg.get("channel"):
            membership["option"]["channel"] = mg["channel"]
        if mode == "create_tier":
            new_tier = dict(mg.get("new_tier") or {})
            if not new_tier.get("id"):
                new_tier["id"] = mg.get("tier_id") or f"auto-{product['id']}"
            if not new_tier.get("title"):
                new_tier["title"] = title
            if not new_tier.get("tagline"):
                new_tier["tagline"] = "官方会员方案"
            if not new_tier.get("includedLabel"):
                new_tier["includedLabel"] = "方案特点："
            if not new_tier.get("features"):
                new_tier["features"] = ["详情请查看商品页"]
            options = list(new_tier.get("options") or [])
            if not any(isinstance(o, dict) and o.get("productId") == product["id"] for o in options):
                options.append(membership["option"])
            new_tier["options"] = options
            membership["new_tier"] = new_tier
            membership["tier_id"] = new_tier["id"]
        body["target_category"] = draft.get("target_category")
        body["fulfillment_mode"] = draft.get("fulfillment_mode") or "self-service-redemption"
        body["membership_placement"] = membership
        if draft.get("plan_presentation"):
            body["plan_presentation"] = draft["plan_presentation"]
    elif surface == PREPARED_SURFACE:
        pg = draft.get("prepared_guess") or {}
        body["fulfillment_mode"] = "credential-delivery"
        body["prepared_placement"] = {
            "login_method": pg.get("login_method"),
            "offer_group_id": pg.get("offer_group_id"),
            "delivery_category": pg.get("delivery_category") or "未接码",
            "warranty_label": pg.get("warranty_label") or "质保",
        }
    else:
        raise ValueError("surface 非官方/成品号，不能发布")
    return body


def effective_title(session: dict[str, Any]) -> str:
    if session.get("force_original_title"):
        return session["product"]["title"]
    return (session.get("draft") or {}).get("proposed_title") or session["product"]["title"]


async def _load_product_and_context(product_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Prefer shop-core publish-context; fall back to state.json product only."""
    context: dict[str, Any] | None = None
    product = _product_from_state(product_id)
    try:
        from shop.core_client import fetch_publish_context

        context = await fetch_publish_context(product_id)
        core_product = context.get("product") if isinstance(context, dict) else None
        if isinstance(core_product, dict):
            product = {
                "id": product_id,
                "title": str(core_product.get("original_title") or core_product.get("display_title") or product_id),
                "url": str(core_product.get("purchase_url") or (product or {}).get("url") or ""),
                "category": str(core_product.get("source_category_name") or (product or {}).get("category") or ""),
                "category_id": (product or {}).get("category_id"),
                "price": str(core_product.get("price") or (product or {}).get("price") or ""),
                "in_stock": bool(core_product.get("in_stock", False)),
                "listed": bool(core_product.get("listed", True)),
                "description": str((product or {}).get("description") or ""),
                "description_html": str(core_product.get("description_html") or (product or {}).get("description_html") or ""),
                "cover_url": str((product or {}).get("cover_url") or ""),
                "detail_image_urls": list((product or {}).get("detail_image_urls") or []),
            }
    except Exception as exc:  # noqa: BLE001 — core optional at review time
        logger.info("publish-context unavailable for %s: %s", product_id, exc)
    if product is None:
        raise KeyError(f"找不到商品 {product_id}（state.json / shop-core）")
    return product, context


async def start_review(product_id: str, *, source: str = "manual") -> tuple[dict[str, Any], str]:
    """创建审核会话。若已有进行中会话，则入队。"""
    product_id = (product_id or "").strip()
    if not product_id:
        raise ValueError("请提供 product_id")
    product, context = await _load_product_and_context(product_id)

    draft = await generate_draft(product, publish_context=context)
    session = _new_session(product, draft, source=source, context=context)

    current = review_sessions.get_current()
    if current and current.get("status") == "awaiting_confirmation":
        review_sessions.enqueue(session)
        msg = (
            f"已有进行中的审核（{current['product']['id']}），"
            f"已将 {product_id} 加入队列（队内 {review_sessions.queue_length()} 个）。\n"
            "先处理当前会话，或回复 `取消` 后再开新审核。"
        )
        return session, msg

    review_sessions.set_current(session)
    return session, format_review_message(session)


async def start_publish_review(product_id: str, *, source: str = "inventory_new") -> tuple[dict[str, Any], str]:
    """Entry used by scheduler when core discovers a new product."""
    return await start_review(product_id, source=source)


async def start_test_review() -> tuple[dict[str, Any], str]:
    # 优先 g28zpj（成品号样例），否则取 state 里第一个商品
    for pid in ("g28zpj", "1ug22c", "90kcuj"):
        if _product_from_state(pid):
            return await start_review(pid, source="test")
    products = load_state()
    if not products:
        raise KeyError("state.json 为空，无法测试")
    return await start_review(next(iter(products)), source="test")


async def _publish_to_core(session: dict[str, Any]) -> dict[str, Any]:
    """Primary apply path: shop-core atomic publish + local audit snapshot."""
    product = session["product"]
    product_id = product["id"]
    final_title = effective_title(session)
    draft = session.get("draft") or {}
    result: dict[str, Any] = {
        "product_id": product_id,
        "final_title": final_title,
        "applied": False,
        "paths": [],
        "mode": "dry-run",
        "core": None,
    }

    approved_dir = Path("data/review-approved")
    approved_dir.mkdir(parents=True, exist_ok=True)
    try:
        publish_body = build_publish_body(session)
    except ValueError as exc:
        result["mode"] = "blocked"
        result["warning"] = str(exc)
        return result

    snapshot = {
        "approved_at": _now(),
        "session_id": session.get("id"),
        "product_id": product_id,
        "original_title": product["title"],
        "final_title": final_title,
        "surface": draft.get("surface"),
        "publish_body": publish_body,
        "note": "详情 HTML 由 core inventory 维护；本快照仅审计上架决策",
    }
    snap_path = approved_dir / f"{product_id}-{session.get('id')}.json"
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["paths"].append(str(snap_path))

    try:
        from shop.core_client import publish_product

        core_result = await publish_product(product_id, publish_body)
        result["core"] = core_result
        result["applied"] = True
        result["mode"] = "shop-core-publish"
        _mark_queue_status(product_id, "published_via_qqbot", final_title=final_title)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("core publish failed, fallback navigator override: %s", exc)
        result["warning"] = f"core 发布失败：{exc}"
        # fall through to legacy navigator write if enabled

    if not REVIEW_APPLY_ENABLED:
        result["mode"] = "snapshot-only"
        return result

    # Legacy filesystem apply (audit / offline)
    legacy = _apply_to_navigator(session)
    result["paths"].extend(legacy.get("paths") or [])
    result["applied"] = bool(legacy.get("applied"))
    result["mode"] = f"fallback:{legacy.get('mode')}"
    return result


def _apply_to_navigator(session: dict[str, Any]) -> dict[str, Any]:
    """写入 shop-navigator overrides + catalog；不可用时写本地快照。"""
    product = session["product"]
    product_id = product["id"]
    final_title = effective_title(session)
    draft = session["draft"]
    usage = draft["usage"]
    surface = draft.get("surface") or "other"

    result: dict[str, Any] = {
        "product_id": product_id,
        "final_title": final_title,
        "applied": False,
        "paths": [],
        "mode": "dry-run",
    }

    approved_dir = Path("data/review-approved")
    approved_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "approved_at": _now(),
        "session_id": session.get("id"),
        "product_id": product_id,
        "original_title": product["title"],
        "final_title": final_title,
        "surface": surface,
        "usage": usage,
        "source_description_excerpt": (product.get("description") or "")[:2000],
        "description_html_synced": True,
        "note": "详情按来源同步，不经模型改写",
    }
    snap_path = approved_dir / f"{product_id}-{session.get('id')}.json"
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["paths"].append(str(snap_path))

    if not REVIEW_APPLY_ENABLED:
        result["mode"] = "snapshot-only"
        return result

    root = NAVIGATOR_ROOT
    if not root or not Path(root).is_dir():
        result["mode"] = "snapshot-only"
        result["warning"] = "NAVIGATOR_ROOT 不可用，仅保存本地快照"
        return result

    root = Path(root)
    catalog_path = root / "data" / "catalog" / "products" / f"{product_id}.json"
    override_path = root / "data" / "overrides" / "products" / f"{product_id}.json"
    override_path.parent.mkdir(parents=True, exist_ok=True)

    override: dict[str, Any] = {}
    if override_path.exists():
        try:
            override = json.loads(override_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            override = {}
    if not isinstance(override, dict):
        override = {}

    display = dict(override.get("display") or {})
    display["title"] = final_title
    if surface in {"prepared-accounts", "official-membership"}:
        display["catalog_surface"] = surface
        display["catalog_visible"] = True
    override["display"] = display
    override["sections"] = {"usage": usage}
    override["editorial"] = {
        "status": "reviewed",
        "generated_by": "qqbot-c2c-review",
        "reviewed_at": _now()[:10],
        "organization": {
            "level": "human_reviewed",
            "coverage_percent": 100,
            "sections_present": ["usage"],
            "requires_manual_review": False,
            "issues": [],
        },
    }
    # 详情：直接记录来源同步意图（HTML 真源仍由 sync/core 负责；此处标注已人审）
    override["source_sync"] = {
        "description_html_policy": "use_latest_source",
        "approved_via": "qqbot-c2c",
        "approved_at": _now(),
    }
    override_path.write_text(json.dumps(override, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["paths"].append(str(override_path))

    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            catalog = {}
        if isinstance(catalog, dict):
            catalog_display = dict(catalog.get("display") or {})
            catalog_display["title"] = final_title
            if surface in {"prepared-accounts", "official-membership"}:
                catalog_display["catalog_surface"] = surface
            catalog["display"] = catalog_display
            sections = dict(catalog.get("sections") or {})
            sections["usage"] = usage
            catalog["sections"] = sections
            editorial = dict(catalog.get("editorial") or {})
            editorial.update(override["editorial"])
            catalog["editorial"] = editorial
            # 详情直接用 state 中最新 html（若有）
            if product.get("description_html"):
                source = dict(catalog.get("source") or {})
                source["description_html"] = product["description_html"]
                if product.get("description"):
                    source["description_markdown"] = product["description"]
                catalog["source"] = source
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result["paths"].append(str(catalog_path))

            index_path = root / "data" / "catalog" / "index.json"
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    index = {}
                products = index.get("products")
                if isinstance(products, list):
                    for summary in products:
                        if isinstance(summary, dict) and summary.get("id") == product_id:
                            summary["title"] = final_title
                            summary["editorial_status"] = "reviewed"
                            break
                    index_path.write_text(
                        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    result["paths"].append(str(index_path))

    queue_path = root / "data" / "review-queue" / f"{product_id}.json"
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            queue = {}
        if isinstance(queue, dict):
            queue["status"] = "approved_via_qqbot"
            queue["approved_at"] = _now()
            queue["final_title"] = final_title
            queue_path.write_text(
                json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result["paths"].append(str(queue_path))

    result["applied"] = True
    result["mode"] = "navigator-override"
    return result


async def _advance_or_idle(lines: list[str]) -> tuple[str, dict[str, Any] | None]:
    nxt = review_sessions.pop_queue()
    if nxt:
        if not nxt.get("draft"):
            nxt["draft"] = await generate_draft(nxt["product"])
        review_sessions.set_current(nxt)
        lines.extend(["", "---", "队列下一件：", format_review_message(nxt)])
        return "\n".join(lines), nxt
    lines.append("\n发 `待审清单` 可继续处理其它商品。")
    return "\n".join(lines), None


def _apply_field_commands(session: dict[str, Any], text: str) -> bool:
    """Mutate draft from structured owner commands. Return True if handled."""
    draft = session.setdefault("draft", {})
    content = text.strip()

    surface_match = SURFACE_RE.match(content)
    if surface_match:
        token = surface_match.group(1).lower()
        if token in {"官方", "官方订阅"}:
            draft["surface"] = OFFICIAL_SURFACE
        elif token in {"成品号", "成品"}:
            draft["surface"] = PREPARED_SURFACE
        else:
            draft["surface"] = "other"
            session["step"] = "blocked"
        return True

    cat = OFFICIAL_CAT_RE.match(content)
    if cat:
        draft["target_category"] = CATEGORY_ALIASES.get(cat.group(1).lower(), cat.group(1).lower())
        return True

    fulfill = FULFILL_RE.match(content)
    if fulfill:
        draft["fulfillment_mode"] = FULFILL_ALIASES.get(fulfill.group(1).lower(), fulfill.group(1))
        return True

    attach = ATTACH_TIER_RE.match(content)
    if attach:
        mg = dict(draft.get("membership_guess") or {})
        mg["mode"] = "attach_option"
        mg["tier_id"] = attach.group(1)
        draft["membership_guess"] = mg
        return True

    login = LOGIN_RE.match(content)
    if login:
        pg = dict(draft.get("prepared_guess") or {})
        pg["login_method"] = login.group(1)
        draft["prepared_guess"] = pg
        return True

    group = GROUP_RE.match(content)
    if group:
        pg = dict(draft.get("prepared_guess") or {})
        pg["offer_group_id"] = group.group(1)
        draft["prepared_guess"] = pg
        return True

    warranty = WARRANTY_RE.match(content)
    if warranty:
        pg = dict(draft.get("prepared_guess") or {})
        pg["warranty_label"] = warranty.group(1).strip()
        draft["prepared_guess"] = pg
        return True

    return False


def _advance_step_after_approve(session: dict[str, Any]) -> str | None:
    """Move wizard forward on approve. Return None when ready to publish."""
    step = session.get("step") or STEP_COPY
    draft = session.get("draft") or {}
    surface = draft.get("surface")

    if step == STEP_SURFACE:
        if surface == OFFICIAL_SURFACE:
            session["step"] = STEP_OFFICIAL
            return STEP_OFFICIAL
        if surface == PREPARED_SURFACE:
            session["step"] = STEP_PREPARED
            return STEP_PREPARED
        session["step"] = "blocked"
        return "blocked"

    if step == STEP_OFFICIAL:
        session["step"] = STEP_COPY
        return STEP_COPY
    if step == STEP_PREPARED:
        session["step"] = STEP_COPY
        return STEP_COPY
    if step == STEP_COPY:
        session["step"] = STEP_FINAL
        return STEP_FINAL
    if step == STEP_FINAL:
        return None
    # legacy single-step sessions
    session["step"] = STEP_FINAL
    return None


async def handle_confirmation(session: dict[str, Any], owner_text: str) -> tuple[str, dict[str, Any] | None]:
    """处理店主对当前审核的回复。返回 (reply_text, new_current_session_or_none)。"""
    intent = classify_owner_reply(owner_text)
    product_id = session["product"]["id"]
    content = (owner_text or "").strip()
    step = session.get("step") or STEP_COPY

    if intent == "cancel":
        review_sessions.clear_current(archive=True)
        lines = [f"已退出 `{product_id}` 本轮审核（未标记完成，仍在待审清单）。"]
        return await _advance_or_idle(lines)

    if intent == "defer":
        review_sessions.mark_deferred(product_id)
        session["status"] = "deferred"
        session["updated_at"] = _now()
        review_sessions.clear_current(archive=True)
        lines = [
            f"已将 `{product_id}` 标为**暂不改**。",
            "它仍在 `待审清单` 里（带 ⏸），下次再处理。",
        ]
        return await _advance_or_idle(lines)

    # structured field commands at any step
    if _apply_field_commands(session, content):
        if session.get("step") == "blocked" or (session.get("draft") or {}).get("surface") == "other":
            session["status"] = "blocked"
            session["updated_at"] = _now()
            review_sessions.clear_current(archive=True)
            lines = [
                f"`{product_id}` 已标记为**不发布**（surface=other / 跳过）。",
                "仍留在 core review_queue，可稍后 `审核 {0}` 重开。".format(product_id),
            ]
            return await _advance_or_idle(lines)
        # if surface chosen explicitly on first step, advance
        if step == STEP_SURFACE and (session.get("draft") or {}).get("surface") in {
            OFFICIAL_SURFACE,
            PREPARED_SURFACE,
        }:
            _advance_step_after_approve(session)
        session["updated_at"] = _now()
        review_sessions.set_current(session)
        return "已更新字段：\n\n" + format_review_message(session), session

    if intent == "skip_complete":
        # On copy step: keep original title and advance; elsewhere omit entirely.
        if step == STEP_COPY:
            session["force_original_title"] = True
            session["final_title"] = session["product"]["title"]
            draft = session.setdefault("draft", {})
            draft["proposed_title"] = session["product"]["title"]
            draft["title_kept_original"] = True
            session["step"] = STEP_FINAL
            session["updated_at"] = _now()
            review_sessions.set_current(session)
            return "已保留原标题，请确认终稿：\n\n" + format_review_message(session), session

        session["status"] = "skipped_complete"
        session["final_title"] = session["product"]["title"]
        session["updated_at"] = _now()
        paths = _mark_queue_status(
            product_id,
            "omitted_via_qqbot",
            final_title=session["product"]["title"],
            note="店主选择不用调整/省略，本轮完成且未改展示",
        )
        review_sessions.clear_deferred(product_id)
        review_sessions.clear_current(archive=True)
        lines = [
            f"已**省略更改并完成** `{product_id}`。",
            "- 标题/步骤：保持原样，未写入新草稿",
            "- 已从待审队列移除（下次清单不再出现，除非资料再次变化）",
        ]
        for path in paths[:3]:
            lines.append(f"  - `{path}`")
        return await _advance_or_idle(lines)

    if intent == "approve" or PUBLISH_RE.match(content):
        next_step = _advance_step_after_approve(session)
        if next_step == "blocked":
            session["status"] = "blocked"
            session["updated_at"] = _now()
            review_sessions.clear_current(archive=True)
            return await _advance_or_idle(
                [f"`{product_id}` 无法自动上架（surface 非官方/成品号）。已退出本轮。"]
            )
        if next_step is not None:
            session["status"] = "awaiting_confirmation"
            session["updated_at"] = _now()
            review_sessions.set_current(session)
            return format_review_message(session), session

        # publish
        session["final_title"] = effective_title(session)
        session["status"] = "publishing"
        session["updated_at"] = _now()
        review_sessions.set_current(session)
        apply_result = await _publish_to_core(session)
        if not apply_result.get("applied"):
            session["status"] = "awaiting_confirmation"
            session["step"] = STEP_FINAL
            session["updated_at"] = _now()
            review_sessions.set_current(session)
            warn = apply_result.get("warning") or "发布未成功"
            return (
                f"发布失败，仍停在终稿，可改字段后重试 `发布`。\n- {warn}",
                session,
            )

        session["status"] = "approved"
        session["core_publish_result"] = apply_result.get("core")
        review_sessions.clear_deferred(product_id)
        review_sessions.clear_current(archive=True)
        surface = (session.get("draft") or {}).get("surface")
        route = "/" if surface == OFFICIAL_SURFACE else "/accounts" if surface == PREPARED_SURFACE else ""
        lines = [
            f"已确认并**上架** `{product_id}`。",
            f"- 最终标题：{session['final_title']}",
            f"- 写入模式：{apply_result.get('mode')}",
        ]
        if route:
            lines.append(f"- 导航：{route}")
        if apply_result.get("paths"):
            lines.append("- 审计快照：")
            for path in apply_result["paths"][:4]:
                lines.append(f"  - `{path}`")
        if apply_result.get("warning"):
            lines.append(f"- 注意：{apply_result['warning']}")
        return await _advance_or_idle(lines)

    # revise via Grok
    feedback = content
    session["status"] = "revising"
    review_sessions.set_current(session)
    try:
        new_draft = await generate_draft(
            session["product"],
            feedback=feedback,
            previous_draft=session.get("draft"),
            publish_context=session.get("context"),
        )
    except GrokError as exc:
        session["status"] = "awaiting_confirmation"
        review_sessions.set_current(session)
        return f"按意见重拟失败：{exc}\n请稍后再试，或换种说法。", session

    session["draft"] = new_draft
    session["final_title"] = effective_title(session)
    session["status"] = "awaiting_confirmation"
    session["round"] = int(session.get("round") or 1) + 1
    session["updated_at"] = _now()
    session["last_feedback"] = feedback
    # keep current step so owner stays in the same wizard stage
    review_sessions.set_current(session)
    return "已按你的意见改了一版，请继续审核：\n\n" + format_review_message(session), session


async def _start_review_safe(product_id: str, *, source: str) -> str:
    try:
        _session, msg = await start_review(product_id, source=source)
        return msg
    except KeyError as exc:
        return f"无法开始审核：{exc}"
    except GrokError as exc:
        return f"生成草稿失败：{exc}"
    except Exception as exc:
        logger.exception("start review failed")
        return f"开始审核时出错：{exc}"


async def handle_owner_text(text: str) -> str:
    """店主私聊文本总入口（在白名单校验之后调用）。"""
    content = (text or "").strip()
    current = review_sessions.get_current()
    in_session = bool(current and current.get("status") in {"awaiting_confirmation", "revising"})

    if HELP_RE.match(content) or content in {"审核帮助"}:
        return format_help()

    if LIST_RE.match(content) or content in {"审核", "上新审核", "review"}:
        return format_pending_menu()

    if STATUS_RE.match(content):
        return format_status(current)

    if START_TEST_RE.match(content):
        try:
            _session, msg = await start_test_review()
            return msg
        except Exception as exc:
            logger.exception("start test review failed")
            return f"审核测试失败：{exc}"

    start = START_RE.match(content)
    if start:
        pid = (start.group(1) or "").strip()
        if not pid:
            return format_help()
        return await _start_review_safe(pid, source="c2c")

    # 进行中会话：确认/修改优先；纯数字提示先结束本轮
    if in_session:
        if re.fullmatch(r"\d{1,3}", content):
            return (
                f"当前正在审 `{current['product']['id']}`。\n"
                f"若要改审清单第 {content} 项，请先 `取消` 或 `暂不改`，再发序号。\n"
                "若这是修改意见，请写成完整句子。"
            )
        try:
            reply, _ = await handle_confirmation(current, content)
            return reply
        except Exception as exc:
            logger.exception("handle confirmation failed")
            return f"处理确认时出错：{exc}"

    # 无会话：按序号开审
    if SELECT_RE.match(content):
        if not review_sessions.get_last_menu():
            build_pending_menu()
        selected_id = resolve_menu_selection(content)
        if not selected_id:
            menu = review_sessions.get_last_menu()
            if not menu:
                return "当前没有待审商品。可 `审核 商品id` 手动开审。"
            return f"序号超出范围（1–{len(menu)}）。请重新发 `待审清单`。"
        return await _start_review_safe(selected_id, source="menu")

    intent = classify_owner_reply(content)
    if intent in {"approve", "skip_complete", "defer", "cancel"}:
        return "当前没有进行中的审核。发送 `待审清单` 查看并选号。"

    return ""


def owner_primary_openid() -> str | None:
    return OWNER_USER_OPENIDS[0] if OWNER_USER_OPENIDS else None
