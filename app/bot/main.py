import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import admin, analytics, clone_product, common, competitors, list_products, new_product, reviews
from app.bot.middlewares import AccessControlMiddleware, DbSessionMiddleware
from app.config import settings

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
    if not settings.telegram_admin_id_list:
        logger.warning(
            "TELEGRAM_ADMIN_IDS не задан — бот отклонит ЛЮБЫЕ входящие сообщения "
            "(whitelist пуст, доступ закрыт по умолчанию)."
        )

    bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Redis FSM storage — состояние диалога переживает рестарт контейнера бота
    # (в отличие от MemoryStorage, которое живёт только в памяти процесса).
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # AccessControlMiddleware — outer middleware на Update: применяется ко ВСЕМ
    # входящим апдейтам централизованно, до роутинга по хендлерам и до открытия
    # сессии БД (см. app/bot/middlewares.py).
    dp.update.outer_middleware(AccessControlMiddleware())
    dp.update.middleware(DbSessionMiddleware())

    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(new_product.router)
    dp.include_router(competitors.router)
    dp.include_router(analytics.router)
    dp.include_router(reviews.router)
    dp.include_router(list_products.router)
    dp.include_router(clone_product.router)

    logger.info("Бот ALICARTUNING запущен, начинаю polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
