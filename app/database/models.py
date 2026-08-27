from sqlalchemy import Column, Integer, BigInteger, String, DateTime, JSON, ForeignKey, Text, Boolean, Float
from sqlalchemy.orm import declarative_base, relationship
import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    registration_date = Column(DateTime, default=datetime.datetime.utcnow)
    subscription_status = Column(String, default="free")  # free / active / expired / blocked
    trial_start_date = Column(DateTime, nullable=True)
    trial_end_date = Column(DateTime, nullable=True)
    subscription_start_date = Column(DateTime, nullable=True)
    subscription_end_date = Column(DateTime, nullable=True)
    tariff = Column(String, nullable=True)  # basic / pro / premium
    settings = Column(JSON, nullable=True)  # language, timezone, notify, format, links, author, date
    limits = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    chats = relationship("Chat", back_populates="user")
    keywords = relationship("Keyword", back_populates="user")
    stopwords = relationship("StopWord", back_populates="user")
    leads = relationship("Lead", back_populates="user")
    notifications = relationship("Notification", back_populates="user")


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chat_telegram_id = Column(BigInteger, nullable=False, index=True)
    username = Column(String, nullable=True)
    title = Column(String, nullable=True)
    type = Column(String, default="chat")  # channel / group / supergroup / chat
    status = Column(String, default="active")  # active / paused / removed
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="chats")
    messages = relationship("ChatMessage", back_populates="chat")


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    word = Column(String, nullable=False)
    category = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="keywords")


class StopWord(Base):
    __tablename__ = "stopwords"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    word = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="stopwords")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_message_id = Column(BigInteger, nullable=True, index=True)
    sender_id = Column(BigInteger, nullable=True)
    sender_username = Column(String, nullable=True)
    text = Column(Text, nullable=True)
    date = Column(DateTime, nullable=True)
    matched_keyword = Column(String, nullable=True)
    is_dup = Column(Boolean, default=False)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    chat = relationship("Chat", back_populates="messages")


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=True)
    text = Column(Text, nullable=True)
    sender_username = Column(String, nullable=True)
    chat_title = Column(String, nullable=True)
    matched_keyword = Column(String, nullable=True)
    link = Column(Text, nullable=True)
    lead_date = Column(DateTime, nullable=True)
    status = Column(String, default="new")  # new / processed / done
    category = Column(String, nullable=True)
    lead_score = Column(Float, nullable=True)
    lead_type = Column(String, nullable=True)
    ai_description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="leads")


class AIResult(Base):
    __tablename__ = "ai_results"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    model = Column(String, nullable=True)
    prompt = Column(Text, nullable=True)
    response = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message = Column(Text, nullable=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    sent = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="notifications")


class RadarState(Base):
    __tablename__ = "radar_state"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    session_name = Column(String, nullable=False)
    session_file = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    status = Column(String, default="inactive")
    last_connection = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# Алиас для обратной совместимости (старый код использует Message)
Message = ChatMessage


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    type = Column(String, default="channel")
    username = Column(String, nullable=True)
    chat_id = Column(BigInteger, nullable=True)
    title = Column(String, nullable=True)
    category = Column(String, nullable=True)
    status = Column(String, default="active")
    last_checked_message_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tariff = Column(String, nullable=False)
    status = Column(String, default="active")
    start_date = Column(DateTime, default=datetime.datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    auto_renew = Column(Boolean, default=False)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    payment_method = Column(String, nullable=True)
    transaction_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class TariffPlan(Base):
    __tablename__ = "tariff_plans"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)  # basic / pro / premium
    name = Column(String, nullable=False)
    price_rub = Column(Float, nullable=False)
    days = Column(Integer, nullable=False)
    chat_limit = Column(Integer, nullable=False)
    keyword_limit = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    referred_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    code = Column(String, unique=True, nullable=False, index=True)
    clicks = Column(Integer, default=0)
    registrations = Column(Integer, default=0)
    payments = Column(Integer, default=0)
    bonus = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id = Column(Integer, primary_key=True)
    admin_id = Column(BigInteger, nullable=True)
    text = Column(Text, nullable=True)
    sent_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id = Column(Integer, primary_key=True)
    admin_id = Column(BigInteger, nullable=True)
    action = Column(String, nullable=True)
    target_id = Column(BigInteger, nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
