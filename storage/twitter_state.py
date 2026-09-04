"""
推文已读快照：记录已经处理过的 tweet id，避免重启或重复扫描刷屏。
"""
import json
from datetime import datetime
from pathlib import Path

_STATE_FILE = Path("twitter_state.json")
_MAX_SEEN = 300


def load_seen_ids() -> list[str]:
    if not _STATE_FILE.exists():
        return []
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        ids = data.get("seen_ids") or []
        return [str(i) for i in ids]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def save_seen_ids(ids: list[str], username: str = "") -> None:
    ordered: list[str] = []
    seen: set[str] = set()
    for tweet_id in ids:
        if tweet_id and tweet_id not in seen:
            ordered.append(tweet_id)
            seen.add(tweet_id)
        if len(ordered) >= _MAX_SEEN:
            break
    data = {
        "username": username,
        "last_scan": datetime.now().isoformat(timespec="seconds"),
        "seen_ids": ordered,
    }
    _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_seen(old_ids: list[str], fetched_ids: list[str]) -> list[str]:
    """把本轮拉到的 id 放到最前，再接上历史 id。"""
    fetched_set = set(fetched_ids)
    return fetched_ids + [i for i in old_ids if i not in fetched_set]
