import asyncio
import datetime
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, delete

from app.database.session import AsyncSessionLocal
from app.database.models import (
    User, Keyword, StopWord, Lead, Referral, RadarState,
    Subscription, Source,
)
from app.bot.keyboards import (
    main_reply_kb, admin_reply_kb, remove_reply_kb,
    back_kb, cancel_kb,
    user_categories_kb, results_kb,
    categories_menu_kb, profile_kb, referral_kb, subscription_kb,
    settings_kb, notifications_kb, help_kb,
    keywords_menu_kb, keyword_actions_kb, keyword_delete_kb,
    stopwords_menu_kb, stopword_actions_kb, stopword_delete_kb,
    admin_kb, admin_users_kb, admin_user_kb,
)
from app.bot.texts import (
    WELCOME,
    RESULTS_TITLE, NO_LEADS, LEAD_CARD,
    CATEGORIES_TEXT, NO_CATEGORIES, CATEGORY_ADDED, CATEGORY_DELETED, CATEGORY_EDITED,
    PROFILE_TEXT, REFERRAL_TEXT, TARIFFS_TEXT, SETTINGS_TEXT, SETTINGS_TZ, SETTINGS_TZ_SAVED,
    NOTIFICATIONS_TEXT, NOTIFICATIONS_ON, NOTIFICATIONS_OFF, NOTIFICATIONS_TEST,
    HELP_TEXT, FAQ, ADMIN_PANEL, ADMIN_STATS,
    BROADCAST_PROMPT, BROADCAST_STARTED, BROADCAST_DONE,
    KEYWORDS_TITLE, NO_KEYWORDS, WORD_ADDED, WORD_EDITED, WORD_DELETED,
    KEYWORDS_IMPORTED, KEYWORDS_EXPORTED,
    STOPWORDS_TITLE, NO_STOPWORDS, STOPWORD_ADDED, STOPWORD_EDITED, STOPWORD_DELETED,
    STATS_TEXT, NO_STATS,
)
from app.services.users import (
    get_or_create_user, get_user, get_user_tariff_name, get_trial_end_text,
    get_keyword_limit, get_remaining_days, activate_subscription,
    get_tariff_keyword_limit, auto_category,
)
from app.services.stats import get_user_stats
from app.parser.telethon_client import TelethonClientManager
from config import settings

logger = logging.getLogger(__name__)
router = Router()

# Состояния ожидания ввода: {telegram_id: {"action": ...}}
WAITING = {}
# Режим выбора категорий через /search: {telegram_id: bool}
SEARCH_MODE = {}

# Тексты reply-кнопок (должны совпадать с app/bot/keyboards.py)
BTN_SEARCH = "Поиск"
BTN_RESULTS = "Результаты"
BTN_KEYWORDS = "Слова"
BTN_STATS = "Статистика"
BTN_HELP = "Помощь"
BTN_PROFILE = "Профиль"
BTN_ADMIN = "Админ панель"


def is_admin(user_id: int) -> bool:
    if settings.admin_id is not None and int(user_id) == int(settings.admin_id):
        return True
    if settings.admin_ids:
        return int(user_id) in [int(x.strip()) for x in settings.admin_ids.split(",") if x.strip()]
    return False


async def _get_user(session, tg_id: int):
    return await get_user(session, tg_id)


def _reply_kb(user_id: int):
    return admin_reply_kb() if is_admin(user_id) else main_reply_kb()


def _detect_chat_type(entity) -> str:
    """Определяет тип чата по entity."""
    from telethon.tl.types import (
        Channel, Chat, User as TLUser,
    )
    if isinstance(entity, TLUser):
        return "private"
    if isinstance(entity, Channel):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    if isinstance(entity, Chat):
        return "group"
    return "chat"


# Кэш username бота для реферальной ссылки
_BOT_USERNAME = None


async def _get_bot_username() -> str:
    """Возвращает username бота (через get_me)."""
    global _BOT_USERNAME
    if _BOT_USERNAME:
        return _BOT_USERNAME
    try:
        from aiogram import Bot
        bot = Bot(token=settings.bot_token)
        me = await bot.get_me()
        _BOT_USERNAME = me.username
        await bot.session.close()
    except Exception:
        # Fallback: на случай, если не удалось получить username
        _BOT_USERNAME = settings.bot_token.split(':')[0]
    return _BOT_USERNAME


async def _get_referral_link(telegram_id: int) -> str:
    """Возвращает текст с реферальной ссылкой пользователя."""
    import secrets
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, telegram_id)
        ref = (await session.execute(select(Referral).where(Referral.owner_id == user.id))).scalar_one_or_none()
        if not ref:
            ref = Referral(owner_id=user.id, code=secrets.token_hex(4))
            session.add(ref)
            await session.commit()
        username = await _get_bot_username()
        link = f"https://t.me/{username}?start={ref.code}"
        text = REFERRAL_TEXT.format(link=link, registrations=ref.registrations, bonus=ref.bonus)
    return text


# ============ СТАРТ ============

@router.message(F.text.regexp(r"^/start(\s+@\w+)?\s*$"))
async def cmd_start(message: Message):
    ref_code = None
    if message.text and len(message.text.split()) > 1:
        ref_code = message.text.split()[1]

    logger.info("Processing /start for user %s", message.from_user.id)
    logger.info("User info: username=%s, first_name=%s, last_name=%s", 
                message.from_user.username, 
                message.from_user.first_name, 
                message.from_user.last_name)

    try:
        async with AsyncSessionLocal() as session:
            await get_or_create_user(
                session,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
                message.from_user.last_name,
                ref_code,
            )
        kb = _reply_kb(message.from_user.id)
        logger.info("Reply keyboard created for %s", message.from_user.id)
        await message.answer(WELCOME, reply_markup=kb, parse_mode="HTML")
        logger.info("Successfully sent /start response to %s", message.from_user.id)
    except Exception as e:
        logger.exception("Failed to process /start for %s: %s", message.from_user.id, e)
        try:
            await message.answer("Произошла ошибка при запуске. Попробуйте позже.")
        except Exception as e2:
            logger.exception("Failed to send error message to %s: %s", message.from_user.id, e2)


# ============ REPLY-КНОПКИ (нижняя навигация) ============

@router.message(F.text == BTN_SEARCH)
async def reply_search(message: Message):
    await _open_category_search(message.from_user.id, message)


async def _open_category_search(user_id: int, message: Message):
    SEARCH_MODE[user_id] = True
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, user_id)
        cats = (user.settings or {}).get("categories", []) if user.settings else []
    text = (
        "<b>Категории</b>\n\n"
        "Нажимай на категории, чтобы выбрать/убрать.\n"
        "Выбрано: " + (", ".join(cats) if cats else "все") + "\n\n"
        "Когда готов — жми «Сохранить выбор»"
    )
    await message.answer(
        text,
        reply_markup=user_categories_kb(cats),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_RESULTS)
async def reply_results(message: Message):
    await _show_results(message)


async def _show_results(message: Message):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, message.from_user.id)
        leads = (await session.execute(
            select(Lead).where(Lead.user_id == user.id).order_by(Lead.created_at.desc())
        )).scalars().all()

        total = (await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id)
        )).scalar_one()

        if not leads:
            await message.answer(NO_LEADS, parse_mode="HTML")
            return

        lead_ids = [l.id for l in leads]
        WAITING[message.from_user.id] = {
            "action": "results_view",
            "lead_ids": lead_ids,
            "index": 0,
            "total": total,
        }
        header = f"Всего лидов: {total}\n\n"
        await _show_lead_card(message, leads[0], 0, len(leads), header=header)


async def _show_lead_card(message: Message, lead: Lead, index: int, total: int, header: str = ""):
    date = lead.lead_date.strftime("%d.%m %H:%M") if lead.lead_date else ""
    text = (header or "") + LEAD_CARD.format(
        matched=lead.matched_keyword or "—",
        chat_title=lead.chat_title or "Источник",
        sender=lead.sender_username or "—",
        date=date,
        text=(lead.text or "")[:2000],
    )
    kb = InlineKeyboardBuilder()
    if lead.link:
        kb.button(text="Открыть", url=lead.link)
    kb.button(text="◀️ Назад", callback_data="results:prev")
    kb.button(text=f"{index + 1}/{total} — нажмите, чтобы выбрать", callback_data="results:pick")
    kb.button(text="Следующий ▶️", callback_data="results:next")
    kb.adjust(2, 1, 2)
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "results:prev")
async def cb_results_prev(cb: CallbackQuery):
    state = WAITING.get(cb.from_user.id)
    if not state or state.get("action") != "results_view":
        await cb.answer("Сначала откройте Результаты")
        return
    ids = state["lead_ids"]
    total = state.get("total", len(ids))
    idx = state["index"] - 1
    if idx < 0:
        idx = len(ids) - 1
    state["index"] = idx
    async with AsyncSessionLocal() as session:
        lead = await session.get(Lead, ids[idx])
    if lead:
        header = f"Всего лидов: {total}\n\n"
        await cb.message.edit_text(header + LEAD_CARD.format(
            matched=lead.matched_keyword or "—",
            chat_title=lead.chat_title or "Источник",
            sender=lead.sender_username or "—",
            date=lead.lead_date.strftime("%d.%m %H:%M") if lead.lead_date else "",
            text=(lead.text or "")[:2000],
        ),
                                   reply_markup=_results_kb(idx, total, lead.link, lead.id), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "results:next")
async def cb_results_next(cb: CallbackQuery):
    state = WAITING.get(cb.from_user.id)
    if not state or state.get("action") != "results_view":
        await cb.answer("Сначала откройте Результаты")
        return
    ids = state["lead_ids"]
    total = state.get("total", len(ids))
    idx = state["index"] + 1
    if idx >= len(ids):
        idx = 0
    state["index"] = idx
    async with AsyncSessionLocal() as session:
        lead = await session.get(Lead, ids[idx])
    if lead:
        header = f"Всего лидов: {total}\n\n"
        await cb.message.edit_text(header + LEAD_CARD.format(
            matched=lead.matched_keyword or "—",
            chat_title=lead.chat_title or "Источник",
            sender=lead.sender_username or "—",
            date=lead.lead_date.strftime("%d.%m %H:%M") if lead.lead_date else "",
            text=(lead.text or "")[:2000],
        ),
                                   reply_markup=_results_kb(idx, total, lead.link, lead.id), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "results:pick")
async def cb_results_pick(cb: CallbackQuery):
    state = WAITING.get(cb.from_user.id)
    if not state or state.get("action") != "results_view":
        await cb.answer("Сначала откройте Результаты")
        return
    total = state.get("total", len(state.get("lead_ids", [])))
    WAITING[cb.from_user.id] = {"action": "results_pick", "lead_ids": state["lead_ids"], "total": total}
    await cb.message.edit_text(f"Введите номер лида (1–{total}):", reply_markup=cancel_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("results:delete:"))
async def cb_results_delete(cb: CallbackQuery):
    state = WAITING.get(cb.from_user.id)
    if not state or state.get("action") != "results_view":
        await cb.answer("Сначала откройте Результаты")
        return
    lead_id = int(cb.data.split(":", 2)[2])
    async with AsyncSessionLocal() as session:
        lead = await session.get(Lead, lead_id)
        if not lead:
            await cb.answer("Лид не найден")
            return
        db_user = (await session.execute(
            select(User).where(User.telegram_id == cb.from_user.id)
        )).scalar_one_or_none()
        if not db_user or lead.user_id != db_user.id:
            await cb.answer("Лид не найден")
            return
        await session.delete(lead)
        await session.commit()
        total = (await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == db_user.id)
        )).scalar_one()
        state["total"] = total
        await cb.answer("Лид удалён")
    ids = state["lead_ids"]
    ids.remove(lead_id)
    if not ids:
        WAITING.pop(cb.from_user.id, None)
        await cb.message.edit_text("Нет результатов.", parse_mode="HTML")
        return
    idx = state["index"]
    if idx >= len(ids):
        idx = 0
    state["index"] = idx
    total = state.get("total", len(ids))
    async with AsyncSessionLocal() as session:
        new_lead = await session.get(Lead, ids[idx])
    if new_lead:
        header = f"Всего лидов: {total}\n\n"
        await cb.message.edit_text(header + LEAD_CARD.format(
            matched=new_lead.matched_keyword or "—",
            chat_title=new_lead.chat_title or "Источник",
            sender=new_lead.sender_username or "—",
            date=new_lead.lead_date.strftime("%d.%m %H:%M") if new_lead.lead_date else "",
            text=(new_lead.text or "")[:2000],
        ),
                                   reply_markup=_results_kb(idx, total, new_lead.link, new_lead.id), parse_mode="HTML")


def _results_kb(index: int, total: int, link: str | None = None, lead_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if link:
        kb.button(text="Открыть", url=link)
    kb.button(text="◀️ Назад", callback_data="results:prev")
    kb.button(text=f"{index + 1}/{total} — нажмите, чтобы выбрать", callback_data="results:pick")
    kb.button(text="Следующий ▶️", callback_data="results:next")
    if lead_id:
        kb.button(text="🗑 Удалить", callback_data=f"results:delete:{lead_id}")
    kb.adjust(2, 1, 2)
    return kb.as_markup()


@router.message(F.text == BTN_KEYWORDS)
async def reply_keywords(message: Message):
    await message.answer("<b>Слова</b>\n\nУправление словами для поиска.",
                         reply_markup=keywords_menu_kb(), parse_mode="HTML")


@router.message(F.text == BTN_STATS)
async def reply_stats(message: Message):
    await _show_stats(message)


async def _show_stats(message: Message):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, message.from_user.id)
        stats = await get_user_stats(session, user)
        if stats["total"] == 0:
            await message.answer(NO_STATS, reply_markup=back_kb("home"), parse_mode="HTML")
            return
        text = STATS_TEXT.format(
            today=stats["today"], yesterday=stats["yesterday"],
            week=stats["week"], month=stats["month"], total=stats["total"],
            active_chats=stats["active_chats"], keywords=stats["keywords"],
            stopwords=stats["stopwords"], notifications=stats["notifications"],
            processed_today=stats["processed_today"],
        )
    await message.answer(text, reply_markup=back_kb("home"), parse_mode="HTML")


@router.message(F.text == BTN_HELP)
async def reply_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=help_kb(), parse_mode="HTML")


@router.message(F.text == BTN_PROFILE)
async def reply_profile(message: Message):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, message.from_user.id)
        leads_count = (await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id)
        )).scalar_one()
        ref = (await session.execute(select(Referral).where(Referral.owner_id == user.id))).scalar_one_or_none()
        referrals = ref.registrations if ref else 0
        kw_limit = get_keyword_limit(user)
        kw_used = (await session.execute(
            select(func.count()).select_from(Keyword).where(Keyword.user_id == user.id)
        )).scalar_one()
        kw_remaining = max(0, kw_limit - kw_used)

        text = PROFILE_TEXT.format(
            telegram_id=user.telegram_id,
            username=user.username or "—",
            reg_date=user.registration_date.strftime("%d.%m.%Y") if user.registration_date else "—",
            tariff=get_user_tariff_name(user),
            days_left=get_remaining_days(user),
            keywords=kw_remaining,
            leads_count=leads_count,
            referrals=referrals,
        )
    await message.answer(text, reply_markup=profile_kb(), parse_mode="HTML")


@router.message(F.text == BTN_ADMIN)
async def reply_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещён")
        return
    await message.answer(ADMIN_PANEL, reply_markup=admin_kb(), parse_mode="HTML")


# ============ ПОИСК: callback-шаги ============

@router.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery):
    WAITING.pop(cb.from_user.id, None)
    SEARCH_MODE.pop(cb.from_user.id, None)
    await cb.message.answer(WELCOME, reply_markup=_reply_kb(cb.from_user.id), parse_mode="HTML")


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery):
    WAITING.pop(cb.from_user.id, None)
    SEARCH_MODE.pop(cb.from_user.id, None)
    await cb.answer("Отменено")
    await cb.message.answer(WELCOME, reply_markup=_reply_kb(cb.from_user.id), parse_mode="HTML")


@router.callback_query(F.data.startswith("cat:toggle:"))
async def cb_cat_toggle(cb: CallbackQuery):
    category = cb.data.split(":", 2)[2]
    logger.info("CAT_TOGGLE: user=%s category=%s", cb.from_user.id, category)
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        user.settings = user.settings or {}
        cats = list(user.settings.get("categories", []))
        if category in cats:
            cats.remove(category)
        else:
            cats.append(category)
        user.settings = dict(user.settings)
        user.settings["categories"] = cats
        session.add(user)
        await session.commit()
        logger.info("CAT_TOGGLE: saved cats=%s", cats)
        from app.parser.worker import invalidate_user_cache
        invalidate_user_cache(user.id)
    text = (
        "<b>Категории</b>\n\n"
        "Нажимай на категории, чтобы выбрать/убрать.\n"
        "Выбрано: " + (", ".join(cats) if cats else "все") + "\n\n"
        "Когда готов — жми «Сохранить выбор»"
    )
    await cb.message.edit_text(text, reply_markup=user_categories_kb(cats), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "cat:save")
async def cb_cat_save(cb: CallbackQuery):
    SEARCH_MODE.pop(cb.from_user.id, None)
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        cats = (user.settings or {}).get("categories", []) if user.settings else []
        logger.info("CAT_SAVE: user=%s cats=%s", cb.from_user.id, cats)
    await cb.message.edit_text(
        f"✅ Сохранено.\nКатегории: {', '.join(cats) if cats else 'все'}",
        reply_markup=None,
        parse_mode="HTML",
    )
    from app.services.parser_runner import run_historical_for_user
    from app.parser.worker import invalidate_user_cache
    invalidate_user_cache(user.id)
    await run_historical_for_user(user.id)
    await cb.answer()


# ============ РЕЗУЛЬТАТЫ (callback) ============

@router.callback_query(F.data == "res:menu")
async def cb_res(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        leads = (await session.execute(
            select(Lead).where(Lead.user_id == user.id).order_by(Lead.created_at.desc())
        )).scalars().all()
        total = (await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id)
        )).scalar_one()
        if not leads:
            await cb.message.edit_text(NO_LEADS, parse_mode="HTML")
            return
        lead_ids = [l.id for l in leads]
        WAITING[cb.from_user.id] = {
            "action": "results_view",
            "lead_ids": lead_ids,
            "index": 0,
            "total": total,
        }
        header = f"Всего лидов: {total}\n\n"
        await _show_lead_card(cb.message, leads[0], 0, len(leads), header=header)


# ============ КАТЕГОРИИ (callback) ============

@router.callback_query(F.data == "cat:menu")
async def cb_cat_menu(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        cats = (user.settings or {}).get("categories", []) if user.settings else []
    if not cats:
        await cb.message.edit_text(NO_CATEGORIES, reply_markup=categories_menu_kb(), parse_mode="HTML")
        return
    text = CATEGORIES_TEXT.format(categories="\n".join(f"• {c}" for c in cats))
    await cb.message.edit_text(text, reply_markup=categories_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "cat:add")
async def cb_cat_add(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "add_category"}
    await cb.message.edit_text("Введите название категории:", reply_markup=cancel_kb())


@router.callback_query(F.data == "cat:edit")
async def cb_cat_edit(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "edit_category"}
    await cb.message.edit_text("Введите название категории для изменения:", reply_markup=cancel_kb())


@router.callback_query(F.data == "cat:del")
async def cb_cat_del(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "del_category"}
    await cb.message.edit_text("Введите название категории для удаления:", reply_markup=cancel_kb())


# ============ КЛЮЧЕВЫЕ СЛОВА (callback) ============

@router.callback_query(F.data == "kw:menu")
async def cb_kw_menu(cb: CallbackQuery):
    await cb.message.edit_text("<b>Ключевые слова</b>", reply_markup=keywords_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "kw:list")
async def cb_kw_list(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        kws = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id).order_by(Keyword.created_at)
        )).scalars().all()

        if not kws:
            await cb.message.edit_text(NO_KEYWORDS, reply_markup=keywords_menu_kb(), parse_mode="HTML")
            return

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        kb = InlineKeyboardBuilder()
        for kw in kws:
            kb.button(text=kw.word, callback_data=f"kw:view:{kw.id}")
        kb.adjust(2)
        kb.row(InlineKeyboardButton(text="Ключевые слова", callback_data="kw:menu"))
        text = KEYWORDS_TITLE.format(keywords=", ".join(k.word for k in kws[:50]))
        await cb.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("kw:view:"))
async def cb_kw_view(cb: CallbackQuery):
    kw_id = int(cb.data.split(":", 2)[2])
    await cb.message.edit_text("Действие со словом:", reply_markup=keyword_actions_kb(kw_id))


@router.callback_query(F.data.startswith("kw:edit:"))
async def cb_kw_edit(cb: CallbackQuery):
    kw_id = int(cb.data.split(":", 2)[2])
    WAITING[cb.from_user.id] = {"action": "edit_keyword", "target_id": kw_id}
    await cb.message.edit_text("Введите новое слово:", reply_markup=cancel_kb())


@router.callback_query(F.data == "kw:del_menu")
async def cb_kw_del_menu(cb: CallbackQuery):
    """Показывает все ключевые слова кнопками для удаления."""
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        kws = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id).order_by(Keyword.created_at)
        )).scalars().all()

        if not kws:
            await cb.message.edit_text(NO_KEYWORDS, reply_markup=keywords_menu_kb(), parse_mode="HTML")
            return
        await cb.message.edit_text(
            "Выберите слово для удаления:",
            reply_markup=keyword_delete_kb(kws),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("kw:del:"))
async def cb_kw_del(cb: CallbackQuery):
    kw_id = int(cb.data.split(":", 2)[2])
    async with AsyncSessionLocal() as session:
        kw = await session.get(Keyword, kw_id)
        if kw:
            await session.delete(kw)
            await session.commit()
    await cb.answer(WORD_DELETED)
    await cb_kw_del_menu(cb)


@router.callback_query(F.data == "kw:add")
async def cb_kw_add(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "add_keyword"}
    await cb.message.edit_text("Введите ключевое слово:", reply_markup=cancel_kb())


@router.callback_query(F.data == "kw:import")
async def cb_kw_import(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "import_keywords"}
    await cb.message.edit_text("Вставьте список слов, каждое с новой строки:", reply_markup=cancel_kb())


@router.callback_query(F.data == "kw:export")
async def cb_kw_export(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        kws = (await session.execute(
            select(Keyword).where(Keyword.user_id == user.id)
        )).scalars().all()
        content = "\n".join([k.word for k in kws]).encode("utf-8")
        await cb.message.answer(KEYWORDS_EXPORTED)
        await cb.message.answer_document(BufferedInputFile(content, filename="keywords.txt"))


# Стоп-слова
@router.callback_query(F.data == "kw:stop")
async def cb_kw_stop(cb: CallbackQuery):
    await cb.message.edit_text("<b>Стоп-слова</b>", reply_markup=stopwords_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "kw:stoplist")
async def cb_kw_stoplist(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        sws = (await session.execute(
            select(StopWord).where(StopWord.user_id == user.id).order_by(StopWord.created_at)
        )).scalars().all()

        if not sws:
            await cb.message.edit_text(NO_STOPWORDS, reply_markup=stopwords_menu_kb(), parse_mode="HTML")
            return

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        kb = InlineKeyboardBuilder()
        for sw in sws:
            kb.button(text=sw.word, callback_data=f"sw:view:{sw.id}")
        kb.adjust(2)
        kb.row(InlineKeyboardButton(text="Стоп-слова", callback_data="kw:stop"))
        text = STOPWORDS_TITLE.format(stopwords=", ".join(s.word for s in sws[:50]))
        await cb.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("sw:view:"))
async def cb_sw_view(cb: CallbackQuery):
    sw_id = int(cb.data.split(":", 2)[2])
    await cb.message.edit_text("Действие со словом:", reply_markup=stopword_actions_kb(sw_id))


@router.callback_query(F.data.startswith("sw:edit:"))
async def cb_sw_edit(cb: CallbackQuery):
    sw_id = int(cb.data.split(":", 2)[2])
    WAITING[cb.from_user.id] = {"action": "edit_stopword", "target_id": sw_id}
    await cb.message.edit_text("Введите новое стоп-слово:", reply_markup=cancel_kb())


@router.callback_query(F.data == "kw:stopdel_menu")
async def cb_sw_del_menu(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        sws = (await session.execute(
            select(StopWord).where(StopWord.user_id == user.id).order_by(StopWord.created_at)
        )).scalars().all()

        if not sws:
            await cb.message.edit_text(NO_STOPWORDS, reply_markup=stopwords_menu_kb(), parse_mode="HTML")
            return
        await cb.message.edit_text(
            "Выберите стоп-слово для удаления:",
            reply_markup=stopword_delete_kb(sws),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("sw:del:"))
async def cb_sw_del(cb: CallbackQuery):
    sw_id = int(cb.data.split(":", 2)[2])
    async with AsyncSessionLocal() as session:
        sw = await session.get(StopWord, sw_id)
        if sw:
            await session.delete(sw)
            await session.commit()
    await cb.answer(STOPWORD_DELETED)
    await cb_sw_del_menu(cb)


@router.callback_query(F.data == "kw:stopadd")
async def cb_sw_add(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "add_stopword"}
    await cb.message.edit_text("Введите стоп-слово:", reply_markup=cancel_kb())


# ============ ПРОФИЛЬ / РЕФЕРАЛЫ / ПОДПИСКА / НАСТРОЙКИ (callback) ============

@router.callback_query(F.data == "prof:menu")
async def cb_prof(cb: CallbackQuery):
    try:
        async with AsyncSessionLocal() as session:
            user = await _get_user(session, cb.from_user.id)
            leads_count = (await session.execute(
                select(func.count()).select_from(Lead).where(Lead.user_id == user.id)
            )).scalar_one()
            ref = (await session.execute(select(Referral).where(Referral.owner_id == user.id))).scalar_one_or_none()
            referrals = ref.registrations if ref else 0
            kw_limit = get_keyword_limit(user)
            kw_used = (await session.execute(
                select(func.count()).select_from(Keyword).where(Keyword.user_id == user.id)
            )).scalar_one()
            kw_remaining = max(0, kw_limit - kw_used)
            text = PROFILE_TEXT.format(
                telegram_id=user.telegram_id,
                username=user.username or "—",
                reg_date=user.registration_date.strftime("%d.%m.%Y") if user.registration_date else "—",
                tariff=get_user_tariff_name(user),
                days_left=get_remaining_days(user),
                keywords=kw_remaining,
                leads_count=leads_count,
                referrals=referrals,
            )
        await cb.message.edit_text(text, reply_markup=profile_kb(), parse_mode="HTML")
    except Exception as e:
        logger.exception("Profile error for %s: %s", cb.from_user.id, e)
        await cb.answer("Ошибка при открытии профиля", show_alert=True)


@router.callback_query(F.data == "ref:menu")
async def cb_ref(cb: CallbackQuery):
    link = await _get_referral_link(cb.from_user.id)
    try:
        await cb.message.edit_text(link, reply_markup=referral_kb(), parse_mode="HTML")
    except Exception:
        await cb.message.answer(link, reply_markup=referral_kb(), parse_mode="HTML")


@router.callback_query(F.data == "ref:link")
async def cb_ref_link(cb: CallbackQuery):
    # Отвечаем новым сообщением со ссылкой, чтобы не ловить "message is not modified"
    link = await _get_referral_link(cb.from_user.id)
    await cb.answer()
    await cb.message.answer(link, reply_markup=referral_kb(), parse_mode="HTML")


@router.callback_query(F.data == "sub:menu")
async def cb_sub(cb: CallbackQuery):
    from app.services.users import DEFAULT_TARIFFS
    tariffs_text = "\n\n".join(
        f"<b>{info['name']}</b>\nСтоимость: {info['price']} ₽ / {info['days']} дней\nКлючевых слов: {info['keywords']}"
        for info in DEFAULT_TARIFFS.values()
    )
    text = TARIFFS_TEXT.format(tariffs=tariffs_text)
    await cb.message.edit_text(text, reply_markup=subscription_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery):
    tariff_code = cb.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)

    from app.services.users import DEFAULT_TARIFFS
    if tariff_code not in DEFAULT_TARIFFS:
        await cb.answer("Неверный тариф", show_alert=True)
        return

    tariff_info = DEFAULT_TARIFFS[tariff_code]
    amount_map = {code: info["price"] * 100 for code, info in DEFAULT_TARIFFS.items()}
    name_map = {code: info["name"] for code, info in DEFAULT_TARIFFS.items()}

    try:
        from app.payments.yookassa import create_yookassa_payment
        return_url = getattr(settings, "yookassa_return_url", None) or "https://t.me/LentaZayaovakBot"
        payment = await create_yookassa_payment(cb.from_user.id, tariff_code, return_url)
        if payment and payment.get("confirmation", {}).get("confirmation_url"):
            await cb.message.answer(
                f"Оплата тарифа <b>{name_map.get(tariff_code, tariff_code)}</b>\n\n"
                f"Нажмите кнопку ниже, чтобы оплатить. Доступны карты, Сбер, СПБ, ЮMoney и другие способы.",
                reply_markup=InlineKeyboardBuilder().button(
                    text=f"Оплатить {amount_map.get(tariff_code, tariff_info['price']*100)/100:.0f} ₽",
                    url=payment["confirmation"]["confirmation_url"],
                ).as_markup(),
                parse_mode="HTML",
            )
            await cb.answer()
            return
    except Exception as e:
        logger.exception("YooKassa payment error: %s", e)
        await cb.answer("Оплата временно недоступна. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data == "set:menu")
async def cb_set(cb: CallbackQuery):
    await cb.message.edit_text(SETTINGS_TEXT, reply_markup=settings_kb(), parse_mode="HTML")


@router.callback_query(F.data == "set:tz")
async def cb_set_tz(cb: CallbackQuery):
    WAITING[cb.from_user.id] = {"action": "set_tz"}
    await cb.message.edit_text(SETTINGS_TZ, reply_markup=cancel_kb())


@router.callback_query(F.data == "set:notifications")
async def cb_set_notifications(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        s = user.settings or {}
        status = "включены" if s.get("notifications", True) else "выключены"
        text = NOTIFICATIONS_TEXT.format(status=status)
    await cb.message.edit_text(text, reply_markup=notifications_kb(), parse_mode="HTML")


@router.callback_query(F.data == "notif:on")
async def cb_notif_on(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        user.settings = dict(user.settings or {})
        user.settings["notifications"] = True
        session.add(user)
        await session.commit()
    await cb.answer(NOTIFICATIONS_ON)
    await cb_set_notifications(cb)


@router.callback_query(F.data == "notif:off")
async def cb_notif_off(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        user.settings = dict(user.settings or {})
        user.settings["notifications"] = False
        session.add(user)
        await session.commit()
    await cb.answer(NOTIFICATIONS_OFF)
    await cb_set_notifications(cb)


@router.callback_query(F.data == "notif:test")
async def cb_notif_test(cb: CallbackQuery):
    await cb.message.answer(NOTIFICATIONS_TEST)
    await cb.answer()


# ============ СТАТИСТИКА (callback) ============

@router.callback_query(F.data == "my:stats")
async def cb_my_stats(cb: CallbackQuery):
    await cb_stats(cb)


@router.callback_query(F.data == "stats:menu")
async def cb_stats(cb: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, cb.from_user.id)
        stats = await get_user_stats(session, user)
        if stats["total"] == 0:
            await cb.message.edit_text(NO_STATS, reply_markup=back_kb("home"), parse_mode="HTML")
            return
        text = STATS_TEXT.format(
            today=stats["today"], yesterday=stats["yesterday"],
            week=stats["week"], month=stats["month"], total=stats["total"],
            active_chats=stats["active_chats"], keywords=stats["keywords"],
            stopwords=stats["stopwords"], notifications=stats["notifications"],
            processed_today=stats["processed_today"],
        )
    await cb.message.edit_text(text, reply_markup=back_kb("home"), parse_mode="HTML")


# ============ ПОМОЩЬ (FAQ) ============

@router.callback_query(F.data == "help:menu")
async def cb_help(cb: CallbackQuery):
    await cb.message.edit_text(HELP_TEXT, reply_markup=help_kb(), parse_mode="HTML")


@router.callback_query(F.data.startswith("faq:"))
async def cb_faq(cb: CallbackQuery):
    idx = int(cb.data.split(":", 1)[1])
    if 0 <= idx < len(FAQ):
        q, a = FAQ[idx]
        await cb.message.edit_text(f"{q}\n\n{a}", reply_markup=back_kb("help:menu"), parse_mode="HTML")


# ============ АДМИН ============

@router.callback_query(F.data == "admin:panel")
async def cb_admin_panel(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Доступ запрещён", show_alert=True)
        return
    await cb.message.edit_text(ADMIN_PANEL, reply_markup=admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin:users")
async def cb_admin_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).order_by(User.created_at.desc()).limit(10))).scalars().all()
        await cb.message.edit_text("<b>Пользователи</b>", reply_markup=admin_users_kb(users), parse_mode="HTML")


@router.callback_query(F.data == "admin:add_chats")
async def cb_admin_add_chats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    WAITING[cb.from_user.id] = {"action": "admin_add_chats"}
    await cb.message.edit_text(
        "<b>Добавить чаты</b>\n\n"
        "Введите ссылки или @username чатов, каждый с новой строки.\n"
        "Поддерживаются публичные и приватные чаты, группы и каналы.",
        reply_markup=cancel_kb(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:list_chats")
async def cb_admin_list_chats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        sources = (await session.execute(select(Source).order_by(Source.created_at.desc()))).scalars().all()
        if not sources:
            await cb.message.edit_text("Чатов пока нет.", reply_markup=back_kb("admin:panel"), parse_mode="HTML")
            return
        kb = InlineKeyboardBuilder()
        for s in sources:
            label = s.title or s.username or str(s.chat_id)
            kb.button(text=f"🗑 {label}", callback_data=f"adm:delchat:{s.id}")
        kb.button(text="Админ", callback_data="admin:panel")
        kb.adjust(1)
        await cb.message.edit_text("<b>Список чатов</b>\n\nВыберите чат для удаления:",
                                   reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:delchat:"))
async def cb_admin_delete_chat(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    source_id = int(cb.data.split(":", 2)[2])
    async with AsyncSessionLocal() as session:
        source = await session.get(Source, source_id)
        if not source:
            await cb.answer("Чат не найден")
            return
        await session.delete(source)
        await session.commit()
    await cb.answer("Чат удалён")
    await cb_admin_list_chats(cb)


@router.callback_query(F.data == "admin:keywords")
async def cb_admin_keywords(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        kws = (await session.execute(select(Keyword).order_by(Keyword.created_at.desc()).limit(20))).scalars().all()
        if not kws:
            await cb.message.edit_text("Ключевых слов пока нет.", reply_markup=back_kb("admin:panel"), parse_mode="HTML")
            return
        lines = [f"• {k.word} (user {k.user_id})" for k in kws]
        await cb.message.edit_text("<b>Ключевые слова</b>\n\n" + "\n".join(lines),
                                   reply_markup=back_kb("admin:panel"), parse_mode="HTML")


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        keywords = (await session.execute(select(func.count()).select_from(Keyword))).scalar_one()
        leads = (await session.execute(select(func.count()).select_from(Lead))).scalar_one()
        sources = (await session.execute(select(func.count()).select_from(Source))).scalar_one()
        text = ADMIN_STATS.format(users=users, keywords=keywords, leads=leads, sources=sources)
        await cb.message.edit_text(text, reply_markup=back_kb("admin:panel"), parse_mode="HTML")


@router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return
    WAITING[cb.from_user.id] = {"action": "admin_broadcast"}
    await cb.message.edit_text(BROADCAST_PROMPT, reply_markup=cancel_kb(), parse_mode="HTML")


# ============ ОБРАБОТКА ВВОДА ============

@router.message(F.text.regexp(r"^[^/]"))
async def handle_text(message: Message):
    logger.info("HANDLE_TEXT from %s: %r", message.from_user.id, message.text)
    state = WAITING.get(message.from_user.id)
    if not state:
        logger.info("HANDLE_TEXT: no state for %s", message.from_user.id)
        return

    action = state.get("action")

    async with AsyncSessionLocal() as session:
        user = await _get_user(session, message.from_user.id)
        if not user:
            return

        # === Категории ===
        if action == "add_category":
            name = message.text.strip()
            user.settings = user.settings or {}
            cats = list(user.settings.get("categories", []))
            if name not in cats:
                cats.append(name)
                user.settings = dict(user.settings)
                user.settings["categories"] = cats
                session.add(user)
                await session.commit()
                await message.answer(CATEGORY_ADDED.format(name=name))
                from app.parser.worker import invalidate_user_cache
                invalidate_user_cache(user.id)
            else:
                await message.answer("Такая категория уже есть.")
            WAITING.pop(message.from_user.id, None)
            if SEARCH_MODE.get(message.from_user.id):
                await _open_category_search(message.from_user.id, message)
            else:
                await _show_categories_msg(message)
            return

        if action == "del_category":
            name = message.text.strip()
            user.settings = user.settings or {}
            cats = list(user.settings.get("categories", []))
            if name in cats:
                cats.remove(name)
                user.settings = dict(user.settings)
                user.settings["categories"] = cats
                session.add(user)
                await session.commit()
                await message.answer(CATEGORY_DELETED.format(name=name))
                from app.parser.worker import invalidate_user_cache
                invalidate_user_cache(user.id)
            else:
                await message.answer("Категория не найдена.")
            WAITING.pop(message.from_user.id, None)
            if SEARCH_MODE.get(message.from_user.id):
                await _open_category_search(message.from_user.id, message)
            else:
                await _show_categories_msg(message)
            return

        if action == "edit_category":
            name = message.text.strip()
            user.settings = user.settings or {}
            cats = user.settings.get("categories", [])
            if name in cats:
                WAITING[message.from_user.id] = {"action": "edit_category_new", "old": name}
                await message.answer("Введите новое название категории:")
                return
            await message.answer("Категория не найдена.")
            WAITING.pop(message.from_user.id, None)
            if SEARCH_MODE.get(message.from_user.id):
                await _open_category_search(message.from_user.id, message)
            else:
                await _show_categories_msg(message)
            return

        if action == "edit_category_new":
            new_name = message.text.strip()
            old = state.get("old")
            user.settings = user.settings or {}
            cats = list(user.settings.get("categories", []))
            if old in cats:
                cats[cats.index(old)] = new_name
                user.settings = dict(user.settings)
                user.settings["categories"] = cats
                session.add(user)
                await session.commit()
                await message.answer(CATEGORY_EDITED.format(name=new_name))
                from app.parser.worker import invalidate_user_cache
                invalidate_user_cache(user.id)
            WAITING.pop(message.from_user.id, None)
            if SEARCH_MODE.get(message.from_user.id):
                await _open_category_search(message.from_user.id, message)
            else:
                await _show_categories_msg(message)
            return

        # === Ключевые слова ===
        if action == "add_keyword":
            word = message.text.strip().lower()
            if not word:
                return
            count = (await session.execute(
                select(func.count()).select_from(Keyword).where(Keyword.user_id == user.id)
            )).scalar_one()
            if count >= get_keyword_limit(user):
                await message.answer("Достигнут лимит ключевых слов. Обновите тариф.")
                return
            exists = (await session.execute(
                select(Keyword).where(Keyword.user_id == user.id, Keyword.word == word)
            )).scalar_one_or_none()
            if exists:
                await message.answer("Такое слово уже есть.")
                return
            session.add(Keyword(user_id=user.id, word=word))
            await session.commit()
            WAITING.pop(message.from_user.id, None)
            await message.answer(WORD_ADDED.format(word=word))
            from app.services.parser_runner import run_historical_for_user
            await run_historical_for_user(user.id, keyword=word)
            await _show_keywords_menu_msg(message)
            return

        if action == "edit_keyword":
            kw_id = state.get("target_id")
            word = message.text.strip().lower()
            kw = await session.get(Keyword, kw_id)
            if kw and kw.user_id == user.id:
                old_word = kw.word
                kw.word = word
                session.add(kw)
                await session.execute(delete(Lead).where(Lead.user_id == user.id, Lead.matched_keyword == old_word))
                await session.commit()
                await message.answer(WORD_EDITED.format(word=word))
            WAITING.pop(message.from_user.id, None)
            await _show_keywords_menu_msg(message)
            return

        if action == "del_keyword":
            word = message.text.strip().lower()
            kw = (await session.execute(
                select(Keyword).where(Keyword.user_id == user.id, Keyword.word == word)
            )).scalar_one_or_none()
            if kw:
                await session.execute(delete(Lead).where(Lead.user_id == user.id, Lead.matched_keyword == word))
                await session.delete(kw)
                await session.commit()
                await message.answer(WORD_DELETED)
            else:
                await message.answer("Слово не найдено.")
            WAITING.pop(message.from_user.id, None)
            await _show_keywords_menu_msg(message)
            return

        if action == "import_keywords":
            lines = [l.strip().lower() for l in message.text.splitlines() if l.strip()]
            added = 0
            limit = get_keyword_limit(user)
            existing = set((await session.execute(
                select(Keyword.word).where(Keyword.user_id == user.id)
            )).scalars().all())
            for line in lines:
                if len(existing) + added >= limit:
                    break
                if line not in existing:
                    session.add(Keyword(user_id=user.id, word=line))
                    added += 1
            await session.commit()
            WAITING.pop(message.from_user.id, None)
            await message.answer(KEYWORDS_IMPORTED.format(count=added))
            await _show_keywords_menu_msg(message)
            return

        # === Стоп-слова ===
        if action == "add_stopword":
            word = message.text.strip().lower()
            session.add(StopWord(user_id=user.id, word=word))
            await session.commit()
            WAITING.pop(message.from_user.id, None)
            await message.answer(STOPWORD_ADDED.format(word=word))
            await _show_keywords_menu_msg(message)
            return

        if action == "edit_stopword":
            sw_id = state.get("target_id")
            word = message.text.strip().lower()
            sw = await session.get(StopWord, sw_id)
            if sw and sw.user_id == user.id:
                sw.word = word
                session.add(sw)
                await session.commit()
                await message.answer(STOPWORD_EDITED.format(word=word))
            WAITING.pop(message.from_user.id, None)
            await _show_keywords_menu_msg(message)
            return

        if action == "del_stopword":
            word = message.text.strip().lower()
            sw = (await session.execute(
                select(StopWord).where(StopWord.user_id == user.id, StopWord.word == word)
            )).scalar_one_or_none()
            if sw:
                await session.delete(sw)
                await session.commit()
                await message.answer(STOPWORD_DELETED)
            else:
                await message.answer("Стоп-слово не найдено.")
            WAITING.pop(message.from_user.id, None)
            await _show_keywords_menu_msg(message)
            return

        # === Настройки ===
        if action == "set_tz":
            tz = message.text.strip()
            user.settings = dict(user.settings or {})
            user.settings["timezone"] = tz
            session.add(user)
            await session.commit()
            WAITING.pop(message.from_user.id, None)
            await message.answer(SETTINGS_TZ_SAVED.format(tz=tz))
            return

        # === Админ: добавить чаты ===
        if action == "admin_add_chats":
            if not is_admin(message.from_user.id):
                return
            lines = [l.strip() for l in message.text.splitlines() if l.strip()]
            added = 0
            errors = []
            new_source_ids = []
            max_retries = 3
            retry_delay = 1
            
            for attempt in range(max_retries):
                try:
                    manager = TelethonClientManager()
                    sessions = manager.list_sessions()
                    if not sessions:
                        await message.answer("Сессия не найдена.")
                        return
                    await manager.ensure_wal(sessions[0])
                    client = await manager.connect(sessions[0])
                    
                    async with AsyncSessionLocal() as session:
                        for line in lines:
                            category = None
                            if "|" in line:
                                identifier, category = line.split("|", 1)
                                identifier = identifier.strip()
                                category = category.strip() or None
                            else:
                                identifier = line.strip()
                            entity = None
                            chat_id = None
                            title = None
                            username = None
                            chat_type = "group"
                            
                            try:
                                if identifier.startswith("https://t.me/"):
                                    identifier = identifier.replace("https://t.me/", "").strip("/")
                                if identifier.startswith("+"):
                                    entity = await client.get_entity(identifier)
                                elif identifier.startswith("@"):
                                    entity = await client.get_entity(identifier)
                                elif identifier.lstrip("-").isdigit():
                                    chat_id = int(identifier)
                                    entity = await client.get_entity(chat_id)
                                else:
                                    entity = await client.get_entity(identifier)
                            except Exception as e:
                                errors.append(f"{line}: не удалось получить чат ({str(e)[:50]})")
                                continue

                            chat_id = getattr(entity, "id", chat_id)
                            if chat_id is None:
                                errors.append(f"{line}: не удалось определить ID")
                                continue
                            
                            username = getattr(entity, "username", None)
                            title = getattr(entity, "title", None) or getattr(entity, "first_name", None)
                            
                            is_private = getattr(entity, "private", False)
                            is_megagroup = getattr(entity, "megagroup", False)
                            is_broadcast = getattr(entity, "broadcast", False)
                            if is_private:
                                chat_type = "private"
                            elif is_megagroup:
                                chat_type = "group"
                            elif is_broadcast:
                                chat_type = "channel"
                            else:
                                chat_type = "group"

                            try:
                                dup = (await session.execute(
                                    select(Source).where(Source.chat_id == int(chat_id))
                                )).scalar_one_or_none()
                                if dup:
                                    errors.append(f"{line}: уже добавлен")
                                    continue
                                if not category:
                                    category = auto_category(title, username)
                                src = Source(
                                    type=chat_type,
                                    username=username,
                                    chat_id=int(chat_id),
                                    title=title,
                                    category=category,
                                    status="active",
                                )
                                session.add(src)
                                new_source_ids.append(src.id)
                                added += 1
                            except Exception as e:
                                errors.append(f"{line}: ошибка сохранения ({str(e)[:40]})")
                        await session.commit()
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        await message.answer(f"Ошибка базы данных: {str(e)[:100]}")
                        return

            # Запуск исторического парсинга по новым чатам ПОСЛЕ коммита
            for sid in new_source_ids:
                try:
                    from app.services.parser_runner import run_historical_for_source
                    asyncio.create_task(run_historical_for_source(sid))
                except Exception as e:
                    logger.warning("Failed to schedule source parse %s: %s", sid, e)

            WAITING.pop(message.from_user.id, None)
            result = f"Добавлено чатов: {added}"
            if errors:
                result += "\n\nОшибки:\n" + "\n".join(errors[:10])
            await message.answer(result)
            return

        # === Админ: рассылка ===
        if action == "admin_broadcast":
            if not is_admin(message.from_user.id):
                return
            text = message.text.strip()
            if not text:
                await message.answer("Текст рассылки пустой.")
                return
            async with AsyncSessionLocal() as session:
                users = (await session.execute(select(User.telegram_id))).scalars().all()
            total = len(users)
            await message.answer(BROADCAST_STARTED.format(total=total))
            ok = 0
            err = 0
            for row in users:
                tid = row[0] if isinstance(row, tuple) else row
                try:
                    await message.bot.send_message(chat_id=tid, text=text)
                    ok += 1
                except Exception:
                    err += 1
            await message.answer(BROADCAST_DONE.format(ok=ok, err=err))
            WAITING.pop(message.from_user.id, None)
            return

        if action == "results_pick":
            text = message.text.strip()
            if not text.isdigit():
                await message.answer("Введите число.", reply_markup=cancel_kb())
                return
            num = int(text)
            state = WAITING.get(message.from_user.id, {})
            ids = state.get("lead_ids", [])
            total = state.get("total", len(ids))
            if num < 1 or num > total:
                await message.answer(f"Введите число от 1 до {total}.", reply_markup=cancel_kb())
                return
            idx = num - 1
            state["index"] = idx
            state["action"] = "results_view"
            async with AsyncSessionLocal() as session:
                lead = await session.get(Lead, ids[idx])
            if lead:
                header = f"Всего лидов: {total}\n\n"
                await message.answer(header + LEAD_CARD.format(
                    matched=lead.matched_keyword or "—",
                    chat_title=lead.chat_title or "Источник",
                    sender=lead.sender_username or "—",
                    date=lead.lead_date.strftime("%d.%m %H:%M") if lead.lead_date else "",
                    text=(lead.text or "")[:2000],
                ),
                                    reply_markup=_results_kb(idx, total, lead.link, lead.id), parse_mode="HTML")
            return


# Вспомогательные функции
async def _show_categories_msg(message: Message):
    async with AsyncSessionLocal() as session:
        user = await _get_user(session, message.from_user.id)
        cats = (user.settings or {}).get("categories", []) if user.settings else []
    if not cats:
        await message.answer(NO_CATEGORIES, reply_markup=categories_menu_kb(), parse_mode="HTML")
        return
    text = CATEGORIES_TEXT.format(categories="\n".join(f"• {c}" for c in cats))
    await message.answer(text, reply_markup=categories_menu_kb(), parse_mode="HTML")


async def _show_keywords_menu_msg(message: Message):
    await message.answer("<b>Ключевые слова</b>", reply_markup=keywords_menu_kb(), parse_mode="HTML")


# ============ КОМАНДЫ ============

@router.message(F.text)
async def cmd_dispatcher(message: Message):
    logger.info("CMD_DISPATCHER from %s: text=%r", message.from_user.id, message.text)
    text = (message.text or "").strip().lower()
    logger.info("DISPATCHER from %s: raw=%r cleaned=%r", message.from_user.id, message.text, text)

    if text in ("/start", "/start@lentazayaovakbot", "/menu", "/menu@lentazayaovakbot"):
        await cmd_start(message)
        return
    if text in ("/help", "/help@lentazayaovakbot"):
        await message.answer(HELP_TEXT, reply_markup=help_kb(), parse_mode="HTML")
        return
    if text in ("/stats", "/stats@lentazayaovakbot"):
        await _show_stats(message)
        return
    if text in ("/profile", "/profile@lentazayaovakbot"):
        await reply_profile(message)
        return
    if text in ("/admin", "/admin@lentazayaovakbot"):
        logger.info("DISPATCHER /admin from %s, is_admin=%s", message.from_user.id, is_admin(message.from_user.id))
        if not is_admin(message.from_user.id):
            await message.answer("Доступ запрещён")
            return
        await message.answer(ADMIN_PANEL, reply_markup=admin_kb(), parse_mode="HTML")
        return
    if text in ("/search", "/search@lentazayaovakbot"):
        await _open_category_search(message.from_user.id, message)
        return
    if text in ("/categories", "/categories@lentazayaovakbot"):
        async with AsyncSessionLocal() as session:
            user = await _get_user(session, message.from_user.id)
            cats = (user.settings or {}).get("categories", []) if user.settings else []
        if not cats:
            await message.answer(NO_CATEGORIES, reply_markup=categories_menu_kb(), parse_mode="HTML")
            return
        text_out = CATEGORIES_TEXT.format(categories="\n".join(f"• {c}" for c in cats))
        await message.answer(text_out, reply_markup=categories_menu_kb(), parse_mode="HTML")
        return
    if text in ("/results", "/results@lentazayaovakbot"):
        await _show_results(message)
        return
    if text in ("/settings", "/settings@lentazayaovakbot"):
        await message.answer(SETTINGS_TEXT, reply_markup=settings_kb(), parse_mode="HTML")
        return
    if text in ("/subscribe", "/subscribe@lentazayaovakbot", "/subscription", "/subscription@lentazayaovakbot"):
        from app.services.users import DEFAULT_TARIFFS
        t = "Доступные тарифы:\n\n" + "\n\n".join(
            f"<b>{info['name']}</b>\nСтоимость: {info['price']} ₽ / {info['days']} дней\nКлючевых слов: {info['keywords']}"
            for info in DEFAULT_TARIFFS.values()
        )
        await message.answer(t, reply_markup=subscription_kb(), parse_mode="HTML")
        return
    if text in ("/payment_diag", "/payment_diag@lentazayaovakbot"):
        from app.tools.payment_diag import diagnose_payment
        data = await diagnose_payment(message.bot, message.from_user.id)
        lines = [
            f"provider_token: {data.get('payment_provider_token')}",
            f"currency: {data.get('currency')}",
            f"bot_username: {data.get('bot_username')}",
            f"can_send_invoices: {data.get('can_send_invoices')}",
        ]
        if data.get("error"):
            lines.append(f"error: {data['error']}")
        await message.answer("Payment diagnose:\n" + "\n".join(lines))
        return
    if text in ("/keywords", "/keywords@lentazayaovakbot", "/слова", "/слова@lentazayaovakbot"):
        await message.answer("<b>Слова</b>\n\nУправление словами для поиска.",
                             reply_markup=keywords_menu_kb(), parse_mode="HTML")
        return
