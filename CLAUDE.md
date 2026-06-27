# CLAUDE.md

用中文回答和写文档。

## 项目简介

QQ 群机器人，监控 `pay.ldxp.cn/shop/manboup` 的商品库存变化，自动发补货/新品通知，支持 @指令查询商品、关键词自动回复、彩虹屁等功能。

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
├── main.py                  # 启动入口
├── config.py                # 从 .env 读取所有配置
├── shop/
│   ├── models.py            # Product / Category 数据类
│   └── scraper.py           # Playwright 爬虫，SELECTORS 可调整
├── bot/
│   ├── handlers.py          # 消息事件处理，继承 botpy.Client
│   └── formatter.py         # 消息文本格式化
├── scheduler/
│   └── tasks.py             # APScheduler 定时扫描 + GPT 批量通知
├── storage/
│   └── state.py             # state.json 快照管理，diff_states() 检测补货
├── keywords.json            # 关键词→回复 配置（支持 reply 单条或 replies 数组随机）
├── category_commands.json   # 分类指令→分类名 映射
└── pics/                    # 回复附带的图片
```

## 环境变量（.env）

| 变量 | 说明 | 默认值 |
|---|---|---|
| `BOT_APPID` | QQ 开放平台 AppID | 必填 |
| `BOT_SECRET` | QQ 开放平台 Secret | 必填 |
| `GROUP_OPENIDS` | 目标群 openid，逗号分隔多群 | 必填 |
| `BOT_OPENID` | 机器人自身的 member openid（过滤自@） | 必填 |
| `SHOP_URL` | 店铺地址 | `https://pay.ldxp.cn/shop/manboup` |
| `SCAN_INTERVAL` | 爬虫扫描间隔（秒） | `60` |
| `SANDBOX` | 沙盒模式，不真实发消息 | `false` |
| `GPT_INSTANT_PRICE_THRESHOLD` | GPT 分类立即通知的价格上限（元） | `20` |
| `GPT_BATCH_INTERVAL` | GPT 分类批量通知间隔（秒） | `1800` |

## 功能说明

### 1. 补货 / 新品通知

- 每 `SCAN_INTERVAL` 秒爬一次店铺，用 `diff_states()` 对比上次快照
- **补货**：之前存在但缺货，现在有货
- **新品**：快照中不存在，现在有货
- 通知屏蔽：`NOTIFY_EXCLUDE_CATEGORIES`（`config.py` 硬编码）中的分类不通知
- **静默时段**：00:00–09:00 不扫描，9 点后首次扫描捕获整夜变化

#### GPT 分类分级通知

GPT 分类（`plus 成品已接码直接登/包括 gpt free`、`没接码,还有Team/长质保商品`）按价格分两条通道：

- 价格 < `GPT_INSTANT_PRICE_THRESHOLD`（默认 20 元）→ 立即通知
- 价格 ≥ 阈值 → 缓冲，每 `GPT_BATCH_INTERVAL`（默认 30 分钟）批量发一次
- 批量发送前用最新快照二次验证：下架或已售罄的过滤掉，不通知

### 2. @机器人 指令

`on_group_message_create` 检测消息里是否含 `<@BOT_OPENID>`，是则走指令流程。

**引用回复过滤**：`message_reference.message_id` 非 None 说明是引用别人消息的回复，@bot 可能来自被引用的旧消息，此时忽略指令只做关键词匹配。

指令优先级（从高到低）：

1. **无内容 / 使用指南触发词**（`使用指南`、`指令`、`help`、`帮助`、`怎么用`）→ 发使用指南
2. **菜单词**（`清单`、`菜单`、`menu`、`商品清单`、`有什么`、`卖什么`）→ 全量分类菜单
3. **分类指令**（`category_commands.json` 中的 key，如 `gpt`、`claude`、`接码`）→ 对应分类有货商品
4. **关键词搜索** → 两步匹配：
   - Step 1：regex 剥离首尾询问词（`有没有`、`有货吗`、`能用吗` 等）后整体子串匹配
   - Step 2：无结果则 jieba 分词过滤停用词，AND 优先 → OR 兜底
   - 有结果 → 返回搜索结果；无结果 → 返回「暂时没找到X相关的有货商品～」（不显示使用指南）

### 3. 关键词自动回复

无 @bot 的普通群消息触发，配置在 `keywords.json`：

- `keywords` 数组：任一关键词命中即触发
- `reply`：单条固定回复
- `replies`：数组，随机选一条（用于彩虹屁等）
- `image`：附带图片的 key（映射到 `config.py` 的 `PICS_URLS`）

### 4. 消息发送

所有回复均为 Markdown 格式（`msg_type=2`）。图片先发文字再发媒体（`msg_type=7`）。

主动推送（补货/新品）通过 `_broadcast()` 遍历 `GROUP_OPENIDS` 发送，不依赖 `msg_id`。

每条触发消息最多可发 5 条回复（`msg_seq` 计数器，按 `msg_id` 追踪）。

## 架构关键点

- **爬虫**：页面有 WAF，使用 Playwright 无头 Chromium 渲染。`SELECTORS` 字典在 `shop/scraper.py` 顶部，页面改版只改这里。`--debug` 保存 `debug_shop.html`。
- **库存快照**：`state.json` 保存上次扫描结果，`diff_states()` 做增量对比。
- **Bot 依赖注入**：`scheduler/tasks.py` 的 `_bot_client` 由 `main.py` 通过 `set_bot_client()` 注入，避免循环导入。
- **关键词缓存**：`keywords.json` 和 `category_commands.json` 首次访问后缓存在内存，重启生效。
- **jieba 预热**：`handlers.py` import 时调用 `jieba.initialize()`，避免首次查询阻塞事件循环。

## 首次配置步骤

1. 复制 `.env.example` 为 `.env`，填写 `BOT_APPID`、`BOT_SECRET`（在 [q.qq.com](https://q.qq.com) 申请）
2. 将机器人加入 QQ 群，从入群事件日志获取 `GROUP_OPENIDS` 和 `BOT_OPENID` 填入 `.env`
3. 运行 `uv run python -m shop.scraper --debug` 检查商品解析是否正确
4. 如选择器不匹配，打开 `debug_shop.html` 用 DevTools 找正确选择器，修改 `shop/scraper.py` 的 `SELECTORS`
5. 确认无误后运行 `uv run python main.py`
