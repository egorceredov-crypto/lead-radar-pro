"""Сервис запуска исторического парсинга в фоне.

Используется, когда пользователь добавляет ключевое слово:
сразу запускается исторический поиск за последние HISTORY_DAYS дней
по всем активным источникам (Source), добавленным администратором.
"""

import asyncio
import logging
from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.database.models import User, Source, Keyword
from app.parser.telethon_client import TelethonClientManager
from app.parser.worker import _historical_search_for_user, _historical_search_for_source, HISTORY_DAYS
from config import settings
from aiogram import Bot

logger = logging.getLogger(__name__)

# Глобальный клиент Telethon (один на процесс)
_client = None
_bot = None

# Защита от повторного запуска исторического поиска по одному источнику
_running_sources = set()


async def _get_client():
    """Возвращает подключённый Telethon-клиент (лениво)."""
    global _client
    if _client is not None:
        return _client
    manager = TelethonClientManager()
    sessions = manager.list_all_sessions()
    if not sessions:
        return None
    for session_name in sessions:
        try:
            _client = await manager.connect(session_name)
            return _client
        except Exception:
            continue
    return None


async def _get_bot():
    """Возвращает Bot (лениво)."""
    global _bot
    if _bot is None:
        _bot = Bot(token=settings.bot_token)
    return _bot


async def run_historical_for_user(user_id: int, keyword: str | None = None):
    """Запускает исторический поиск для конкретного пользователя в фоне.
    
    Если передан keyword, ищутся только совпадения по этому слову.
    Иначе ищутся все ключевые слова пользователя.
    """
    logger.info("RUN_HIST_FOR_USER start user_id=%s keyword=%s", user_id, keyword)
    async def task():
        try:
            logger.info("RUN_HIST_FOR_USER getting client for user_id=%s", user_id)
            client = await _get_client()
            if client is None:
                logger.error("RUN_HIST_FOR_USER FAILED user_id=%s reason=no_client", user_id)
                return
            logger.info("RUN_HIST_FOR_USER client ok for user_id=%s", user_id)
            bot = await _get_bot()
            logger.info("RUN_HIST_FOR_USER bot ok for user_id=%s", user_id)
            async with AsyncSessionLocal() as session:
                user = await session.get(User, user_id)
                if not user:
                    logger.error("RUN_HIST_FOR_USER FAILED user_id=%s reason=user_not_found", user_id)
                    return
                logger.info("RUN_HIST_FOR_USER user found id=%s telegram_id=%s status=%s", user.id, user.telegram_id, user.subscription_status)
                from app.services.users import check_subscription
                if not await check_subscription(session, user):
                    logger.error("RUN_HIST_FOR_USER FAILED user_id=%s reason=subscription_check_failed status=%s", user_id, user.subscription_status)
                    return
                logger.info("RUN_HIST_FOR_USER subscription ok for user_id=%s", user_id)
            logger.info("RUN_HIST_FOR_USER calling _historical_search_for_user user_id=%s keyword=%s", user_id, keyword)
            await _historical_search_for_user(user, client, bot, keyword=keyword)
            logger.info("RUN_HIST_FOR_USER completed user_id=%s", user_id)
        except Exception:
            logger.exception("RUN_HIST_FOR_USER ERROR user_id=%s", user_id)

    asyncio.create_task(task())


async def run_historical_for_source(source_id: int):
    """Запускает исторический поиск для конкретного источника (чата) для всех
    активных пользователей. Используется сразу после добавления чата админом."""
    if source_id in _running_sources:
        logger.info("Historical search already running for source %s", source_id)
        return
    _running_sources.add(source_id)

    async def task():
        try:
            client = await _get_client()
            if client is None:
                logger.warning("No Telethon client for source historical search")
                return
            bot = await _get_bot()
            async with AsyncSessionLocal() as session:
                source = await session.get(Source, source_id)
                if not source:
                    return
                users = (await session.execute(
                    select(User).where(User.subscription_status.in_(["free", "active"]))
                )).scalars().all()
                from app.services.users import check_subscription
                active_users = []
                for user in users:
                    if await check_subscription(session, user):
                        active_users.append(user)
                users = active_users
            for user in users:
                await _historical_search_for_source(user, source, client, bot)
        except Exception:
            logger.exception("Historical search for source %s failed", source_id)
        finally:
            _running_sources.discard(source_id)

    asyncio.create_task(task())


async def run_historical_for_all():
    """Запускает исторический поиск для всех активных пользователей."""
    async def task():
        try:
            client = await _get_client()
            if client is None:
                return
            bot = await _get_bot()
            async with AsyncSessionLocal() as session:
                users = (await session.execute(
                    select(User).where(User.subscription_status.in_(["free", "active"]))
                )).scalars().all()
                from app.services.users import check_subscription
                active_users = []
                for user in users:
                    if await check_subscription(session, user):
                        active_users.append(user)
                users = active_users
            for user in users:
                await _historical_search_for_user(user, client, bot)
        except Exception:
            logger.exception("Historical search for all failed")

    asyncio.create_task(task())