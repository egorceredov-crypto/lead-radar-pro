import asyncio
from app.database.session import AsyncSessionLocal
from app.database.models import User, Source
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        sources = (await session.execute(select(Source).where(Source.status == 'active'))).scalars().all()
        
        source_cats = set()
        for s in sources:
            if s.category:
                source_cats.add(s.category)
        
        print('Source categories:')
        for c in sorted(source_cats):
            print(f'  {repr(c)}')
        
        print()
        for user in users:
            cats = (user.settings or {}).get('categories', [])
            print(f'User {user.id} categories:')
            for c in cats:
                print(f'  {repr(c)}')
            
            matching = [c for c in cats if c in source_cats]
            print(f'  Matching: {matching}')
            print()

asyncio.run(main())
