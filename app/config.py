from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""

    # DB
    database_url: str = "postgresql+asyncpg://alicartuning:alicartuning@localhost:5432/alicartuning"
    database_url_sync: str = "postgresql+psycopg2://alicartuning:alicartuning@localhost:5432/alicartuning"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Wildberries
    wb_api_key: str = ""
    wb_api_base_url: str = "https://content-api.wildberries.ru"

    # Ozon
    ozon_client_id: str = ""
    ozon_api_key: str = ""
    ozon_api_base_url: str = "https://api-seller.ozon.ru"

    # AI
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""

    # Storage
    storage_backend: str = "local"
    storage_local_path: str = "./storage"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # Brand
    brand_name: str = "ALICARTUNING"

    environment: str = "development"
    log_level: str = "INFO"

    @property
    def telegram_admin_id_list(self) -> list[int]:
        return [int(x) for x in self.telegram_admin_ids.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
