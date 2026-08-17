# Lead Radar PRO

SaaS-платформа для поиска лидов в Telegram.

## Возможности

- Поиск лидов по ключевым словам в Telegram-чатах
- Подписки: BASIC (10 слов), PRO (30 слов), PREMIUM (100 слов)
- Платежи через YooKassa
- Реферальная система
- Админ-панель и веб-интерфейс
- Парсинг новых сообщений и исторический поиск
- AI-анализ лидов

## Требования

- Python 3.11+
- Telegram Bot Token
- Telegram API_ID / API_HASH
- YooKassa (shop_id, secret_key)
- Для Bothost: переменные окружения, постоянное хранилище `data/`

## Установка

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/MacOS
# или .venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

```env
BOT_TOKEN=...
API_ID=...
API_HASH=...
SESSION_STRING=...  # опционально, для серверного запуска
OWNER_SESSION=beauty_userbot.session
DATABASE_URL=sqlite+aiosqlite:///./data/lead_radar.db?check_same_thread=False&timeout=30
REDIS_URL=redis://localhost:6379/0
ADMIN_ID=...
ADMIN_IDS=...
AI_API_KEY=...
PAYMENT_PROVIDER_TOKEN=...
CURRENCY=RUB
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
YOOKASSA_RETURN_URL=...
PROXY_HOST=
PROXY_PORT=
PROXY_TYPE=
TRIAL_DAYS=3
REFERRAL_BONUS=100.0
DEFAULT_TIMEZONE=Asia/Omsk
MOCK_MODE=false
```

## Получение String Session

Для серверного запуска (Bothost, VPS) вместо `.session` файла используйте `SESSION_STRING`:

```bash
python -c "
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import settings

async def main():
    client = TelegramClient('temp_session', settings.api_id, settings.api_hash)
    await client.start()
    print('SESSION_STRING=', StringSession.save(client.session))
    await client.disconnect()

import asyncio
asyncio.run(main())
"
```

Скопируйте вывод `SESSION_STRING` в `.env`.

## Инициализация базы данных

```bash
python -m app.scripts.init_db
python -m app.scripts.migrate_db
```

## Запуск

### Локально (Windows)

```bash
python -m app.manage bot        # Бот + парсер в одном процессе
python -m app.manage web        # Веб-панель
python -m app.manage parser     # Парсер отдельно
python -m app.manage ai         # AI-воркер
```

### На Bothost / Linux

Рекомендуемый способ — запуск через `python -m app.manage bot` в фоне.

Bothost Basic:
- до 5 ботов
- 2 vCPU
- 1 GB RAM
- 5 GB SSD
- переменные окружения
- авто-бэкапы
- Git-деплой

На Bothost создайте переменные окружения в панели управления, загрузите код через Git и запустите:

```bash
python -m app.manage bot
```

Убедитесь, что папка `data/` находится в постоянном хранилище (не в `/tmp`).

## Архитектура

- `app/bot/` — Aiogram бот, хендлеры, клавиатуры, тексты
- `app/parser/` — Telethon парсер, мониторинг новых сообщений, исторический поиск
- `app/services/` — бизнес-логика: пользователи, подписки, рефералы, статистика
- `app/payments/` — YooKassa и Telegram Payments
- `app/web/` — FastAPI веб-панель
- `app/ai/` — AI-анализ лидов
- `app/database/` — SQLAlchemy модели и сессии

## Тарифы

| Тариф | Цена | Длительность | Слова |
|-------|------|--------------|-------|
| BASIC | 499 ₽ | 30 дней | 10 |
| PRO | 1299 ₽ | 30 дней | 30 |
| PREMIUM | 2999 ₽ | 30 дней | 100 |

Лимиты и длительность хранятся в `app/services/users.py` (`DEFAULT_TARIFFS`) и могут быть переопределены через таблицу `tariff_plans` в БД.

## Безопасность

- `.env` и секреты не попадают в Git (см. `.gitignore`)
- `SESSION_STRING` хранится только в переменных окружения
- Платежные данные не хранятся в коде
- Веб-панель не имеет аутентификации — не открывайте её публично без reverse proxy

## Docker (опционально)

```bash
docker compose up -d --build
```

Примечание: `docker-compose.yml` настроен на PostgreSQL. По умолчанию проект использует SQLite.

## Лицензия

Proprietary
