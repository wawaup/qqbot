import pytest

from twitter.classifier import is_worth_forwarding, parse_verdict, preview_text
from twitter.models import Tweet


def _tweet(tweet_id: str, text: str, **kwargs) -> Tweet:
    data = dict(
        id=tweet_id,
        url=f"https://x.com/x/status/{tweet_id}",
        text=text,
        created_timestamp=1,
        author_handle="wawaup1024",
        author_name="x",
    )
    data.update(kwargs)
    return Tweet(**data)


def test_preview_text_truncates_to_150_and_includes_comment_quote():
    root = _tweet("1", "A" * 80)
    comment = _tweet("2", "B" * 80, is_reply=True, reply_to_id="1")
    quoted = _tweet("3", "正文", quote_text="C" * 40)
    assert preview_text([root]) == "A" * 80
    combined = preview_text([root, comment])
    assert len(combined) == 150
    assert combined.startswith("A" * 80)
    assert "B" in combined
    assert preview_text([quoted]).endswith("C" * 40)


def test_prompt_skips_fun_prompts_keeps_real_evals():
    from twitter.classifier import SYSTEM_PROMPT

    assert "好玩的提示词" in SYSTEM_PROMPT
    assert "Codex Astra" in SYSTEM_PROMPT
    assert "AI 趣味互动不要转" in SYSTEM_PROMPT


def test_preview_text_includes_article_title():
    tweet = _tweet(
        "1",
        "https://x.com/i/article/123",
        article_title="持续更新：GPT 降智恢复方案",
        article_preview="capacity 报错已经影响到很多用户",
    )
    preview = preview_text([tweet])
    assert preview.startswith("https://x.com/i/article/123")
    assert "长文：持续更新：GPT 降智恢复方案" in preview
    assert "capacity 报错" in preview


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("YES", True),
        ("yes.", True),
        ("转发", True),
        ("NO", False),
        ("skip", False),
        ("跳过", False),
        ("", None),
        ("嗯也许吧", None),
    ],
)
def test_parse_verdict(raw, expected):
    assert parse_verdict(raw) == expected


@pytest.mark.asyncio
async def test_empty_preview_skips_without_http(monkeypatch):
    called = []

    class _Boom:
        def __init__(self, *args, **kwargs):
            called.append(True)

    monkeypatch.setattr("twitter.classifier.httpx.AsyncClient", _Boom)
    assert await is_worth_forwarding("   ") is False
    assert called == []


@pytest.mark.asyncio
async def test_is_worth_forwarding_reads_yes(monkeypatch):
    monkeypatch.setattr("config.TWITTER_LLM_API_KEY", "sk-test")
    monkeypatch.setattr("config.TWITTER_LLM_API_BASE", "https://api.openai.com/v1")
    monkeypatch.setattr("config.TWITTER_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr("config.TWITTER_LLM_API_PROTOCOL", "openai")
    monkeypatch.setattr("config.TWITTER_LLM_HTTP_PROXY", "")
    monkeypatch.setattr("config.TWITTER_HTTP_PROXY", "")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "YES"}}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            assert url.endswith("/chat/completions")
            assert json["messages"][1]["content"] == "GPT 降智了"
            return _Resp()

    monkeypatch.setattr("twitter.classifier.httpx.AsyncClient", _Client)
    assert await is_worth_forwarding("GPT 降智了") is True


@pytest.mark.asyncio
async def test_antigravity_uses_messages_api(monkeypatch):
    monkeypatch.setattr("config.TWITTER_LLM_API_KEY", "sk-test")
    monkeypatch.setattr("config.TWITTER_LLM_API_BASE", "https://alpu.asia")
    monkeypatch.setattr("config.TWITTER_LLM_MODEL", "gemini-3.1-pro-high")
    monkeypatch.setattr("config.TWITTER_LLM_API_PROTOCOL", "antigravity")
    monkeypatch.setattr("config.TWITTER_LLM_HTTP_PROXY", "")
    monkeypatch.setattr("config.TWITTER_HTTP_PROXY", "http://127.0.0.1:7890")

    seen = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "NO"}]}

    class _Client:
        def __init__(self, **kwargs):
            seen["proxy"] = kwargs.get("proxy")
            seen["x-api-key"] = kwargs.get("headers", {}).get("x-api-key")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            seen["url"] = url
            seen["model"] = json["model"]
            assert json["messages"][0]["content"] == "今天吃了火锅"
            return _Resp()

    monkeypatch.setattr("twitter.classifier.httpx.AsyncClient", _Client)
    assert await is_worth_forwarding("今天吃了火锅") is False
    assert seen["url"] == "https://alpu.asia/v1/messages"
    assert seen["model"] == "gemini-3.1-pro-high"
    assert seen["x-api-key"] == "sk-test"
    assert seen["proxy"] is None


@pytest.mark.asyncio
async def test_missing_api_key_returns_none(monkeypatch):
    monkeypatch.setattr("config.TWITTER_LLM_API_KEY", "")
    assert await is_worth_forwarding("Claude 补货了") is None
