import os
from dotenv import load_dotenv

load_dotenv()

BOT_APPID = os.getenv("BOT_APPID", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")
BOT_OPENID = os.getenv("BOT_OPENID", "")  # 机器人在群里的 member openid，用于过滤 @自己

# 支持多群：逗号分隔，如 "openid1,openid2"
GROUP_OPENIDS = [g.strip() for g in os.getenv("GROUP_OPENIDS", "").split(",") if g.strip()]

SHOP_URL = os.getenv("SHOP_URL", "https://wzyp.cn/shop/manboup")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
SANDBOX = os.getenv("SANDBOX", "false").lower() == "true"

STATE_FILE = "state.json"
KEYWORDS_FILE = "keywords.json"
CATEGORY_COMMANDS_FILE = "category_commands.json"

# 补货/上新/上架通知屏蔽的分类（按分类 id 匹配，不受改名影响）
NOTIFY_EXCLUDE_CATEGORIES: set[int] = set()

# 同一商品两次通知之间的最小间隔（秒），防止反复上架/补货刷屏，默认 15 分钟
# 覆盖新品/上架/补货三种事件，同一 goods_key 共用一份冷却
NOTIFY_COOLDOWN: int = int(os.getenv("NOTIFY_COOLDOWN", "900"))

# Twitter / X 新帖转发
TWITTER_ENABLED = os.getenv("TWITTER_ENABLED", "true").lower() == "true"
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME", "wawaup1024").lstrip("@")
TWITTER_DISPLAY_NAME = os.getenv("TWITTER_DISPLAY_NAME", "曼波小店资讯")
TWITTER_SCAN_INTERVAL = int(os.getenv("TWITTER_SCAN_INTERVAL", "900"))  # 默认 15 分钟
TWITTER_API_BASE = os.getenv("TWITTER_API_BASE", "https://api.fxtwitter.com")
TWITTER_HTTP_PROXY = os.getenv("TWITTER_HTTP_PROXY", "")  # 国内服务器访问 X 需要代理，如 http://127.0.0.1:7890
TWITTER_INCLUDE_RETWEETS = os.getenv("TWITTER_INCLUDE_RETWEETS", "true").lower() == "true"
TWITTER_INCLUDE_REPLIES = os.getenv("TWITTER_INCLUDE_REPLIES", "false").lower() == "true"  # 回复别人的帖；自己的串推始终转发

# keywords.json 的 image 字段 → 图片直链映射
PICS_URLS: dict[str, str] = {
    "pics/展开商品说明.png": "http://120.27.141.92/pics/expand-tip.png",
    "pics/meme1.jpg":       "http://120.27.141.92/pics/meme1.jpg",
    "pics/meme2.jpg":       "http://120.27.141.92/pics/meme2.jpg",
    "pics/meme3.jpg":       "http://120.27.141.92/pics/meme3.jpg",
}
