# CLAUDE.md

用中文回答和写文档。

## 项目简介

QQ 群机器人，监控 `pay.ldxp.cn/shop/manboup` 的商品库存变化，自动发补货通知，支持指令查询商品清单和关键词自动回复。

## 常用命令

```bash
# 安装依赖（需要先安装 uv）
uv sync
uv run playwright install chromium

# 调试爬虫（保存页面HTML + 打印解析结果，用来校准选择器）
uv run python -m shop.scraper --debug

# 验证爬虫正常工作
uv run python -m shop.scraper

# 启动机器人
uv run python main.py
```

## 项目结构

```
qqbot/
├── main.py              # 启动入口
├── config.py            # 从 .env 读取所有配置
├── shop/
│   ├── models.py        # Product / Category 数据类
│   └── scraper.py       # Playwright 爬虫，SELECTORS 可调整
├── bot/
│   ├── handlers.py      # 消息事件（@指令 + 关键词），继承 botpy.Client
│   └── formatter.py     # 消息文本格式化
├── scheduler/
│   └── tasks.py         # APScheduler 定时扫描，set_bot_client() 注入依赖
├── storage/
│   └── state.py         # state.json 快照管理，diff_states() 检测补货
├── keywords.json        # 关键词→回复 配置，无需改代码直接编辑
└── pics/                # 回复附带的图片（meme2.jpg 等）
```

## 架构关键点

- **爬虫**：页面有 WAF，使用 Playwright 无头 Chromium 渲染。`shop/scraper.py` 顶部的 `SELECTORS` 字典集中管理 CSS 选择器，页面改版时只需修改这里。运行 `--debug` 会保存 `debug_shop.html` 供选择器排查。
- **库存对比**：`storage/state.py` 的 `diff_states(old, new)` 逻辑：`old` 中不存在或 `in_stock=false`，`new` 中 `in_stock=true` → 视为补货。
- **Bot 依赖注入**：`scheduler/tasks.py` 的 `_bot_client` 由 `main.py` 启动时通过 `set_bot_client()` 注入，避免循环导入。
- **关键词配置**：修改 `keywords.json` 即可增删关键词，首次加载后缓存在内存，重启生效。图片路径相对于项目根目录。

## 首次配置步骤

1. 复制 `.env.example` 为 `.env`，填写 `BOT_APPID`、`BOT_SECRET`（在 [q.qq.com](https://q.qq.com) 申请）
2. 将机器人加入 QQ 群，从入群事件日志获取 `GROUP_OPENID` 并填入 `.env`
3. 运行 `uv run python -m shop.scraper --debug` 检查商品解析是否正确
4. 如选择器不匹配，打开 `debug_shop.html` 用浏览器 DevTools 找到正确选择器，修改 `shop/scraper.py` 的 `SELECTORS`
5. 确认无误后运行 `main.py`
