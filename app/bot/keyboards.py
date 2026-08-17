"""Клавиатуры Lead Radar PRO: reply-навигация снизу + inline-подменю."""

from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============ НИЖНЯЯ НАВИГАЦИЯ (reply) ============

def main_reply_kb() -> ReplyKeyboardMarkup:
    """Компактная основная навигация — крупные кнопки снизу."""
    kb = [
        [KeyboardButton(text="/search")],
        [KeyboardButton(text="/results"), KeyboardButton(text="/keywords")],
        [KeyboardButton(text="/profile"), KeyboardButton(text="/subscribe")],
        [KeyboardButton(text="/help")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def admin_reply_kb() -> ReplyKeyboardMarkup:
    """Админская нижняя навигация."""
    kb = [
        [KeyboardButton(text="/admin")],
        [KeyboardButton(text="/search")],
        [KeyboardButton(text="/results"), KeyboardButton(text="/keywords")],
        [KeyboardButton(text="/profile"), KeyboardButton(text="/subscribe")],
        [KeyboardButton(text="/help")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def remove_reply_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# ============ ОБЩИЕ ============

def back_kb(action: str = "home") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data=action)
    return kb.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="cancel")
    return kb.as_markup()


# ============ РЕЗУЛЬТАТЫ ============

def results_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Главная", callback_data="home")
    kb.adjust(1)
    return kb.as_markup()


# ============ КАТЕГОРИИ ============

def categories_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Выбрать категории", callback_data="cat:select")
    kb.button(text="Добавить свою", callback_data="cat:add")
    kb.button(text="Удалить свою", callback_data="cat:del")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(1)
    return kb.as_markup()


def user_categories_kb(selected: list[str]) -> InlineKeyboardMarkup:
    from app.bot.texts import CATEGORIES
    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        mark = "✅" if cat in selected else "⬜"
        kb.button(text=f"{mark} {cat}", callback_data=f"cat:toggle:{cat}")
    kb.button(text="Сохранить выбор", callback_data="cat:save")
    kb.button(text="Добавить свою", callback_data="cat:add")
    kb.button(text="Удалить свою", callback_data="cat:del")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(2)
    return kb.as_markup()


# ============ КЛЮЧЕВЫЕ СЛОВА ============

def keywords_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Список слов", callback_data="kw:list")
    kb.button(text="Добавить слово", callback_data="kw:add")
    kb.button(text="Удалить слово", callback_data="kw:del_menu")
    kb.button(text="Стоп-слова", callback_data="kw:stop")
    kb.button(text="Импорт слов", callback_data="kw:import")
    kb.button(text="Экспорт слов", callback_data="kw:export")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(2)
    return kb.as_markup()


def keyword_actions_kb(word_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Изменить", callback_data=f"kw:edit:{word_id}")
    kb.button(text="Удалить", callback_data=f"kw:del:{word_id}")
    kb.button(text="Ключевые слова", callback_data="kw:list")
    kb.adjust(1)
    return kb.as_markup()


def keyword_delete_kb(keywords: list) -> InlineKeyboardMarkup:
    """Keyboard with all keywords as buttons for deletion."""
    kb = InlineKeyboardBuilder()
    for kw in keywords:
        kb.button(text=kw.word, callback_data=f"kw:del:{kw.id}")
    kb.button(text="Ключевые слова", callback_data="kw:menu")
    kb.adjust(2)
    return kb.as_markup()


def stopwords_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Список стоп-слов", callback_data="kw:stoplist")
    kb.button(text="Добавить стоп-слово", callback_data="kw:stopadd")
    kb.button(text="Удалить стоп-слово", callback_data="kw:stopdel_menu")
    kb.button(text="Ключевые слова", callback_data="kw:menu")
    kb.adjust(1)
    return kb.as_markup()


def stopword_delete_kb(stopwords: list) -> InlineKeyboardMarkup:
    """Keyboard with all stopwords as buttons for deletion."""
    kb = InlineKeyboardBuilder()
    for sw in stopwords:
        kb.button(text=sw.word, callback_data=f"sw:del:{sw.id}")
    kb.button(text="Стоп-слова", callback_data="kw:stop")
    kb.adjust(2)
    return kb.as_markup()


def stopword_actions_kb(word_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Изменить", callback_data=f"sw:edit:{word_id}")
    kb.button(text="Удалить", callback_data=f"sw:del:{word_id}")
    kb.button(text="Стоп-слова", callback_data="kw:stoplist")
    kb.adjust(1)
    return kb.as_markup()


# ============ ПРОФИЛЬ ============

def profile_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Реферальная система", callback_data="ref:menu")
    kb.button(text="Подписка", callback_data="sub:menu")
    kb.button(text="Статистика", callback_data="my:stats")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(1)
    return kb.as_markup()


# ============ РЕФЕРАЛЫ ============

def referral_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Показать ссылку", callback_data="ref:link")
    kb.button(text="Профиль", callback_data="prof:menu")
    kb.adjust(1)
    return kb.as_markup()


# ============ ПОДПИСКА ============

def subscription_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="BASIC", callback_data="buy:basic")
    kb.button(text="PRO", callback_data="buy:pro")
    kb.button(text="PREMIUM", callback_data="buy:premium")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(1)
    return kb.as_markup()


# ============ НАСТРОЙКИ ============

def settings_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Часовой пояс", callback_data="set:tz")
    kb.button(text="Уведомления", callback_data="set:notifications")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(2)
    return kb.as_markup()


def notifications_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Включить", callback_data="notif:on")
    kb.button(text="Выключить", callback_data="notif:off")
    kb.button(text="Тест", callback_data="notif:test")
    kb.button(text="Настройки", callback_data="set:menu")
    kb.adjust(1)
    return kb.as_markup()


# ============ ПОМОЩЬ ============

def help_kb() -> InlineKeyboardMarkup:
    from app.bot.texts import FAQ
    kb = InlineKeyboardBuilder()
    for i, (q, _) in enumerate(FAQ):
        kb.button(text=q, callback_data=f"faq:{i}")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(1)
    return kb.as_markup()


# ============ АДМИН ============

def admin_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Пользователи", callback_data="admin:users")
    kb.button(text="Добавить чаты", callback_data="admin:add_chats")
    kb.button(text="Список чатов", callback_data="admin:list_chats")
    kb.button(text="Удалить чат", callback_data="admin:delete_chat")
    kb.button(text="Ключевые слова", callback_data="admin:keywords")
    kb.button(text="Статистика", callback_data="admin:stats")
    kb.button(text="Рассылка", callback_data="admin:broadcast")
    kb.button(text="Главная", callback_data="home")
    kb.adjust(2)
    return kb.as_markup()


def admin_users_kb(users: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for u in users[:10]:
        name = u.first_name or u.username or str(u.telegram_id)
        kb.button(text=f"{name} ({u.telegram_id})", callback_data=f"admin:user:{u.id}")
    kb.button(text="Админ", callback_data="admin:panel")
    kb.adjust(1)
    return kb.as_markup()


def admin_user_kb(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Изменить тариф", callback_data=f"adm:tariff:{user_id}")
    kb.button(text="Выдать подписку", callback_data=f"adm:grant:{user_id}")
    kb.button(text="Удалить подписку", callback_data=f"adm:revoke:{user_id}")
    kb.button(text="Заблокировать", callback_data=f"adm:block:{user_id}")
    kb.button(text="Разблокировать", callback_data=f"adm:unblock:{user_id}")
    kb.button(text="Пользователи", callback_data="admin:users")
    kb.adjust(1)
    return kb.as_markup()
