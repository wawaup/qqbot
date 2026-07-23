"""成品号标题中的质保天数解析，以及「仅上架时间变化 vs 质保缩短」判定。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 中文数字（常见质保天数）
_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "二十": 20,
    "二十五": 25,
    "三十": 30,
}

# 质保 N 天 / 质保N天 / 质保三十天 / 30天质保 / 质保 30 天
_WARRANTY_PATTERNS = [
    re.compile(
        r"质保\s*(?P<num>\d{1,3}|[一二两三四五六七八九十]{1,3})\s*天",
        re.I,
    ),
    re.compile(
        r"(?P<num>\d{1,3})\s*天\s*质保",
        re.I,
    ),
    re.compile(
        r"质保\s*(?P<num>\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:日|号)?",
        re.I,
    ),
    re.compile(
        r"质保订阅\s*(?P<num>\d{1,3})\s*天",
        re.I,
    ),
]

# 更像「上架/补货时间话术」而不是质保条款的片段
_LISTING_TIME_PATTERNS = [
    re.compile(r"今日补货"),
    re.compile(r"不定时"),
    re.compile(r"刚刚上架"),
    re.compile(r"新上架"),
    re.compile(r"刚上架"),
    re.compile(r"今日上架"),
    re.compile(r"补货不定时"),
    re.compile(r"\d{1,2}月\d{1,2}日"),
    re.compile(r"\d{1,2}/\d{1,2}"),
    re.compile(r"今晚补"),
    re.compile(r"马上补"),
]


def _parse_num(token: str) -> int | None:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token)


def extract_warranty_days(title: str) -> int | None:
    """从标题提取质保天数；多种写法取第一个能解析的。"""
    text = title or ""
    for pattern in _WARRANTY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        days = _parse_num(match.group("num"))
        if days is not None and 0 < days <= 365:
            return days
    return None


def is_prepared_account_title(title: str, category: str = "") -> bool:
    text = f"{title} {category}"
    return any(k in text for k in ("成品", "成品号", "账号", "邮箱交付", "接码成品"))


def listing_time_noise_only(old_title: str, new_title: str) -> bool:
    """标题变化是否主要是上架/补货时间话术（质保天数未变时使用）。"""
    if (old_title or "").strip() == (new_title or "").strip():
        return False
    # 去掉常见时间话术后若核心标题仍接近，视为仅上架时间变化
    def scrub(t: str) -> str:
        s = t or ""
        for pattern in _LISTING_TIME_PATTERNS:
            s = pattern.sub("", s)
        s = re.sub(r"[\s\|\-_/【】\[\]（）()·,，.。]+", "", s)
        return s

    return scrub(old_title) == scrub(new_title) and scrub(old_title) != ""


@dataclass(frozen=True)
class WarrantyTitleChange:
    old_days: int | None
    new_days: int | None
    shortened: bool
    listing_time_only: bool
    important: bool
    summary: str


def analyze_warranty_title_change(
    old_title: str,
    new_title: str,
    *,
    category: str = "",
) -> WarrantyTitleChange | None:
    """分析成品号标题变化。非成品号返回 None。"""
    if not is_prepared_account_title(new_title or old_title, category):
        return None
    old_days = extract_warranty_days(old_title)
    new_days = extract_warranty_days(new_title)
    listing_only = listing_time_noise_only(old_title, new_title)
    shortened = (
        old_days is not None
        and new_days is not None
        and new_days < old_days
    )
    # 用户点名：30 天质保成品号，质保变短必须通知；仅上架时间可忽略
    important = shortened
    if shortened:
        summary = f"质保缩短：{old_days} 天 → {new_days} 天"
    elif listing_only and old_days == new_days:
        summary = "标题仅上架/补货时间话术变化，质保未变"
    elif old_days != new_days:
        summary = f"质保天数变化：{old_days} → {new_days}"
    else:
        summary = "成品号标题变化（质保天数未缩短）"
    return WarrantyTitleChange(
        old_days=old_days,
        new_days=new_days,
        shortened=shortened,
        listing_time_only=listing_only and not shortened,
        important=important,
        summary=summary,
    )
