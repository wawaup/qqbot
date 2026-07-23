from bot.warranty import (
    analyze_warranty_title_change,
    extract_warranty_days,
    listing_time_noise_only,
)
from storage import content_state


def test_extract_warranty_days():
    assert extract_warranty_days("GPT Plus 成品【质保三十天】") == 30
    assert extract_warranty_days("质保30天渠道-成品") == 30
    assert extract_warranty_days("谷歌邮件 成品 Plus（质保3天）") == 3
    assert extract_warranty_days("质保 25 天 成品号") == 25
    assert extract_warranty_days("30天质保成品") == 30
    assert extract_warranty_days("无质保 日抛") is None


def test_listing_time_noise_only():
    old = "【质保三十天】GPT plus 稳定号"
    new = "今日补货不定时-【质保三十天】GPT plus 稳定号"
    assert listing_time_noise_only(old, new) is True
    assert listing_time_noise_only(old, "【质保二十五天】GPT plus 稳定号") is False


def test_warranty_shortened_is_important():
    result = analyze_warranty_title_change(
        "质保30天 谷歌邮箱成品",
        "质保25天 谷歌邮箱成品",
        category="成品",
    )
    assert result is not None
    assert result.shortened is True
    assert result.important is True
    assert result.old_days == 30
    assert result.new_days == 25


def test_listing_time_only_title_change_is_suppressed_in_diff():
    previous = content_state.build_snapshot(
        {
            "fvqtzr": {
                "title": "【质保三十天】GPT plus 稳定号",
                "url": "https://pay.ldxp.cn/item/fvqtzr",
                "category": "成品号",
                "description_html": "<p>same</p>",
                "cover_url": "https://cdn.example/a.png",
                "detail_image_urls": [],
            }
        }
    )
    current = content_state.build_snapshot(
        {
            "fvqtzr": {
                "title": "今日补货不定时-【质保三十天】GPT plus 稳定号",
                "url": "https://pay.ldxp.cn/item/fvqtzr",
                "category": "成品号",
                "description_html": "<p>same</p>",
                "cover_url": "https://cdn.example/a.png",
                "detail_image_urls": [],
            }
        }
    )
    changes = content_state.diff_snapshots(previous, current)
    assert changes == []


def test_warranty_shortened_is_not_suppressed():
    previous = content_state.build_snapshot(
        {
            "qk5qf4": {
                "title": "质保30天渠道-PLUS-成品",
                "url": "https://pay.ldxp.cn/item/qk5qf4",
                "category": "成品",
                "description_html": "<p>same</p>",
                "cover_url": "",
                "detail_image_urls": [],
            }
        }
    )
    current = content_state.build_snapshot(
        {
            "qk5qf4": {
                "title": "质保25天渠道-PLUS-成品",
                "url": "https://pay.ldxp.cn/item/qk5qf4",
                "category": "成品",
                "description_html": "<p>same</p>",
                "cover_url": "",
                "detail_image_urls": [],
            }
        }
    )
    changes = content_state.diff_snapshots(previous, current)
    assert len(changes) == 1
    assert changes[0].warranty_shortened is True
    assert "30" in changes[0].warranty_summary and "25" in changes[0].warranty_summary
