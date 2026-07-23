import pytest

from bot import llm
from bot.handlers import _is_owner_openid
from config import OWNER_USER_OPENIDS


def test_extract_text_joins_text_blocks():
    data = {
        "content": [
            {"type": "text", "text": "第一段"},
            {"type": "tool_use", "id": "x"},
            {"type": "text", "text": "第二段"},
        ]
    }
    assert llm._extract_text(data) == "第一段\n第二段"


def test_extract_text_requires_text():
    with pytest.raises(llm.GrokError):
        llm._extract_text({"content": [{"type": "tool_use"}]})


def test_owner_openid_allowlist(monkeypatch):
    monkeypatch.setattr("bot.handlers.OWNER_USER_OPENIDS", ["AAA", "BBB"])
    assert _is_owner_openid("AAA") is True
    assert _is_owner_openid("CCC") is False
    assert _is_owner_openid("") is False
    assert _is_owner_openid(None) is False


@pytest.mark.asyncio
async def test_chat_requires_config(monkeypatch):
    monkeypatch.setattr(llm, "GROK_API_KEY", "")
    monkeypatch.setattr(llm, "GROK_API_BASE_URL", "")
    monkeypatch.setattr(llm, "GROK_MODEL", "")
    with pytest.raises(llm.GrokError):
        await llm.chat("hello")


def test_owner_list_is_list():
    assert isinstance(OWNER_USER_OPENIDS, list)
