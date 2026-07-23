# qqbot 服务器部署（Ubuntu + systemd）

前提：**shop-core** 已在同机或内网可达地址运行（API + worker + Postgres）。  
bot 默认 `INVENTORY_SOURCE=shop-core`，不要只拷贝 JSON 当库存源。

## 1. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

## 2. 代码与依赖

```bash
# git clone 或 scp 到例如 /root/qqbot
cd /root/qqbot
uv sync
# 仅当 INVENTORY_SOURCE=ldxp 时需要 Playwright：
# uv run playwright install chromium
```

## 3. 环境变量 `.env`

至少：

```dotenv
BOT_APPID=...
BOT_SECRET=...
GROUP_OPENIDS=...
BOT_OPENID=...
OWNER_USER_OPENIDS=...   # 店主 C2C 审核

INVENTORY_SOURCE=shop-core
SHOP_CORE_BASE_URL=http://127.0.0.1:18080
SHOP_CORE_INTERNAL_TOKEN=与 shop-core 相同的 internal token

# 可选：本机仍暴露兼容 status（可代理到 core）
STATUS_API_ENABLED=true
STATUS_API_HOST=0.0.0.0
STATUS_API_PORT=8080
STATUS_API_PROXY_CORE=true
STATUS_API_ALLOWED_ORIGIN=https://你的导航站域名

CONTENT_CHECK_INTERVAL=600
CONTENT_CHANGE_GROUP_OPENIDS=
CONTENT_CHANGE_USER_OPENIDS=

# 私聊审核 Grok
# GROK_API_KEY=
# GROK_API_BASE_URL=
# GROK_MODEL=grok-4.5
```

关键词等配置用 core seed，不必在服务器放 `keywords.json`：

```bash
cd /path/to/shop-core/backend
uv run python scripts/seed_bot_blobs.py \
  --keywords /path/to/qqbot/config.example/keywords.json \
  --category-commands /path/to/qqbot/config.example/category_commands.json
```

`GROUP_OPENIDS` 只用于补货/新品等群通知；留空则不发这类主动通知。  
私信目标必须是开放平台 `user_openid`，不能填普通 QQ 号。

## 4. systemd

```bash
uv run which python   # 记下 venv python 路径
sudo nano /etc/systemd/system/qqbot.service
```

```ini
[Unit]
Description=QQ Shop Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/qqbot
ExecStart=/root/qqbot/.venv/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable qqbot
sudo systemctl start qqbot
sudo systemctl status qqbot
journalctl -u qqbot -f
```

## 5. 与导航站

navigator 应直接请求 **shop-core** 的 `/api/catalog/bundle` 与 status，而不是依赖 bot 本机 JSON。  
若仍用 bot 的 8080 作兼容 status，请用 Nginx/Caddy 配 HTTPS 反代，避免 HTTPS 页请求裸 HTTP。

更新代码后：

```bash
sudo systemctl restart qqbot
```
