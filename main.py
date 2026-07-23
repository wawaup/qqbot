import asyncio
import logging

# 必须在 import botpy 之前设置，否则 botpy 的 basicConfig 抢先注册 WARNING 级别的 root handler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)

import botpy

from api.server import set_bot_client as set_api_bot_client
from api.server import start_status_server
from bot.handlers import BotHandlers
from config import (
    BOT_APPID,
    BOT_SECRET,
    SANDBOX,
    STATUS_API_ALLOWED_ORIGIN,
    STATUS_API_ENABLED,
    STATUS_API_HOST,
    STATUS_API_PORT,
)
from scheduler.tasks import (
    check_catalog_content_changes,
    create_scheduler,
    scan_and_notify,
    set_bot_client,
)

logger = logging.getLogger(__name__)

_scheduler = create_scheduler()


class App(BotHandlers):
    _initialized = False  # 防止重连时重复初始化

    async def on_ready(self):
        logger.info(f"机器人「{self.robot.name}」已上线")
        await super().on_ready()  # 注册 group_message_create parser 补丁
        set_bot_client(self)
        set_api_bot_client(self)

        if not App._initialized:
            App._initialized = True
            _scheduler.start()
            logger.info(f"定时扫描已启动，间隔 {__import__('config').SCAN_INTERVAL} 秒")
            await scan_and_notify(first_run=True)
            await check_catalog_content_changes()
        else:
            logger.info("重连成功，调度器继续运行")


if __name__ == "__main__":
    if not BOT_APPID or not BOT_SECRET:
        raise RuntimeError("请在 .env 中配置 BOT_APPID 和 BOT_SECRET")

    status_server = None
    status_thread = None
    try:
        if STATUS_API_ENABLED:
            status_server, status_thread = start_status_server(
                STATUS_API_HOST,
                STATUS_API_PORT,
                STATUS_API_ALLOWED_ORIGIN,
            )

        asyncio.set_event_loop(asyncio.new_event_loop())
        intents = botpy.Intents(public_messages=True)
        client = App(intents=intents, is_sandbox=SANDBOX)
        client.run(appid=BOT_APPID, secret=BOT_SECRET)
    finally:
        if status_server is not None:
            status_server.shutdown()
            status_server.server_close()
        if status_thread is not None:
            status_thread.join(timeout=2)
