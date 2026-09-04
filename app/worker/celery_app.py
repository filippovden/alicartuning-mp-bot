"""Celery-воркер для фоновых задач: асинхронная публикация, генерация изображений (раздел 5 ТЗ)."""

import logging

from celery import Celery

from app.config import settings

celery_app = Celery(
    "alicartuning_mp_bot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json", timezone="Europe/Moscow")

celery_app.conf.beat_schedule = {
    "sync-ozon-categories-daily": {
        "task": "sync_ozon_categories",
        "schedule": 24 * 60 * 60,  # раз в сутки — дерево категорий меняется редко
    },
    "poll-reviews-hourly": {
        "task": "poll_reviews",
        "schedule": 60 * 60,  # раздел 4 ТЗ, V3: «уведомления о новых отзывах»
    },
    "snapshot-competitor-prices-daily": {
        "task": "snapshot_competitor_prices",
        "schedule": 24 * 60 * 60,  # тайминг-аналитика требует ежедневной истории цен
    },
    "check-wb-card-status": {
        "task": "check_wb_card_status",
        "schedule": 45 * 60,  # раздел E.2 ТЗ: раз в 30-60 минут, best-effort (см. ProductService.check_wb_card_status)
    },
    "snapshot-tracked-shops-daily": {
        "task": "snapshot_tracked_shops",
        "schedule": 24 * 60 * 60,  # тренд цены магазина-конкурента (/shop) копится с первого запроса
    },
}


@celery_app.task(name="publish_product")
def publish_product_task(product_id: int) -> dict:
    """Фоновая публикация карточки на WB/Ozon (вызывается из бота при высокой нагрузке)."""
    import asyncio

    from app.db.session import async_session_factory
    from app.services.product_service import ProductService

    async def _run() -> dict:
        async with async_session_factory() as session:
            service = ProductService(session)
            summary = await service.publish(product_id)
            return {
                "product_id": product_id,
                "wb": summary.wb.status.value if summary.wb else None,
                "ozon": summary.ozon.status.value if summary.ozon else None,
            }

    return asyncio.run(_run())


@celery_app.task(name="process_product_images")
def process_product_images_task(product_id: int) -> dict:
    """Фоновая обработка фото товара: удаление фона + фирменный шаблон (раздел 11 ТЗ, V2)."""
    import asyncio

    from app.db.session import async_session_factory
    from app.services.product_service import ProductService

    async def _run() -> dict:
        async with async_session_factory() as session:
            service = ProductService(session)
            processed = await service.process_product_images(product_id)
            return {"product_id": product_id, "processed": len(processed)}

    return asyncio.run(_run())


@celery_app.task(name="generate_infographic")
def generate_infographic_task(product_id: int, count: int = 1) -> dict:
    """Фоновая генерация инфографики преимуществ (раздел 11, 12 ТЗ, V2)."""
    import asyncio

    from app.db.session import async_session_factory
    from app.services.product_service import ProductService

    async def _run() -> dict:
        async with async_session_factory() as session:
            service = ProductService(session)
            images = await service.generate_infographic_images(product_id, count=count)
            return {"product_id": product_id, "created": len(images)}

    return asyncio.run(_run())


@celery_app.task(name="sync_ozon_categories")
def sync_ozon_categories_task() -> dict:
    """Периодическая синхронизация дерева категорий Ozon (см. app/services/category_search.py)."""
    import asyncio

    from app.db.session import async_session_factory
    from app.services.category_search import sync_ozon_category_tree

    async def _run() -> dict:
        async with async_session_factory() as session:
            count = await sync_ozon_category_tree(session)
            return {"synced": count}

    return asyncio.run(_run())


@celery_app.task(name="poll_reviews")
def poll_reviews_task() -> dict:
    """Периодический опрос новых отзывов WB/Ozon; уведомляет админов о негативных
    отзывах в Telegram (раздел 4 ТЗ, V3 — «уведомления о новых отзывах»)."""
    import asyncio

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    from app.config import settings
    from app.db.models import ReviewSentiment
    from app.db.session import async_session_factory
    from app.services.marketplaces.base import MarketplaceAPIError
    from app.services.review_service import sync_ozon_reviews, sync_wb_reviews

    async def _run() -> dict:
        new_reviews = []
        async with async_session_factory() as session:
            try:
                new_reviews += await sync_wb_reviews(session)
            except MarketplaceAPIError:
                logging.getLogger(__name__).warning("poll_reviews: WB Feedbacks API недоступен", exc_info=True)
            try:
                new_reviews += await sync_ozon_reviews(session)
            except MarketplaceAPIError:
                logging.getLogger(__name__).warning("poll_reviews: Ozon Reviews API недоступен", exc_info=True)

        negative = [r for r in new_reviews if r.sentiment == ReviewSentiment.NEGATIVE and not r.is_answered]
        if negative and settings.telegram_bot_token and settings.telegram_admin_id_list:
            bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            text = f"⚠️ Новых негативных отзывов: {len(negative)}. Проверьте /reviews в боте."
            for admin_id in settings.telegram_admin_id_list:
                try:
                    await bot.send_message(admin_id, text)
                except Exception:
                    logging.getLogger(__name__).warning("Не удалось отправить уведомление админу %s", admin_id, exc_info=True)
            await bot.session.close()

        return {"new_reviews": len(new_reviews), "negative": len(negative)}

    return asyncio.run(_run())


@celery_app.task(name="snapshot_competitor_prices")
def snapshot_competitor_prices_task() -> dict:
    """Ежедневный снимок цен конкурентов по всем товарам с указанным SKU/моделью
    авто — накапливает историю для тайминг-аналитики (app/services/pricing_intelligence.py).

    После снимков проактивно проверяет тренд по свежеснятым товарам и, если хотя
    бы по одному тренд уже значимый (см. TREND_SIGNIFICANT_PCT), шлёт админам
    один дайджест — раньше тренд можно было увидеть только вручную через
    /analytics <ID>, и никто не узнавал о нём, пока сам не спросит.

    Тем же прогоном (не отдельным beat, раздел 4 ТЗ v4) считает короткий
    SEO-дайджест по опубликованным товарам — см. seo_coach.build_daily_seo_digest:
    попадают только карточки, где реально есть что поправить (цена/название),
    остальное не спамит."""
    import asyncio

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from sqlalchemy import select

    from app.config import settings
    from app.db.models import Product
    from app.db.session import async_session_factory
    from app.services.pricing_intelligence import (
        check_significant_price_trends,
        format_trend_digest,
        snapshot_competitor_prices,
    )
    from app.services.seo_coach import build_daily_seo_digest, format_seo_digest

    async def _run() -> dict:
        async with async_session_factory() as session:
            stmt = select(Product).where(Product.vendor_code.is_not(None))
            products = list((await session.execute(stmt)).scalars().all())

            snapshotted: list[Product] = []
            for product in products:
                snapshot = await snapshot_competitor_prices(session, product)
                if snapshot is not None:
                    snapshotted.append(product)

            alerts = await check_significant_price_trends(session, snapshotted)
            seo_digest_lines = await build_daily_seo_digest(products)

            messages = []
            if alerts:
                messages.append(format_trend_digest(alerts))
            if seo_digest_lines:
                messages.append(format_seo_digest(seo_digest_lines))

            if messages and settings.telegram_bot_token and settings.telegram_admin_id_list:
                bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                for admin_id in settings.telegram_admin_id_list:
                    for text in messages:
                        try:
                            await bot.send_message(admin_id, text)
                        except Exception:
                            logging.getLogger(__name__).warning(
                                "Не удалось отправить дайджест админу %s", admin_id, exc_info=True
                            )
                await bot.session.close()

            return {
                "products_checked": len(products),
                "snapshots_taken": len(snapshotted),
                "trend_alerts": len(alerts),
                "seo_digest_items": len(seo_digest_lines),
            }

    return asyncio.run(_run())


@celery_app.task(name="snapshot_tracked_shops")
def snapshot_tracked_shops_task() -> dict:
    """Ежедневный ре-снимок магазинов-конкурентов, по которым хоть раз запускали
    /shop — без этого тренд цены магазина никогда бы не появился, ведь после
    первого ручного запроса больше никто не станет сам заново вызывать /shop
    каждый день (см. app/services/pricing_intelligence.py: save_shop_snapshot,
    get_tracked_shop_seller_ids)."""
    import asyncio

    from app.db.session import async_session_factory
    from app.services.competitor_analysis import CompetitorAnalysisError, fetch_wb_shop
    from app.services.pricing_intelligence import get_tracked_shop_seller_ids, save_shop_snapshot

    async def _run() -> dict:
        async with async_session_factory() as session:
            seller_ids = await get_tracked_shop_seller_ids(session)

            snapshotted = 0
            for seller_id in seller_ids:
                try:
                    report = await fetch_wb_shop(seller_id)
                except CompetitorAnalysisError:
                    logging.getLogger(__name__).warning(
                        "snapshot_tracked_shops: не удалось обновить магазин %s", seller_id, exc_info=True
                    )
                    continue
                await save_shop_snapshot(session, seller_id, report)
                snapshotted += 1

            return {"shops_tracked": len(seller_ids), "snapshots_taken": snapshotted}

    return asyncio.run(_run())


@celery_app.task(name="check_wb_card_status")
def check_wb_card_status_task() -> dict:
    """Периодическая best-effort проверка статуса опубликованных карточек WB
    (раздел E.2 ТЗ) — см. ProductService.check_wb_card_status про то, почему
    это заготовка, а не полноценная модерация с причинами отклонения."""
    import asyncio

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from sqlalchemy import select

    from app.config import settings
    from app.db.models import Product
    from app.db.session import async_session_factory
    from app.services.product_service import ProductService

    async def _run() -> dict:
        async with async_session_factory() as session:
            service = ProductService(session)
            stmt = select(Product).where(Product.wb_nm_id.is_not(None))
            products = list((await session.execute(stmt)).scalars().all())

            notices = []
            for product in products:
                notice = await service.check_wb_card_status(product)
                if notice:
                    notices.append(notice)

            if notices and settings.telegram_bot_token and settings.telegram_admin_id_list:
                bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                text = "🔎 <b>Статус карточек WB изменился:</b>\n" + "\n".join(f"• {n}" for n in notices)
                for admin_id in settings.telegram_admin_id_list:
                    try:
                        await bot.send_message(admin_id, text)
                    except Exception:
                        logging.getLogger(__name__).warning(
                            "Не удалось отправить уведомление о статусе WB админу %s", admin_id, exc_info=True
                        )
                await bot.session.close()

            return {"products_checked": len(products), "status_changes": len(notices)}

    return asyncio.run(_run())
