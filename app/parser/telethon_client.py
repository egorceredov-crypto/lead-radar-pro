import os
import asyncio
import logging
from typing import Dict, Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import RPCError
from config import settings

logger = logging.getLogger(__name__)


class TelethonClientManager:
    _shared_clients: Dict[str, TelegramClient] = {}
    _connect_lock = asyncio.Lock()

    def __init__(self, sessions_dir: str = "sessions", api_id: int | None = None, api_hash: str | None = None):
        self.sessions_dir = sessions_dir
        self.api_id = int(api_id) if api_id else (int(settings.api_id) if settings.api_id else None)
        self.api_hash = api_hash or settings.api_hash
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.clients = TelethonClientManager._shared_clients

    def _session_path(self, name: str) -> str:
        return os.path.join(self.sessions_dir, name)

    def _build_proxy(self) -> Optional[dict]:
        host = getattr(settings, "proxy_host", None)
        port = getattr(settings, "proxy_port", None)
        ptype = getattr(settings, "proxy_type", None)
        if host and port and ptype:
            try:
                port_int = int(port)
                ptype_lower = str(ptype).lower()
                if ptype_lower == "socks5":
                    return dict(proxy_type="socks5", address=host, port=port_int)
                if ptype_lower == "socks4":
                    return dict(proxy_type="socks4", address=host, port=port_int)
                if ptype_lower in ("http", "https"):
                    return dict(proxy_type="http", address=host, port=port_int)
                logger.warning("Unknown proxy type: %s", ptype)
            except Exception:
                logger.exception("Failed to build proxy config")
        return None

    def list_sessions(self) -> list:
        session_string = getattr(settings, "session_string", None)
        if session_string:
            return ["string_session"]
        owner = getattr(settings, 'owner_session', None)
        if owner:
            p = os.path.join(self.sessions_dir, owner)
            return [owner] if os.path.exists(p) else []
        if not os.path.exists(self.sessions_dir):
            return []
        return [f for f in os.listdir(self.sessions_dir) if not f.startswith('.')]

    async def connect(self, name: str) -> TelegramClient:
        if name in self.clients:
            return self.clients[name]

        async with TelethonClientManager._connect_lock:
            if name in self.clients:
                return self.clients[name]

            if not self.api_id or not self.api_hash:
                raise RuntimeError("Telethon API_ID/API_HASH not configured")

            proxy = self._build_proxy()
            session_string = getattr(settings, "session_string", None)
            
            if session_string:
                client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash, proxy=proxy)
            else:
                await self.ensure_wal(name)
                session_path = self._session_path(name)
                client = TelegramClient(session_path, self.api_id, self.api_hash, proxy=proxy)
            
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    if session_string:
                        raise RuntimeError(f"StringSession for {name} is not authorized. Please generate a new one.")
                    try:
                        await client.start()
                    except RPCError as e:
                        logger.warning("Session %s not authorized: %s", name, e)
            except Exception as e:
                logger.exception("Failed to connect session %s: %s", name, e)
                raise

            self.clients[name] = client
            return client

    async def disconnect(self, name: str) -> None:
        client = self.clients.get(name)
        if client:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error disconnecting %s", name)
            finally:
                del self.clients[name]

    async def check(self, name: str) -> bool:
        client = self.clients.get(name)
        if not client:
            return False
        try:
            await client.get_me()
            return True
        except Exception:
            return False

    async def ensure_wal(self, name: str):
        session_path = self._session_path(name) + ".session"
        if not os.path.exists(session_path):
            return
        import sqlite3
        for attempt in range(5):
            try:
                conn = sqlite3.connect(session_path, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.close()
                return
            except sqlite3.OperationalError:
                if attempt < 4:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.warning("Failed to set WAL mode for session %s", name)
