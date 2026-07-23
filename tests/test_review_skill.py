import json
from pathlib import Path

import pytest

from bot import review
from storage import review_sessions


def test_classify_owner_reply():
    assert review.classify_owner_reply("可以") == "approve"
    assert review.classify_owner_reply("OK") == "approve"
    assert review.classify_owner_reply("不用调整") == "skip_complete"
    assert review.classify_owner_reply("省略") == "skip_complete"
    assert review.classify_owner_reply("暂不改") == "defer"
    assert review.classify_owner_reply("取消") == "cancel"
    assert review.classify_owner_reply("标题改成更短一点，去掉渠道字样") == "revise"
    assert review.classify_owner_reply("审核状态") == "status"
    assert review.classify_owner_reply("待审清单") == "list"


def test_normalize_draft_defaults():
    product = {
        "id": "g28zpj",
        "title": "GPT Plus成品UPI渠道（iCloud邮箱）",
        "category": "成品",
    }
    draft = review._normalize_draft(
        {
            "proposed_title": "ChatGPT Plus 成品账号（iCloud 邮箱）",
            "usage": {
                "steps": [
                    {"title": "打开官网", "body_markdown": "打开 https://chatgpt.com/"},
                ]
            },
            "issues": ["确认质保"],
        },
        product,
    )
    assert draft["proposed_title"].startswith("ChatGPT")
    assert draft["surface"] == "prepared-accounts"
    assert draft["usage"]["steps"][0]["id"] == "step-01"
    assert draft["issues"] == ["确认质保"]


def test_format_review_message_includes_title_compare():
    session = {
        "round": 1,
        "step": "await_copy",
        "product": {
            "id": "g28zpj",
            "title": "原标题A",
            "price": "21.50",
            "url": "https://pay.ldxp.cn/item/g28zpj",
        },
        "draft": {
            "proposed_title": "建议标题B",
            "title_reason": "更清晰",
            "surface": "prepared-accounts",
            "usage": {
                "intro_markdown": "",
                "steps": [{"title": "登录", "body_markdown": "用邮箱登录"}],
            },
            "issues": [],
        },
    }
    text = review.format_review_message(session)
    assert "原标题：原标题A" in text
    assert "建议标题：建议标题B" in text
    assert "不用调整" in text
    assert "暂不改" in text
    assert "可以" in text


def test_build_publish_body_official_and_prepared():
    official = {
        "product": {"id": "newplus", "title": "原"},
        "draft": {
            "proposed_title": "ChatGPT Plus · 新渠道",
            "short_title": "Plus 新",
            "surface": "official-membership",
            "target_category": "gpt-official-recharge",
            "fulfillment_mode": "self-service-redemption",
            "usage": {"steps": []},
            "membership_guess": {
                "mode": "attach_option",
                "tier_group_id": "gpt",
                "tier_id": "gpt-plus",
                "option_label": "1 个月",
                "channel": "新渠道",
            },
        },
    }
    body = review.build_publish_body(official)
    assert body["catalog_surface"] == "official-membership"
    assert body["membership_placement"]["option"]["productId"] == "newplus"

    prepared = {
        "product": {"id": "acc1", "title": "成品"},
        "draft": {
            "proposed_title": "成品标题",
            "surface": "prepared-accounts",
            "usage": {"steps": []},
            "prepared_guess": {
                "login_method": "google-email-2fa",
                "offer_group_id": "gpt-plus-google-email",
                "delivery_category": "未接码",
                "warranty_label": "质保首登",
            },
        },
    }
    body2 = review.build_publish_body(prepared)
    assert body2["prepared_placement"]["login_method"] == "google-email-2fa"


def test_guess_surface_prefers_prepared_and_official_keywords():
    assert review._guess_surface("GPT Plus成品UPI渠道", "成品") == "prepared-accounts"
    assert review._guess_surface("Grok Super直充卡密（两个月）", "GROK和其他") == "official-membership"
    assert review._guess_surface("神秘礼包", "其他") == "other"


def test_pending_menu_prefers_shop_core_queue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_sessions, "_FILE", Path("review_sessions.json"))
    monkeypatch.setattr(review, "NAVIGATOR_ROOT", tmp_path / "nav")
    monkeypatch.setattr(
        review,
        "load_state",
        lambda: {"87x586": {"title": "state标题", "price": "139.00"}},
    )

    def _fake_core(*, pending_only=True):
        return [
            {
                "product_id": "87x586",
                "status": "pending_manual_enrichment",
                "title": "Grok Super直充卡密（两个月）",
                "payload": {
                    "changed_fields": ["description"],
                    "detected_at": "2026-07-22T13:25:38Z",
                },
            }
        ]

    monkeypatch.setattr("shop.core_client.fetch_review_queue_sync", _fake_core)
    menu = review.build_pending_menu()
    assert len(menu) == 1
    assert menu[0]["product_id"] == "87x586"
    assert menu[0]["source"] == "shop-core"
    assert menu[0]["title"].startswith("Grok Super")
    assert "description" in menu[0]["changed_fields"]


def test_pending_menu_numbers_and_selection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_sessions, "_FILE", Path("review_sessions.json"))
    root = tmp_path / "nav"
    qdir = root / "data" / "review-queue"
    qdir.mkdir(parents=True)
    (qdir / "g28zpj.json").write_text(
        json.dumps(
            {
                "product_id": "g28zpj",
                "status": "pending_manual_enrichment",
                "changed_fields": ["title"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (qdir / "90kcuj.json").write_text(
        json.dumps(
            {
                "product_id": "90kcuj",
                "status": "pending_manual_enrichment",
                "changed_fields": ["description"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(review, "NAVIGATOR_ROOT", root)
    # 强制走 navigator 文件路径，避免测试机上 live shop-core 污染
    monkeypatch.setattr(
        "shop.core_client.fetch_review_queue_sync",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("core disabled in test")),
    )
    monkeypatch.setattr(
        review,
        "load_state",
        lambda: {
            "g28zpj": {"title": "成品A", "price": "21.50"},
            "90kcuj": {"title": "订阅B", "price": "144.00"},
        },
    )
    menu = review.build_pending_menu()
    assert [m["product_id"] for m in menu] == ["g28zpj", "90kcuj"]
    text = review.format_pending_menu(menu)
    assert "1." in text and "2." in text
    assert review.resolve_menu_selection("1") == "g28zpj"
    assert review.resolve_menu_selection("审核 2") == "90kcuj"


def test_apply_writes_override(tmp_path, monkeypatch):
    product_id = "g28zpj"
    catalog_dir = tmp_path / "data" / "catalog" / "products"
    override_dir = tmp_path / "data" / "overrides" / "products"
    catalog_dir.mkdir(parents=True)
    override_dir.mkdir(parents=True)
    catalog = {
        "id": product_id,
        "display": {"title": "旧展示标题"},
        "sections": {},
        "editorial": {},
        "source": {"description_html": "<p>old</p>"},
    }
    (catalog_dir / f"{product_id}.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(review, "NAVIGATOR_ROOT", tmp_path)
    monkeypatch.setattr(review, "REVIEW_APPLY_ENABLED", True)
    monkeypatch.chdir(tmp_path)

    session = {
        "id": "abc123",
        "product": {
            "id": product_id,
            "title": "原标题",
            "description_html": "<p>new html</p>",
            "description": "new md",
        },
        "draft": {
            "proposed_title": "新标题",
            "surface": "prepared-accounts",
            "usage": {
                "heading": "操作步骤",
                "render": "timeline",
                "layout": "vertical",
                "intro_markdown": "",
                "steps": [{"id": "step-01", "title": "打开", "body_markdown": "打开官网", "image_asset": None}],
            },
        },
        "force_original_title": False,
    }
    result = review._apply_to_navigator(session)
    assert result["applied"] is True
    override = json.loads((override_dir / f"{product_id}.json").read_text(encoding="utf-8"))
    assert override["display"]["title"] == "新标题"
    assert override["sections"]["usage"]["steps"][0]["title"] == "打开"
    updated = json.loads((catalog_dir / f"{product_id}.json").read_text(encoding="utf-8"))
    assert updated["display"]["title"] == "新标题"
    assert updated["source"]["description_html"] == "<p>new html</p>"


def test_keep_title_then_effective_title():
    session = {
        "product": {"title": "原标题"},
        "draft": {"proposed_title": "建议标题"},
        "force_original_title": True,
    }
    assert review.effective_title(session) == "原标题"


def test_review_sessions_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_sessions, "_FILE", Path("review_sessions.json"))
    review_sessions.set_current({"id": "1", "status": "awaiting_confirmation"})
    assert review_sessions.get_current()["id"] == "1"
    review_sessions.enqueue({"id": "2"})
    assert review_sessions.queue_length() == 1
    review_sessions.clear_current(archive=True)
    assert review_sessions.get_current() is None
    assert review_sessions.pop_queue()["id"] == "2"


@pytest.mark.asyncio
async def test_handle_owner_text_no_session_approve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_sessions, "_FILE", Path("review_sessions.json"))
    review_sessions.set_current(None)
    text = await review.handle_owner_text("可以")
    assert "没有进行中的审核" in text
