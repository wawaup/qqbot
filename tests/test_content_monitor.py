from unittest.mock import AsyncMock, Mock

import pytest

from bot.formatter import format_content_change_notice
from scheduler import tasks
from shop.scraper import _extract_detail_image_urls
from storage import content_state


def _entry(
    *,
    title: str = "GPT Plus",
    description_html: str = "<p>old</p>",
    cover_url: str = "https://cdn.example/cover-old.png",
    detail_image_urls: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "url": "https://pay.ldxp.cn/item/test",
        "description_html": description_html,
        "cover_url": cover_url,
        "detail_image_urls": detail_image_urls or ["https://cdn.example/detail-old.png"],
        "listed": True,
    }


def test_content_snapshot_detects_only_catalog_material_changes():
    previous = content_state.build_snapshot({"existing": _entry()})
    current = content_state.build_snapshot(
        {
            "existing": _entry(
                title="GPT Plus 新标题",
                description_html="<p>new</p>",
                cover_url="https://cdn.example/cover-new.png",
                detail_image_urls=["https://cdn.example/detail-new.png"],
            ),
            "new-product": _entry(title="New product"),
        }
    )

    changes = content_state.diff_snapshots(previous, current)

    assert [(change.product_id, change.changed_fields) for change in changes] == [
        ("existing", ("title", "description", "cover", "detail_images")),
        ("new-product", ("new_product",)),
    ]


def test_content_change_notice_lists_actionable_fields():
    changes = [
        content_state.ContentChange(
            product_id="existing",
            title="GPT Plus 新标题",
            url="https://pay.ldxp.cn/item/test",
            changed_fields=("description", "detail_images"),
        )
    ]

    message = format_content_change_notice(changes)

    assert "商品资料变化待处理" in message
    assert "商品说明、详情图片" in message
    assert "https://pay.ldxp.cn/item/test" in message


def test_detail_image_urls_are_normalized_and_deduplicated():
    html = """
    <img src="https://cdn.example/detail.png">
    <img data-src="/uploads/second.png">
    <img src="https://cdn.example/detail.png">
    """

    assert _extract_detail_image_urls(html) == (
        "https://cdn.example/detail.png",
        "https://pay.ldxp.cn/uploads/second.png",
    )


@pytest.mark.asyncio
async def test_content_check_advances_baseline_only_after_notification(monkeypatch):
    previous = content_state.build_snapshot({"existing": _entry()})
    current_products = {
        "existing": _entry(description_html="<p>changed</p>")
    }
    save_snapshot = Mock()
    bot_client = type(
        "BotClient",
        (),
        {"send_content_change_notice": AsyncMock(return_value=False)},
    )()

    monkeypatch.setattr(tasks.state, "load_state", lambda: current_products)
    monkeypatch.setattr(tasks.content_state, "load_snapshot", lambda: previous)
    monkeypatch.setattr(tasks.content_state, "save_snapshot", save_snapshot)
    monkeypatch.setattr(tasks, "_bot_client", bot_client)

    await tasks.check_catalog_content_changes()
    save_snapshot.assert_not_called()

    bot_client.send_content_change_notice.return_value = True
    await tasks.check_catalog_content_changes()
    save_snapshot.assert_called_once()


def test_scheduler_runs_content_check_every_ten_minutes():
    scheduler = tasks.create_scheduler()
    job = scheduler.get_job("catalog_content_check")

    assert job is not None
    assert job.trigger.interval.total_seconds() == 600
