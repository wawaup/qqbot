"""
从 FxTwitter 公开接口拉取指定账号的时间线。

接口：GET {TWITTER_API_BASE}/2/profile/{handle}/statuses?count=20
无需官方 API Key。国内服务器访问 Twitter / FxTwitter 通常需要在
TWITTER_HTTP_PROXY 配 outbound 代理。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import httpx

from twitter.models import Tweet

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _client_kwargs(proxy: str = "") -> dict:
    kwargs: dict = {
        "timeout": 30,
        "headers": {"User-Agent": _UA, "Accept": "application/json"},
        "follow_redirects": True,
        "trust_env": False,  # 只用 TWITTER_HTTP_PROXY，避免误走系统代理或直连
    }
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def _prefer_large(url: str) -> str:
    """把 Twitter 图链的 name=orig 换成 large，避免超大原图被 QQ 拒收。"""
    if not url:
        return url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if qs.get("name") == ["orig"]:
        qs["name"] = ["large"]
        query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=query))
    return url


def _photo_urls(media: dict) -> list[str]:
    urls: list[str] = []
    for photo in media.get("photos") or []:
        url = photo.get("url") or ""
        if url:
            urls.append(_prefer_large(url))
    return urls


def _video_info(media: dict) -> tuple[bool, str]:
    videos = media.get("videos") or []
    if not videos:
        return False, ""
    thumb = videos[0].get("thumbnail_url") or ""
    return True, thumb


def parse_status(item: dict) -> Tweet | None:
    """把 FxTwitter 单条 status JSON 转成 Tweet；无法识别则返回 None。"""
    if not item or item.get("type") != "status":
        return None
    tweet_id = str(item.get("id") or "")
    if not tweet_id:
        return None

    author = item.get("author") or {}
    reply = item.get("replying_to")
    reply_to = None
    reply_to_id = None
    if isinstance(reply, dict):
        reply_to = reply.get("screen_name") or None
        if reply.get("status"):
            reply_to_id = str(reply["status"])

    quote = item.get("quote")
    quote_handle = None
    quote_text = None
    if isinstance(quote, dict) and quote.get("type") == "status":
        q_author = quote.get("author") or {}
        quote_handle = q_author.get("screen_name") or None
        quote_text = quote.get("text") or None

    media = item.get("media") or {}
    photos = _photo_urls(media) if isinstance(media, dict) else []
    has_video, video_thumb = _video_info(media) if isinstance(media, dict) else (False, "")

    # 只保留正文自己的图，不带引用推、评论里的图
    seen: set[str] = set()
    unique_photos: list[str] = []
    for url in photos:
        if url not in seen:
            seen.add(url)
            unique_photos.append(url)
        if len(unique_photos) >= 4:
            break

    if not unique_photos and video_thumb:
        unique_photos = [video_thumb]

    return Tweet(
        id=tweet_id,
        url=item.get("url") or f"https://x.com/i/status/{tweet_id}",
        text=(item.get("text") or "").strip(),
        created_timestamp=int(item.get("created_timestamp") or 0),
        author_handle=author.get("screen_name") or "",
        author_name=author.get("name") or "",
        is_retweet=bool(item.get("reposted_by")),
        is_reply=bool(reply_to),
        reply_to_handle=reply_to,
        reply_to_id=reply_to_id,
        quote_handle=quote_handle,
        quote_text=quote_text,
        photos=unique_photos,
        has_video=has_video,
        video_thumb=video_thumb,
    )


def parse_timeline(payload: dict) -> list[Tweet]:
    tweets: list[Tweet] = []
    for item in payload.get("results") or []:
        tweet = parse_status(item)
        if tweet:
            tweets.append(tweet)
    return tweets


def _handle(username: str) -> str:
    return username.lstrip("@").lower()


def is_self_reply(tweet: Tweet, username: str) -> bool:
    """回复自己的帖（串推），不是回复别人。"""
    return tweet.is_reply and (tweet.reply_to_handle or "").lower() == _handle(username)


def should_forward(
    tweet: Tweet,
    username: str,
    *,
    include_replies: bool,
    include_retweets: bool,
) -> bool:
    """是否把这条推文转发到群。默认：原创 + 引用 + 自己的串推 + 转发；跳过回复别人。"""
    if tweet.is_retweet and not include_retweets:
        return False
    if tweet.is_reply and not is_self_reply(tweet, username) and not include_replies:
        return False
    return True


def group_threads(tweets: list[Tweet], username: str) -> list[list[Tweet]]:
    """把回复自己的帖挂到原帖后面，回复别人的帖单独成组。

    时间线里找不到父帖时（太旧被挤出 20 条），这条串推自己当根。
    """
    by_id = {t.id: t for t in tweets}
    children: dict[str, list[Tweet]] = {}
    roots: list[Tweet] = []
    for t in sorted(tweets, key=lambda x: (x.created_timestamp, x.id)):
        parent_id = t.reply_to_id if is_self_reply(t, username) else None
        if parent_id and parent_id in by_id:
            children.setdefault(parent_id, []).append(t)
        else:
            roots.append(t)

    def flatten(root: Tweet) -> list[Tweet]:
        chain = [root]
        for child in children.get(root.id, []):
            chain.extend(flatten(child))
        return chain

    return [flatten(root) for root in roots]


def pick_new_tweets(tweets: list[Tweet], seen_ids: set[str]) -> list[Tweet]:
    """返回尚未见过的推文，按时间从旧到新，保证群里阅读顺序正常。"""
    new = [t for t in tweets if t.id not in seen_ids]
    return sorted(new, key=lambda t: t.created_timestamp)


def pick_new_threads(
    tweets: list[Tweet],
    seen_ids: set[str],
    username: str,
    *,
    include_replies: bool,
    include_retweets: bool,
) -> list[list[Tweet]]:
    """有新内容的串推（原帖+回复自己的续帖合成一组），按原帖时间从旧到新。"""
    threads: list[list[Tweet]] = []
    for thread in group_threads(tweets, username):
        root = thread[0]
        if not should_forward(
            root,
            username,
            include_replies=include_replies,
            include_retweets=include_retweets,
        ):
            continue
        if any(t.id not in seen_ids for t in thread):
            threads.append(thread)
    return threads


def collect_photos(tweets: list[Tweet], limit: int = 4) -> list[str]:
    """只取传入推文的正文图，最多 limit 张。"""
    urls: list[str] = []
    seen: set[str] = set()
    for t in tweets:
        for url in t.photos:
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                return urls
    return urls


async def fetch_timeline(username: str | None = None, count: int = 20) -> list[Tweet]:
    from config import TWITTER_API_BASE, TWITTER_HTTP_PROXY, TWITTER_USERNAME

    handle = (username or TWITTER_USERNAME).lstrip("@")
    if not handle:
        raise RuntimeError("未配置 TWITTER_USERNAME")
    if not TWITTER_HTTP_PROXY:
        logger.warning("TWITTER_HTTP_PROXY 未配置，推特请求将直连（国内服务器通常会失败）")
    else:
        logger.info(f"推特请求走代理 {TWITTER_HTTP_PROXY}")

    url = f"{TWITTER_API_BASE.rstrip('/')}/2/profile/{handle}/statuses"
    async with httpx.AsyncClient(**_client_kwargs(TWITTER_HTTP_PROXY)) as client:
        resp = await client.get(url, params={"count": count})
        if resp.status_code == 204:
            return []
        resp.raise_for_status()
        data = resp.json()

    if not isinstance(data, dict):
        raise RuntimeError(f"FxTwitter 返回异常: {data!r:.200}")
    code = data.get("code")
    if code not in (200, None):
        raise RuntimeError(f"FxTwitter 返回错误 code={code}")
    return parse_timeline(data)


async def download_images(urls: list[str]) -> list[bytes]:
    """下载图片字节；单张失败就跳过，不阻断文字发送。"""
    if not urls:
        return []
    from config import TWITTER_HTTP_PROXY

    images: list[bytes] = []
    async with httpx.AsyncClient(**_client_kwargs(TWITTER_HTTP_PROXY)) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if content_type and not content_type.startswith("image/"):
                    logger.warning(f"跳过非图片资源: {url} ({content_type})")
                    continue
                if resp.content:
                    images.append(resp.content)
            except Exception as e:
                logger.warning(f"下载推文图片失败: {url} ({e})")
    return images


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    async def _run():
        from config import TWITTER_USERNAME

        tweets = await fetch_timeline()
        print(f"# @{TWITTER_USERNAME} 共 {len(tweets)} 条")
        for t in tweets:
            flags = []
            if t.is_retweet:
                flags.append("转发")
            if t.is_reply:
                kind = "串推" if t.reply_to_handle and t.reply_to_handle.lower() == TWITTER_USERNAME.lower() else "回复别人"
                flags.append(f"{kind}@{t.reply_to_handle}")
            if t.quote_handle:
                flags.append(f"引用@{t.quote_handle}")
            if t.photos:
                flags.append(f"{len(t.photos)}图")
            if t.has_video:
                flags.append("视频")
            tag = f" [{' '.join(flags)}]" if flags else ""
            print(f"{t.id}{tag}")
            print(f"  {t.text[:80].replace(chr(10), ' / ')}")
            print(f"  {t.url}")
            if "--debug" in sys.argv:
                print(json.dumps({
                    "id": t.id,
                    "photos": t.photos,
                    "has_video": t.has_video,
                }, ensure_ascii=False, indent=2))

    asyncio.run(_run())
