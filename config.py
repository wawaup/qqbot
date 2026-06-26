import os
from dotenv import load_dotenv

load_dotenv()

BOT_APPID = os.getenv("BOT_APPID", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")
BOT_OPENID = os.getenv("BOT_OPENID", "")  # 机器人在群里的 member openid，用于过滤 @自己

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

# GPT 分类：低于此价格的商品补货立即通知，其余走 30 分钟批量通知
GPT_INSTANT_PRICE_THRESHOLD: float = float(os.getenv("GPT_INSTANT_PRICE_THRESHOLD", "20"))
# GPT 分类批量通知间隔（秒），默认 30 分钟
GPT_BATCH_INTERVAL: int = int(os.getenv("GPT_BATCH_INTERVAL", "1800"))
# GPT 分类对应的实际店铺分类名（与爬虫解析的 product.category 精确匹配）
GPT_CATEGORIES: set[str] = {
    "plus 成品已接码直接登/包括 gpt free",
    "没接码,还有Team/长质保商品",
}

# keywords.json 的 image 字段 → 图片直链映射
PICS_URLS: dict[str, str] = {
    "pics/展开商品说明.png": "https://s41.ax1x.com/2026/06/24/pmtnnc6.png",
    "pics/meme1.jpg":       "https://s41.ax1x.com/2026/06/24/pmtnVhR.jpg",
    "pics/meme2.jpg":       "https://s41.ax1x.com/2026/06/24/pmtne91.jpg",
    "pics/meme3.jpg":       "https://s41.ax1x.com/2026/06/24/pmtnm1x.jpg",
}
