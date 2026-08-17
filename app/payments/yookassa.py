import logging
from typing import Optional
import uuid
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database.session import AsyncSessionLocal
from app.database.models import User, Payment
from app.services.users import activate_subscription, DEFAULT_TARIFFS
from config import settings

logger = logging.getLogger(__name__)

YOOKASSA_API_URL = "https://api.yookassa.ru/v3"


async def create_yookassa_payment(user_id: int, tariff: str, return_url: str) -> Optional[dict]:
    tariff_info = DEFAULT_TARIFFS.get(tariff)
    if not tariff_info:
        return None

    amount = tariff_info["price"]
    label = tariff_info["name"]
    days = tariff_info["days"]

    shop_id = getattr(settings, "yookassa_shop_id", None)
    secret_key = getattr(settings, "yookassa_secret_key", None)
    if not shop_id or not secret_key:
        logger.error("YooKassa credentials missing")
        return None

    if not return_url or return_url.startswith("https://example.com"):
        return_url = "https://t.me/LentaZayaovakBot"

    payload = {
        "amount": {
            "value": f"{amount:.2f}",
            "currency": settings.currency,
        },
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "capture": True,
        "description": f"Подписка {label} на {days} дней",
        "metadata": {
            "user_id": str(user_id),
            "tariff": tariff,
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{YOOKASSA_API_URL}/payments",
                json=payload,
                auth=(shop_id, secret_key),
                headers={
                    "Content-Type": "application/json",
                    "Idempotence-Key": str(uuid.uuid4()),
                },
            )
            response.raise_for_status()
            data = response.json()
            logger.info("YooKassa payment created: id=%s, status=%s", data.get("id"), data.get("status"))
            return data
    except httpx.HTTPStatusError as e:
        logger.error("YooKassa HTTP error: %s, body=%s", e.response.status_code, e.response.text)
        return None
    except Exception as e:
        logger.exception("YooKassa create payment error: %s", e)
        return None


async def process_yookassa_webhook(data: dict) -> bool:
    event = data.get("event")
    if event != "payment.succeeded":
        return False

    payment_data = data.get("object", {})
    payment_id = payment_data.get("id")
    transaction_id = payment_data.get("payment_method", {}).get("id") or payment_id
    amount = payment_data.get("amount", {}).get("value")
    currency = payment_data.get("amount", {}).get("currency")
    metadata = payment_data.get("metadata", {})
    user_id = metadata.get("user_id")
    tariff = metadata.get("tariff")

    if not user_id or not tariff:
        logger.error("YooKassa webhook missing metadata: %s", metadata)
        return False

    async with AsyncSessionLocal() as session:
        if transaction_id:
            existing_payment = (await session.execute(
                select(Payment).where(Payment.transaction_id == transaction_id)
            )).scalar_one_or_none()
            if existing_payment:
                logger.warning("Duplicate YooKassa webhook: transaction_id=%s", transaction_id)
                return True

        user = (await session.execute(
            select(User).where(User.id == int(user_id))
        )).scalar_one_or_none()

        if not user:
            logger.error("YooKassa webhook user not found: user_id=%s", user_id)
            return False

        pay = Payment(
            user_id=user.id,
            amount=float(amount) if amount else 0.0,
            currency=currency or settings.currency,
            payment_method="yookassa",
            transaction_id=transaction_id,
            status="success",
        )
        session.add(pay)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.warning("Duplicate YooKassa payment insert: transaction_id=%s", transaction_id)
            return True
        await session.refresh(pay)

        ok = await activate_subscription(session, user, tariff, payment_id=pay.id)
        await session.commit()
        logger.info("YooKassa subscription activation: user=%s, tariff=%s, result=%s", user.id, tariff, ok)
        return ok
