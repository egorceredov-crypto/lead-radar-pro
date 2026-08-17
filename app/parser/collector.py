import asyncio
import logging
from app.parser.session_manager import SessionManager
from app.parser.telethon_client import TelethonClientManager

logger = logging.getLogger(__name__)


async def main():
    sess_mgr = SessionManager()
    manager = TelethonClientManager()

    sessions = manager.list_sessions()
    if not sessions:
        logger.info("No session files found in %s", sess_mgr.sessions_dir)

    for s in sessions:
        try:
            client = await manager.connect(s)
            ok = await manager.check(s)
            logger.info("Connected session %s: %s", s, ok)
        except Exception:
            logger.exception("Failed to initialize session %s", s)

    while True:
        await asyncio.sleep(60)


if __name__ == '__main__':
    asyncio.run(main())
