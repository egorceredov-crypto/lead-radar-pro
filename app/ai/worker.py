import asyncio
import logging
from datetime import datetime
from app.database.session import init_db, AsyncSessionLocal
from app.ai.analyzer import analyze_message
from app.database.models import Message as DBMessage, Lead, AIResult
from config import settings
from aiogram import Bot

logger = logging.getLogger(__name__)


async def process_unprocessed_messages(bot: Bot):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            DBMessage.__table__.select().where(DBMessage.processed == False)
        )
        rows = result.fetchall()
        for row in rows:
            message = row[0] if isinstance(row, tuple) else row
            try:
                text = message.text or ''
                analysis = await analyze_message(text)
                message.processed = True
                session.add(message)

                ai_result = AIResult(
                    lead_id=None,
                    model='rule-based',
                    prompt=f'Analyze message for lead quality: {text[:200]}',
                    response=str(analysis),
                    score=analysis['score'],
                )
                session.add(ai_result)
                await session.commit()
                await session.refresh(ai_result)

                if analysis['score'] >= 0.5:
                    lead = Lead(
                        user_id=0,
                        message_id=message.id,
                        category=None,
                        lead_score=analysis['score'],
                        lead_type=analysis['type'],
                        ai_description=analysis['description'],
                    )
                    session.add(lead)
                    await session.commit()
                    await session.refresh(lead)

                    if settings.admin_id:
                        try:
                            await bot.send_message(
                                int(settings.admin_id),
                                f"🤖 AI обнаружил лид ({analysis['type']}) {int(analysis['score']*100)}%:\n{text[:300]}"
                            )
                        except Exception:
                            logger.exception("Failed to notify admin about AI lead")
            except Exception:
                logger.exception("Failed processing message %s", message.id)


async def main():
    await init_db()
    bot = Bot(token=settings.bot_token)
    logger.info('AI worker started')
    try:
        while True:
            await process_unprocessed_messages(bot)
            await asyncio.sleep(30)
    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
