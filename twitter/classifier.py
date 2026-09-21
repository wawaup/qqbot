"""用 LLM 判断推文是否值得转发到 QQ 群。"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from twitter.models import Tweet

logger = logging.getLogger(__name__)

PREVIEW_LIMIT = 150
OPENAI_PROTOCOL = "openai"
ANTHROPIC_PROTOCOLS = frozenset({"anthropic", "antigravity"})

SYSTEM_PROMPT = """你是 QQ 群资讯过滤器。判断这条 X/Twitter 帖子是否值得转发给关注 AI 工具、科技资讯和小铺动态的群用户。

转发（YES）：
- 知识贴：教程、用法、踩坑、经验、评测
- 资讯贴：产品更新、行业新闻、政策变化、店铺补货/有货/缺货/价格/活动等对用户有用的信息
- 技术贴：模型、API、工具、能力变化、技术细节
- 短讯发布也要转：哪怕只有一句话，只要在宣布模型/产品上线、更新、故障、政策（例如「GPT-6来了」），不要当成无信息量短帖
- 长文必须转：X 长文、博客、文章链接。正文几乎只有链接，或摘录里带「长文：」标题，也视为资讯，不要因为看起来像一条链接就跳过
- 模型能力测试要转：针对具体模型的拷打、出题、对比，并且有测试过程或结果（即使题目很整活）。例如用 Codex Astra 跑一道题并说出对错

不转发（NO）：
- 闲聊、心情、日常；完全没有事件或主题的无信息量短帖（有发布事件的短讯不算这类）
- 主要为了给 X 账号涨粉、求互动、引流的运营帖（纯口号、求关注、空洞营销、互关互赞、吐槽没流量）
- AI 趣味互动不要转：好玩的提示词、让 Agent 填图/算命/根据「你了解我的一切」整活、跟风玩梗。没有补货、没有产品动态、没有可复用的技术信息，只是有趣，就当闲聊。例如「除了 AI 就是钱」+ 引用一个有趣提示词

只根据给定摘录判断。只输出 YES 或 NO，不要解释。"""

_TOKEN_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff]+")
_YES = {"YES", "Y", "TRUE", "1", "KEEP", "FORWARD", "转发"}
_NO = {"NO", "N", "FALSE", "0", "SKIP", "跳过"}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "alpu.asia", "www.alpu.asia"}


def preview_text(thread: list[Tweet], limit: int = PREVIEW_LIMIT) -> str:
    """拼接原帖、评论、引用和长文标题，截取前 limit 字给模型。"""
    parts: list[str] = []
    for tweet in thread:
        if tweet.text:
            parts.append(tweet.text.strip())
        if tweet.article_title:
            parts.append(f"长文：{tweet.article_title.strip()}")
        if tweet.article_preview:
            parts.append(tweet.article_preview.strip())
        if tweet.quote_text:
            parts.append(tweet.quote_text.strip())
    return "\n".join(parts)[:limit].strip()


def parse_verdict(raw: str) -> bool | None:
    """把模型输出解析成 True=转发 / False=跳过；无法识别则 None。"""
    text = (raw or "").strip().strip("`").strip()
    if not text:
        return None
    first = text.splitlines()[0]
    token = _TOKEN_RE.sub(" ", first).strip().upper().split()
    if not token:
        return None
    word = token[0]
    if word in _YES:
        return True
    if word in _NO:
        return False
    return None


def _normalize_protocol(protocol: str) -> str:
    normalized = (protocol or OPENAI_PROTOCOL).strip().lower()
    if normalized in ANTHROPIC_PROTOCOLS or normalized == OPENAI_PROTOCOL:
        return normalized
    return OPENAI_PROTOCOL


def _extract_content(payload: dict, protocol: str) -> str:
    if protocol in ANTHROPIC_PROTOCOLS:
        blocks = payload.get("content") or []
        texts = [
            str(block.get("text") or "").strip()
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t)

    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, str):
        return content.strip()
    return ""


def _request_args(protocol: str, api_base: str, api_key: str, model: str, preview: str) -> tuple[str, dict, dict]:
    base = api_base.rstrip("/")
    if protocol in ANTHROPIC_PROTOCOLS:
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "system": SYSTEM_PROMPT,
            "temperature": 0,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": preview}],
        }
        return url, headers, body

    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": preview},
        ],
    }
    return url, headers, body


def _client_kwargs(api_base: str, headers: dict) -> dict:
    from config import TWITTER_HTTP_PROXY, TWITTER_LLM_HTTP_PROXY

    kwargs: dict = {
        "timeout": 20,
        "headers": headers,
        "follow_redirects": True,
        "trust_env": False,
    }
    proxy = TWITTER_LLM_HTTP_PROXY
    if proxy is None:
        host = (urlparse(api_base).hostname or "").lower()
        proxy = "" if host in _LOCAL_HOSTS else TWITTER_HTTP_PROXY
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


async def is_worth_forwarding(text: str) -> bool | None:
    """True=转发，False=跳过，None=调用失败（本轮不发、不记已读，下轮重试）。"""
    preview = (text or "").strip()
    if not preview:
        return False

    from config import (
        TWITTER_LLM_API_BASE,
        TWITTER_LLM_API_KEY,
        TWITTER_LLM_API_PROTOCOL,
        TWITTER_LLM_MODEL,
    )

    if not TWITTER_LLM_API_KEY:
        logger.error("TWITTER_TOPIC_FILTER 已开启但未配置 TWITTER_LLM_API_KEY")
        return None

    protocol = _normalize_protocol(TWITTER_LLM_API_PROTOCOL)
    url, headers, body = _request_args(
        protocol, TWITTER_LLM_API_BASE, TWITTER_LLM_API_KEY, TWITTER_LLM_MODEL, preview
    )

    try:
        async with httpx.AsyncClient(**_client_kwargs(TWITTER_LLM_API_BASE, headers)) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            raw = _extract_content(resp.json(), protocol)
    except Exception as e:
        logger.warning(f"资讯过滤调用模型失败: {e}")
        return None

    verdict = parse_verdict(raw)
    if verdict is None:
        logger.warning(f"资讯过滤无法解析模型输出: {raw!r}")
    return verdict
