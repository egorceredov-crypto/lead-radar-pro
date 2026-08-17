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


def main():
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['check', 'imports', 'web', 'bot', 'parser', 'ai'])
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


if __name__ == '__main__':
    main()
