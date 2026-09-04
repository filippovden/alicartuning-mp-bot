from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.services.category_search import sync_ozon_category_tree
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.marketplaces.ozon import NO_API_KEY_STATUS_CODE

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _sync_categories_error_text(exc: MarketplaceAPIError) -> str:
    """Человеческий текст для чата по MarketplaceAPIError без URL/HTTP-деталей
    (раздел 3 ТЗ v6) — путь и сырой код остаются только в логе сервера."""
    if exc.status_code == NO_API_KEY_STATUS_CODE:
        return "Нет ключа Ozon — справочник не обновить. Выкладка на Wildberries от этого не зависит."
    if exc.status_code == 404:
        return "Ozon не отдал дерево категорий. Напиши тому, кто ставил бота."
    if exc.status_code in (401, 403):
        return "Ключ Ozon не подходит. Проверь Client ID и API-ключ в кабинете."
    return "Ozon сейчас не отвечает. Попробуй через несколько минут."


def is_admin_id(user_id: int | None) -> bool:
    # Fail-closed, симметрично AccessControlMiddleware (app/bot/middlewares.py):
    # пустой TELEGRAM_ADMIN_IDS не должен открывать доступ никому. Практически эта
    # ветка уже недостижима — outer-middleware отклоняет апдейт раньше, чем он
    # дойдёт до хендлера, — но дублировать fail-open здесь было бы миной на случай
    # изменения порядка middleware или прямого вызова хендлера.
    admin_ids = settings.telegram_admin_id_list
    return bool(admin_ids) and user_id in admin_ids


def _is_admin(message: Message) -> bool:
    return is_admin_id(message.from_user.id if message.from_user else None)


async def sync_categories(answer, session) -> None:
    """Тело /synccategories, вынесенное отдельно от Message — раздел 5 ТЗ v6:
    кнопка «Категории Ozon» на экране «Ещё» зовёт тот же код, только шлёт ответ
    через callback.message.answer, а не message.answer."""
    await answer("⏳ Синхронизирую дерево категорий Ozon...")
    try:
        count = await sync_ozon_category_tree(session)
    except MarketplaceAPIError as exc:
        logger.warning("Не удалось синхронизировать категории Ozon: %s", exc.message, exc_info=True)
        await answer(f"❌ {_sync_categories_error_text(exc)}")
        return

    await answer(
        f"Справочник категорий Ozon обновлён: {count} позиций.\n"
        "Теперь бот сможет подставлять категорию при создании товара."
    )


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
    await sync_categories(message.answer, session)
