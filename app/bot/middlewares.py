from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import async_session_factory
from app.services.product_service import ProductService


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию БД и ProductService на каждое обновление (см. архитектуру, раздел 5 ТЗ)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            data["product_service"] = ProductService(session)
            return await handler(event, data)
