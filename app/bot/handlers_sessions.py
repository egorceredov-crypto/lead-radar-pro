import os
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from app.parser.session_manager import SessionManager
from app.database.session import AsyncSessionLocal
from app.database.models import TelegramAccount, User
from config import settings

router = Router()
sess_mgr = SessionManager()


@router.message(Command("add_session"))
async def cmd_add_session(message: Message):
    await message.answer("Отправьте файл сессии (.session) как документ. Только админ может загружать сессию.")


@router.message(lambda message: message.document is not None)
async def handle_session_file(message: Message):
    doc = message.document
    if not doc.file_name.endswith('.session'):
        await message.answer('Файл должен иметь расширение .session')
        return
    # Only admin allowed to upload owner session
    if not message.from_user or int(message.from_user.id) != int(settings.admin_id or 0):
        await message.answer('Создать сессию может только администратор.')
        return

    local_path = await doc.download(destination=doc.file_name)
    owner_name = getattr(settings, 'owner_session', doc.file_name)
    file_path = sess_mgr.add_session_file(local_path, owner_name)

    async with AsyncSessionLocal() as session:
        # record/update TelegramAccount entry for owner session
        q = await session.execute(select(TelegramAccount).where(TelegramAccount.session_name == owner_name))
        ta = q.scalar_one_or_none()
        if not ta:
            ta = TelegramAccount(
                user_id=None,
                session_name=owner_name,
                session_file=file_path,
                status='inactive'
            )
            session.add(ta)
        else:
            ta.session_file = file_path
            ta.status = 'inactive'
            session.add(ta)
        await session.commit()

    if os.path.exists(doc.file_name):
        try:
            os.remove(doc.file_name)
        except OSError:
            pass

    await message.answer(f'Файл сессии сохранён как {owner_name}')
