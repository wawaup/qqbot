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

logger = logging.getLogger(__name__)

_scheduler = create_scheduler()


class App(BotHandlers):
    _initialized = False  # 防止重连时重复初始化

    async def on_ready(self):
        logger.info(f"机器人「{self.robot.name}」已上线")
        await super().on_ready()  # 注册 group_message_create parser 补丁
        set_bot_client(self)

        if not App._initialized:
            App._initialized = True
            _scheduler.start()
            logger.info(f"定时扫描已启动，间隔 {__import__('config').SCAN_INTERVAL} 秒")
            await scan_and_notify(first_run=True)
        else:
            logger.info("重连成功，调度器继续运行")


if __name__ == "__main__":
    if not BOT_APPID or not BOT_SECRET:
        raise RuntimeError("请在 .env 中配置 BOT_APPID 和 BOT_SECRET")

    asyncio.set_event_loop(asyncio.new_event_loop())
    intents = botpy.Intents(public_messages=True)
    client = App(intents=intents, is_sandbox=SANDBOX)
    client.run(appid=BOT_APPID, secret=BOT_SECRET)
