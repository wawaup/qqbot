from dataclasses import dataclass, field


@dataclass
class Tweet:
    id: str
    url: str
    text: str
    created_timestamp: int
    author_handle: str
    author_name: str
    is_retweet: bool = False
    is_reply: bool = False
    reply_to_handle: str | None = None
    reply_to_id: str | None = None
    quote_handle: str | None = None
    quote_text: str | None = None
    photos: list[str] = field(default_factory=list)
    has_video: bool = False
    video_thumb: str = ""
