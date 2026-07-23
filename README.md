# qqbot

QQ 群机器人：库存通知、关键词回复、店主 C2C 上新审核。  
**推荐库存与配置真相源：shop-core**（`INVENTORY_SOURCE=shop-core`）。

## 启动前依赖

1. **shop-core** 已运行（API + worker），见 monorepo / `shop-core/README.md`  
   Docker 启动时会 **自动 bootstrap** bot 关键词等到 `app_blobs`，服务器不必再跑 `seed_bot_blobs.py`。
2. 本机若不用 Docker、且空库：`cd ../shop-core/backend && uv run python -m shop_core.bootstrap`

## 本地启动

```bash
cd qqbot
cp .env.example .env
# 填写 BOT_APPID / BOT_SECRET / GROUP_OPENIDS / BOT_OPENID
# 推荐对接 core：
#   INVENTORY_SOURCE=shop-core
#   SHOP_CORE_BASE_URL=http://127.0.0.1:18080
#   SHOP_CORE_INTERNAL_TOKEN=<与 shop-core 相同>
#   OWNER_USER_OPENIDS=<店主 user_openid，C2C 审核必填>
# 私聊 Grok 审核还需 GROK_* 或 monorepo 根 .env.local 的 ANTHROPIC_*

uv sync
# 仅当 INVENTORY_SOURCE=ldxp 走本地爬虫时需要：
# uv run playwright install chromium

uv run python main.py
```

可选自带 status HTTP（默认开，可把请求代理到 core）：

- `STATUS_API_ENABLED=true`
- `STATUS_API_PORT=8080`
- `STATUS_API_PROXY_CORE=true`（有 `SHOP_CORE_BASE_URL` 时把 status 转到 core）

## 配置与数据

| 内容 | 生产位置 | 本地 |
|---|---|---|
| 关键词 / 分类指令 | core `app_blobs` | `config.example/*` 仅作 seed；根目录 JSON 已 ignore |
| 库存快照 / 内容指纹 / 审核会话 | core `app_blobs` | 失败时回退本地文件（勿提交） |
| 关键词附图 | `PICS_URLS` 远程直链 | `pics/` 可选本地副本（gitignore） |

不要把 `state.json`、`keywords.json`、`*.har` 提交进 git。

## 常用运维

```bash
# 本地爬虫调试（仅 ldxp 源）
uv run python -m shop.scraper --debug

# 测试
uv run pytest
```

服务器 systemd 示例见 [`deploy.md`](deploy.md)（部署时同样配置 `SHOP_CORE_*`，不要只靠本机 JSON）。

功能细节、指令表与审核话术见 [`CLAUDE.md`](CLAUDE.md) / [`群机器人使用说明.md`](群机器人使用说明.md)。
