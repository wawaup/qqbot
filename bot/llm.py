"""C2C 私聊用的 LLM 客户端（Anthropic 兼容网关，模型实际为 Grok）。"""

from __future__ import annotations

import logging

import httpx

from config import (
    GROK_API_BASE_URL,
    GROK_API_KEY,
    GROK_MAX_TOKENS,
    GROK_MODEL,
    GROK_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是「曼波导购」的内部助手，通过 QQ 私聊协助店主处理成品号上新、"
    "官方订阅商品卡片整理、标题/操作步骤审核等运营事务。"
    "回答简洁、直接、用中文。不要编造未提供的商品数据。"
)


class GrokError(RuntimeError):
    """Grok / 网关调用失败。"""


def grok_configured() -> bool:
    return bool(GROK_API_KEY and GROK_API_BASE_URL and GROK_MODEL)


async def chat(user_text: str, *, system: str | None = None, max_tokens: int | None = None) -> str:
    """调用 Anthropic Messages 兼容接口，返回助手文本。"""
    return await chat_messages(
        [{"role": "user", "content": user_text}],
        system=system,
        max_tokens=max_tokens,
    )


async def chat_messages(
    messages: list[dict],
    *,
    system: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """多轮 messages 调用。"""
    if not grok_configured():
        raise GrokError("Grok API 未配置（需要 GROK_API_KEY / GROK_API_BASE_URL / GROK_MODEL）")

    base = GROK_API_BASE_URL.rstrip("/")
    # 兼容只写域名、或已带 /v1 的写法
    if base.endswith("/v1"):
        url = f"{base}/messages"
    else:
        url = f"{base}/v1/messages"

    headers = {
        "x-api-key": GROK_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": GROK_MODEL,
        "max_tokens": max_tokens or GROK_MAX_TOKENS,
        "system": system or SYSTEM_PROMPT,
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=GROK_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or "")[:300]
        logger.error("Grok API HTTP %s: %s", exc.response.status_code, body)
        raise GrokError(f"Grok API 请求失败（HTTP {exc.response.status_code}）") from exc
    except httpx.HTTPError as exc:
        logger.error("Grok API 网络错误: %s", exc)
        raise GrokError(f"Grok API 网络错误: {exc}") from exc
    except ValueError as exc:
        raise GrokError("Grok API 返回了非 JSON 响应") from exc

    return _extract_text(data)


def _extract_text(data: dict) -> str:
    content = data.get("content")
    if not isinstance(content, list):
        raise GrokError("Grok API 响应缺少 content")

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text = block["text"].strip()
            if text:
                parts.append(text)
    if not parts:
        raise GrokError("Grok API 未返回文本内容")
    return "\n".join(parts)
