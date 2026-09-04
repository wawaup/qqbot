from pathlib import Path

import pytest

from bot.formatter import format_tweet_notice
from storage import twitter_state
from twitter.fetcher import group_threads, parse_timeline, pick_new_threads, pick_new_tweets, should_forward

FIXTURE = Path(__file__).parent / "fixtures" / "sample_statuses.json"

USERNAME = "wawaup1024"


@pytest.fixture
def tweets():
    import json
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return parse_timeline(payload)


def _by_id(tweets):
    return {t.id: t for t in tweets}


def test_parse_original_with_photo(tweets):
    t = _by_id(tweets)["2095706521854513424"]
    assert not t.is_reply
    assert not t.is_retweet
    assert t.author_handle == "wawaup1024"
    assert "bankreset" in t.text
    assert t.photos == [
        "https://pbs.twimg.com/media/HRVx-RgbMAANZyF.jpg?name=large"
    ]
    assert should_forward(t, USERNAME, include_replies=False, include_retweets=True)


def test_self_reply_is_forwarded(tweets):
    t = _by_id(tweets)["2095740814685356278"]
    assert t.is_reply
    assert t.reply_to_handle == "wawaup1024"
    assert t.reply_to_id == "2095706521854513424"
    assert should_forward(t, USERNAME, include_replies=False, include_retweets=True)


def test_self_replies_grouped_with_parent(tweets):
    threads = group_threads(tweets, USERNAME)
    bundled = next(
        th for th in threads if th[0].id == "2095706521854513424"
    )
    assert [t.id for t in bundled] == [
        "2095706521854513424",
        "2095740814685356278",
    ]
    text = format_tweet_notice(bundled[0], "曼波小店资讯", thread=bundled)
    assert "bankreset" in text
    assert "#chatGPT" not in text
    assert "🧵 续" not in text
    assert "**💬 追加评论**" in text
    assert "本次羊毛的最佳实践" in text
    assert text.startswith("**🌐 曼波小店资讯** 2026年9月4日 10:52")
    assert "**💬 追加评论** 2026年9月4日 13:08" in text
    assert "**🔗 原贴链接**" in text
    # 加粗只包小标题，时间在加粗标记外面
    assert "**🌐 曼波小店资讯 2026年9月4日 10:52**" not in text

    new = pick_new_threads(
        tweets,
        seen_ids=set(),
        username=USERNAME,
        include_replies=False,
        include_retweets=True,
    )
    assert any(th[0].id == "2095706521854513424" and len(th) == 2 for th in new)
    others = next(th for th in threads if th[0].id == "9999999999999999999")
    assert len(others) == 1
    assert not should_forward(
        others[0], USERNAME, include_replies=False, include_retweets=True
    )


def test_reply_to_others_skipped_by_default(tweets):
    t = _by_id(tweets)["9999999999999999999"]
    assert t.is_reply
    assert t.reply_to_handle == "someone_else"
    assert not should_forward(t, USERNAME, include_replies=False, include_retweets=True)
    assert should_forward(t, USERNAME, include_replies=True, include_retweets=True)


def test_retweet_respects_flag(tweets):
    t = _by_id(tweets)["2095217830711198073"]
    assert t.is_retweet
    assert t.author_handle == "MaxForAI"
    assert should_forward(t, USERNAME, include_replies=False, include_retweets=True)
    assert not should_forward(t, USERNAME, include_replies=False, include_retweets=False)


def test_quote_fields(tweets):
    t = _by_id(tweets)["2095751662799568973"]
    assert t.quote_handle == "Abomination81"
    assert "GPT 6 Astra" in (t.quote_text or "")
    text = format_tweet_notice(t, "曼波小店资讯")
    assert text.startswith("**🌐 曼波小店资讯**")
    assert "引用 @Abomination81" in text
    assert t.url in text
    assert not text.startswith("#")


def test_format_retweet(tweets):
    t = _by_id(tweets)["2095217830711198073"]
    text = format_tweet_notice(t, "曼波小店资讯")
    assert text.startswith("**🔁 转发了 @MaxForAI**")
    assert t.url in text
    assert "**🔗 原贴链接**" in text


def test_pick_new_tweets_oldest_first(tweets):
    new = pick_new_tweets(tweets, seen_ids=set())
    ids = [t.id for t in new]
    assert ids == sorted(ids, key=lambda i: _by_id(tweets)[i].created_timestamp)
    remaining = pick_new_tweets(tweets, seen_ids={ids[0]})
    assert remaining[0].id == ids[1]


def test_merge_and_persist_seen_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(twitter_state, "_STATE_FILE", tmp_path / "twitter_state.json")
    assert twitter_state.load_seen_ids() == []
    merged = twitter_state.merge_seen(["old1", "keep"], ["new1", "keep"])
    assert merged[:3] == ["new1", "keep", "old1"]
    twitter_state.save_seen_ids(merged, username=USERNAME)
    assert twitter_state.load_seen_ids() == ["new1", "keep", "old1"]
