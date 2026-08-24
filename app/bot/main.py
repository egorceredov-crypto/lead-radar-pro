import asyncio
import logging
import os
import sys
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery
from aiogram import BaseMiddleware
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramConflictError

print("BOT_ENTRY_START", flush=True)

from app.database.session import init_db
from app.bot.handlers_sessions import router as sessions_router
from app.bot.handlers_parse import router as parse_router
from app.bot.handlers_user import router as user_router
from app.payments.telegram import router as payments_router
from app.parser.worker import main as parser_main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
    force=True,
)
for _name in ("aiogram", "app.bot", "app.parser", "app.database"):
    logging.getLogger(_name).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

BOT_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot.pid")


def _acquire_bot_lock() -> bool:
    try:
        if os.path.exists(BOT_LOCK_FILE):
            with open(BOT_LOCK_FILE, "r") as f:
                content = f.read().strip()
            if content and content.isdigit():
                pid = int(content)
                try:
                    os.kill(pid, 0)
                    logger.warning("Another bot instance is already running (pid=%s). Exiting.", pid)
                    return False
                except ProcessLookupError:
                    logger.info("Stale bot lock file found (pid=%s). Removing.", pid)
                    os.remove(BOT_LOCK_FILE)
                except PermissionError:
                    logger.warning("Another bot instance is already running (pid=%s). Exiting.", pid)
                    return False
        with open(BOT_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        logger.warning("Bot lock check failed: %s", e)
        return True


def _release_bot_lock():
    try:
        if os.path.exists(BOT_LOCK_FILE):
            os.remove(BOT_LOCK_FILE)
    except Exception:
        pass


class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            logger.info("MESSAGE from %s: text=%r", event.from_user.id, event.text)
        elif isinstance(event, CallbackQuery):
            logger.info("CALLBACK from %s: data=%r", event.from_user.id, event.data)
        try:
            return await handler(event, data)
        except Exception as e:
            logger.exception("Handler error for %s: %s", event.from_user.id if hasattr(event, 'from_user') else 'unknown', e)
            if isinstance(event, Message):
                try:
                    await event.answer("Ошибка. Попробуйте позже.")
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("Ошибка. Попробуйте позже.", show_alert=True)
                except Exception:
                    pass


def _build_bot(settings):
    session = None
    proxy_host = getattr(settings, "proxy_host", None)
    proxy_port = getattr(settings, "proxy_port", None)
    proxy_type = getattr(settings, "proxy_type", None)
    if proxy_host and proxy_port and proxy_type:
        try:
            import aiohttp_socks
            proxy_url = f"{proxy_type}://{proxy_host}:{proxy_port}"
            session = AiohttpSession(proxy=proxy_url)
            logger.info("Using proxy for bot: %s", proxy_url)
        except Exception as e:
            logger.warning("Failed to setup proxy for bot: %s", e)
    return Bot(token=settings.bot_token, session=session)


async def main():
    logger.info("Starting bot...")
    if not _acquire_bot_lock():
        logger.error("Another bot instance is already running. Exiting.")
        sys.exit(1)
    
    try:
        from config import settings
    except Exception as e:
        logger.exception("Configuration error: %s", e)
        logger.error("Please ensure BOT_TOKEN, API_ID, API_HASH, and SESSION_STRING are set in .env or environment variables.")
        _release_bot_lock()
        sys.exit(1)
    
    try:
        bot = _build_bot(settings)
    except Exception as e:
        logger.exception("Failed to build bot: %s", e)
        _release_bot_lock()
        sys.exit(1)
    
    dp = Dispatcher()
    router = Router()
    router.include_router(payments_router)
    router.include_router(user_router)
    router.include_router(sessions_router)
    router.include_router(parse_router)
    dp.include_router(router)
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    
    try:
        await init_db()
    except Exception as e:
        logger.exception("Database init failed: %s", e)
        raise
    logger.info("Database initialized")
    
    async def _start_parser():
        logger.info("Parser task starting...")
        try:
            await parser_main(bot)
        except Exception as e:
            logger.exception("Parser worker crashed: %s", e)
        finally:
            logger.info("Parser task finished")
    
    task = asyncio.create_task(_start_parser())
    logger.info("Parser worker started, task id=%s", id(task))
    
    try:
        while True:
            try:
                await dp.start_polling(bot)
            except TelegramConflictError:
                logger.error("TelegramConflictError: another instance is polling with the same token. Waiting 30s before retry.")
                await asyncio.sleep(30)
            except Exception as e:
                logger.error("Polling crashed: %s", e)
                await asyncio.sleep(5)
    finally:
        _release_bot_lock()


if __name__ == '__main__':
    try:
        logger.info("Bot entry point starting")
        asyncio.run(main())
    except Exception as e:
        logger.exception("Bot entry point crashed: %s", e)
        sys.exit(1)
