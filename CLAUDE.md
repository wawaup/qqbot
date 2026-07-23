# CLAUDE.md

用中文回答和写文档。

## 项目简介

QQ 群机器人，监控 `pay.ldxp.cn/shop/manboup` 的商品库存变化，自动发新品/上架/补货通知，支持 @指令查询商品、关键词自动回复、彩虹屁等功能。

## 常用命令

日常启动见 [`README.md`](README.md)。对接 shop-core 时不必装 Playwright。

```bash
# 安装依赖（需要先安装 uv）
uv sync
# 仅 INVENTORY_SOURCE=ldxp 时需要：
# uv run playwright install chromium

# 调试爬虫（仅 ldxp 源）
uv run python -m shop.scraper --debug
uv run python -m shop.scraper

# 启动机器人（先保证 shop-core :18080 可用）
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
| `OWNER_USER_OPENIDS` | 允许私聊调用 Grok 的 user_openid，逗号分隔 | 空=仅回显 openid，不调 LLM |
| `GROK_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | Grok 网关密钥（也可从 monorepo 根 `.env.local` 读） | 私聊助手需要 |
| `GROK_API_BASE_URL` / `ANTHROPIC_BASE_URL` | Anthropic 兼容网关地址 | 私聊助手需要 |
| `GROK_MODEL` | 模型名，默认 `grok-4.5` | 可选 |
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
- **上架不做实时通知**：`relisted_products` 从不走即时推送。00:00–09:00 静默时段检测到的上架事件会缓冲进每日汇总；静默时段之外检测到的上架事件直接忽略，不通知也不缓冲
- **静默时段**：00:00–09:00 继续扫描、更新快照、正常做冷却标记，只跳过发送新品/补货的实时通知；确保用户 @查询 始终拿到最新库存，也不会漏发静默时段检测到的事件
- **每日汇总**：静默时段检测到的新品/上架/补货事件不会被丢弃，而是缓冲进 `scheduler/tasks.py` 的 `_quiet_buffer`（按商品 id 去重，同一商品多次触发只保留最后一次事件类型），09:00（`QUIET_END`）由独立的 `daily_digest` cron 任务统一发一条汇总通知。发送前 `_revalidate_buffered_events()` 会用最新的 `state.json` 快照重新校验每个缓冲商品：已被下架（`listed=False`）、缺货或已从快照里消失的商品会被剔除，同时用快照里的最新价格/标题等字段刷新，避免汇总里出现失效商品或过期信息；校验完成后清空缓冲区。`daily_digest` job 显式指定 `timezone=CST`，避免跟 `_in_quiet_hours()` 用的东八区时间不一致

### 2. 分类以 id 而非名称匹配

- 商城接口每个商品自带稳定的数字 `category.id`，改分类名不影响它
- `category_commands.json`、`NOTIFY_EXCLUDE_CATEGORIES` 都按 `category_id` 匹配，JSON 里保留的 `name` 仅作人读注释
- 若店铺后台新增/删除分类，需要重新跑一次爬虫查出新 `id` 并更新配置

### 3. @机器人 指令

`on_group_message_create`（以及 `on_group_at_message_create`）检测消息里是否含 `<@BOT_OPENID>`，是则走指令流程。

**引用回复过滤**：QQ 下发的原始 payload 里带一个 `message_type` 字段——普通消息是 `0`，用户引用/回复一条消息时是 `103`（`bot/handlers.py` 的 `REFERENCE_REPLY_MESSAGE_TYPE`）。但 botpy 的 `GroupMessage` 类没有解析这个字段，所以 `_patch_group_message_parser()` 把 `group_message_create` 和 `group_at_message_create` 两个事件的 parser 都换成了会额外带上 `message_type` 的 `_GroupMessageWithType`。只要 `message_type == 103` 就判定为引用/回复消息（`is_reference_reply`），**指令路由和关键词自动回复都不触发**，直接忽略；只有非引用的普通消息才会走关键词自动回复。（历史上曾用"`<@botid>` 在 `content` 里出现次数是否大于 1"判断，但引用/回复消息的 `content` 并不会带出被引用消息的原文或 @ 标签，这个次数一直是 1，跟直接 @ 完全没区别，因此这个判断从未真正生效过。）

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
- 返回内容：标题、价格、商品说明（截断约 400 字）、链接

### 4. 关键词自动回复

配置在 `keywords.json`，@bot 或不 @bot 发普通群消息命中关键词都会触发：

- `keywords` 数组：任一关键词命中即触发
- `reply`：单条固定回复
- `replies`：数组，随机选一条（用于彩虹屁等）
- `image`：附带图片的 key（映射到 `config.py` 的 `PICS_URLS`）

### 5. 消息发送

所有回复均为 Markdown 格式（`msg_type=2`）。图片先发文字再发媒体（`msg_type=7`）。

主动推送（新品/上架/补货）通过 `_broadcast()` 遍历 `GROUP_OPENIDS` 发送，不依赖 `msg_id`。

每条触发消息最多可发 5 条回复（`msg_seq` 计数器，按 `msg_id` 追踪）。

### 6. C2C 私聊（店主助手）

- 事件：`on_c2c_message_create`（`public_messages` intent 已覆盖）
- **白名单**：只有 `OWNER_USER_OPENIDS` 里的 `user_openid` 才会调用 Grok；其他人私聊只会收到自己的 openid 提示，**绝不消耗 LLM token**
- 获取 openid：用自己的 QQ 私聊机器人任意内容 → 回复/日志里的 `user_openid` → 写入 `.env` 的 `OWNER_USER_OPENIDS` 后重启
- Grok：`bot/llm.py` 走 Anthropic Messages 兼容接口（`/v1/messages`），密钥优先 `GROK_*`，否则读 monorepo 根 `.env.local` 的 `ANTHROPIC_*`
- 授权用户发 `openid` / `whoami` 可复查自己的 openid 与配置状态

### 7. 上新审核 Skill（C2C）

- 实现：`bot/review.py` + `storage/review_sessions.py` + `bot/warranty.py`
- **两张清单**（互不混排，序号各自从 1 起；最近一次清单用于 `1` / `审核 1`）：
  1. `待审清单` / `审核`：资料变更再审（shop-core review-queue + 已挂 surface 的 catalog 待审）。**不含** hidden 首次上架；文末提示「另有 N 个未上架 → 发 `上架审核`」
  2. `上架审核` / `待上架`：导航站仍为 **hidden / 未公开** 的商品，走首次上架
- 其它指令：`审核 <product_id>` / `审核测试` / `审核状态` / `审核帮助`
- 审核中回复语义：
  - `可以` / `OK` → **提交草稿并完成**（移出待审）
  - `不用调整` / `省略` → **完成且不改**，移出待审（不写 overrides）
  - `暂不改` → 退出本轮，**仍留在待审清单**（标记 ⏸），下次再改
  - 其它长句 → 修改意见，Grok 重拟后再审
  - `取消` → 退出本轮，不标记完成，仍在清单
- 私聊稿同时展示**原标题 vs 建议标题**；详情 HTML 按来源同步，不经模型改写
- 确认后优先 `shop-core` publish；失败再回退 navigator overrides（若 `REVIEW_APPLY_ENABLED`）
- **成品号质保特例**（`bot/warranty.py` + `content_state.diff_snapshots`）：
  - 标题仅「上架/补货时间」话术变化、质保天数不变 → **不通知**
  - 质保天数缩短（如 30→25）→ **强制通知并置顶**
- HTTP 测试：`POST /api/v1/review/test`、`POST /api/v1/review/start`、`GET /api/v1/review/status`

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
