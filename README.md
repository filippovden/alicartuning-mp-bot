# ALICARTUNING MP Bot

Telegram AI-менеджер маркетплейсов для магазина **ALICARTUNING** — автоматизирует
создание карточек товаров на Wildberries и Ozon: собирает данные о товаре в диалоге,
генерирует SEO-название/описание через LLM (Claude), проверяет карточку по чек-листу
и публикует товар через API обеих площадок.

Реализация соответствует MVP из технического задания: диалоговый Telegram-бот,
FastAPI backend, PostgreSQL, интеграция с Wildberries Content API и Ozon Seller API,
генерация контента через Anthropic API.

## Архитектура

```
Telegram Bot (aiogram) ──┐
                          ├──> ProductService ──> PostgreSQL
FastAPI backend ─────────┘         │
                                    ├──> AIContentService (Anthropic Claude)
                                    ├──> WildberriesClient (Content API)
                                    └──> OzonClient (Seller API)

Celery + Redis — фоновая публикация и будущая обработка изображений (V2).
```

Структура проекта:

```
app/
  bot/                  # Telegram-бот (aiogram): диалог /new, /list, /status, /edit
  api/                  # FastAPI роуты (/newProduct, /saveDraft, /publish/{id}, ...)
  db/                   # SQLAlchemy-модели и сессия (см. ER-диаграмму в ТЗ, раздел 6)
  services/
    marketplaces/        # Клиенты Wildberries и Ozon API + маппинг полей
    ai/                   # Промпты и обёртка над Anthropic API
    validation.py          # Предпубликационные проверки карточки (раздел 10 ТЗ)
    product_service.py    # Оркестрация: черновик → AI → валидация → публикация
    storage.py             # Хранилище файлов (фото товаров)
  worker/                # Celery-приложение для фоновых задач
alembic/                 # Миграции БД
tests/                   # Юнит-тесты (валидация, клиенты WB/Ozon — на моках)
```

## Быстрый старт (Docker)

1. Скопируйте `.env.example` в `.env` и заполните ключи:

   ```bash
   cp .env.example .env
   ```

   Обязательно укажите:
   - `TELEGRAM_BOT_TOKEN` — токен бота от @BotFather
   - `WB_API_KEY` — ключ Wildberries Seller API (кабинет продавца → Настройки → Доступ к API)
   - `OZON_CLIENT_ID`, `OZON_API_KEY` — из кабинета продавца Ozon
   - `ANTHROPIC_API_KEY` — ключ Anthropic (для генерации текста карточек)

2. Запустите всё через docker-compose:

   ```bash
   docker compose up --build
   ```

   Поднимутся: PostgreSQL, Redis, FastAPI backend (`:8000`), Telegram-бот (polling) и Celery-воркер.
   При старте `api`-сервис сам применит миграции (`alembic upgrade head`).

3. Откройте бота в Telegram и отправьте `/start`, затем `/new`, чтобы создать первый товар.

## Локальный запуск без Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# поднимите PostgreSQL и Redis локально (или через docker compose up postgres redis)
cp .env.example .env   # и поправьте DATABASE_URL/REDIS_URL на localhost

alembic upgrade head

# в одном терминале — backend
uvicorn app.main:app --reload

# в другом — бот
python -m app.bot.main
```

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

Тесты покрывают:
- правила валидации карточки (раздел 10 ТЗ): обязательные поля, длина текста,
  запрещённые слова, числовые проверки, уникальность артикула;
- клиенты Wildberries и Ozon API (на моках `respx`, без реальных запросов к площадкам).

## Команды бота

| Команда | Назначение |
|---|---|
| `/start` | приветствие, регистрация пользователя |
| `/new` | диалоговое создание нового товара (раздел 8 ТЗ) |
| `/list` | список товаров и черновиков |
| `/status <ID>` | статус публикации товара и лог попыток |
| `/edit <ID>` | редактирование поля товара |
| `/cancel` | отмена текущего диалога |

## Как это работает

1. **Диалог `/new`** — бот последовательно спрашивает категорию, название, бренд,
   артикул, цены, штрихкод, комплектацию, материал, цвет, габариты, вес и фото.
   После выбора категории бот пытается подтянуть обязательные характеристики
   категории через API WB/Ozon и добавляет вопросы по ним динамически.
2. **Генерация контента** — `AIContentService` вызывает Claude по шаблонам промптов
   из раздела 12 ТЗ и формирует SEO-название, описание, буллеты преимуществ и
   ключевые слова в стиле ALICARTUNING.
3. **Валидация** — перед публикацией `validate_product()` проверяет карточку по
   чек-листу (обязательные поля, длина текста, запрещённые слова, фото, числа,
   уникальность SKU) и выводит список проблем, если карточка не готова.
4. **Публикация** — `ProductService.publish()` формирует запросы через
   `mapping.py` (раздел 9 ТЗ) и вызывает `WildberriesClient.create_card()` и
   `OzonClient.import_products()`, результат сохраняется в `PUBLISH_LOGS`.

## Известные ограничения MVP

- Сопоставление внутренней категории с `subjectID` (WB) и `category_id`/`type_id`
  (Ozon) в текущей версии создаётся по названию без строгой валидации — в проде
  стоит добавить админ-интерфейс подбора категории по дереву API.
- Обработка изображений (удаление фона, инфографика), анализ конкурентов и
  AI-аналитика продаж относятся к V2/V3 из ТЗ и не входят в этот MVP-каркас.
- Хранилище файлов по умолчанию — локальный диск (`STORAGE_LOCAL_PATH`); для
  продакшена нужно подключить S3-совместимое хранилище (заготовка полей в `.env`).
