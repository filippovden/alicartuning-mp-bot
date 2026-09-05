"""Прогресс-полоска для долгих операций (срез v8) — раздел 0 ТЗ: «анимация»
процентов в Telegram — это ОДНО сообщение, которое бот несколько раз
редактирует (edit_text), а не поток отдельных сообщений и не настоящая
покадровая анимация (Telegram не отдаёт прогресс нейросети посекундно, а
крутить процент без реальных точек в коде — обман пользователя и путь к
флуд-лимиту на editMessageText).

Использование — см. app/bot/handlers/quick_create.py (сборка карточки) и
app/bot/handlers/new_product.py (публикация):

    handle = await start_progress(message.answer, "Собираю карточку")
    ...
    await set_progress(handle, 35, "Категория")
    ...
    await fail_progress(handle, "Не получилось собрать текст.", reply_markup=retry_kb)
"""

from __future__ import annotations

import html
import logging
import time
from asyncio import sleep as _sleep

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

BAR_LENGTH = 10
DEFAULT_TITLE = "Собираю карточку"
# Раздел 1 ТЗ v8: не чаще раза в 0.8-1.0с — иначе бот словит флуд-лимит на
# editMessageText, если реальные шаги кода идут быстрее (например, категория
# сразу пропущена).
MIN_EDIT_INTERVAL = 0.9


def render_progress(percent: int, step: str, title: str = DEFAULT_TITLE) -> str:
    """Раздел 1 ТЗ v8: 10 клеток, filled = round(percent / 10). Шаг и заголовок
    экранируются — оба всегда наши собственные строки, но сообщение уходит с
    parse_mode=HTML по умолчанию (см. app/bot/main.py), так что случайный
    «<» в будущем не должен ронять отправку (та же категория бага, что уже
    чинили в WELCOME/product_preview)."""
    percent = max(0, min(100, percent))
    filled = round(percent / 10)
    bar = "█" * filled + "░" * (BAR_LENGTH - filled)
    return f"⏳ {html.escape(title)}\n\n{bar} {percent}%\n{html.escape(step)}"


class ProgressHandle:
    """Обёртка вокруг одного сообщения-полоски.

    _disabled=True после первого неудачного edit (сообщение слишком старое,
    удалено и т.п. — раздел 0 ТЗ v8: «не падать, просто прислать следующее
    обычное сообщение») — дальнейшие set_progress/fail_progress на этом
    хэндле молча ничего не делают, а вызывающий код просто продолжает как
    обычно своими message.answer(...)."""

    def __init__(self, message: Message, title: str) -> None:
        self.message = message
        self.title = title
        self._last_edit_at = time.monotonic()
        self._disabled = False

    async def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_edit_at
        if elapsed < MIN_EDIT_INTERVAL:
            await _sleep(MIN_EDIT_INTERVAL - elapsed)

    async def update(self, percent: int, step: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        if self._disabled:
            return
        await self._wait_for_slot()
        text = render_progress(percent, step, title=self.title)
        try:
            await self.message.edit_text(text, reply_markup=reply_markup)
        except TelegramRetryAfter as exc:
            # Раздел 1 ТЗ v8: один повтор после RetryAfter, потом забить на полоску.
            await _sleep(exc.retry_after)
            try:
                await self.message.edit_text(text, reply_markup=reply_markup)
            except Exception:
                logger.warning("Прогресс: повтор edit после RetryAfter тоже не прошёл", exc_info=True)
                self._disabled = True
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                pass  # тот же текст и клавиатура — не ошибка, просто нечего менять
            else:
                logger.warning("Прогресс: edit_text не прошёл (%s) — дальше не пытаемся", exc)
                self._disabled = True
        except Exception:
            logger.warning("Прогресс: неожиданная ошибка edit_text — дальше не пытаемся", exc_info=True)
            self._disabled = True
        finally:
            self._last_edit_at = time.monotonic()

    async def fail(self, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        """Правит полоску на финальный текст ошибки — раздел 2.A ТЗ v8: сбой
        AI не должен оставлять полоску зависшей на середине шкалы. Отключает
        хэндл — дальнейшие set_progress на нём молча ничего не делают."""
        if not self._disabled:
            await self._wait_for_slot()
            try:
                await self.message.edit_text(f"⚠️ {html.escape(text)}", reply_markup=reply_markup)
            except Exception:
                logger.warning("Прогресс: edit_text для текста ошибки не прошёл", exc_info=True)
        self._disabled = True
        self._last_edit_at = time.monotonic()


async def start_progress(answer, title: str = DEFAULT_TITLE, *, step: str = "Начинаю") -> ProgressHandle:
    message = await answer(render_progress(0, step, title=title))
    return ProgressHandle(message, title)


async def set_progress(handle: ProgressHandle, percent: int, step: str) -> None:
    await handle.update(percent, step)


async def fail_progress(handle: ProgressHandle, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await handle.fail(text, reply_markup=reply_markup)


async def finish_progress(handle: ProgressHandle) -> None:
    """По ТЗ v8 полоску не обязательно скрывать — «лучше оставить, заказчик
    видит, что не зависло». Просто помечает хэндл завершённым, чтобы случайный
    повторный set_progress после этого молча ничего не делал."""
    handle._disabled = True
