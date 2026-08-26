"""
Safe migration script: SQLite -> PostgreSQL.

Usage:
    python migrate_sqlite_to_postgres.py

It reads existing SQLite database from data/lead_radar.db and writes
all data into PostgreSQL defined by DATABASE_URL or default:
postgresql+asyncpg://postgres:1234@localhost:5432/monitor_db
"""
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from config import settings
from app.database.models import Base

SQLITE_URL = "sqlite+aiosqlite:///./data/lead_radar.db"
PG_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:1234@localhost:5432/monitor_db")

TABLES = [
    "users",
    "chats",
    "keywords",
    "stopwords",
    "chat_messages",
    "leads",
    "notifications",
    "radar_state",
    "telegram_accounts",
    "sources",
    "subscriptions",
    "payments",
    "tariff_plans",
    "referrals",
    "broadcasts",
    "admin_logs",
    "ai_results",
]


async def migrate():
    sqlite_engine = create_async_engine(SQLITE_URL, future=True)
    pg_engine = create_async_engine(PG_URL, future=True)

    sqlite_session = async_sessionmaker(bind=sqlite_engine, expire_on_commit=False)()
    pg_session = async_sessionmaker(bind=pg_engine, expire_on_commit=False)()

    try:
        async with pg_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        print(f"PostgreSQL schema init failed: {e}")
        return

    for table in TABLES:
        print(f"Migrating {table}...")
        try:
            rows = await sqlite_session.execute(text(f"SELECT * FROM {table}"))
            keys = rows.keys()
            records = [dict(zip(keys, row)) for row in rows.fetchall()]
        except Exception as e:
            print(f"  read sqlite failed: {e}")
            continue

        if not records:
            print(f"  skipped: no rows")
            continue

        placeholders = ", ".join([f":{i}" for i in range(len(keys))])
        columns = ", ".join(keys)
        insert_sql = text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})")

        async with pg_session.begin() as conn:
            for record in records:
                try:
                    await conn.execute(insert_sql, {str(i): record[key] for i, key in enumerate(keys)})
                except Exception as e:
                    print(f"  insert failed: {e}")
                    continue
        print(f"  migrated {len(records)} rows")

    await pg_session.commit()
    await sqlite_session.close()
    await pg_session.close()
    await sqlite_engine.dispose()
    await pg_engine.dispose()
    print("Migration finished.")


if __name__ == "__main__":
    asyncio.run(migrate())
