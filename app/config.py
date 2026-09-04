from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""

    # Backend API (FastAPI, app/api/routes/*) — общий секрет в заголовке X-API-Key
    # (см. app.api.deps.require_api_token). Fail-closed, симметрично
    # telegram_admin_ids: пустое значение отклоняет ВСЕ запросы, а не отключает
    # проверку — иначе порт API, опубликованный docker-compose на хост, был бы
    # доступен без единого ключа и позволял бы дергать /publish/{id} с реальными
    # ключами WB/Ozon магазина.
    api_auth_token: str = ""

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
    wb_stats_api_base_url: str = "https://statistics-api.wildberries.ru"
    wb_feedbacks_api_base_url: str = "https://feedbacks-api.wildberries.ru"

    # Несколько магазинов (раздел 1 ТЗ v5) — до 4 кабинетов WB + до 4 Ozon.
    # JSON-массив {"id","name","platform":"wb"|"ozon","api_key","client_id"}.
    # Пусто = используются одиночные wb_api_key/ozon_client_id/ozon_api_key
    # ниже как «магазин по умолчанию» — старый .env не ломается.
    # См. app/services/shops.py. Не путать с ShopSnapshot (снимки ЧУЖИХ
    # магазинов-конкурентов на WB через /shop) — это про СВОИ кабинеты.
    shops_json: str = ""

    # Ozon
    ozon_client_id: str = ""
    ozon_api_key: str = ""
    ozon_api_base_url: str = "https://api-seller.ozon.ru"
    # Ставка НДС для /v2/product/import (поле vat): "0" — без НДС (УСН и т.п.),
    # "0.05"/"0.07"/"0.10"/"0.20" — соответствующая ставка. Уточните у бухгалтера
    # фактический режим налогообложения магазина перед публикацией в проде.
    ozon_default_vat: str = "0"

    # Опрос nmID после создания карточки WB (ProductService._wait_for_wb_nm_id) —
    # /content/v2/cards/upload создаёт карточку асинхронно, nmID появляется не
    # сразу. 12 попыток по 3с — до ~36с общего ожидания; WB может индексировать
    # карточку дольше 5×2с (прежнее значение), из-за чего фото не успевали
    # загрузиться при первой попытке публикации.
    wb_nm_id_poll_attempts: int = 12
    wb_nm_id_poll_delay_seconds: float = 3.0

    # AI
    anthropic_api_key: str = ""
    # claude-sonnet-5 — действующий ID модели Anthropic (актуально на 2026 год, серия
    # "5" использует короткие ID без даты-суффикса, в отличие от старых поколений).
    # Sonnet-tier выбран сознательно: генерация SEO-текстов/ответов на отзывы — это
    # высокочастотная, не самая сложная задача, где Opus избыточен по цене/задержке.
    # При необходимости переопределяется через ANTHROPIC_MODEL в .env без правки кода.
    # ⚠️ Перед продом один раз проверьте это значение живым ключом — доступность
    # моделей может меняться, а это единственная строка, от которой зависит вся
    # генерация контента карточек.
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""

    # xAI Grok Imagine — генерация AI-инфографики для карточек (замена MVP
    # Pillow-рендера, см. app/services/ai/grok_imagine.py). Без ключа
    # используется старый Pillow-fallback (app/services/image_pipeline.py),
    # бот не падает.
    xai_api_key: str = ""
    xai_image_model: str = "grok-imagine-image-quality"

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

    # Веб-админка (SQLAdmin) — раздел «Admin-инструмент сопоставления категорий».
    # Панель монтируется, только если задан пароль — без него по умолчанию выключена
    # (не хотим случайно поднять бизнес-данные без авторизации).
    admin_panel_username: str = "admin"
    admin_panel_password: str = ""
    admin_panel_secret_key: str = ""

    @property
    def telegram_admin_id_list(self) -> list[int]:
        return [int(x) for x in self.telegram_admin_ids.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
