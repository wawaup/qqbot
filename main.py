"""
启动入口：初始化 Bot + 定时扫描任务。

botpy 1.x 的 client.run() 是同步阻塞调用，
scheduler 和首次扫描在 on_ready 回调里启动。
"""
import asyncio
import logging

# 必须在 import botpy 之前设置，否则 botpy 的 basicConfig 抢先注册 WARNING 级别的 root handler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)

import botpy

from bot.handlers import BotHandlers
from config import BOT_APPID, BOT_SECRET, SANDBOX
from scheduler.tasks import create_scheduler, scan_and_notify, set_bot_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_scheduler = create_scheduler()


class App(BotHandlers):
    async def on_ready(self):
        logger.info(f"机器人「{self.robot.name}」已上线")

        # 必须先调用父类，注册 group_message_create 的 parser 补丁
        await super().on_ready()

        set_bot_client(self)
        _scheduler.start()
        logger.info(f"定时扫描已启动，间隔 {__import__('config').SCAN_INTERVAL} 秒")
        await scan_and_notify(first_run=True)


if __name__ == "__main__":
    if not BOT_APPID or not BOT_SECRET:
        raise RuntimeError("请在 .env 中配置 BOT_APPID 和 BOT_SECRET")

    # Python 3.10+ 不再自动创建事件循环，botpy __init__ 需要提前设好
    asyncio.set_event_loop(asyncio.new_event_loop())

    intents = botpy.Intents(public_messages=True)
    client = App(intents=intents, is_sandbox=SANDBOX)
    client.run(appid=BOT_APPID, secret=BOT_SECRET)
