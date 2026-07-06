# CLAUDE.md

用中文回答和写文档。

## 项目简介

QQ 群机器人，监控 `pay.ldxp.cn/shop/manboup` 的商品库存变化，自动发新品/上架/补货通知，支持 @指令查询商品、关键词自动回复、彩虹屁等功能。

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
│   └── tasks.py             # APScheduler 定时扫描 + 通知调度
├── storage/
│   └── state.py             # state.json 快照管理（合并式），diff_states() 检测新品/上架/补货
├── keywords.json            # 关键词→回复 配置（支持 reply 单条或 replies 数组随机）
├── category_commands.json   # 分类指令→分类 id 映射
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
| `NOTIFY_COOLDOWN` | 同一商品通知冷却（秒），覆盖新品/上架/补货三种事件 | `900`（15 分钟） |

## 功能说明

### 1. 新品 / 上架 / 补货通知

- 每 `SCAN_INTERVAL` 秒爬一次店铺，用 `diff_states()` 对比上次快照，三种事件互斥、按优先级判定：
  1. **新品**：`goods_key` 之前从未出现过，现在在架且有货
  2. **上架**：`goods_key` 之前出现过但已下架（`listed=False`），现在重新在架且有货
  3. **补货**：`goods_key` 一直在架，只是缺货变有货
- 商城接口不会返回下架商品（下架即从列表消失），所以 `state.json` 采用**合并式**保存：本次扫描消失的商品不删除，只把 `listed` 置为 `False`，用于识别"上架"事件
- 通知冷却：`NOTIFY_COOLDOWN`（默认 15 分钟）按 `goods_key` 生效，同一商品无论触发新品/上架/补货哪一种，冷却期内只通知一次，防止反复上下架刷屏
- 通知屏蔽：`NOTIFY_EXCLUDE_CATEGORIES`（`config.py` 硬编码，按分类 `id` 匹配）中的分类不通知
- **静默时段**：00:00–09:00 继续扫描、更新快照、正常做冷却标记，只跳过发送通知；确保用户 @查询 始终拿到最新库存，也不会漏发静默时段检测到的事件
- **每日汇总**：静默时段检测到的新品/上架/补货事件不会被丢弃，而是缓冲进 `scheduler/tasks.py` 的 `_quiet_buffer`（按商品 id 去重，同一商品多次触发只保留最后一次事件类型），09:00（`QUIET_END`）由独立的 `daily_digest` cron 任务统一发一条汇总通知，然后清空缓冲区。`daily_digest` job 显式指定 `timezone=CST`，避免跟 `_in_quiet_hours()` 用的东八区时间不一致

### 2. 分类以 id 而非名称匹配

- 商城接口每个商品自带稳定的数字 `category.id`，改分类名不影响它
- `category_commands.json`、`NOTIFY_EXCLUDE_CATEGORIES` 都按 `category_id` 匹配，JSON 里保留的 `name` 仅作人读注释
- 若店铺后台新增/删除分类，需要重新跑一次爬虫查出新 `id` 并更新配置

### 3. 库存数 / 折扣价展示

- `shop/scraper.py` 从接口的 `extend.stock_count`（剩余库存）、`market_price`（划线原价，`0` 表示无折扣）、`description`（HTML 商品说明，用 `BeautifulSoup(...).get_text("\n", strip=True)` 清洗成纯文本）、`image`（商品图片直链）里采集这 4 个字段，存到 `Product` 上并持久化进 `state.json`
- `bot/formatter.py` 的 `_stock_label()` 在有库存数时展示「剩N件」；`_price_label()` 在 `market_price > price > 0` 时用删除线展示原价（`~~原价r~~ 现价r`），否则展示原有的单一价格；这两者用在分类列表、搜索结果、三种通知（新品/上架/补货）和每日汇总里

### 4. @机器人 指令

`on_group_message_create` 检测消息里是否含 `<@BOT_OPENID>`，是则走指令流程。

**引用回复过滤**：`message_reference.message_id` 非 None 说明是引用别人消息的回复，@bot 可能来自被引用的旧消息，此时忽略指令只做关键词匹配。

指令优先级（从高到低）：

1. **无内容 / 使用指南触发词**（`使用指南`、`指令`、`help`、`帮助`、`怎么用`）→ 发使用指南
2. **菜单词**（`清单`、`菜单`、`menu`、`商品清单`、`有什么`、`卖什么`）→ 全量分类菜单
3. **关键词自动回复**（`keywords.json`，见下节）→ 命中则直接回复，优先于分类指令（避免"邮箱失效"这类 FAQ 短语被"邮箱"分类指令截胡）
4. **分类指令**（`category_commands.json` 中的 key，如 `gpt`、`claude`、`接码`）→ 对应分类有货商品
5. **商品详情**（`详情`/`商品详情`/`详细信息` + 序号或商品名）→ 见下方"商品详情查询"一节
6. **关键词搜索**（商品标题搜索，与上面的"关键词自动回复"是两回事）→ 两步匹配：
   - Step 1：regex 剥离首尾询问词（`有没有`、`有货吗`、`能用吗` 等）后整体子串匹配
   - Step 2：无结果则 jieba 分词过滤停用词，AND 优先 → OR 兜底
   - 有结果 → 返回搜索结果；无结果 → 返回「暂时没找到X相关的有货商品～」（不显示使用指南）

**商品详情查询细节**：

- 触发词用**锚定正则**匹配（`^(?:商品详情|详细信息|详情)\s*(.*)$`），只在消息**开头**是这些词时才生效，避免跟 `keywords.json` 里"订单详情"这类 FAQ 关键词的"详情"子串冲突（关键词自动回复优先级更高，"订单详情"会先被 Step 3 命中，走不到这里）
- 参数是纯数字 → 按序号从`该用户在该群最近一次查看的列表`里取商品；参数是文字或为空 → 走标题搜索取第一个结果
- **列表缓存**：`bot/handlers.py` 的 `_last_shown`（key 为 `(group_openid, member_openid)`）只在**分类指令**和**关键词搜索**返回结果后写入，全量菜单（`format_product_menu`）不写入——因为菜单按分类分组、序号在每个分类内重新从 1 开始，全局序号会有歧义
- 返回内容：标题、价格（含折扣）、库存、商品说明（截断约 400 字）、链接；商品带图片时额外发一张图

### 5. 关键词自动回复

配置在 `keywords.json`，@bot 或不 @bot 发普通群消息命中关键词都会触发：

- `keywords` 数组：任一关键词命中即触发
- `reply`：单条固定回复
- `replies`：数组，随机选一条（用于彩虹屁等）
- `image`：附带图片的 key（映射到 `config.py` 的 `PICS_URLS`）

### 6. 消息发送

所有回复均为 Markdown 格式（`msg_type=2`）。图片先发文字再发媒体（`msg_type=7`）。

主动推送（新品/上架/补货）通过 `_broadcast()` 遍历 `GROUP_OPENIDS` 发送，不依赖 `msg_id`。

每条触发消息最多可发 5 条回复（`msg_seq` 计数器，按 `msg_id` 追踪）。

## 架构关键点

- **爬虫**：页面有 WAF，使用 Playwright 无头 Chromium 渲染。`SELECTORS` 字典在 `shop/scraper.py` 顶部，页面改版只改这里。`--debug` 保存 `debug_shop.html`。
- **库存快照**：`state.json` 保存上次扫描结果，合并式更新（下架商品保留记录、仅标记 `listed=False`，不删除），`diff_states()` 基于此做新品/上架/补货三态判定。
- **Bot 依赖注入**：`scheduler/tasks.py` 的 `_bot_client` 由 `main.py` 通过 `set_bot_client()` 注入，避免循环导入。
- **关键词缓存**：`keywords.json` 和 `category_commands.json` 首次访问后缓存在内存，重启生效。
- **jieba 预热**：`handlers.py` import 时调用 `jieba.initialize()`，避免首次查询阻塞事件循环。

## 首次配置步骤

1. 复制 `.env.example` 为 `.env`，填写 `BOT_APPID`、`BOT_SECRET`（在 [q.qq.com](https://q.qq.com) 申请）
2. 将机器人加入 QQ 群，从入群事件日志获取 `GROUP_OPENIDS` 和 `BOT_OPENID` 填入 `.env`
3. 运行 `uv run python -m shop.scraper --debug` 检查商品解析是否正确
4. 如选择器不匹配，打开 `debug_shop.html` 用 DevTools 找正确选择器，修改 `shop/scraper.py` 的 `SELECTORS`
5. 确认无误后运行 `uv run python main.py`
