import asyncio
import datetime
import logging
import os
from sqlalchemy import text

from app.database.session import AsyncSessionLocal, engine
from config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)


async def get_pg_dsn() -> str | None:
    url = str(settings.database_url)
    if url.startswith("postgresql"):
        return url
    return None


async def backup_postgres() -> str | None:
    """Create a PostgreSQL dump file and return its path."""
    dsn = await get_pg_dsn()
    if not dsn:
        return None
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"pg_backup_{timestamp}.sql")
    try:
        import subprocess
        env = os.environ.copy()
        env["PGPASSWORD"] = "1234"
        result = subprocess.run(
            [
                "pg_dump",
                "-h", "db",
                "-p", "5432",
                "-U", "postgres",
                "-d", "monitor_db",
                "-f", backup_path,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            logger.error("PostgreSQL backup failed: %s", result.stderr)
            return None
        logger.info("PostgreSQL backup created: %s", backup_path)
        return backup_path
    except FileNotFoundError:
        logger.warning("pg_dump not found; skip backup")
        return None
    except Exception as e:
        logger.error("PostgreSQL backup error: %s", e)
        return None


async def restore_postgres_if_empty() -> bool:
    """Restore PostgreSQL from the latest backup if the database is empty."""
    dsn = await get_pg_dsn()
    if not dsn:
        return False
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT count(*) FROM users"))
            user_count = result.scalar_one()
        if user_count > 0:
            return False
    except Exception as e:
        logger.error("Failed to check users count for restore: %s", e)
        return False

    backups = sorted([
        f for f in os.listdir(BACKUP_DIR) if f.startswith("pg_backup_") and f.endswith(".sql")
    ])
    if not backups:
        return False

    latest = os.path.join(BACKUP_DIR, backups[-1])
    logger.info("Restoring PostgreSQL from backup: %s", latest)
    try:
        import subprocess
        env = os.environ.copy()
        env["PGPASSWORD"] = "1234"
        result = subprocess.run(
            [
                "psql",
                "-h", "db",
                "-p", "5432",
                "-U", "postgres",
                "-d", "monitor_db",
                "-f", latest,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            logger.error("PostgreSQL restore failed: %s", result.stderr)
            return False
        logger.info("PostgreSQL restored from %s", latest)
        return True
    except FileNotFoundError:
        logger.warning("psql not found; skip restore")
        return False
    except Exception as e:
        logger.error("PostgreSQL restore error: %s", e)
        return False


async def start_periodic_backup(interval_seconds: int = 3600):
    """Periodically backup PostgreSQL."""
    while True:
        try:
            await backup_postgres()
        except Exception as e:
            logger.error("Periodic backup failed: %s", e)
        await asyncio.sleep(interval_seconds)
