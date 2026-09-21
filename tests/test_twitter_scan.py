import pytest

from scheduler import tasks
from storage import twitter_state
from twitter.models import Tweet


def _tweet(tweet_id: str, **kwargs) -> Tweet:
    data = dict(
        id=tweet_id,
        url=f"https://x.com/wawaup1024/status/{tweet_id}",
        text="hello",
        created_timestamp=int(tweet_id),
        author_handle="wawaup1024",
        author_name="曼波波波",
    )
    data.update(kwargs)
    return Tweet(**data)


@pytest.mark.asyncio
async def test_first_run_snapshots_without_sending(monkeypatch, tmp_path):
    monkeypatch.setattr(twitter_state, "_STATE_FILE", tmp_path / "twitter_state.json")
    monkeypatch.setattr(tasks, "TWITTER_TOPIC_FILTER", False)

    sent: list[str] = []

    class _Bot:
        async def send_tweet_notice(self, thread):
            sent.append(thread[0].id)

    monkeypatch.setattr(tasks, "_bot_client", _Bot())
    timeline = [_tweet("10"), _tweet("20")]

    async def fake_fetch(_username):
        return list(timeline)

    monkeypatch.setattr("twitter.fetcher.fetch_timeline", fake_fetch)

    await tasks.scan_tweets_and_notify(first_run=True)
    assert sent == []
    assert twitter_state.load_seen_ids() == ["10", "20"]

    timeline.append(_tweet("30"))
    await tasks.scan_tweets_and_notify(first_run=False)
    assert sent == ["30"]
    assert twitter_state.load_seen_ids()[:3] == ["10", "20", "30"]


@pytest.mark.asyncio
async def test_skips_replies_to_others(monkeypatch, tmp_path):
    monkeypatch.setattr(twitter_state, "_STATE_FILE", tmp_path / "twitter_state.json")
    twitter_state.save_seen_ids(["1"], username="wawaup1024")
    monkeypatch.setattr(tasks, "TWITTER_TOPIC_FILTER", False)

    sent: list[str] = []

    class _Bot:
        async def send_tweet_notice(self, thread):
            sent.append(thread[0].id)

    monkeypatch.setattr(tasks, "_bot_client", _Bot())

    async def fake_fetch(_username):
        return [
            _tweet("1"),
            _tweet("2", is_reply=True, reply_to_handle="someone_else", reply_to_id="99"),
            _tweet("3"),
        ]

    monkeypatch.setattr("twitter.fetcher.fetch_timeline", fake_fetch)
    monkeypatch.setattr(tasks, "TWITTER_INCLUDE_REPLIES", False)

    await tasks.scan_tweets_and_notify(first_run=False)
    assert sent == ["3"]


@pytest.mark.asyncio
async def test_self_replies_sent_with_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(twitter_state, "_STATE_FILE", tmp_path / "twitter_state.json")
    twitter_state.save_seen_ids(["1"], username="wawaup1024")
    monkeypatch.setattr(tasks, "TWITTER_TOPIC_FILTER", False)

    sent: list[list[str]] = []

    class _Bot:
        async def send_tweet_notice(self, thread):
            sent.append([t.id for t in thread])

    monkeypatch.setattr(tasks, "_bot_client", _Bot())

    async def fake_fetch(_username):
        return [
            _tweet("1"),
            _tweet("2", text="主帖"),
            _tweet(
                "3",
                text="续帖",
                is_reply=True,
                reply_to_handle="wawaup1024",
                reply_to_id="2",
            ),
            _tweet(
                "4",
                is_reply=True,
                reply_to_handle="someone_else",
                reply_to_id="99",
            ),
        ]

    monkeypatch.setattr("twitter.fetcher.fetch_timeline", fake_fetch)
    monkeypatch.setattr(tasks, "TWITTER_INCLUDE_REPLIES", False)

    await tasks.scan_tweets_and_notify(first_run=False)
    assert sent == [["2", "3"]]


@pytest.mark.asyncio
async def test_skips_off_topic_chatter(monkeypatch, tmp_path):
    monkeypatch.setattr(twitter_state, "_STATE_FILE", tmp_path / "twitter_state.json")
    twitter_state.save_seen_ids(["1"], username="wawaup1024")
    monkeypatch.setattr(tasks, "TWITTER_TOPIC_FILTER", True)

    sent: list[str] = []

    class _Bot:
        async def send_tweet_notice(self, thread):
            sent.append(thread[0].id)

    monkeypatch.setattr(tasks, "_bot_client", _Bot())

    async def fake_classify(text: str):
        return "火锅" not in text

    monkeypatch.setattr("twitter.classifier.is_worth_forwarding", fake_classify)

    async def fake_fetch(_username):
        return [
            _tweet("1"),
            _tweet("2", text="今天吃了火锅，天气真好"),
            _tweet("3", text="GPT 降智了，plus 额度被砍"),
        ]

    monkeypatch.setattr("twitter.fetcher.fetch_timeline", fake_fetch)

    await tasks.scan_tweets_and_notify(first_run=False)
    assert sent == ["3"]
    assert "2" in twitter_state.load_seen_ids()


@pytest.mark.asyncio
async def test_classifier_failure_retries_next_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(twitter_state, "_STATE_FILE", tmp_path / "twitter_state.json")
    twitter_state.save_seen_ids(["1"], username="wawaup1024")
    monkeypatch.setattr(tasks, "TWITTER_TOPIC_FILTER", True)

    sent: list[str] = []

    class _Bot:
        async def send_tweet_notice(self, thread):
            sent.append(thread[0].id)

    monkeypatch.setattr(tasks, "_bot_client", _Bot())

    async def fake_classify(_text: str):
        return None

    monkeypatch.setattr("twitter.classifier.is_worth_forwarding", fake_classify)

    async def fake_fetch(_username):
        return [_tweet("1"), _tweet("2", text="Claude 补货了")]

    monkeypatch.setattr("twitter.fetcher.fetch_timeline", fake_fetch)

    await tasks.scan_tweets_and_notify(first_run=False)
    assert sent == []
    assert "2" not in twitter_state.load_seen_ids()
