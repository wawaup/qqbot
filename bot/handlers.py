"""
消息事件处理：@机器人指令 + 关键词自动回复。
"""
import json
import logging
import random
import re
import traceback
from pathlib import Path

import botpy
from botpy.manage import GroupManageEvent
from botpy.message import GroupMessage

from bot.formatter import format_category_products, format_product_menu, format_search_results
from config import BOT_OPENID, CATEGORY_COMMANDS_FILE, KEYWORDS_FILE, PICS_URLS
from storage.state import load_state

logger = logging.getLogger(__name__)

MENU_KEYWORDS = {"menu", "清单", "菜单", "商品清单", "有什么", "卖什么"}

# 搜索时剥离的末尾/开头询问词（按长度降序，先替换最长的）
_QUERY_STRIP = re.compile(
    r"(有没有|有货吗|有吗|有么|在吗|卖吗|能买吗|还有|怎么样|多少钱|什么价|价格|啥价)\s*$"
    r"|^\s*(有没有|还有|求推荐)\s*"
)

HELP_TEXT = (
    "【曼波导购bot 使用指南】\n"
    "成品号新手教程请看：https://www.xtpu.asia/#/\n\n"
    "@我 发分类指令查有货商品：\n"
    "  推荐 / gpt正价 / 正价冲\n"
    "  gpt\n"
    "  接码\n"
    "  claude\n"
    "  gemini\n"
    "  grok / 其他\n"
    "  苹果id / 邮箱服务\n"
    "  清单 / 菜单 / menu → 查看全部分类\n\n"
    "@我 发商品关键词搜索有货商品：\n"
    "  例：@bot codex / @bot plus / @bot 网页号\n"
    "  支持「有没有」结尾：@bot codex有没有\n\n"
    "直接在群里发关键词可自动回复：\n"
    "  店铺链接 / 在哪买\n"
    "  质保首登 / 活多久 / 会封吗\n"
    "  哪里查订单 / 订单卡密\n"
    "  售后 / 用不了\n"
    "  怎么登录 / 反代 / 邮件接码\n\n"
    "⚠️ 00:00-09:00 为免打扰时段，补货/新品通知暂停推送\n"
    "该时段如需查看有货商品，请 @我 发分类指令"
)

_keywords_cache: list[dict] | None = None
_category_commands_cache: dict[str, list[str]] | None = None


def _load_keywords() -> list[dict]:
    global _keywords_cache
    if _keywords_cache is None:
        path = Path(KEYWORDS_FILE)
        _keywords_cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return _keywords_cache


def _load_category_commands() -> dict[str, list[str]]:
    global _category_commands_cache
    if _category_commands_cache is None:
        path = Path(CATEGORY_COMMANDS_FILE)
        _category_commands_cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _category_commands_cache


def _match_keyword(text: str) -> dict | None:
    for rule in _load_keywords():
        for kw in rule.get("keywords", []):
            if kw in text:
                return rule
    return None


def _match_category_command(text: str) -> tuple[str, list[str]] | None:
    text_lower = text.lower()
    for cmd, categories in _load_category_commands().items():
        if cmd.lower() in text_lower:
            return cmd, categories
    return None


def _extract_search_term(text: str) -> str:
    """去掉首尾询问词，返回核心搜索关键词。"""
    return _QUERY_STRIP.sub("", text).strip()


def _search_by_title(products: dict, query: str) -> list:
    """在有货商品标题中做大小写不敏感的子串搜索。"""
    q = query.lower()
    return [p for p in products.values() if p.in_stock and q in p.title.lower()]


# 每条触发消息最多回复5条，用 msg_id 追踪当前 seq
_seq: dict[str, int] = {}


def _next_seq(msg_id: str) -> int:
    _seq[msg_id] = _seq.get(msg_id, 0) + 1
    return _seq[msg_id]


async def _reply_text(message: GroupMessage, text: str) -> None:
    await message._api.post_group_message(
        group_openid=message.group_openid,
        msg_type=0,
        msg_id=message.id,
        msg_seq=_next_seq(message.id),
        content=text,
    )


async def _reply_image(message: GroupMessage, text: str, image_url: str) -> None:
    try:
        # 先发文字
        if text:
            await _reply_text(message, text)
        media = await message._api.post_group_file(
            group_openid=message.group_openid,
            file_type=1,
            url=image_url,
        )
        # 再发图片
        await message._api.post_group_message(
            group_openid=message.group_openid,
            msg_type=7,
            msg_id=message.id,
            msg_seq=_next_seq(message.id),
            media=media,
        )
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
        state = self._connection.state

        def parse_group_message_create(payload):
            msg = GroupMessage(state.api, payload.get("id"), payload.get("d", {}))
            state._dispatch("group_message_create", msg)

        state.parsers["group_message_create"] = parse_group_message_create
        logger.info("group_message_create 事件已注册")

    async def on_group_add_robot(self, event: GroupManageEvent):
        logger.info(f"[群管理] 机器人加入群 group_openid={event.group_openid}")

    async def on_group_msg_receive(self, event: GroupManageEvent):
        logger.info(f"[群管理] 群开启主动消息 group_openid={event.group_openid}")

    async def on_group_at_message_create(self, message: GroupMessage):
        """有人 @机器人 时触发。"""
        # 记录完整原始消息，方便排查问题
        logger.info(
            f"[AT消息] group_openid={message.group_openid} "
            f"id={message.id} content={message.content!r}"
        )
        try:
            content = re.sub(r"<@[^>]+>", "", message.content).strip()

            if any(kw in content for kw in MENU_KEYWORDS):
                await self._send_menu(message)
            else:
                cat_match = _match_category_command(content)
                if cat_match:
                    cmd, categories = cat_match
                    await self._send_category(message, cmd, categories)
                else:
                    # 标题关键词搜索：剥离询问词后在商品标题里匹配
                    term = _extract_search_term(content)
                    if term:
                        results = _search_by_title(self._state_to_products(), term)
                        if results:
                            await self._send_search_results(message, term, results)
                            return
                    await _reply_text(message, HELP_TEXT)

        except Exception:
            logger.error(f"[AT消息] 处理异常:\n{traceback.format_exc()}")

    async def on_group_message_create(self, message: GroupMessage):
        """群内消息：带 <@!> 的路由到 AT 处理，否则做关键词匹配。"""
        logger.info(
            f"[群消息] group_openid={message.group_openid} "
            f"content={message.content!r}"
        )
        try:
            content = (message.content or "").strip()
            # QQ 群里 @机器人 实际以 GROUP_MESSAGE_CREATE 下发，内容带 <@botid>
            # 只响应 @自己，忽略 @其他人的消息
            bot_tag = f"<@{BOT_OPENID}>" if BOT_OPENID else None
            is_at_bot = (bot_tag and bot_tag in content) or (not BOT_OPENID and bool(re.search(r"<@[^>]+>", content)))
            if is_at_bot:
                clean = re.sub(r"<@[^>]+>", "", content).strip()
                if any(kw in clean for kw in MENU_KEYWORDS):
                    await self._send_menu(message)
                else:
                    cat_match = _match_category_command(clean)
                    if cat_match:
                        cmd, categories = cat_match
                        await self._send_category(message, cmd, categories)
                    else:
                        term = _extract_search_term(clean)
                        if term:
                            results = _search_by_title(self._state_to_products(), term)
                            if results:
                                await self._send_search_results(message, term, results)
                                return
                        await _reply_text(message, HELP_TEXT)
            else:
                rule = _match_keyword(content)
                if rule:
                    await self._send_keyword_reply(message, rule)
        except Exception:
            logger.error(f"[群消息] 处理异常:\n{traceback.format_exc()}")

    def _state_to_products(self) -> dict:
        from shop.models import Product
        return {
            pid: Product(
                id=pid,
                title=d["title"],
                url=d["url"],
                category=d["category"],
                in_stock=d["in_stock"],
                price=str(d.get("price", "")),
            )
            for pid, d in load_state().items()
        }

    async def _send_menu(self, message: GroupMessage):
        await _reply_text(message, format_product_menu(self._state_to_products()))

    async def _send_category(self, message: GroupMessage, cmd: str, categories: list[str]):
        await _reply_text(
            message,
            format_category_products(self._state_to_products(), categories, cmd),
        )

    async def _send_search_results(self, message: GroupMessage, term: str, results: list):
        await _reply_text(message, format_search_results(term, results))

    async def _send_keyword_reply(self, message: GroupMessage, rule: dict):
        # 支持 replies 数组（随机选一条）或单条 reply
        replies = rule.get("replies")
        reply_text = random.choice(replies) if replies else rule.get("reply", "")
        image_url = PICS_URLS.get(rule.get("image", ""), "")
        if image_url:
            await _reply_image(message, reply_text, image_url)
        else:
            await _reply_text(message, reply_text)

    async def _broadcast(self, text: str, label: str, count: int) -> None:
        from config import GROUP_OPENIDS
        if not GROUP_OPENIDS:
            logger.warning("GROUP_OPENIDS 未配置，无法发送通知")
            return
        for group_openid in GROUP_OPENIDS:
            try:
                await self.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=0,
                    content=text,
                )
                logger.info(f"[{group_openid}] {label}已发送：{count} 个商品")
            except Exception as e:
                logger.error(f"[{group_openid}] {label}失败: {e}")

    async def send_restock_notice(self, products: list) -> None:
        from bot.formatter import format_restock_notice
        await self._broadcast(format_restock_notice(products), "补货通知", len(products))

    async def send_new_product_notice(self, products: list) -> None:
        from bot.formatter import format_new_product_notice
        await self._broadcast(format_new_product_notice(products), "新品通知", len(products))
