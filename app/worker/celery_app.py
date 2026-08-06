"""Celery-воркер для фоновых задач: асинхронная публикация, генерация изображений (раздел 5 ТЗ)."""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "alicartuning_mp_bot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json", timezone="Europe/Moscow")


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
