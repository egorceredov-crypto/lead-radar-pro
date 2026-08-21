import asyncio
import datetime
import logging
import time
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


async def _add_lead_to_batch(user_id: int, bot: Bot):
    """Добавляет лид в пакет для пользователя. Отправляет сводку, если прошло достаточно времени."""
    async with _lead_batch_lock:
        if user_id not in _lead_batches:
            _lead_batches[user_id] = {"count": 0, "last_sent": 0.0}
        _lead_batches[user_id]["count"] += 1
        batch = _lead_batches[user_id]
        now = time.monotonic()
        if batch["count"] >= _LEAD_BATCH_MIN_COUNT and (now - batch["last_sent"]) >= _LEAD_BATCH_DELAY:
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
        logger.info("Sent lead batch notification to user %s: %d leads", user_id, count)
    except Exception as e:
        logger.error("Failed to send lead batch notification to user %s: %s", user_id, e)

def _normalize_chat_id(chat_id: int) -> int:
    if isinstance(chat_id, int) and chat_id < 0 and str(abs(chat_id)).startswith("100"):
        return int(str(abs(chat_id))[3:])
    return chat_id


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
    Учитывает выбранные категории. Если категории не совпадают ни с одним источником — возвращает пустой set."""
    if user.id in _user_effective_source_ids:
        return _user_effective_source_ids[user.id]
    async with AsyncSessionLocal() as session:
        sources_query = select(Source).where(Source.status == "active")
        user_cats = (user.settings or {}).get("categories", []) if user.settings else []
        if user_cats:
            normalized_user_cats = [_normalize_category(c) for c in user_cats]
            all_sources = (await session.execute(sources_query)).scalars().all()
            sources = [s for s in all_sources if s.category and _normalize_category(s.category) in normalized_user_cats]
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
                    async with AsyncSessionLocal() as s2:
                        if await _has_stopword(text, user, s2):
                            continue
                    kw_words = kw.word.lower().split()
                    if not all(w in text.lower() for w in kw_words):
                        continue
                    if await _save_lead(user, source, entity, msg, text, kw.word, bot):
                        local_saved += 1
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
        try:
            await bot.send_message(user.telegram_id, "ℹ️ Нет активных источников или ключевых слов для поиска.")
            logger.info("HIST_SEARCH_MSG_SENT user=%s msg=no_sources_keywords", user.id)
        except Exception as e:
            logger.error("HIST_SEARCH_MSG_FAILED user=%s err=%s", user.id, e)
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

    failed_sources = []
    for src, entity in zip(search_sources, entities):
        if entity is None or isinstance(entity, Exception):
            failed_sources.append(src.title or src.username or str(src.id))

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
                    local_found += 1
                    logger.info("HIST_SEARCH_LEAD user=%s source=%s kw=%s msg_id=%s saved=%s", user.id, src.id, kw.word, msg.id, saved_successfully)
                return local_found, local_saved
            except Exception as e:
                logger.warning("HIST_SEARCH_KW_ERROR user=%s source=%s kw=%s err=%s", user.id, src.id, kw.word, e)
                return 0, 0

    search_tasks = []
    for src, entity in zip(search_sources, entities):
        if entity is None or isinstance(entity, Exception):
            continue
        for kw in keywords:
            search_tasks.append(_search_one(src, entity, kw))

    results = await asyncio.gather(*search_tasks)
    for f, s in results:
        found += f
        saved += s

    logger.info("HIST_SEARCH_END user=%s found=%s saved=%s", user.id, found, saved)
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
    effective_sources = first_user

    async def handler(event):
        try:
            text = getattr(event.message, "message", None) or getattr(event.message, "text", None) or ""
            if not text:
                monitor_logger.debug("Monitor: empty text, skipping")
                return

            chat = await event.get_chat()
            chat_id = getattr(chat, "id", None)
            if chat_id is None:
                monitor_logger.debug("Monitor: no chat_id, skipping")
                return

            monitor_logger.debug("Monitor: new message in chat_id=%s text=%s", chat_id, text[:100])

            chat_username = getattr(chat, "username", None)
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
                    monitor_logger.debug("Monitor: no active source for chat_id=%s username=%s, skipping", chat_id, chat_username)
                    return

                fresh_user = await session.get(User, user_id)
                if fresh_user is None:
                    monitor_logger.warning("Monitor: user %s not found, skipping", user_id)
                    return
                effective_sources = await _get_user_effective_source_ids(fresh_user)
                if source.id not in effective_sources:
                    monitor_logger.debug("Monitor: source %s not in user %s effective sources, skipping", source.id, user_id)
                    return

                async with AsyncSessionLocal() as s2:
                    matched = await _match_keywords(text, fresh_user, s2)
                    if not matched:
                        monitor_logger.debug("Monitor: no keyword match for user %s in source %s", user_id, source.id)
                        return
                    if await _has_stopword(text, fresh_user, s2):
                        monitor_logger.debug("Stopword matched for user %s in chat %s", user_id, chat_id)
                        return
                monitor_logger.info("Monitor matched keyword '%s' for user %s in source %s", matched, user_id, source.id)
                saved = await _save_lead(fresh_user, source, chat, event.message, text, matched, bot)
                if saved:
                    s = fresh_user.settings or {}
                    if s.get("notifications", True):
                        await _add_lead_to_batch(fresh_user.telegram_id, bot)
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
    try:
        async with AsyncSessionLocal() as session:
            sources = (await session.execute(
                select(Source).where(Source.status == "active")
            )).scalars().all()
            for idx, cl in enumerate(clients):
                missing = []
                for src in sources:
                    try:
                        entity = await cl.get_entity(src.chat_id or src.username)
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
                    client_idx = None
                    for i, u in enumerate(client_users):
                        if u is not None and u.id == user.id:
                            client_idx = i
                            break
                    if client_idx is not None:
                        await _historical_search_for_user(user, clients[client_idx], bot)
                    else:
                        await _historical_search_for_user(user, clients[0], bot)
                    await asyncio.sleep(0.1)
            except Exception:
                logger.exception("Periodic historical search error")
            await asyncio.sleep(3600)  # раз в час

    asyncio.create_task(periodic_historical())
    logger.info("Periodic historical search started (every 1h)")

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
