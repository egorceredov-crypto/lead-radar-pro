import argparse
import asyncio
import importlib
import compileall
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_compile():
    print('Running compileall on', ROOT)
    ok = compileall.compile_dir(ROOT, quiet=0)
    if not ok:
        print('Compilation errors found')
        sys.exit(2)
    print('Compilation OK')


async def _import_module(name: str):
    print('Importing', name)
    importlib.import_module(name)
    print('Imported', name)


async def run_imports():
    modules = ['app.bot.main', 'app.parser.worker', 'app.ai.worker', 'app.web.main']
    for m in modules:
        await _import_module(m)
    print('All imports OK')


async def run_web():
    # Run uvicorn programmatically
    try:
        import uvicorn
    except ImportError:
        print('uvicorn not installed')
        sys.exit(1)
    config = uvicorn.Config('app.web.main:app', host='127.0.0.1', port=8000, log_level='info')
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot():
    mod = importlib.import_module('app.bot.main')
    if hasattr(mod, 'main'):
        try:
            print('RUNNING_BOT', flush=True)
            await mod.main()
        except Exception as e:
            print('BOT_CRASH:', e, flush=True)
            raise
    else:
        print('No main() in app.bot.main')


async def run_parser():
    mod = importlib.import_module('app.parser.worker')
    if hasattr(mod, 'main'):
        await mod.main()
    else:
        print('No main() in app.parser.worker')


async def run_ai():
    mod = importlib.import_module('app.ai.worker')
    if hasattr(mod, 'main'):
        await mod.main()
    else:
        print('No main() in app.ai.worker')


async def run_backfill_access_hashes():
    from app.database.session import AsyncSessionLocal
    from app.database.models import Source
    from app.parser.telethon_client import TelethonClientManager
    from app.parser.worker import _resolve_source
    from sqlalchemy import select
    from telethon.errors import FloodWaitError

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    manager = TelethonClientManager()
    sessions = manager.list_all_sessions()
    if not sessions:
        print('No Telethon sessions found')
        sys.exit(1)

    client = None
    for session_name in sessions:
        try:
            client = await manager.connect(session_name)
            break
        except Exception:
            continue

    if client is None:
        print('Failed to connect Telethon client')
        sys.exit(1)

    print('Telethon client connected')

    async with AsyncSessionLocal() as session:
        sources = (await session.execute(
            select(Source).where(Source.status == 'active', Source.access_hash.is_(None))
        )).scalars().all()

    if not sources:
        print('No sources with NULL access_hash')
        return

    print(f'Found {len(sources)} sources without access_hash')

    updated = 0
    failed = 0
    skipped = 0

    for src in sources:
        try:
            entity = await _resolve_source(client, src, user_id=getattr(src, 'user_id', None))
        except FloodWaitError as e:
            print(f'FloodWaitError after {updated} updated, {failed} failed: {e.seconds}s')
            break
        except Exception as e:
            print(f'ERROR source={src.id} title={src.title} err={e}')
            failed += 1
            continue

        if entity is None:
            print(f'SKIP source={src.id} title={src.title} reason=resolve_null')
            skipped += 1
            continue

        access_hash = getattr(entity, 'access_hash', None)
        if access_hash is None:
            print(f'SKIP source={src.id} title={src.title} reason=no_access_hash_on_entity')
            skipped += 1
            continue

        async with AsyncSessionLocal() as session:
            db_src = await session.get(Source, src.id)
            if db_src is None:
                print(f'SKIP source={src.id} reason=not_found_in_db')
                skipped += 1
                continue
            if db_src.access_hash is not None:
                print(f'SKIP source={src.id} reason=already_has_access_hash')
                skipped += 1
                continue
            db_src.access_hash = access_hash
            await session.commit()
            updated += 1
            print(f'UPDATED source={src.id} title={src.title} access_hash={access_hash}')

    print(f'backfill finished: updated={updated} failed={failed} skipped={skipped}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['check', 'imports', 'web', 'bot', 'parser', 'ai', 'backfill-access-hashes'])
    args = p.parse_args()

    if args.cmd == 'check':
        run_compile()
    elif args.cmd == 'imports':
        asyncio.run(run_imports())
    elif args.cmd == 'web':
        asyncio.run(run_web())
    elif args.cmd == 'bot':
        asyncio.run(run_bot())
    elif args.cmd == 'parser':
        asyncio.run(run_parser())
    elif args.cmd == 'ai':
        asyncio.run(run_ai())
    elif args.cmd == 'backfill-access-hashes':
        asyncio.run(run_backfill_access_hashes())


if __name__ == '__main__':
    main()
