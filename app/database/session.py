from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import OperationalError
from config import settings
from .models import Base
import asyncio
import logging

logger = logging.getLogger(__name__)

_engine_kwargs = {
    "future": True,
    "echo": False,
}
if not str(settings.database_url).startswith("sqlite"):
    _engine_kwargs.update(
        {
            "pool_size": 20,
            "max_overflow": 10,
            "pool_timeout": 60,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
        }
    )

engine = create_async_engine(settings.database_url, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_db():
    max_retries = 30
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                if str(settings.database_url).startswith("sqlite"):
                    await conn.execute(text("PRAGMA journal_mode=WAL"))
            logger.info("Database initialized successfully")
            return
        except OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning("Database connection failed (attempt %d/%d): %s. Retrying in %ds...", 
                              attempt + 1, max_retries, e, retry_delay)
                await asyncio.sleep(retry_delay)
            else:
                logger.error("Failed to connect to database after %d attempts", max_retries)
                raise
        except Exception as e:
            logger.error("Unexpected error during database initialization: %s", e)
            raise

