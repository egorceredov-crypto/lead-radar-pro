from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from app.database.session import AsyncSessionLocal
from app.database.models import User, Lead, Message, Subscription, TelegramAccount, Payment
from app.database.models import Source
from app.parser.telethon_client import TelethonClientManager
from app.services.users import auto_category
from config import settings
from telethon.errors import RPCError
import logging

logger = logging.getLogger(__name__)

import os
from pathlib import Path

app = FastAPI(title="Lead Radar PRO Web")
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
else:
    logger.warning("Static directory not found: %s", _static_dir)
templates = Jinja2Templates(directory="app/web/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    async with AsyncSessionLocal() as session:
        users_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        leads_count = (await session.execute(select(func.count()).select_from(Lead))).scalar_one()
        messages_count = (await session.execute(select(func.count()).select_from(Message))).scalar_one()
        active_subscriptions = (
            await session.execute(
                select(func.count()).select_from(Subscription).where(Subscription.status == 'active')
            )
        ).scalar_one()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "users_count": users_count,
            "leads_count": leads_count,
            "messages_count": messages_count,
            "active_subscriptions": active_subscriptions,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/sessions")
async def api_sessions():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            TelegramAccount.__table__.select()
        )
        rows = res.fetchall()
        out = []
        for r in rows:
            acc = r[0] if isinstance(r, tuple) else r
            out.append({
                "id": acc.id,
                "session_name": acc.session_name,
                "session_file": acc.session_file,
                "status": acc.status,
                "last_connection": acc.last_connection.isoformat() if acc.last_connection else None,
            })
    return {"sessions": out}


@app.get("/api/payments")
async def api_payments():
    async with AsyncSessionLocal() as session:
        res = await session.execute(Payment.__table__.select().order_by(Payment.created_at.desc()).limit(50))
        rows = res.fetchall()
        out = []
        for r in rows:
            p = r[0] if isinstance(r, tuple) else r
            out.append({
                "id": p.id,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })
    return {"payments": out}


@app.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request):
    async with AsyncSessionLocal() as session:
        res = await session.execute(Payment.__table__.select().order_by(Payment.created_at.desc()).limit(50))
        rows = res.fetchall()
        payments = []
        for r in rows:
            p = r[0] if isinstance(r, tuple) else r
            payments.append({
                "id": p.id,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })
    return templates.TemplateResponse(request, "payments.html", {"request": request, "payments": payments})


@app.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    async with AsyncSessionLocal() as session:
        res = await session.execute(TelegramAccount.__table__.select())
        rows = res.fetchall()
        sessions = []
        for r in rows:
            acc = r[0] if isinstance(r, tuple) else r
            sessions.append({
                "id": acc.id,
                "session_name": acc.session_name,
                "session_file": acc.session_file,
                "status": acc.status,
            })
    return templates.TemplateResponse(request, "sessions.html", {"request": request, "sessions": sessions})


@app.get("/sessions/activate/{sid}")
async def activate_session(sid: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(TelegramAccount.__table__.select().where(TelegramAccount.id == sid))
        row = res.scalar_one_or_none()
        if row:
            row.status = 'active'
            session.add(row)
            await session.commit()
    return {"ok": True}


@app.get("/sessions/deactivate/{sid}")
async def deactivate_session(sid: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(TelegramAccount.__table__.select().where(TelegramAccount.id == sid))
        row = res.scalar_one_or_none()
        if row:
            row.status = 'inactive'
            session.add(row)
            await session.commit()
    return {"ok": True}


@app.get("/chats", response_class=HTMLResponse)
async def chats_page(request: Request):
    async with AsyncSessionLocal() as session:
        res = await session.execute(Source.__table__.select())
        rows = res.fetchall()
        chats = []
        for r in rows:
            s = r[0] if isinstance(r, tuple) else r
            chats.append({
                "id": s.id,
                "chat_id": s.chat_id,
                "username": s.username,
                "title": s.title,
                "type": s.type,
                "status": s.status,
            })
    return templates.TemplateResponse(request, "chats.html", {"request": request, "chats": chats})


@app.get("/leads", response_class=HTMLResponse)
async def leads_page(request: Request):
    async with AsyncSessionLocal() as session:
        res = await session.execute(Lead.__table__.select().order_by(Lead.created_at.desc()).limit(100))
        rows = res.fetchall()
        leads = []
        for r in rows:
            p = r[0] if isinstance(r, tuple) else r
            leads.append({
                "id": p.id,
                "text": (p.text or "")[:200],
                "sender_username": p.sender_username,
                "chat_title": p.chat_title,
                "matched_keyword": p.matched_keyword,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "link": p.link,
            })
    return templates.TemplateResponse(request, "leads.html", {"request": request, "leads": leads})


def _detect_chat_type(entity) -> str:
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


@app.post("/chats/add")
async def chats_add(request: Request):
    form = await request.form()
    identifier = form.get('identifier')
    if not identifier:
        return {"error": "identifier required"}

    if identifier.startswith("https://t.me/"):
        identifier = identifier.replace("https://t.me/", "").strip("/")
    if identifier.startswith("@"):
        identifier = identifier[1:]

    manager = TelethonClientManager()
    sessions = manager.list_sessions()
    if not sessions:
        return {"error": "owner session not found"}

    sname = sessions[0]
    try:
        client = await manager.connect(sname)
    except Exception as e:
        return {"error": f"failed to connect owner session: {e}"}

    try:
        entity = await client.get_entity(identifier)
    except RPCError:
        return {"error": "failed to resolve identifier"}
    except Exception as e:
        return {"error": f"entity resolution error: {e}"}

    chat_id = getattr(entity, 'id', None)
    title = getattr(entity, 'title', None) or getattr(entity, 'username', None) or getattr(entity, 'first_name', None)
    uname = getattr(entity, 'username', None)
    chat_type = _detect_chat_type(entity)

    async with AsyncSessionLocal() as session:
        existing = await session.execute(Source.__table__.select().where(Source.chat_id == int(chat_id)))
        if existing.scalar_one_or_none():
            return {"ok": True, "message": "already exists"}
        category = auto_category(title, uname)
        src = Source(user_id=None, type=chat_type, username=uname, chat_id=int(chat_id), title=title, category=category, status='active')
        session.add(src)
        await session.flush()
        source_id = src.id
        await session.commit()

    # Запуск исторического парсинга по новому чату
    try:
        from app.services.parser_runner import run_historical_for_source
        run_historical_for_source(source_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to schedule source parse %s: %s", source_id, e)

    return {"ok": True}


@app.get("/chats/delete/{cid}")
async def chats_delete(cid: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(Source.__table__.select().where(Source.id == cid))
        row = res.scalar_one_or_none()
    if row:
        await session.execute(Source.__table__.delete().where(Source.id == cid))
        await session.commit()
    return {"ok": True}


@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "invalid"}

    try:
        from app.payments.yookassa import process_yookassa_webhook
        ok = await process_yookassa_webhook(data)
    except Exception as e:
        logger.exception("YooKassa webhook processing error: %s", e)
        return {"status": "error"}

    return {"status": "ok", "processed": ok}
