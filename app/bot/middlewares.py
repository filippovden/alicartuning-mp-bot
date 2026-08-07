from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.config import settings
from app.db.session import async_session_factory
from app.services.product_service import ProductService

DENIED_TEXT = "Доступ запрещён."

# Поля Update, у которых может быть отправитель (aiogram.types.Update).
_USER_EVENT_ATTRS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "message_reaction",
)


def _extract_user_id(update: Update) -> int | None:
    for attr in _USER_EVENT_ATTRS:
        obj = getattr(update, attr, None)
        if obj is None:
            continue
        # Большинство типов Update отдают отправителя через from_user, но
        # PollAnswer и MessageReactionUpdated — через user (см. aiogram.types).
        user = getattr(obj, "from_user", None) or getattr(obj, "user", None)
        if user is not None:
            return user.id
    return None


class AccessControlMiddleware(BaseMiddleware):
    """Централизованный whitelist доступа (outer middleware на dp.update).

    Пропускает ЛЮБЫЕ update только от telegram_id из settings.telegram_admin_id_list.
    Пустой список — отказ всем (fail closed), а не открытый доступ по умолчанию.
    Работает на уровне Update, то есть применяется ко всем типам событий бота
    (сообщения, callback-запросы и т.д.), а не только к отдельным хендлерам.
    """

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        allowed_ids = settings.telegram_admin_id_list
        user_id = _extract_user_id(event)

        if not allowed_ids or user_id is None or user_id not in allowed_ids:
            await self._deny(event)
            return None

        return await handler(event, data)

    @staticmethod
    async def _deny(update: Update) -> None:
        if update.message is not None:
            await update.message.answer(DENIED_TEXT)
        elif update.callback_query is not None:
            await update.callback_query.answer(DENIED_TEXT, show_alert=True)
        # для прочих типов апдейтов (my_chat_member и т.п.) осмысленный ответ
        # пользователю невозможен — молча игнорируем.


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
