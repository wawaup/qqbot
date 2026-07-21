"""
消息事件处理：@机器人指令 + 关键词自动回复。
"""
import json
import logging
import random
import re
import traceback
from pathlib import Path

import jieba
import botpy
from botpy.manage import GroupManageEvent
from botpy.message import GroupMessage
from botpy.types import message as msg_types

jieba.initialize()  # 启动时预加载词典，避免首次查询阻塞事件循环

from bot.formatter import (
    filter_category_products,
    format_category_products,
    format_daily_digest,
    format_product_detail,
    format_product_menu,
    format_search_results,
)
from config import BOT_OPENID, CATEGORY_COMMANDS_FILE, KEYWORDS_FILE, PICS_URLS
from storage.state import load_state

logger = logging.getLogger(__name__)

# 引用/回复消息的 message_type，QQ 原始 payload 里才有，botpy 的 GroupMessage 没有解析这个字段
REFERENCE_REPLY_MESSAGE_TYPE = 103


class _GroupMessageWithType(GroupMessage):
    """在 GroupMessage 基础上带上原始 message_type，用来判断是否为引用/回复消息。"""

    __slots__ = ("message_type",)

    def __init__(self, api, event_id, data):
        super().__init__(api, event_id, data)
        self.message_type = data.get("message_type", 0)


MENU_KEYWORDS = {"menu", "清单", "菜单", "商品清单", "有什么", "卖什么"}

# 直接触发使用指南的词（@bot 后面只有这些词，或什么都没跟）
HELP_TRIGGERS = {"使用指南", "指令", "help", "帮助", "怎么用"}

# 搜索时剥离的末尾/开头询问词
_QUERY_STRIP = re.compile(
    r"(有没有|有货吗|有吗|有么|在吗|卖吗|能买吗|能用吗|还有|怎么样|多少钱|什么价|价格|啥价|咋样|行吗|好用吗)\s*$"
    r"|^\s*(有没有|还有|求推荐|想买个|买个|想买)\s*"
)

# 详情指令：详情/商品详情/详细信息 + 序号 或 商品名，锚定开头避免跟"订单详情"等 FAQ 关键词冲突
_DETAIL_RE = re.compile(r"^(?:商品详情|详细信息|详情)\s*(.*)$")

# jieba 分词后过滤掉的虚词/查询词
_STOP_TOKENS = frozenset({
    "有没有", "有货吗", "有吗", "有么", "在吗", "卖吗", "能买吗",
    "有货", "缺货", "求推荐", "推荐", "怎么样", "如何", "哪里",
    "有", "吗", "么", "的", "了", "呢", "啊", "哦", "嗯", "哈",
    "是", "也", "都", "就", "还", "又", "再", "最", "很", "真",
    "这", "那", "这个", "那个", "什么", "哪个", "哪些", "多少",
    "想买", "想买个", "买个", "找个", "找下", "帮我找", "帮找", "帮我", "帮",
    "可以", "买", "找", "看", "要", "想", "用", "上", "能用", "咋样",
    "多少钱", "什么价", "价格", "啥价",
    "号", "款", "个", "种", "类", "些",
})

HELP_TEXT = (
    "# 🤖 曼波导购bot 使用指南\n\n"
    "👉 新手不知道怎么用成品号？先看这个：[点击查看教程](https://www.xtpu.asia/#/)\n\n"
    "## 1️⃣ 按分类查看有货商品\n"
    "@我 + 下面任意一个词：\n"
    "`推荐` `gpt正价` `正价冲` `gpt` `接码` `claude` `gemini` `grok` `其他` `苹果id` `邮箱服务`\n\n"
    "想看全部分类？@我 + `清单` / `菜单` / `menu`\n\n"
    "## 2️⃣ 搜索想要的商品\n"
    "@我 + 想找的东西，随便说都行，比如：\n"
    "`@我 plus`　`@我 有没有codex`　`@我 想买个网页号`\n\n"
    "## 3️⃣ 查看商品详情\n"
    "先按分类或搜索看到列表，再 @我 + `详情 序号`（如 `详情 2`），或直接 @我 + `详情 商品名`\n\n"
    "## 4️⃣ 常见问题一问就答\n"
    "不管有没有 @我，发下面这些都会自动回复：\n"
    "- 💬 店铺链接、在哪买\n"
    "- 💬 质保说明\n"
    "- 💬 订单查询、订单详情、订单卡密\n"
    "- 💬 售后服务、用不了怎么办\n"
    "- 💬 怎么登录、反代教程、邮箱接码怎么弄\n"
    "- 💬 2FA登录、密钥登录、邮箱风控/防邮箱失效\n\n"
    "---\n"
    "⚠️ 00:00-09:00 是免打扰时段，补货/新品通知暂停推送\n"
    "这段时间想看有货商品，@我 发分类指令依然实时有效～"
)

_keywords_cache: list[dict] | None = None
_category_commands_cache: dict[str, list[str]] | None = None


def _load_keywords() -> list[dict]:
    global _keywords_cache
    if _keywords_cache is None:
        path = Path(KEYWORDS_FILE)
        _keywords_cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return _keywords_cache


def _load_category_commands() -> dict[str, list[dict]]:
    global _category_commands_cache
    if _category_commands_cache is None:
        path = Path(CATEGORY_COMMANDS_FILE)
        _category_commands_cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _category_commands_cache


def _match_keyword(text: str) -> dict | None:
    text_lower = text.lower()
    for rule in _load_keywords():
        for kw in rule.get("keywords", []):
            if kw.lower() in text_lower:
                return rule
    return None


def _match_category_command(text: str) -> tuple[str, list[int]] | None:
    text_lower = text.lower()
    for cmd, categories in _load_category_commands().items():
        if cmd.lower() in text_lower:
            return cmd, [c["id"] for c in categories]
    return None


def _extract_search_term(text: str) -> str:
    """去掉首尾询问词，返回核心搜索关键词（用于整体子串匹配，统一小写）。"""
    return _QUERY_STRIP.sub("", text).strip().lower()


def _extract_search_tokens(text: str) -> list[str]:
    """jieba 分词后过滤停用词和单个汉字，返回有意义的词列表（用于多词搜索）。"""
    result = []
    for t in jieba.lcut(text):
        t = t.strip()
        if not t or t in _STOP_TOKENS:
            continue
        # 单个汉字太泛，丢弃；单个英文/数字保留（如 "4", "o"）
        if len(t) == 1 and '一' <= t <= '鿿':
            continue
        result.append(t.lower())  # 统一小写，匹配时不区分大小写
    return result


def _search_by_title(products: dict, query: str) -> list:
    """在有货商品标题中做大小写不敏感的子串搜索。"""
    q = query.lower()
    return [p for p in products.values() if p.in_stock and q in p.title.lower()]


def _search_by_tokens(products: dict, tokens: list[str]) -> list:
    """按分词结果搜索：优先 AND（所有词都命中），无结果则 OR（任一词命中）。"""
    if not tokens:
        return []
    in_stock = [p for p in products.values() if p.in_stock]
    tl = [t.lower() for t in tokens]
    results = [p for p in in_stock if all(t in p.title.lower() for t in tl)]
    if results:
        return results
    seen: set[str] = set()
    or_results: list = []
    for p in in_stock:
        if p.id not in seen and any(t in p.title.lower() for t in tl):
            or_results.append(p)
            seen.add(p.id)
    return or_results


# 每个用户最近一次查看的商品列表（分类指令/搜索结果），供"详情+序号"指令定位
# key: (group_openid, member_openid)
_last_shown: dict[tuple[str, str], list] = {}

# 每条触发消息最多回复5条，用 msg_id 追踪当前 seq
_seq: dict[str, int] = {}


def _next_seq(msg_id: str) -> int:
    _seq[msg_id] = _seq.get(msg_id, 0) + 1
    return _seq[msg_id]


async def _reply_markdown(message: GroupMessage, text: str) -> None:
    await message._api.post_group_message(
        group_openid=message.group_openid,
        msg_type=2,
        msg_id=message.id,
        msg_seq=_next_seq(message.id),
        markdown=msg_types.MarkdownPayload(content=text),
    )


async def _send_image_only(message: GroupMessage, image_url: str) -> None:
    media = await message._api.post_group_file(
        group_openid=message.group_openid,
        file_type=1,
        url=image_url,
    )
    await message._api.post_group_message(
        group_openid=message.group_openid,
        msg_type=7,
        msg_id=message.id,
        msg_seq=_next_seq(message.id),
        media=media,
    )


async def _reply_image(message: GroupMessage, text: str, image_url: str) -> None:
    try:
        # 先发文字
        if text:
            await _reply_markdown(message, text)
        # 再发图片
        await _send_image_only(message, image_url)
    except Exception as e:
        logger.warning(f"图片发送失败: {e}")


class BotHandlers(botpy.Client):

    def ws_dispatch(self, event: str, *args, **kwargs):
        """覆盖默认的 ws_dispatch，把所有事件都打到 INFO 日志。"""
        logger.info(f"[事件] {event}")
        super().ws_dispatch(event, *args, **kwargs)

    async def on_error(self, event_name: str, *args, **kwargs):
        """覆盖默认的 on_error，把 handler 里的异常完整打印出来。"""
        logger.error(f"[{event_name}] 未捕获异常:\n{traceback.format_exc()}")

    async def on_ready(self):
        """首次连接成功，注册 group_message_create parser 补丁。"""
        self._patch_group_message_parser()

    async def on_resumed(self):
        """Session timeout (4009) 后 Resume 重连成功，防御性地重新注册补丁。"""
        logger.info("Resume 重连成功，重新注册 parser 补丁")
        self._patch_group_message_parser()

    def _patch_group_message_parser(self):
        """botpy 默认的 GroupMessage 不解析 message_type 字段，而这个字段（103=引用/回复消息）
        是唯一能区分"引用回复"和"直接发消息"的信号，所以两个 group 消息 parser 都要重新注册，
        换成会带上 message_type 的 _GroupMessageWithType。"""
        state = self._connection.state

        def parse_group_message_create(payload):
            msg = _GroupMessageWithType(state.api, payload.get("id"), payload.get("d", {}))
            state._dispatch("group_message_create", msg)

        state.parsers["group_message_create"] = parse_group_message_create
        logger.info("group_message_create 事件已注册")

        def parse_group_at_message_create(payload):
            msg = _GroupMessageWithType(state.api, payload.get("id"), payload.get("d", {}))
            state._dispatch("group_at_message_create", msg)

        state.parsers["group_at_message_create"] = parse_group_at_message_create
        logger.info("group_at_message_create 事件已注册")

    async def on_group_add_robot(self, event: GroupManageEvent):
        logger.info(f"[群管理] 机器人加入群 group_openid={event.group_openid}")

    async def on_group_msg_receive(self, event: GroupManageEvent):
        logger.info(f"[群管理] 群开启主动消息 group_openid={event.group_openid}")

    async def on_group_at_message_create(self, message: GroupMessage):
        """有人 @机器人 时触发。"""
        content = message.content or ""
        is_reference_reply = getattr(message, "message_type", 0) == REFERENCE_REPLY_MESSAGE_TYPE
        logger.info(
            f"[AT消息] group_openid={message.group_openid} "
            f"id={message.id} content={content!r} is_ref={is_reference_reply}"
        )
        try:
            if is_reference_reply:
                # 引用/回复消息带出的 @bot 不是这次主动发起的指令，忽略
                logger.info("[AT消息] 引用/回复消息，忽略，不触发任何规则")
                return
            clean = re.sub(r"<@[^>]+>", "", content).strip()
            await self._handle_at_command(message, clean)
        except Exception:
            logger.error(f"[AT消息] 处理异常:\n{traceback.format_exc()}")

    async def on_group_message_create(self, message: GroupMessage):
        """群内消息：带 <@!> 的路由到 AT 处理，否则做关键词匹配。"""
        try:
            content = (message.content or "").strip()
            # QQ 群里 @机器人 实际以 GROUP_MESSAGE_CREATE 下发，内容带 <@botid>
            # 只响应 @自己，忽略 @其他人的消息
            bot_tag = f"<@{BOT_OPENID}>" if BOT_OPENID else None
            is_at_bot = (bot_tag and bot_tag in content) or (not BOT_OPENID and bool(re.search(r"<@[^>]+>", content)))
            is_reference_reply = getattr(message, "message_type", 0) == REFERENCE_REPLY_MESSAGE_TYPE
            logger.info(
                f"[群消息] group_openid={message.group_openid} "
                f"content={content!r} is_at={is_at_bot} is_ref={is_reference_reply}"
            )
            if is_reference_reply:
                # 引用/回复消息不是这次主动发起的，指令和关键词匹配都不触发
                logger.info("[群消息] 引用/回复消息，忽略，不触发任何规则")
            elif is_at_bot:
                clean = re.sub(r"<@[^>]+>", "", content).strip()
                await self._handle_at_command(message, clean)
            else:
                rule = _match_keyword(content)
                if rule:
                    await self._send_keyword_reply(message, rule)
        except Exception:
            logger.error(f"[群消息] 处理异常:\n{traceback.format_exc()}")

    async def _handle_at_command(self, message: GroupMessage, clean: str) -> None:
        """处理 @bot 后的指令文本（已剥离 @tag）。"""
        # 空内容 或 明确要帮助 → 使用指南
        if not clean or clean in HELP_TRIGGERS:
            await _reply_markdown(message, HELP_TEXT)
            return

        # 全量菜单
        if any(kw in clean for kw in MENU_KEYWORDS):
            await self._send_menu(message)
            return

        # 关键词自动回复（店铺链接/质保/售后等 FAQ），@我 时也生效
        kw_rule = _match_keyword(clean)
        if kw_rule:
            await self._send_keyword_reply(message, kw_rule)
            return

        # 分类指令
        cat_match = _match_category_command(clean)
        if cat_match:
            cmd, categories = cat_match
            await self._send_category(message, cmd, categories)
            return

        # 商品详情：详情+序号（配合上一次列表）或 详情+商品名（配合搜索）
        detail_match = _DETAIL_RE.match(clean)
        if detail_match:
            await self._handle_detail_command(message, detail_match.group(1).strip())
            return

        # 关键词搜索：先整体匹配，再 jieba 分词多词匹配
        products = self._state_to_products()
        term = _extract_search_term(clean)
        results = _search_by_title(products, term) if term else []
        tokens = None
        if not results:
            tokens = _extract_search_tokens(clean)
            results = _search_by_tokens(products, tokens)

        # 显示词：token 兜底成功时用 token 拼接，避免显示"帮我找plus号"这类原始脏词
        if tokens and results:
            display = " ".join(tokens)
        else:
            display = term or clean
        if results:
            await self._send_search_results(message, display, results)
        else:
            await _reply_markdown(message, f"暂时没找到「{display}」相关的有货商品～")

    def _state_to_products(self) -> dict:
        from shop.models import Product
        return {
            pid: Product(
                id=pid,
                title=d["title"],
                url=d["url"],
                category=d["category"],
                category_id=d.get("category_id"),
                in_stock=d["in_stock"],
                price=str(d.get("price", "")),
                description=d.get("description", ""),
            )
            for pid, d in load_state().items()
            if d.get("listed", True)
        }

    async def _send_menu(self, message: GroupMessage):
        await _reply_markdown(message, format_product_menu(self._state_to_products()))

    async def _send_category(self, message: GroupMessage, cmd: str, category_ids: list[int]):
        items = filter_category_products(self._state_to_products(), category_ids)
        self._remember_shown(message, items)
        await _reply_markdown(message, format_category_products(items, cmd))

    async def _send_search_results(self, message: GroupMessage, term: str, results: list):
        self._remember_shown(message, results)
        await _reply_markdown(message, format_search_results(term, results))

    def _remember_shown(self, message: GroupMessage, items: list) -> None:
        _last_shown[(message.group_openid, message.author.member_openid)] = items

    async def _handle_detail_command(self, message: GroupMessage, arg: str) -> None:
        product = None
        if arg.isdigit():
            shown = _last_shown.get((message.group_openid, message.author.member_openid))
            index = int(arg) - 1
            if shown and 0 <= index < len(shown):
                product = shown[index]
            else:
                await _reply_markdown(message, "请先查看分类或搜索商品，再发送「详情+序号」～")
                return
        else:
            products = self._state_to_products()
            term = _extract_search_term(arg) if arg else ""
            results = _search_by_title(products, term) if term else []
            if not results and arg:
                results = _search_by_tokens(products, _extract_search_tokens(arg))
            if results:
                product = results[0]
            else:
                await _reply_markdown(message, "没找到该商品，请发送「详情+序号」或「详情+商品名」～")
                return
        await self._send_product_detail(message, product)

    async def _send_product_detail(self, message: GroupMessage, product) -> None:
        await _reply_markdown(message, format_product_detail(product))

    async def _send_keyword_reply(self, message: GroupMessage, rule: dict):
        # 支持 replies 数组（随机选一条）或单条 reply
        replies = rule.get("replies")
        reply_text = random.choice(replies) if replies else rule.get("reply", "")
        image_url = PICS_URLS.get(rule.get("image", ""), "")
        if image_url:
            await _reply_image(message, reply_text, image_url)
        else:
            await _reply_markdown(message, reply_text)

    async def _broadcast(self, text: str, label: str, count: int) -> None:
        from config import GROUP_OPENIDS
        if not GROUP_OPENIDS:
            logger.warning("GROUP_OPENIDS 未配置，无法发送通知")
            return
        for group_openid in GROUP_OPENIDS:
            try:
                await self.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=2,
                    markdown=msg_types.MarkdownPayload(content=text),
                )
                logger.info(f"[{group_openid}] {label}已发送：{count} 个商品")
            except Exception as e:
                logger.error(f"[{group_openid}] {label}失败: {e}")

    async def send_content_change_notice(self, changes: list) -> bool:
        from bot.formatter import format_content_change_notice
        from config import CONTENT_CHANGE_GROUP_OPENIDS, CONTENT_CHANGE_USER_OPENIDS

        if not CONTENT_CHANGE_GROUP_OPENIDS and not CONTENT_CHANGE_USER_OPENIDS:
            logger.warning("商品资料变化通知目标未配置")
            return False

        text = format_content_change_notice(changes)
        sent = False
        for group_openid in CONTENT_CHANGE_GROUP_OPENIDS:
            try:
                await self.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=2,
                    markdown=msg_types.MarkdownPayload(content=text),
                )
                logger.info("[%s] 商品资料变化通知已发送", group_openid)
                sent = True
            except Exception as error:
                logger.error("[%s] 商品资料变化通知失败: %s", group_openid, error)

        for user_openid in CONTENT_CHANGE_USER_OPENIDS:
            try:
                await self.api.post_c2c_message(
                    openid=user_openid,
                    msg_type=2,
                    markdown=msg_types.MarkdownPayload(content=text),
                )
                logger.info("[%s] 商品资料变化私信已发送", user_openid)
                sent = True
            except Exception as error:
                logger.error("[%s] 商品资料变化私信失败: %s", user_openid, error)
        return sent

    async def send_restock_notice(self, products: list) -> None:
        from bot.formatter import format_restock_notice
        await self._broadcast(format_restock_notice(products), "补货通知", len(products))

    async def send_new_product_notice(self, products: list) -> None:
        from bot.formatter import format_new_product_notice
        await self._broadcast(format_new_product_notice(products), "新品通知", len(products))

    async def send_daily_digest(self, events: list) -> None:
        await self._broadcast(format_daily_digest(events), "每日汇总", len(events))
