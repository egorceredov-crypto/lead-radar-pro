import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from app.database.session import AsyncSessionLocal
from app.database.models import User, Payment
from app.services.users import activate_subscription, DEFAULT_TARIFFS
from config import settings

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("buy_basic"))
async def buy_basic(message: Message):
    await send_payment_link(message, "basic")


@router.message(Command("buy_pro"))
async def buy_pro(message: Message):
    await send_payment_link(message, "pro")


@router.message(Command("buy_premium"))
async def buy_premium(message: Message):
    await send_payment_link(message, "premium")


async def send_payment_link(message: Message, tariff: str):
    if tariff not in DEFAULT_TARIFFS:
        await message.answer("Неизвестный тариф")
        return

    amount_map = {code: info["price"] * 100 for code, info in DEFAULT_TARIFFS.items()}
    name_map = {code: info["name"] for code, info in DEFAULT_TARIFFS.items()}

    try:
        from app.payments.yookassa import create_yookassa_payment
        return_url = getattr(settings, "yookassa_return_url", None) or "https://t.me/LentaZayaovakBot"
        payment = await create_yookassa_payment(message.from_user.id, tariff, return_url)
        if payment and payment.get("confirmation", {}).get("confirmation_url"):
            await message.answer(
                f"Оплата тарифа <b>{name_map.get(tariff, tariff)}</b>\n\n"
                f"Нажмите кнопку ниже, чтобы оплатить. Доступны карты, Сбер, СПБ, ЮMoney и другие способы.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text=f"Оплатить {amount_map.get(tariff, DEFAULT_TARIFFS[tariff]['price']*100)/100:.0f} ₽",
                        url=payment["confirmation"]["confirmation_url"],
                    )
                ]]),
                parse_mode="HTML",
            )
            return
    except Exception as e:
        logger.exception("YooKassa payment error: %s", e)
        await message.answer("Оплата временно недоступна. Попробуйте позже.")


@router.message(lambda message: message.successful_payment is not None)
async def successful_payment(message: Message):
    await message.answer("Оплата через Telegram Payments сейчас не используется. Используйте кнопку оплаты из меню подписки.")
