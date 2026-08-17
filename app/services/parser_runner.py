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
    sessions = manager.list_sessions()
    if not sessions:
        return None
    _client = await manager.connect(sessions[0])
    return _client


async def _get_bot():
    """Возвращает Bot (лениво)."""
    global _bot
    if _bot is None:
        _bot = Bot(token=settings.bot_token)
    return _bot


async def run_historical_for_user(user_id: int):
    """Запускает исторический поиск для конкретного пользователя в фоне."""
    async def task():
        try:
            client = await _get_client()
            if client is None:
                logger.warning("No Telethon client for historical search")
                return
            bot = await _get_bot()
            async with AsyncSessionLocal() as session:
                user = await session.get(User, user_id)
                if not user:
                    return
                from app.services.users import check_subscription
                if not await check_subscription(session, user):
                    return
            await _historical_search_for_user(user, client, bot)
        except Exception:
            logger.exception("Historical search task failed for user %s", user_id)

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