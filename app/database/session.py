from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from config import settings
from .models import Base

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if str(settings.database_url).startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))

