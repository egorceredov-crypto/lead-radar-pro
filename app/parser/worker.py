import asyncio
import datetime
import logging
from sqlalchemy import select
from telethon import events
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterEmpty

from app.parser.telethon_client import TelethonClientManager
from app.database.session import AsyncSessionLocal, init_db
from app.database.models import (
    Source, Keyword, StopWord, Lead, ChatMessage, Notification, User
)
from config import settings
from aiogram import Bot

logger = logging.getLogger(__name__)

# Период исторического поиска по умолчанию (дней). Админ может изменить.
HISTORY_DAYS = 7


def _build_link(chat, msg_id: int) -> str | None:
    """Строит ссылку на сообщение."""
    username = getattr(chat, "username", None)
    chat_id = getattr(chat, "id", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if chat_id:
        return f"https://t.me/c/{abs(chat_id)}/{msg_id}"
    return None


async def _match_keywords(text: str, user: User, session) -> str | None:
    """Возвращает первое совпавшее ключевое слово или None."""
    if not text:
        return None
    text_lower = text.lower()

    kws = (await session.execute(
        select(Keyword).where(Keyword.user_id == user.id)
    )).scalars().all()
    for kw in kws:
        if kw.word.lower() in text_lower:
            return kw.word
    return None


async def _has_stopword(text: str, user: User, session) -> bool:
    """True, если текст содержит минус-слово."""
    if not text:
        return False
    text_lower = text.lower()
    sws = (await session.execute(
        select(StopWord).where(StopWord.user_id == user.id)
    )).scalars().all()
    for sw in sws:
        if sw.word.lower() in text_lower:
            return True
    return False


async def _save_lead(user: User, source: Source, chat, msg, text: str, matched: str, bot: Bot, is_historical_search: bool = False) -> bool:
    """Сохраняет лид и отправляет уведомление. Защита от дублей. Возвращает True, если лид сохранён."""
    async with AsyncSessionLocal() as session:
        dup = (await session.execute(
            select(ChatMessage).where(
                ChatMessage.telegram_message_id == msg.id,
                ChatMessage.user_id == user.id,
            )
        )).scalar_one_or_none()
        if dup:
            logger.debug("Duplicate message %s for user %s, skipping", msg.id, user.id)
            return False

        sender = None
        try:
            sender = await msg.get_sender()
        except Exception:
            pass
        sender_username = getattr(sender, "username", None) or getattr(sender, "first_name", None) or "—"

        cm = ChatMessage(
            chat_id=source.id,
            user_id=user.id,
            telegram_message_id=msg.id,
            sender_id=getattr(sender, "id", None),
            sender_username=sender_username,
            text=text,
            date=msg.date,
            matched_keyword=matched,
            processed=True,
        )
        session.add(cm)
        await session.flush()

        link = _build_link(chat, msg.id)
        lead = Lead(
            user_id=user.id,
            message_id=cm.id,
            chat_id=source.id,
            text=text,
            sender_username=sender_username,
            chat_title=source.title or source.username or "Источник",
            matched_keyword=matched,
            link=link,
            lead_date=msg.date,
            status="new",
        )
        session.add(lead)
        await session.flush()

        # Уведомление только при мониторинге новых сообщений, не при историческом поиске
        if not is_historical_search:
            s = user.settings or {}
            if s.get("notifications", True):
                try:
                    notif_text = (
                        f"Новое совпадение: {matched}\n\n"
                        f"{text[:300]}\n\n"
                        f"Источник: {source.title or source.username or '—'}"
                    )
                    if s.get("show_author", True):
                        notif_text += f"\nАвтор: {sender_username}"
                    if s.get("show_date", True) and msg.date:
                        notif_text += f"\nДата: {msg.date.strftime('%d.%m %H:%M')}"
                    if s.get("show_link", True) and link:
                        notif_text += f"\n{link}"
                    await bot.send_message(user.telegram_id, notif_text)
                except Exception:
                    logger.exception("Failed to send notification to user %s", user.id)

        notif = Notification(
            user_id=user.id,
            message=f"Совпадение: {matched}",
            lead_id=lead.id,
            sent=True,
        )
        session.add(notif)
        await session.commit()
        logger.info("Lead saved: user=%s, source=%s, keyword=%s, msg_id=%s", user.id, source.id, matched, msg.id)
        return True


async def _historical_search_for_source(user: User, source: Source, client, bot: Bot):
    """Ищет сообщения за последние HISTORY_DAYS дней по всем ключевым словам
    пользователя в конкретном источнике (чате). Используется сразу после добавления чата."""
    user_cats = (user.settings or {}).get("categories", []) if user.settings else []
    if user_cats:
        if not source.category or source.category not in user_cats:
            return

    async with AsyncSessionLocal() as session:
        keywords = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id)
        )).scalars().all()

    if not keywords:
        return

    min_date = datetime.datetime.utcnow() - datetime.timedelta(days=HISTORY_DAYS)
    found = 0
    saved = 0

    try:
        entity = await client.get_entity(source.chat_id or source.username)
    except Exception as e:
        logger.warning("Historical-source: cannot resolve source %s: %s", source.id, e)
        return

    for kw in keywords:
        try:
            result = await client(SearchRequest(
                peer=entity,
                q=kw.word,
                filter=InputMessagesFilterEmpty(),
                min_date=min_date,
                max_date=None,
                offset_id=0,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
            ))
            for msg in result.messages:
                text = getattr(msg, "message", "") or ""
                if not text:
                    continue
                async with AsyncSessionLocal() as s2:
                    if await _has_stopword(text, user, s2):
                        continue
                kw_words = kw.word.lower().split()
                if not all(w in text.lower() for w in kw_words):
                    continue
                if await _save_lead(user, source, entity, msg, text, kw.word, bot):
                    saved += 1
                found += 1
        except Exception as e:
            logger.warning("Historical-source search error in %s for %s: %s", source.id, kw.word, e)

    logger.info("Historical search for user %s in source %s: found %s matches", user.id, source.id, found)


async def _historical_search_for_user(user: User, client, bot: Bot):
    """Ищет сообщения за последние HISTORY_DAYS дней по ключевым словам пользователя
    во всех активных источниках (Source), добавленных админом."""
    async with AsyncSessionLocal() as session:
        sources_query = select(Source).where(Source.status == "active")
        user_cats = (user.settings or {}).get("categories", []) if user.settings else []
        if user_cats:
            sources_query = sources_query.where(Source.category.in_(user_cats))
        sources = (await session.execute(sources_query)).scalars().all()
        keywords = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id)
        )).scalars().all()

    if not sources or not keywords:
        logger.info("No sources or keywords for user %s, skipping historical search", user.id)
        return

    min_date = datetime.datetime.utcnow() - datetime.timedelta(days=HISTORY_DAYS)
    found = 0
    saved = 0
    total_keywords = len(keywords)
    total_sources = len(sources)

    logger.info("Starting historical search for user %s: %d sources, %d keywords", user.id, total_sources, total_keywords)

    try:
        await bot.send_message(user.telegram_id, f"🔍 Поиск: {total_sources} источников, {total_keywords} слов")
    except Exception:
        pass

    for source in sources:
        logger.info("Processing source %s (%s) for user %s", source.id, source.title or source.username, user.id)
        try:
            entity = await client.get_entity(source.chat_id or source.username)
            logger.info("Resolved source %s to entity", source.id)
        except Exception as e:
            logger.warning("Historical: cannot resolve source %s: %s", source.id, e)
            try:
                await bot.send_message(user.telegram_id, f"⚠️ Не удалось получить чат: {source.title or source.username}")
            except Exception:
                pass
            continue

        for kw in keywords:
            try:
                logger.info("Searching keyword '%s' in source %s", kw.word, source.id)
                result = await client(SearchRequest(
                    peer=entity,
                    q=kw.word,
                    filter=InputMessagesFilterEmpty(),
                    min_date=min_date,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=100,
                    max_id=0,
                    min_id=0,
                    hash=0,
                ))
                logger.info("Found %d messages for keyword '%s' in source %s", len(result.messages), kw.word, source.id)
                for msg in result.messages:
                    text = getattr(msg, "message", "") or ""
                    if not text:
                        continue
                    # Проверяем минус-слова
                    async with AsyncSessionLocal() as s2:
                        if await _has_stopword(text, user, s2):
                            continue
                    # Проверяем, что сообщение реально содержит ключевое слово
                    if kw.word.lower() not in text.lower():
                        continue
                    if await _save_lead(user, source, entity, msg, text, kw.word, bot, is_historical_search=True):
                        saved += 1
                    found += 1
                    logger.info("Saved lead %d for user %s from source %s", found, user.id, source.id)
            except Exception as e:
                logger.warning("Historical search error in source %s for %s: %s", source.id, kw.word, e)

    logger.info("Historical search for user %s: found %s matches, saved %s new leads", user.id, found, saved)
    try:
        await bot.send_message(user.telegram_id, f"✅ Поиск завершен.\nНайдено совпадений: {found}\nНовых лидов: {saved}\nПосмотреть: /results")
    except Exception:
        pass


async def _monitor_new_messages(client, bot: Bot):
    """Постоянный мониторинг новых сообщений во всех активных источниках."""
    async def handler(event):
        try:
            text = getattr(event.message, "message", None) or getattr(event.message, "text", None) or ""
            if not text:
                return

            chat = await event.get_chat()
            chat_id = getattr(chat, "id", None)
            if chat_id is None:
                return

            chat_username = getattr(chat, "username", None)
            # Нормализуем chat_id: для каналов/супергрупп Telethon может добавлять префикс -100
            normalized_chat_id = chat_id
            if isinstance(normalized_chat_id, int) and normalized_chat_id < 0 and str(abs(normalized_chat_id)).startswith("100"):
                normalized_chat_id = int(str(abs(normalized_chat_id))[3:])
            # Находим источник (Source) по chat_id или username
            async with AsyncSessionLocal() as session:
                query = select(Source).where(Source.status == "active")
                if chat_username:
                    normalized_username = chat_username.lstrip("@").lower()
                    query = query.where(
                        (Source.chat_id == normalized_chat_id) |
                        (Source.username == normalized_username) |
                        (Source.username == chat_username)
                    )
                else:
                    query = query.where(Source.chat_id == normalized_chat_id)
                source = (await session.execute(query)).scalar_one_or_none()
                if not source:
                    logger.debug("No active source for chat_id=%s, skipping", chat_id)
                    return

                users = (await session.execute(
                    select(User).where(User.subscription_status.in_(["free", "active"]))
                )).scalars().all()
                active_users = []
                for user in users:
                    from app.services.users import check_subscription
                    if await check_subscription(session, user):
                        active_users.append(user)
                users = active_users

            for user in users:
                user_cats = (user.settings or {}).get("categories", []) if user.settings else []
                if user_cats:
                    if not source.category or source.category not in user_cats:
                        continue
                async with AsyncSessionLocal() as s2:
                    matched = await _match_keywords(text, user, s2)
                    if not matched:
                        continue
                    if await _has_stopword(text, user, s2):
                        logger.debug("Stopword matched for user %s in chat %s", user.id, chat_id)
                        continue
                logger.info("Monitor matched keyword '%s' for user %s in source %s", matched, getattr(user, 'id', user), getattr(source, 'id', source))
                await _save_lead(user, source, chat, event.message, text, matched, bot)
        except Exception:
            logger.exception("Monitor handler error")

    client.add_event_handler(handler, events.NewMessage)


async def main(bot=None):
    if bot is None:
        from config import settings
        from aiogram import Bot
        bot = Bot(token=settings.bot_token)
    manager = TelethonClientManager()
    sessions = manager.list_sessions()
    if not sessions:
        logger.warning("No session files found. Parsing disabled.")
        return

    client = await manager.connect(sessions[0])
    me = await client.get_me()
    logger.info("Connected Telethon as %s", getattr(me, "username", "?"))

    # Постоянный мониторинг новых сообщений
    asyncio.create_task(_monitor_new_messages(client, bot))
    logger.info("New-message monitoring started")

    # Периодический исторический поиск для всех пользователей
    async def periodic_historical():
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    users = (await session.execute(
                        select(User).where(User.subscription_status.in_(["free", "active"]))
                    )).scalars().all()
                    active_users = []
                    for user in users:
                        from app.services.users import check_subscription
                        if await check_subscription(session, user):
                            active_users.append(user)
                    users = active_users
                for user in users:
                    await _historical_search_for_user(user, client, bot)
            except Exception:
                logger.exception("Periodic historical search error")
            await asyncio.sleep(3600)  # раз в час

    asyncio.create_task(periodic_historical())
    logger.info("Periodic historical search started (every 1h)")

    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    from config import settings
    from aiogram import Bot
    asyncio.run(main(Bot(token=settings.bot_token)))
