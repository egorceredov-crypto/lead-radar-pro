import datetime
import math
import secrets
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, Subscription, Referral, RadarState, TariffPlan, Lead, Keyword
from config import settings

DEFAULT_TARIFFS = {
    "basic": {"name": "BASIC", "price": 499, "days": 30, "keywords": 10},
    "pro": {"name": "PRO", "price": 1299, "days": 30, "keywords": 30},
    "premium": {"name": "PREMIUM", "price": 2999, "days": 30, "keywords": 100},
}


async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str = None, first_name: str = None, last_name: str = None, ref_code: str = None) -> User:
    result = await session.execute(select(User).where(User.telegram_id == int(telegram_id)))
    user = result.scalar_one_or_none()

    if not user:
        now = datetime.datetime.utcnow()
        trial_end = now + datetime.timedelta(days=settings.trial_days)

        user = User(
            telegram_id=int(telegram_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            subscription_status="free",
            trial_start_date=now,
            trial_end_date=trial_end,
            settings={
                "language": "ru",
                "timezone": settings.default_timezone,
                "notifications": True,
                "format": "text",
                "show_link": True,
                "show_author": True,
                "show_date": True,
                "delay": 0,
                "autostart": True,
                "autoupdate": True,
            },
            limits={"keywords": 3},
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(select(User).where(User.telegram_id == int(telegram_id)))
            user = result.scalar_one_or_none()
            if not user:
                raise

        user_changed = True

        await session.execute(
            text("INSERT OR IGNORE INTO radar_state (user_id, enabled, updated_at) VALUES (:uid, 1, :ts)"),
            {"uid": user.id, "ts": datetime.datetime.utcnow()},
        )
        await session.execute(
            text("INSERT OR IGNORE INTO referrals (owner_id, code, registrations, clicks, bonus, created_at) VALUES (:oid, :code, 0, 0, 0, :ts)"),
            {"oid": user.id, "code": secrets.token_hex(4), "ts": datetime.datetime.utcnow()},
        )

        if ref_code:
            await handle_referral_register(session, user, ref_code)

        if user_changed:
            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
    else:
        user_changed = False

        if username and user.username != username:
            user.username = username
            user_changed = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            user_changed = True
        if last_name and user.last_name != last_name:
            user.last_name = last_name
            user_changed = True
        if not user.settings:
            user.settings = {
                "language": "ru",
                "timezone": settings.default_timezone,
                "notifications": True,
                "format": "text",
                "show_link": True,
                "show_author": True,
                "show_date": True,
                "delay": 0,
                "autostart": True,
                "autoupdate": True,
            }
            user_changed = True
        if not user.limits:
            user.limits = {"keywords": 3}
            user_changed = True
        if not user.trial_start_date and user.subscription_status is None:
            now = datetime.datetime.utcnow()
            user.subscription_status = "free"
            user.trial_start_date = now
            user.trial_end_date = now + datetime.timedelta(days=settings.trial_days)
            user_changed = True

        radar_res = await session.execute(select(RadarState).where(RadarState.user_id == user.id))
        if not radar_res.scalar_one_or_none():
            await session.execute(
                text("INSERT OR IGNORE INTO radar_state (user_id, enabled, updated_at) VALUES (:uid, 1, :ts)"),
                {"uid": user.id, "ts": datetime.datetime.utcnow()},
            )
            user_changed = True

        ref_res = await session.execute(select(Referral).where(Referral.owner_id == user.id))
        if not ref_res.scalar_one_or_none():
            await session.execute(
                text("INSERT OR IGNORE INTO referrals (owner_id, code, registrations, clicks, bonus, created_at) VALUES (:oid, :code, 0, 0, 0, :ts)"),
                {"oid": user.id, "code": secrets.token_hex(4), "ts": datetime.datetime.utcnow()},
            )
            user_changed = True

        if user_changed:
            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    return user


async def handle_referral_register(session: AsyncSession, new_user: User, ref_code: str):
    result = await session.execute(select(Referral).where(Referral.code == ref_code))
    ref = result.scalar_one_or_none()
    if not ref or ref.owner_id == new_user.id:
        return

    # Защита от повторной выдачи награды за одну и ту же регистрацию
    if ref.referred_id is not None and ref.referred_id == new_user.id:
        return

    ref.referred_id = new_user.id
    ref.registrations += 1
    ref.clicks += 1
    session.add(ref)

    # Bonus to owner: +1 день и +2 слова к лимиту за регистрацию приглашённого
    owner_result = await session.execute(select(User).where(User.id == ref.owner_id))
    owner = owner_result.scalar_one_or_none()
    if owner:
        owner.limits = owner.limits or {}
        owner.limits["keywords"] = owner.limits.get("keywords", 10) + 2
        # +1 день к подписке/пробному периоду
        if owner.subscription_end_date:
            owner.subscription_end_date = owner.subscription_end_date + datetime.timedelta(days=1)
        elif owner.trial_end_date:
            owner.trial_end_date = owner.trial_end_date + datetime.timedelta(days=1)
        session.add(owner)


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == int(telegram_id)))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


def get_user_tariff_name(user: User) -> str:
    if user.subscription_status == "blocked":
        return " Заблокирован"
    if user.subscription_status == "active":
        return f" {user.tariff.upper() if user.tariff else 'ACTIVE'}"
    if user.subscription_status == "expired":
        return " Просрочена"
    return "FREE"


def get_trial_end_text(user: User) -> str:
    if user.subscription_end_date:
        return user.subscription_end_date.strftime("%d.%m.%Y")
    if user.trial_end_date:
        return user.trial_end_date.strftime("%d.%m.%Y")
    return "—"


def get_remaining_days(user: User) -> int:
    now = datetime.datetime.utcnow()
    if user.subscription_status in ("trial", "free"):
        end = user.trial_end_date
    else:
        end = user.subscription_end_date
    if not end:
        return 0
    remaining = (end - now).total_seconds() / 86400
    return max(0, math.ceil(remaining))


def get_keyword_limit(user: User) -> int:
    if user.telegram_id == 7733702903:
        return 999999
    if user.subscription_status == "active" and user.subscription_end_date:
        now = datetime.datetime.utcnow()
        if user.subscription_end_date > now:
            limits = user.limits or {}
            return limits.get("keywords", 10)
    if user.subscription_status in ("trial", "free") and user.trial_end_date:
        now = datetime.datetime.utcnow()
        if user.trial_end_date > now:
            return 3
    return 0


def get_tariff_keyword_limit(user: User) -> int:
    if user.telegram_id == 7733702903:
        return 999999
    if user.subscription_status == "active":
        limits = user.limits or {}
        return limits.get("keywords", 10)
    if user.subscription_status in ("trial", "free"):
        return 3
    return 0


async def activate_subscription(session: AsyncSession, user: User, tariff_code: str, payment_id: int = None) -> bool:
    tariff_info = DEFAULT_TARIFFS.get(tariff_code)
    if not tariff_info:
        return False

    # Try DB tariff first
    db_res = await session.execute(select(TariffPlan).where(TariffPlan.code == tariff_code, TariffPlan.is_active == True))
    db_tariff = db_res.scalar_one_or_none()
    if db_tariff:
        tariff_info = {
            "name": db_tariff.name,
            "price": db_tariff.price_rub,
            "days": db_tariff.days,
            "keywords": db_tariff.keyword_limit,
        }

    now = datetime.datetime.utcnow()
    days = tariff_info["days"]

    # Close old active subscriptions for this user
    old_subs = (await session.execute(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active")
    )).scalars().all()
    for old in old_subs:
        old.status = "replaced"
        session.add(old)

    # If user already has active paid subscription and it's not expired, extend it instead of resetting
    if user.subscription_status == "active" and user.subscription_end_date and user.subscription_end_date > now:
        end_date = user.subscription_end_date + datetime.timedelta(days=days)
        user.subscription_start_date = user.subscription_start_date or now
        user.subscription_end_date = end_date
    else:
        end_date = now + datetime.timedelta(days=days)
        user.subscription_status = "active"
        user.subscription_start_date = now
        user.subscription_end_date = end_date

    user.tariff = tariff_code
    user.limits = {"keywords": tariff_info["keywords"]}

    sub = Subscription(
        user_id=user.id,
        tariff=tariff_code,
        status="active",
        start_date=user.subscription_start_date,
        end_date=end_date,
        payment_id=payment_id,
        auto_renew=False,
    )
    session.add(sub)
    session.add(user)

    # Bonus for referral owner: +7 дней и +10 слов (только один раз за одного приглашённого)
    ref_res = await session.execute(select(Referral).where(Referral.referred_id == user.id))
    ref = ref_res.scalar_one_or_none()
    if ref and ref.payments == 0:
        owner_res = await session.execute(select(User).where(User.id == ref.owner_id))
        owner = owner_res.scalar_one_or_none()
        if owner:
            # +10 слов к лимиту
            owner.limits = owner.limits or {}
            owner.limits["keywords"] = owner.limits.get("keywords", 10) + 10
            # +7 дней к подписке/пробному периоду
            if owner.subscription_end_date:
                owner.subscription_end_date = owner.subscription_end_date + datetime.timedelta(days=7)
            elif owner.trial_end_date:
                owner.trial_end_date = owner.trial_end_date + datetime.timedelta(days=7)
            session.add(owner)
            ref.payments += 1
            ref.bonus += settings.referral_bonus * 2
            session.add(ref)

    await session.commit()
    return True


def format_tariff_price(amount: float) -> float:
    return float(amount)


async def check_subscription(session: AsyncSession, user: User) -> bool:
    """Returns True if user has active trial, paid subscription, or free grace period."""
    if user.subscription_status == "blocked":
        return False

    now = datetime.datetime.utcnow()

    if user.subscription_status == "active" and user.subscription_end_date:
        if user.subscription_end_date > now:
            return True
        user.subscription_status = "free"
        user.limits = {"keywords": 3}
        user.trial_end_date = now + datetime.timedelta(days=7)
        session.add(user)
        await session.commit()
        return True

    if user.subscription_status == "trial" and user.trial_end_date:
        if user.trial_end_date > now:
            return True
        user.subscription_status = "free"
        user.limits = {"keywords": 3}
        user.trial_end_date = now + datetime.timedelta(days=7)
        session.add(user)
        await session.commit()
        return True

    if user.subscription_status == "free" and user.trial_end_date:
        if user.trial_end_date > now:
            return True
        # Free expired -> stopped
        user.subscription_status = "expired"
        user.limits = {"keywords": 0}
        user.trial_end_date = None
        session.add(user)
        await session.commit()
        return False

    return False


_CATEGORY_RULES = [
    ("Дизайн", ["дизайн", "design"]),
    ("Программирование", ["программ", "разработ", "developer", "code", "python", "js", "frontend", "backend", "gamedev"]),
    ("Создание сайтов", ["сайт", "site", "веб", "web"]),
    ("Маркетплейсы", ["маркетплейс", "wb", "wildberries", "ozon", "яндекс", "yandex"]),
    ("SMM / Соцсети", ["smm", "соцсет", "social"]),
    ("Реклама / Таргет", ["таргет", "реклам", "ads", "advertising"]),
    ("Копирайтинг", ["копирайт", "текст", "writer", "seo"]),
    ("Фото / Видео", ["фото", "видео", "фотограф", "видеограф", "video", "photo"]),
    ("Визажист", ["визаж", "makeup"]),
    ("Барбер", ["барбер", "barber"]),
    ("Парикмахер", ["парикмахер", "парик", "hair"]),
    ("Маникюр", ["маникюр", "маник", "nail"]),
    ("Бровист / Лэшмейкер", ["бров", "lash", "ресниц"]),
    ("Массажист", ["массаж", "massage"]),
    ("Репетитор", ["репетитор"]),
    ("Английский", ["английск", "english"]),
    ("Математика", ["математ", "math"]),
    ("Авто", ["авто", "машин", "автомоб", "car"]),
    ("Ремонт / Строительство", ["ремонт", "строит", "отделк"]),
    ("Клининговые услуги", ["клининг", "уборк", "cleaning"]),
    ("Доставка / Перевозки", ["доставк", "перевоз", "груз"]),
    ("Недвижимость", ["недвижим", "риелтор", "realty", "real estate"]),
    ("Юрист", ["юрист", "lawyer", "legal"]),
    ("Бухгалтер", ["бухгалт", "бухг", "accountant"]),
    ("Продажи", ["продаж", "sales", "менеджер"]),
]


def auto_category(title: str | None, username: str | None) -> str:
    text = f"{(title or '').lower()} {(username or '').lower()}"
    for category, keywords in _CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category
    return "Другое"