import os
from dotenv import load_dotenv

load_dotenv()

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
