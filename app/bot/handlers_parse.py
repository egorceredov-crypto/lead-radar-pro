import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.types import InputMessagesFilterEmpty

from app.parser.telethon_client import TelethonClientManager

logger = logging.getLogger(__name__)

router = Router()
manager = TelethonClientManager()


@router.message(Command("parse"))
async def cmd_parse(message: Message):
    """Парсинг сообщений по ключевому слову через подключённую сессию."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Укажите ключевое слово для поиска.\n\n"
            "Пример: /parse ищу барбера"
        )
        return

    keyword = args[1].strip()
    await message.answer(f"Ищу сообщения по запросу: «{keyword}»...")

    sessions = manager.list_sessions()
    if not sessions:
        await message.answer("Не найдена сессия Telegram. Загрузите её через /add_session")
        return

    found = 0
    for s in sessions:
        try:
            client = await manager.connect(s)
            me = await client.get_me()
            await message.answer(f"Подключено к аккаунту: {me.first_name} (@{me.username})")

            # Ищем по диалогам (чатам, группам, каналам)
            async for dialog in client.iter_dialogs():
                try:
                    result = await client(SearchRequest(
                        peer=dialog.entity,
                        q=keyword,
                        filter=InputMessagesFilterEmpty(),
                        min_date=None,
                        max_date=None,
                        offset_id=0,
                        add_offset=0,
                        limit=20,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    ))
                    for msg in result.messages:
                        text = getattr(msg, 'message', '') or ''
                        if keyword.lower() in text.lower():
                            found += 1
                            sender = await msg.get_sender()
                            sender_name = getattr(sender, 'first_name', None) or getattr(sender, 'username', None) or 'Неизвестно'
                            chat_title = getattr(dialog.entity, 'title', None) or dialog.name or 'Чат'
                            await message.answer(
                                f"Найдено в: {chat_title}\n"
                                f"Автор: {sender_name}\n"
                                f"Сообщение:\n{text[:500]}\n\n"
                                f"Ссылка: https://t.me/c/{abs(dialog.entity.id)}/{msg.id}",
                                parse_mode="HTML"
                            )
                            if found >= 20:
                                break
                except Exception as e:
                    logger.debug("Search error in %s: %s", dialog.name, e)
                    continue
                if found >= 20:
                    break
        except Exception as e:
            logger.exception("Failed to process session %s", s)
            await message.answer(f"Ошибка с сессией {s}: {e}")
            continue

        if found >= 20:
            break

    if found == 0:
        await message.answer(f"По запросу «{keyword}» ничего не найдено.")
    else:
        await message.answer(f"Готово! Найдено сообщений: {found}")
