# Bot 配置种子（可进 git）

这些文件**只作初始导入模板**，运行时不要依赖仓库根目录的 `keywords.json` / `category_commands.json`
（那些路径已 gitignore，且生产真相源是 shop-core `app_blobs`）。

## 导入到 shop-core

```bash
cd ../shop-core/backend
uv run alembic upgrade head   # 含 app_blobs 表

# 优先：从本目录 seed（无需在 qqbot 根生成运行时 JSON）
uv run python scripts/seed_bot_blobs.py \
  --keywords ../../qqbot/config.example/keywords.json \
  --category-commands ../../qqbot/config.example/category_commands.json

# 若本机仍有历史 state.json 等，可整目录导入：
# uv run python scripts/seed_bot_blobs.py --qqbot-root ../../qqbot
```

导入后 bot 在配置了 `SHOP_CORE_BASE_URL` + `SHOP_CORE_INTERNAL_TOKEN` 时会：

- 读关键词 / 分类指令：优先 DB blob
- 写库存快照 / 内容指纹 / 审核会话：优先 DB blob

本地 JSON 仅作 core 不可用时的回退缓存，可随时删除。
`pics/` 为关键词附图本地副本（`config.PICS_URLS` 映射到 CDN/服务器直链），**不进数据库**。