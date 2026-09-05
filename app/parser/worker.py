import asyncio
import datetime
import logging
import time
from sqlalchemy import func, select
from telethon import events
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterEmpty

from app.parser.telethon_client import TelethonClientManager
from app.database.session import AsyncSessionLocal, init_db
from app.database.models import (
    Source, Keyword, StopWord, Lead, ChatMessage, Notification, User
)
from app.services.users import auto_category
from config import settings
from aiogram import Bot

logger = logging.getLogger(__name__)

# Период исторического поиска по умолчанию (дней). Админ может изменить.
HISTORY_DAYS = 3
QUICK_SEARCH_SOURCES_LIMIT = 20
SEARCH_DELAY = 0.5

# Пакетное уведомление о новых лидах: {user_id: {"count": int, "last_sent": float}}
_lead_batches: dict[int, dict] = {}
_lead_batch_lock = asyncio.Lock()
_LEAD_BATCH_DELAY = 30  # секунд между сводками
_LEAD_BATCH_MIN_COUNT = 1  # минимальное количество лидов для отправки сводки

# Кэш эффективных источников пользователей: user_id -> set(source_id)
# Учитывает категории пользователя и fallback на все источники, если категории не совпадают.
_user_effective_source_ids: dict[int, set[int]] = {}


def invalidate_user_cache(user_id: int):
    """Invalidate cached effective sources for a user (e.g. after category change)."""
    _user_effective_source_ids.pop(user_id, None)


def invalidate_all_user_caches():
    """Invalidate cached effective sources for all users (e.g. after sources change)."""
    _user_effective_source_ids.clear()


# ============ Reminder state helpers ============

def _get_reminder_settings(user: User) -> dict:
    settings = getattr(user, "settings", None) or {}
    if not isinstance(settings, dict):
        settings = {}
    return settings


async def _persist_reminder_settings(user: User, settings: dict):
    """Persist reminder settings to database."""
    async with AsyncSessionLocal() as session:
        db_user = await session.get(User, user.id)
        if db_user:
            db_user.settings = settings
            await session.commit()


async def _should_send_keyword_reminder(user: User) -> tuple[bool, str | None]:
    """Return (should_send, stage_text). Stages: 1h, 6h, 24h."""
    settings = _get_reminder_settings(user)
    reg_date = getattr(user, "registration_date", None)
    if reg_date is None:
        return False, None

    now = datetime.datetime.utcnow()
    stage = settings.get("keyword_reminder_stage", 0)
    last_sent_str = settings.get("last_keyword_reminder_sent")

    # Backward compatibility: old code stored stage as 1 higher than actual
    if stage == 2:
        stage = 1
    elif stage == 3:
        stage = 2

    if stage == 0:
        hours_since_reg = (now - reg_date).total_seconds() / 3600
        if hours_since_reg >= 1:
            return True, "1"
        return False, None

    if stage == 1:
        if last_sent_str:
            last_sent = datetime.datetime.fromisoformat(last_sent_str)
            if (now - last_sent).total_seconds() >= 6 * 3600:
                return True, "2"
        return False, None

    if stage == 2:
        if last_sent_str:
            last_sent = datetime.datetime.fromisoformat(last_sent_str)
            if (now - last_sent).total_seconds() >= 24 * 3600:
                return True, "3"
        return False, None

    return False, None


async def _should_send_source_reminder(user: User) -> bool:
    settings = _get_reminder_settings(user)
    now = datetime.datetime.utcnow()
    last_sent_str = settings.get("last_source_reminder_sent")
    if last_sent_str:
        try:
            last_sent = datetime.datetime.fromisoformat(last_sent_str)
            if (now - last_sent).total_seconds() < 86400:
                return False
        except Exception:
            pass
    return True


async def _send_keyword_reminder(user: User, bot: Bot, stage: str):
    texts = {
        "1": "Похоже, поиск ещё не настроен. Добавь ключевые слова — например: «Python разработчик», «Telegram бот», «автоматизация». После этого бот начнёт искать подходящие заявки.",
        "2": "Напоминаю про настройку поиска. Добавь ключевые слова, по которым нужно искать клиентов, и бот сможет начать работу.",
        "3": "Поиск пока не настроен. Если хочешь получать подходящие заявки автоматически, добавь ключевые слова и источники.",
    }
    text = texts.get(stage, texts["1"])
    try:
        await bot.send_message(user.telegram_id, text)
        settings = _get_reminder_settings(user)
        settings["last_keyword_reminder_sent"] = datetime.datetime.utcnow().isoformat()
        next_stage = int(stage) + 1 if int(stage) < 3 else 3
        settings["keyword_reminder_stage"] = next_stage
        await _persist_reminder_settings(user, settings)
        logger.info("REMINDER_SENT user=%s type=keyword stage=%s", user.id, stage)
    except Exception as e:
        logger.error("REMINDER_ERROR user=%s type=keyword error=%s", user.id, e)


async def _send_source_reminder(user: User, bot: Bot):
    text = "Поиск активирован. Бот автоматически проверяет доступные Telegram-чаты по вашим ключевым словам."
    try:
        await bot.send_message(user.telegram_id, text)
        settings = _get_reminder_settings(user)
        settings["last_source_reminder_sent"] = datetime.datetime.utcnow().isoformat()
        await _persist_reminder_settings(user, settings)
        logger.info("REMINDER_SENT user=%s type=source", user.id)
    except Exception as e:
        logger.error("REMINDER_ERROR user=%s type=source error=%s", user.id, e)


async def _process_user_reminders(user: User, bot: Bot):
    """Process setup reminders for a single user based on current state."""
    async with AsyncSessionLocal() as session:
        keywords = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id)
        )).scalars().all()
        effective_source_ids = await _get_user_effective_source_ids(user)

    has_keywords = len(keywords) > 0
    has_sources = len(effective_source_ids) > 0

    if has_keywords and has_sources:
        return

    if has_keywords and not has_sources:
        if await _should_send_source_reminder(user):
            await _send_source_reminder(user, bot)
        return

    if not has_keywords:
        should_send, stage = await _should_send_keyword_reminder(user)
        if should_send and stage:
            await _send_keyword_reminder(user, bot, stage)


async def _periodic_reminders(bot: Bot):
    """Check and send setup reminders once per hour."""
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
                try:
                    await _process_user_reminders(user, bot)
                except Exception:
                    logger.exception("Reminder processing failed for user %s", user.id)
                await asyncio.sleep(0.1)
        except Exception:
            logger.exception("Periodic reminders error")
        await asyncio.sleep(3600)


_source_last_checked: dict[int, int] = {}


async def _get_last_checked_message_id(source_id: int) -> int:
    """Get the last checked message ID for a source, initializing from DB if needed."""
    if source_id in _source_last_checked:
        return _source_last_checked[source_id]
    async with AsyncSessionLocal() as session:
        source = await session.get(Source, source_id)
        db_last = getattr(source, "last_checked_message_id", None)
        if db_last:
            _source_last_checked[source_id] = int(db_last)
            return int(db_last)
        result = await session.execute(
            select(func.max(ChatMessage.telegram_message_id)).where(
                ChatMessage.chat_id == source_id
            )
        )
        max_id = result.scalar_one_or_none() or 0
    _source_last_checked[source_id] = max_id
    return max_id


def _update_last_checked_message_id(source_id: int, message_id: int):
    """Update the last checked message ID for a source."""
    current = _source_last_checked.get(source_id, 0)
    if message_id > current:
        _source_last_checked[source_id] = message_id
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_persist_last_checked(source_id, message_id))
            else:
                loop.run_until_complete(_persist_last_checked(source_id, message_id))
        except Exception as e:
            logger.debug("Failed to persist last_checked_message_id for source %s: %s", source_id, e)


async def _persist_last_checked(source_id: int, message_id: int):
    async with AsyncSessionLocal() as session:
        source = await session.get(Source, source_id)
        if source is not None:
            source.last_checked_message_id = message_id
            await session.commit()


async def _search_sources_polling(user: User, client, bot, sources, keywords) -> tuple[int, int, int]:
    """Search a list of sources for new messages. Returns (new_leads_count, total_new_messages, total_matched)."""
    total_new_messages = 0
    total_matched = 0
    new_leads_count = 0

    for source in sources:
        try:
            entity = await client.get_entity(_normalize_chat_id(source.chat_id) or source.username)
        except Exception as e:
            logger.warning("Polling: cannot resolve source %s for user %s: %s", source.id, user.id, e)
            continue

        try:
            last_checked_id = await _get_last_checked_message_id(source.id)
            messages = await client.get_messages(entity, limit=100)
            new_in_source = [m for m in messages if getattr(m, "id", 0) > last_checked_id and not _is_outgoing_message(m)]
            logger.info("POLLING_SOURCE user=%s source=%s chat_id=%s title=%s last_checked_id=%s fetched=%s new=%s", user.id, source.id, source.chat_id, source.title, last_checked_id, len(messages), len(new_in_source))
            logger.info("PARSER_SOURCE chat_id=%s title=%s type=%s source_id=%s", source.chat_id, source.title, source.type, source.id)
            total_new_messages += len(new_in_source)
        except Exception as e:
            logger.warning("Polling: cannot get messages for source %s user %s: %s", source.id, user.id, e)
            continue

        for msg in new_in_source:
            text = getattr(msg, "message", "") or ""
            if not text:
                _update_last_checked_message_id(source.id, msg.id)
                continue

            async with AsyncSessionLocal() as s2:
                dup = (await s2.execute(
                    select(ChatMessage).where(
                        ChatMessage.telegram_message_id == msg.id,
                        ChatMessage.user_id == user.id,
                    )
                )).scalar_one_or_none()
                if dup:
                    _update_last_checked_message_id(source.id, msg.id)
                    continue

            async with AsyncSessionLocal() as s3:
                matched = await _match_keywords(text, user, s3)
                if not matched:
                    _update_last_checked_message_id(source.id, msg.id)
                    continue
                if await _has_stopword(text, user, s3):
                    _update_last_checked_message_id(source.id, msg.id)
                    continue

            saved = await _save_lead(user, source, entity, msg, text, matched, bot)
            if saved:
                new_leads_count += 1
                total_matched += 1
            _update_last_checked_message_id(source.id, msg.id)

    return new_leads_count, total_new_messages, total_matched


async def _poll_new_messages_for_user(user: User, client, bot: Bot):
    """Проверяет новые сообщения в источниках пользователя."""
    source_ids = await _get_user_effective_source_ids(user)
    if not source_ids:
        logger.info("POLLING_SKIP user=%s reason=no_sources", user.id)
        return

    async with AsyncSessionLocal() as session:
        sources = (await session.execute(
            select(Source).where(Source.id.in_(source_ids))
        )).scalars().all()

    if not sources:
        logger.info("POLLING_SKIP user=%s reason=no_sources_resolved", user.id)
        return

    async with AsyncSessionLocal() as session:
        keywords = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id)
        )).scalars().all()

    if not keywords:
        logger.info("POLLING_SKIP user=%s reason=no_keywords", user.id)
        return

    new_leads_count, total_new_messages, total_matched = await _search_sources_polling(user, client, bot, sources, keywords)

    user_cats = (user.settings or {}).get("categories", []) if user.settings else []
    remaining_sources = []
    if new_leads_count == 0 and user_cats:
        async with AsyncSessionLocal() as session:
            all_sources = (await session.execute(
                select(Source).where(Source.status == "active")
            )).scalars().all()
        remaining_ids = {s.id for s in all_sources} - {s.id for s in sources}
        if remaining_ids:
            async with AsyncSessionLocal() as session:
                remaining_sources = (await session.execute(
                    select(Source).where(Source.id.in_(remaining_ids))
                )).scalars().all()
            logger.info("POLLING_FALLBACK user=%s remaining_sources=%d", user.id, len(remaining_sources))
            fallback_leads, fallback_new, fallback_matched = await _search_sources_polling(user, client, bot, remaining_sources, keywords)
            new_leads_count += fallback_leads
            total_new_messages += fallback_new
            total_matched += fallback_matched

    logger.info("POLLING_CYCLE user=%s sources=%d new_messages=%s matched=%s saved=%s", user.id, len(sources) + len(remaining_sources), total_new_messages, total_matched, new_leads_count)

    if new_leads_count > 0:
        try:
            await bot.send_message(
                user.telegram_id,
                f"🔎 Найдены новые лиды: {new_leads_count}\nПосмотреть в /results",
            )
            logger.info("POLLING_NOTIFICATION_SENT user=%s count=%s", user.id, new_leads_count)
        except Exception as e:
            logger.error("POLLING_NOTIFICATION_ERROR user=%s error=%s", user.id, e)


async def _periodic_polling(clients, client_users, bot):
    """Проверяет новые сообщения каждую минуту для всех пользователей."""
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
                client_idx = None
                for i, u in enumerate(client_users):
                    if u is not None and u.id == user.id:
                        client_idx = i
                        break
                if client_idx is not None:
                    await _poll_new_messages_for_user(user, clients[client_idx], bot)
                else:
                    await _poll_new_messages_for_user(user, clients[0], bot)
                await asyncio.sleep(0.1)
        except Exception:
            logger.exception("Periodic polling error")
        await asyncio.sleep(60)


async def _add_lead_to_batch(user_id: int, bot: Bot):
    """Добавляет лид в пакет для пользователя. Отправляет сводку, если прошло достаточно времени."""
    async with _lead_batch_lock:
        if user_id not in _lead_batches:
            _lead_batches[user_id] = {"count": 0, "last_sent": 0.0}
        _lead_batches[user_id]["count"] += 1
        batch = _lead_batches[user_id]
        now = time.monotonic()
        if batch["count"] >= _LEAD_BATCH_MIN_COUNT:
            if batch["last_sent"] == 0.0 or (now - batch["last_sent"]) >= _LEAD_BATCH_DELAY:
                count = batch["count"]
                batch["count"] = 0
                batch["last_sent"] = now
                asyncio.create_task(_send_lead_batch(user_id, bot, count))


async def _send_lead_batch(user_id: int, bot: Bot, count: int):
    """Отправляет сводку по найденным лидам."""
    try:
        await bot.send_message(
            user_id,
            f"🔎 Найдены новые лиды: {count}\nПосмотреть в /results",
        )
        logger.info("LEAD_NOTIFICATION_SENT user=%s count=%s", user_id, count)
    except Exception as e:
        logger.error("Failed to send lead batch notification to user %s: %s", user_id, e)

def _normalize_chat_id(chat_id: int) -> int:
    if isinstance(chat_id, int) and chat_id < 0 and str(abs(chat_id)).startswith("100"):
        return int(str(abs(chat_id))[3:])
    return chat_id


def _is_bot_entity(entity) -> bool:
    return bool(getattr(entity, "bot", False))


def _is_outgoing_message(msg) -> bool:
    return bool(getattr(msg, "outgoing", False))


def _detect_chat_type(entity) -> str:
    from telethon.tl.types import Channel, Chat, User as TLUser
    if isinstance(entity, TLUser):
        return "private"
    if isinstance(entity, Channel):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    if isinstance(entity, Chat):
        return "group"
    return "chat"


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


def _normalize_category(name: str) -> str:
    """Normalize category name for matching: lowercase, strip, remove common plural suffixes."""
    if not name:
        return ""
    s = name.strip().lower()
    for suffix in ("ы", "и"):
        if s.endswith(suffix):
            s = s[:-1]
    return s


async def _get_user_effective_source_ids(user: User) -> set[int]:
    """Возвращает set ID источников, которые пользователь должен получать.
    Учитывает выбранные категории. Если категории не совпадают ни с одним источником — возвращает все активные источники."""
    if user.id in _user_effective_source_ids:
        return _user_effective_source_ids[user.id]
    async with AsyncSessionLocal() as session:
        sources_query = select(Source).where(Source.status == "active")
        user_cats = (user.settings or {}).get("categories", []) if user.settings else []
        if user_cats:
            normalized_user_cats = [_normalize_category(c) for c in user_cats]
            all_sources = (await session.execute(sources_query)).scalars().all()
            sources = [s for s in all_sources if s.category and _normalize_category(s.category) in normalized_user_cats]
            if not sources:
                sources = all_sources
        else:
            sources = (await session.execute(sources_query)).scalars().all()
        source_ids = {s.id for s in sources}
    _user_effective_source_ids[user.id] = source_ids
    return source_ids


async def _get_user_effective_source_ids_by_id(user_id: int) -> set[int]:
    """Reload user from DB and return effective source IDs."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            return set()
        return await _get_user_effective_source_ids(user)


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

        notif = Notification(
            user_id=user.id,
            message=f"Совпадение: {matched}",
            lead_id=lead.id,
            sent=True,
        )
        session.add(notif)
        await session.commit()
        logger.info("NEW_LEAD user=%s keyword=%s source=%s message_id=%s", user.id, matched, source.id, msg.id)
        logger.info("Lead saved: user=%s, source=%s, keyword=%s, msg_id=%s", user.id, source.id, matched, msg.id)
        return True


async def _historical_search_for_source(user: User, source: Source, client, bot: Bot):
    """Ищет сообщения за последние HISTORY_DAYS дней по всем ключевым словам
    пользователя в конкретном источнике (чате). Используется сразу после добавления чата."""
    effective_sources = await _get_user_effective_source_ids(user)
    if source.id not in effective_sources:
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
        entity = await client.get_entity(_normalize_chat_id(source.chat_id) or source.username)
    except Exception as e:
        logger.warning("Historical-source: cannot resolve source %s: %s", source.id, e)
        return

    if _is_bot_entity(entity):
        logger.info("HISTORICAL_SKIP_BOT source=%s title=%s", source.id, source.title)
        return

    search_semaphore = asyncio.Semaphore(3)

    async def _search_one(kw):
        local_found = 0
        local_saved = 0
        async with search_semaphore:
            try:
                result = await client(SearchRequest(
                    peer=entity,
                    q=kw.word,
                    filter=InputMessagesFilterEmpty(),
                    min_date=min_date,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=50,
                    max_id=0,
                    min_id=0,
                    hash=0,
                ))
                for msg in result.messages:
                    text = getattr(msg, "message", "") or ""
                    if not text:
                        continue
                    if _is_outgoing_message(msg):
                        continue
                    async with AsyncSessionLocal() as s2:
                        if await _has_stopword(text, user, s2):
                            continue
                    kw_words = kw.word.lower().split()
                    if not all(w in text.lower() for w in kw_words):
                        continue
                    if await _save_lead(user, source, entity, msg, text, kw.word, bot):
                        local_saved += 1
                        _update_last_checked_message_id(source.id, msg.id)
                    local_found += 1
            except Exception as e:
                logger.warning("Historical-source search error in %s for %s: %s", source.id, kw.word, e)
            return local_found, local_saved

    results = await asyncio.gather(*[_search_one(kw) for kw in keywords])
    for f, s in results:
        found += f
        saved += s

    logger.info("Historical search for user %s in source %s: found %s matches", user.id, source.id, found)


async def _historical_search_for_user(user: User, client, bot: Bot, keyword: str | None = None):
    """Ищет сообщения за последние HISTORY_DAYS дней по ключевым словам пользователя
    во всех активных источниках (Source), добавленных админом.

    Если передан keyword, ищутся только совпадения по этому слову.
    """
    logger.info("HIST_SEARCH_START user_db_id=%s telegram_id=%s keyword=%s", user.id, user.telegram_id, keyword)
    source_ids = await _get_user_effective_source_ids(user)
    async with AsyncSessionLocal() as session:
        sources = (await session.execute(
            select(Source).where(Source.id.in_(source_ids))
        )).scalars().all()
        keywords_query = select(Keyword).where(Keyword.user_id == user.id)
        if keyword:
            keywords_query = keywords_query.where(Keyword.word == keyword)
        keywords = (await session.execute(keywords_query)).scalars().all()
        kw_words = [kw.word for kw in keywords]
        logger.info("HIST_SEARCH user=%s sources_count=%s keywords_count=%s kw_list=%s", user.id, len(sources), len(keywords), kw_words[:10])

    if not sources or not keywords:
        logger.info("HIST_SEARCH_SKIP user=%s reason=%s", user.id, "no_sources" if not sources else "no_keywords")
        return

    min_date = datetime.datetime.utcnow() - datetime.timedelta(days=HISTORY_DAYS)
    found = 0
    saved = 0
    total_keywords = len(keywords)
    total_sources = len(sources)

    logger.info("HIST_SEARCH_STARTING user=%s sources=%s keywords=%s min_date=%s", user.id, total_sources, total_keywords, min_date.isoformat())

    try:
        await bot.send_message(user.telegram_id, f"🔍 Поиск: {total_sources} источников, {total_keywords} слов")
        logger.info("HIST_SEARCH_MSG_SENT user=%s msg=search_started", user.id)
    except Exception as e:
        logger.error("HIST_SEARCH_MSG_FAILED user=%s err=%s", user.id, e)

    search_sources = sources
    if keyword and len(sources) > QUICK_SEARCH_SOURCES_LIMIT:
        search_sources = sources[:QUICK_SEARCH_SOURCES_LIMIT]
        logger.info("HIST_SEARCH_QUICK user=%s keyword=%s limited_sources=%s", user.id, keyword, len(search_sources))

    async def _resolve(src):
        try:
            return await client.get_entity(_normalize_chat_id(src.chat_id) or src.username)
        except Exception as e:
            logger.warning("HIST_SEARCH_RESOLVE_FAIL user=%s source=%s err=%s", user.id, src.id, e)
            return None

    resolve_tasks = [_resolve(src) for src in search_sources]
    entities = await asyncio.gather(*resolve_tasks, return_exceptions=True)

    resolved = []
    for src, entity in zip(search_sources, entities):
        if entity is None or isinstance(entity, Exception):
            continue
        if _is_bot_entity(entity):
            logger.info("HISTORICAL_SKIP_BOT user=%s source=%s title=%s", user.id, src.id, src.title)
            continue
        logger.info("HISTORICAL_SOURCE user=%s source=%s chat_id=%s title=%s type=%s", user.id, src.id, src.chat_id, src.title, src.type)
        resolved.append((src, entity))

    failed_sources = [
        src.title or src.username or str(src.id)
        for src, entity in zip(search_sources, entities)
        if entity is None or isinstance(entity, Exception)
    ]
    if failed_sources:
        try:
            await bot.send_message(user.telegram_id, f"⚠️ Не удалось получить чаты: {', '.join(failed_sources)}")
        except Exception:
            pass

    search_semaphore = asyncio.Semaphore(3)
    found = 0
    saved = 0

    async def _search_one(src, entity, kw):
        if entity is None or isinstance(entity, Exception):
            return 0, 0
        async with search_semaphore:
            try:
                result = await client(SearchRequest(
                    peer=entity,
                    q=kw.word,
                    filter=InputMessagesFilterEmpty(),
                    min_date=min_date,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=50,
                    max_id=0,
                    min_id=0,
                    hash=0,
                ))
                local_found = 0
                local_saved = 0
                logger.info("HIST_SEARCH_KW_RESULT user=%s source=%s kw=%s found_msgs=%s", user.id, src.id, kw.word, len(result.messages))
                for msg in result.messages:
                    text = getattr(msg, "message", "") or ""
                    if not text:
                        continue
                    if _is_outgoing_message(msg):
                        continue
                    async with AsyncSessionLocal() as s2:
                        if await _has_stopword(text, user, s2):
                            logger.info("HIST_SEARCH_STOPWORD user=%s source=%s msg_id=%s", user.id, src.id, msg.id)
                            continue
                    kw_words = kw.word.lower().split()
                    if not all(w in text.lower() for w in kw_words):
                        logger.info("HIST_SEARCH_KW_MISMATCH user=%s source=%s msg_id=%s kw=%s", user.id, src.id, msg.id, kw.word)
                        continue
                    saved_successfully = await _save_lead(user, src, entity, msg, text, kw.word, bot, is_historical_search=True)
                    if saved_successfully:
                        local_saved += 1
                        _update_last_checked_message_id(src.id, msg.id)
                    local_found += 1
                    logger.info("HIST_SEARCH_LEAD user=%s source=%s kw=%s msg_id=%s saved=%s", user.id, src.id, kw.word, msg.id, saved_successfully)
                return local_found, local_saved
            except Exception as e:
                logger.warning("HIST_SEARCH_KW_ERROR user=%s source=%s kw=%s err=%s", user.id, src.id, kw.word, e)
                return 0, 0

    search_tasks = []
    for src, entity in resolved:
        for kw in keywords:
            search_tasks.append(_search_one(src, entity, kw))

    results = await asyncio.gather(*search_tasks)
    for f, s in results:
        found += f
        saved += s

    user_cats = (user.settings or {}).get("categories", []) if user.settings else []
    if saved == 0 and user_cats:
        async with AsyncSessionLocal() as session:
            all_sources = (await session.execute(
                select(Source).where(Source.status == "active")
            )).scalars().all()
        remaining_ids = {s.id for s in all_sources} - {s.id for s in sources}
        if remaining_ids:
            async with AsyncSessionLocal() as session:
                remaining_sources = (await session.execute(
                    select(Source).where(Source.id.in_(remaining_ids))
                )).scalars().all()
            logger.info("HIST_SEARCH_FALLBACK user=%s remaining_sources=%d", user.id, len(remaining_sources))
            unresolved = {src.id for src in remaining_sources if src not in {r[0] for r in resolved}}
            additional = []
            for src in remaining_sources:
                if src.id in unresolved:
                    entity = await _resolve(src)
                    if entity is None or isinstance(entity, Exception):
                        failed_sources.append(src.title or src.username or str(src.id))
                        continue
                    if _is_bot_entity(entity):
                        logger.info("HISTORICAL_SKIP_BOT user=%s source=%s title=%s", user.id, src.id, src.title)
                        continue
                    logger.info("HISTORICAL_SOURCE user=%s source=%s chat_id=%s title=%s type=%s", user.id, src.id, src.chat_id, src.title, src.type)
                    additional.append((src, entity))
            search_tasks = []
            for src, entity in additional:
                for kw in keywords:
                    search_tasks.append(_search_one(src, entity, kw))
            results = await asyncio.gather(*search_tasks)
            for f, s in results:
                found += f
                saved += s

    logger.info("HIST_SEARCH_END user=%s found=%s saved=%s", user.id, found, saved)
    if found > 0 or saved > 0:
        try:
            await bot.send_message(user.telegram_id, f"✅ Поиск завершен.\nНайдено совпадений: {found}\nНовых лидов: {saved}\nПосмотреть: /results")
            logger.info("HIST_SEARCH_MSG_SENT user=%s msg=search_complete", user.id)
        except Exception as e:
            logger.error("HIST_SEARCH_MSG_FAILED user=%s err=%s", user.id, e)


async def _monitor_new_messages(client, user_id: int, bot: Bot):
    """Постоянный мониторинг новых сообщений для конкретного пользователя."""
    monitor_logger = logging.getLogger("app.parser.monitor")
    monitor_logger.setLevel(logging.INFO)
    first_user = await _get_user_effective_source_ids_by_id(user_id)
    monitor_logger.info("Monitor started for user_id=%s sources=%d", user_id, len(first_user))

    async def handler(event):
        try:
            text = getattr(event.message, "message", None) or getattr(event.message, "text", None) or ""
            if not text:
                return

            if _is_outgoing_message(event.message):
                return

            chat = await event.get_chat()
            chat_id = getattr(chat, "id", None)
            if chat_id is None:
                return

            if _is_bot_entity(chat):
                monitor_logger.info("Monitor skip bot chat chat_id=%s", chat_id)
                return

            chat_username = getattr(chat, "username", None)
            chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", None)
            normalized_chat_id = chat_id
            if isinstance(normalized_chat_id, int) and normalized_chat_id < 0 and str(abs(normalized_chat_id)).startswith("100"):
                normalized_chat_id = int(str(abs(normalized_chat_id))[3:])
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
                    chat_type = _detect_chat_type(chat)
                    category = auto_category(chat_title, chat_username)
                    source = Source(
                        type=chat_type,
                        username=chat_username,
                        chat_id=int(normalized_chat_id),
                        title=chat_title,
                        category=category,
                        status="active",
                    )
                    session.add(source)
                    await session.flush()
                    invalidate_all_user_caches()
                    try:
                        msgs = await client.get_messages(chat, limit=1)
                        if msgs:
                            source.last_checked_message_id = msgs[0].id
                        else:
                            source.last_checked_message_id = event.message.id
                    except Exception:
                        source.last_checked_message_id = event.message.id
                    await session.commit()
                    monitor_logger.info("Monitor auto-created source id=%s title=%s category=%s", source.id, source.title, category)

                monitor_logger.info("PARSER_SOURCE chat_id=%s title=%s type=%s source_id=%s", normalized_chat_id, source.title, source.type, source.id)

                fresh_user = await session.get(User, user_id)
                if fresh_user is None:
                    monitor_logger.warning("Monitor: user %s not found, skipping", user_id)
                    return

                effective_sources = await _get_user_effective_source_ids_by_id(user_id)
                if source.id not in effective_sources:
                    monitor_logger.info("Monitor skip source=%s not in effective sources", source.id)
                    return

                async with AsyncSessionLocal() as s2:
                    matched = await _match_keywords(text, fresh_user, s2)
                    if not matched:
                        _update_last_checked_message_id(source.id, event.message.id)
                        return
                    if await _has_stopword(text, fresh_user, s2):
                        _update_last_checked_message_id(source.id, event.message.id)
                        return
            monitor_logger.info("NEW_LEAD user=%s keyword=%s source=%s message_id=%s", user_id, matched, source.id, event.message.id)
            monitor_logger.info("Monitor matched keyword '%s' for user %s in source %s", matched, user_id, source.id)
            saved = await _save_lead(fresh_user, source, chat, event.message, text, matched, bot)
            _update_last_checked_message_id(source.id, event.message.id)
            print(f"REALTIME_DEBUG after_save_lead user={user_id} saved={saved}", flush=True)
            monitor_logger.info("DEBUG: after _save_lead saved=%s", saved)
            if saved:
                s = fresh_user.settings or {}
                monitor_logger.info("DEBUG: notifications setting=%s", s.get("notifications", True))
                if s.get("notifications", True):
                    monitor_logger.info("DEBUG: sending notification to user %s", fresh_user.telegram_id)
                    try:
                        await _add_lead_to_batch(fresh_user.telegram_id, bot)
                        logger.info("REALTIME_NOTIFY user=%s keyword=%s", fresh_user.telegram_id, matched)
                        await bot.send_message(
                            fresh_user.telegram_id,
                            f"🔎 Новый лид: {matched}\nПосмотреть в /results",
                        )
                        monitor_logger.info("LEAD_NOTIFICATION_SENT user=%s keyword=%s", fresh_user.telegram_id, matched)
                    except Exception as e:
                        monitor_logger.error("LEAD_NOTIFICATION_ERROR user=%s error=%s", fresh_user.telegram_id, e)
        except Exception:
            monitor_logger.exception("Monitor handler error")

    client.add_event_handler(handler, events.NewMessage)


async def main(bot=None):
    if bot is None:
        from config import settings
        from aiogram import Bot
        bot = Bot(token=settings.bot_token)
    manager = TelethonClientManager()
    sessions = manager.list_all_sessions()
    if not sessions:
        logger.warning("No session files found. Parsing disabled.")
        return

    clients = []
    client_users = []  # list of User objects matched to each client
    for session_name in sessions:
        try:
            client = await manager.connect(session_name)
            me = await client.get_me()
            tg_id = getattr(me, "id", None)
            logger.info("Connected Telethon as %s (id=%s, session=%s)", getattr(me, "username", "?"), tg_id, session_name)
            matched_user = None
            if tg_id is not None:
                async with AsyncSessionLocal() as session:
                    matched_user = (await session.execute(
                        select(User).where(User.telegram_id == int(tg_id))
                    )).scalar_one_or_none()
            if matched_user:
                logger.info("Session %s matched to user id=%s telegram_id=%s", session_name, matched_user.id, matched_user.telegram_id)
            else:
                logger.warning("Session %s (telegram_id=%s) is not linked to any user in DB", session_name, tg_id)
            clients.append(client)
            client_users.append(matched_user)
        except Exception as e:
            logger.exception("Failed to connect session %s: %s", session_name, e)

    if not clients:
        logger.warning("No Telethon clients connected. Parsing disabled.")
        return

    # Проверка участия в активных источниках для всех клиентов
    # Автодобавление доступных Telegram-чатов в систему источников
    try:
        async with AsyncSessionLocal() as session:
            existing_sources = (await session.execute(
                select(Source).where(Source.status == "active")
            )).scalars().all()
            existing_ids = {int(s.chat_id) for s in existing_sources if s.chat_id is not None}
            existing_usernames = {str(s.username).lower() for s in existing_sources if s.username}

            for idx, cl in enumerate(clients):
                try:
                    added_for_client = 0
                    skipped_bot = 0
                    skipped_dup = 0
                    skipped_me = 0
                    type_counts = {"total": 0, "groups": 0, "supergroups": 0, "channels": 0, "private": 0, "other": 0}
                    async for dialog in cl.iter_dialogs():
                        chat = dialog.entity
                        type_counts["total"] += 1
                        chat_id = getattr(chat, "id", None)
                        if chat_id is None:
                            continue
                        if _is_bot_entity(chat):
                            skipped_bot += 1
                            continue
                        if matched_user and getattr(chat, "id", None) == getattr(me, "id", None):
                            skipped_me += 1
                            continue
                        normalized_chat_id = _normalize_chat_id(int(chat_id))
                        username = getattr(chat, "username", None)
                        title = getattr(chat, "title", None) or getattr(chat, "first_name", None)
                        normalized_username = username.lower() if username else None
                        chat_type = _detect_chat_type(chat)
                        if chat_type == "group":
                            from telethon.tl.types import Channel
                            if isinstance(chat, Channel):
                                type_counts["supergroups"] += 1
                            else:
                                type_counts["groups"] += 1
                        elif chat_type == "channel":
                            type_counts["channels"] += 1
                        elif chat_type == "private":
                            type_counts["private"] += 1
                        else:
                            type_counts["other"] += 1

                        if normalized_chat_id in existing_ids or (normalized_username and normalized_username in existing_usernames):
                            skipped_dup += 1
                            continue

                        category = auto_category(title, username)
                        src = Source(
                            type=chat_type,
                            username=username,
                            chat_id=normalized_chat_id,
                            title=title,
                            category=category,
                            status="active",
                        )
                        session.add(src)
                        await session.flush()
                        existing_ids.add(normalized_chat_id)
                        if normalized_username:
                            existing_usernames.add(normalized_username)
                        added_for_client += 1
                        logger.info("Auto-added source id=%s title=%s category=%s", src.id, src.title, category)
                    if added_for_client:
                        await session.commit()
                        invalidate_all_user_caches()
                        logger.info("Auto-added %d sources for client %d", added_for_client, idx)
                    logger.info(
                        "DIALOG_STATS client=%d total=%d groups=%d supergroups=%d channels=%d private=%d other=%d skipped_bot=%d skipped_me=%d skipped_dup=%d",
                        idx, type_counts["total"], type_counts["groups"], type_counts["supergroups"],
                        type_counts["channels"], type_counts["private"], type_counts["other"],
                        skipped_bot, skipped_me, skipped_dup
                    )
                except Exception:
                    logger.exception("Auto-source discovery failed for client %d", idx)
    except Exception:
        logger.exception("Startup auto-source discovery failed")

    # Проверка участия в активных источниках для всех клиентов
    try:
        async with AsyncSessionLocal() as session:
            sources = (await session.execute(
                select(Source).where(Source.status == "active")
            )).scalars().all()
            for idx, cl in enumerate(clients):
                missing = []
                for src in sources:
                    try:
                        entity = await cl.get_entity(_normalize_chat_id(src.chat_id) or src.username)
                        if not getattr(entity, "id", None):
                            missing.append(f"{src.id}:{src.title or src.username}")
                    except Exception:
                        missing.append(f"{src.id}:{src.title or src.username}")
                if missing:
                    logger.warning("Parser account %d is NOT in these sources: %s", idx + 1, ", ".join(missing))
                else:
                    logger.info("Parser account %d is in all %d active sources", idx + 1, len(sources))
    except Exception:
        logger.exception("Startup source membership check failed")

    from app.database.backup import restore_postgres_if_empty, start_periodic_backup
    restored = await restore_postgres_if_empty()
    if restored:
        logger.info("Database restored from backup")

    asyncio.create_task(start_periodic_backup(interval_seconds=3600))
    logger.info("Periodic PostgreSQL backup started (every 1h)")

    # Build cache of effective source IDs for all users
    async with AsyncSessionLocal() as session:
        users = (await session.execute(
            select(User)
        )).scalars().all()
        for user in users:
            source_ids = await _get_user_effective_source_ids(user)
            logger.info("User %s effective sources: %d", user.id, len(source_ids))

    # Постоянный мониторинг новых сообщений: все пользователи через доступные клиенты, без фильтра по подписке
    for user in users:
        client_idx = None
        for i, u in enumerate(client_users):
            if u is not None and u.id == user.id:
                client_idx = i
                break
        if client_idx is not None:
            asyncio.create_task(_monitor_new_messages(clients[client_idx], user.id, bot))
            logger.info("New-message monitoring started for user=%s telegram_id=%s session_idx=%s", user.id, user.telegram_id, client_idx)
        else:
            asyncio.create_task(_monitor_new_messages(clients[0], user.id, bot))
            logger.info("New-message monitoring started for user=%s telegram_id=%s fallback_session_idx=0", user.id, user.telegram_id)

    asyncio.create_task(_periodic_reminders(bot))
    logger.info("Periodic setup reminders started (every 1h)")

    asyncio.create_task(_periodic_polling(clients, client_users, bot))
    logger.info("Periodic new-message polling started (every 1m)")

    for idx, cl in enumerate(clients):
        asyncio.create_task(cl.run_until_disconnected())
        logger.info("Telethon client %s started event loop", idx)

    try:
        while True:
            await asyncio.sleep(60)
    finally:
        for cl in clients:
            try:
                await cl.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    from config import settings
    from aiogram import Bot
    asyncio.run(main(Bot(token=settings.bot_token)))
