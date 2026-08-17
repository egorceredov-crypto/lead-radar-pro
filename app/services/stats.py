import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, Chat, Keyword, StopWord, Lead, Notification, ChatMessage, RadarState


async def get_user_stats(session: AsyncSession, user: User) -> dict:
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today - datetime.timedelta(days=1)
    week_start = today - datetime.timedelta(days=7)
    month_start = today - datetime.timedelta(days=30)

    today_count = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id, Lead.created_at >= today)
        )
    ).scalar_one()

    yesterday_count = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id,
                                                          Lead.created_at >= yesterday_start,
                                                          Lead.created_at < today)
        )
    ).scalar_one()

    week_count = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id, Lead.created_at >= week_start)
        )
    ).scalar_one()

    month_count = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id, Lead.created_at >= month_start)
        )
    ).scalar_one()

    total_count = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.user_id == user.id)
        )
    ).scalar_one()

    active_chats = (
        await session.execute(
            select(func.count()).select_from(Chat).where(Chat.user_id == user.id, Chat.status == "active")
        )
    ).scalar_one()

    keywords_count = (
        await session.execute(
            select(func.count()).select_from(Keyword).where(Keyword.user_id == user.id)
        )
    ).scalar_one()

    stopwords_count = (
        await session.execute(
            select(func.count()).select_from(StopWord).where(StopWord.user_id == user.id)
        )
    ).scalar_one()

    notifications_count = (
        await session.execute(
            select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.sent == True)
        )
    ).scalar_one()

    # Messages processed today
    processed_today = (
        await session.execute(
            select(func.count()).select_from(ChatMessage).where(
                ChatMessage.user_id == user.id,
                ChatMessage.processed == True,
                ChatMessage.created_at >= today,
            )
        )
    ).scalar_one()

    avg = round(total_count / 30, 1) if total_count > 0 else 0

    return {
        "today": today_count,
        "yesterday": yesterday_count,
        "week": week_count,
        "month": month_count,
        "total": total_count,
        "active_chats": active_chats,
        "keywords": keywords_count,
        "stopwords": stopwords_count,
        "notifications": notifications_count,
        "processed_today": processed_today,
        "avg": avg,
    }


async def get_admin_stats(session: AsyncSession) -> dict:
    total_users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    blocked_users = (await session.execute(
        select(func.count()).select_from(User).where(User.subscription_status == "blocked")
    )).scalar_one()
    active_subs = (await session.execute(
        select(func.count()).select_from(User).where(User.subscription_status == "active")
    )).scalar_one()
    free_users = (await session.execute(
        select(func.count()).select_from(User).where(User.subscription_status == "free")
    )).scalar_one()

    total_chats = (await session.execute(select(func.count()).select_from(Chat))).scalar_one()
    total_keywords = (await session.execute(select(func.count()).select_from(Keyword))).scalar_one()
    total_leads = (await session.execute(select(func.count()).select_from(Lead))).scalar_one()
    total_messages = (await session.execute(select(func.count()).select_from(ChatMessage))).scalar_one()

    active_radars = (await session.execute(
        select(func.count()).select_from(RadarState).where(RadarState.enabled == True)
    )).scalar_one()

    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    leads_today = (await session.execute(
        select(func.count()).select_from(Lead).where(Lead.created_at >= today)
    )).scalar_one()

    return {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "active_subs": active_subs,
        "free_users": free_users,
        "total_chats": total_chats,
        "total_keywords": total_keywords,
        "total_leads": total_leads,
        "total_messages": total_messages,
        "active_radars": active_radars,
        "leads_today": leads_today,
    }


async def get_chat_stats(session: AsyncSession, chat_id: int) -> dict:
    total_messages = (await session.execute(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.chat_id == chat_id)
    )).scalar_one()

    total_leads = (await session.execute(
        select(func.count()).select_from(Lead).where(Lead.chat_id == chat_id)
    )).scalar_one()

    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = (await session.execute(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.chat_id == chat_id,
            ChatMessage.created_at >= today,
        )
    )).scalar_one()

    return {
        "total_messages": total_messages,
        "total_leads": total_leads,
        "messages_today": messages_today,
    }