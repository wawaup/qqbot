# Bot 配置种子（可进 git）

这些文件**只作初始导入模板**，运行时不要依赖仓库里的 `keywords.json` / `category_commands.json`。

## 导入到 shop-core

```bash
cd ../shop-core/backend
uv run alembic upgrade head   # 含 app_blobs 表
uv run python scripts/seed_bot_blobs.py --qqbot-root ../../qqbot
# 或先把 example 拷到 qqbot 根再 seed：
# cp config.example/keywords.json ../keywords.json
# cp config.example/category_commands.json ../category_commands.json
```

导入后 bot 在配置了 `SHOP_CORE_BASE_URL` + `SHOP_CORE_INTERNAL_TOKEN` 时会：

- 读关键词 / 分类指令：优先 DB blob
- 写库存快照 / 内容指纹 / 审核会话：优先 DB blob

本地 JSON 仅作 core 不可用时的回退，且已被 `.gitignore` 忽略。
