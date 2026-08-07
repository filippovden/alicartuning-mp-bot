from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.services.category_search import sync_ozon_category_tree
from app.services.marketplaces.base import MarketplaceAPIError

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    # Fail-closed, симметрично AccessControlMiddleware (app/bot/middlewares.py):
    # пустой TELEGRAM_ADMIN_IDS не должен открывать доступ никому. Практически эта
    # ветка уже недостижима — outer-middleware отклоняет апдейт раньше, чем он
    # дойдёт до хендлера, — но дублировать fail-open здесь было бы миной на случай
    # изменения порядка middleware или прямого вызова хендлера.
    admin_ids = settings.telegram_admin_id_list
    return bool(admin_ids) and message.from_user.id in admin_ids


@router.message(Command("synccategories"))
async def cmd_sync_categories(message: Message, session) -> None:
    """Пересобирает кэш дерева категорий Ozon (см. app/services/category_search.py).

    Дерево может содержать несколько тысяч узлов — запрос к Ozon может занять
    десятки секунд, поэтому в продакшене эту операцию стоит вынести в Celery-задачу
    (см. app/worker/celery_app.py: sync_ozon_categories_task) и вызывать по расписанию.
    """
    if not _is_admin(message):
        await message.answer("Команда доступна только администраторам.")
        return

    await message.answer("⏳ Синхронизирую дерево категорий Ozon...")
    try:
        count = await sync_ozon_category_tree(session)
    except MarketplaceAPIError as exc:
        logger.exception("Не удалось синхронизировать категории Ozon")
        await message.answer(f"❌ Не удалось получить дерево категорий Ozon: {exc.message}")
        return

    await message.answer(f"✅ Готово. Загружено {count} категорий (пар category_id/type_id).")
