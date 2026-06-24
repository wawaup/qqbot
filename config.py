import os
from dotenv import load_dotenv

load_dotenv()

BOT_APPID = os.getenv("BOT_APPID", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")

# 支持多群：逗号分隔，如 "openid1,openid2"
GROUP_OPENIDS = [g.strip() for g in os.getenv("GROUP_OPENIDS", "").split(",") if g.strip()]

SHOP_URL = os.getenv("SHOP_URL", "https://pay.ldxp.cn/shop/manboup")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
SANDBOX = os.getenv("SANDBOX", "false").lower() == "true"

STATE_FILE = "state.json"
KEYWORDS_FILE = "keywords.json"
CATEGORY_COMMANDS_FILE = "category_commands.json"

# 补货通知屏蔽的分类（精确匹配分类名）
NOTIFY_EXCLUDE_CATEGORIES: set[str] = {
    "苹果id/谷歌/微软/iCloud邮箱",
}

# keywords.json 的 image 字段 → 图片直链映射
PICS_URLS: dict[str, str] = {
    "pics/展开商品说明.png": "https://s41.ax1x.com/2026/06/24/pmtnnc6.png",
    "pics/meme1.jpg":       "https://s41.ax1x.com/2026/06/24/pmtnVhR.jpg",
    "pics/meme2.jpg":       "https://s41.ax1x.com/2026/06/24/pmtne91.jpg",
    "pics/meme3.jpg":       "https://s41.ax1x.com/2026/06/24/pmtnm1x.jpg",
}
