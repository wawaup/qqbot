import os
from pathlib import Path

from dotenv import load_dotenv

# qqbot/.env 优先；monorepo 根目录 .env.local 作为 Grok 网关等本地密钥的补充来源
_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT.parent / ".env.local", override=False)

BOT_APPID = os.getenv("BOT_APPID", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")
BOT_OPENID = os.getenv("BOT_OPENID", "")  # 机器人在群里的 member openid，用于过滤 @自己

# 支持多群：逗号分隔，如 "openid1,openid2"
GROUP_OPENIDS = [g.strip() for g in os.getenv("GROUP_OPENIDS", "").split(",") if g.strip()]

# 商品说明/图片变化使用独立目标，不与补货通知群共用。
CONTENT_CHANGE_GROUP_OPENIDS = [
    value.strip()
    for value in os.getenv("CONTENT_CHANGE_GROUP_OPENIDS", "").split(",")
    if value.strip()
]
CONTENT_CHANGE_USER_OPENIDS = [
    value.strip()
    for value in os.getenv("CONTENT_CHANGE_USER_OPENIDS", "").split(",")
    if value.strip()
]

# 允许与机器人私聊并调用 Grok 的 user_openid（逗号分隔）。
# 未配置时：仅回显对方 openid，不调用 LLM，避免 token 被他人消耗。
OWNER_USER_OPENIDS = [
    value.strip()
    for value in os.getenv("OWNER_USER_OPENIDS", "").split(",")
    if value.strip()
]

# Grok（经 Anthropic 兼容网关）。优先读 qqbot/.env，也可从 monorepo 根 .env.local 注入。
GROK_API_KEY = os.getenv("GROK_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN", "")
GROK_API_BASE_URL = (
    os.getenv("GROK_API_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL", "")
).rstrip("/")
GROK_MODEL = (
    os.getenv("GROK_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL")
    or "grok-4.5"
)
GROK_MAX_TOKENS = int(os.getenv("GROK_MAX_TOKENS", "1024"))
GROK_TIMEOUT_SECONDS = float(os.getenv("GROK_TIMEOUT_SECONDS", "60"))
# 上新审核拟稿通常更长
GROK_REVIEW_MAX_TOKENS = int(os.getenv("GROK_REVIEW_MAX_TOKENS", "2500"))

# shop-navigator 根目录：仅当显式开启 REVIEW_APPLY_ENABLED 时写入 legacy overrides。
# 生产默认关闭：发布必须以 shop-core publish 成功为准。
_NAV_DEFAULT = _ROOT.parent / "shop-navigator"
NAVIGATOR_ROOT = Path(
    os.getenv("NAVIGATOR_ROOT", str(_NAV_DEFAULT if _NAV_DEFAULT.is_dir() else ""))
).expanduser()
REVIEW_APPLY_ENABLED = os.getenv("REVIEW_APPLY_ENABLED", "false").lower() == "true"
# 本地测试接口：POST /api/v1/review/start （仅绑定本机时建议开启）
REVIEW_API_ENABLED = os.getenv("REVIEW_API_ENABLED", "true").lower() == "true"
REVIEW_API_TOKEN = os.getenv("REVIEW_API_TOKEN", "")  # 非空则要求 Header X-Review-Token

SHOP_URL = os.getenv("SHOP_URL", "https://pay.ldxp.cn/shop/manboup")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
CONTENT_CHECK_INTERVAL = int(os.getenv("CONTENT_CHECK_INTERVAL", "600"))
SANDBOX = os.getenv("SANDBOX", "false").lower() == "true"

# shop-core shared backend. When configured, inventory is read from core instead of LDXP scrape.
SHOP_CORE_BASE_URL = os.getenv("SHOP_CORE_BASE_URL", "").rstrip("/")
SHOP_CORE_INTERNAL_TOKEN = os.getenv("SHOP_CORE_INTERNAL_TOKEN", "")
SHOP_CORE_TIMEOUT_SECONDS = float(os.getenv("SHOP_CORE_TIMEOUT_SECONDS", "15"))
INVENTORY_SOURCE = os.getenv(
    "INVENTORY_SOURCE",
    "shop-core" if SHOP_CORE_BASE_URL else "ldxp",
).strip().lower()
# When core blobs are enabled: put 失败是否禁止写本地权威态（默认 true，避免分叉丢更新）。
# 设为 false 仅用于离线应急。成功写入 core 后仍会镜像一份本地缓存便于只读降级。
_BLOB_FAIL_CLOSED_DEFAULT = "true" if (SHOP_CORE_BASE_URL and SHOP_CORE_INTERNAL_TOKEN) else "false"
BLOB_FAIL_CLOSED = os.getenv("BLOB_FAIL_CLOSED", _BLOB_FAIL_CLOSED_DEFAULT).lower() == "true"

STATUS_API_ENABLED = os.getenv("STATUS_API_ENABLED", "true").lower() == "true"
STATUS_API_HOST = os.getenv("STATUS_API_HOST", "0.0.0.0")
STATUS_API_PORT = int(os.getenv("STATUS_API_PORT", "8080"))
STATUS_API_ALLOWED_ORIGIN = os.getenv("STATUS_API_ALLOWED_ORIGIN", "*")
# When true and shop-core is configured, local status API proxies core status.
STATUS_API_PROXY_CORE = os.getenv("STATUS_API_PROXY_CORE", "true").lower() == "true"

STATE_FILE = "state.json"
KEYWORDS_FILE = "keywords.json"
CATEGORY_COMMANDS_FILE = "category_commands.json"

# 补货/上新/上架通知屏蔽的分类（按分类 id 匹配，不受改名影响）
NOTIFY_EXCLUDE_CATEGORIES: set[int] = set()

# 同一商品两次通知之间的最小间隔（秒），防止反复上架/补货刷屏，默认 15 分钟
# 覆盖新品/上架/补货三种事件，同一 goods_key 共用一份冷却
NOTIFY_COOLDOWN: int = int(os.getenv("NOTIFY_COOLDOWN", "900"))

# keywords.json 的 image 字段 → 图片直链映射
PICS_URLS: dict[str, str] = {
    "pics/展开商品说明.png": "http://120.27.141.92/pics/expand-tip.png",
    "pics/meme1.jpg":       "http://120.27.141.92/pics/meme1.jpg",
    "pics/meme2.jpg":       "http://120.27.141.92/pics/meme2.jpg",
    "pics/meme3.jpg":       "http://120.27.141.92/pics/meme3.jpg",
}
