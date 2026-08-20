import asyncio
import sys
sys.path.insert(0, ".")

from app.services.parser_runner import run_historical_for_user

async def main():
    print("Starting historical search for user 6...")
    await run_historical_for_user(6)
    print("Historical search task started")

if __name__ == "__main__":
    asyncio.run(main())
