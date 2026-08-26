from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    bot_token: str
    api_id: str | None = None
    api_hash: str | None = None
    database_url: str = "postgresql+asyncpg://postgres:1234@db:5432/monitor_db"
    redis_url: str = "redis://localhost:6379/0"
    admin_id: int | None = None
    admin_ids: str | None = None
    ai_api_key: str | None = None
    payment_provider_token: str | None = None
    currency: str = "RUB"
    mock_mode: bool = False
    owner_session: str = "beauty_userbot.session"
    session_string: str | None = None
    trial_days: int = 3
    referral_bonus: float = 100.0
    default_timezone: str = "Asia/Omsk"
    proxy_host: str | None = None
    proxy_port: str | None = None
    proxy_type: str | None = None
    yookassa_shop_id: str | None = None
    yookassa_secret_key: str | None = None
    yookassa_return_url: str | None = None


settings = Settings()
