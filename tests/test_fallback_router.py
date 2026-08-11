"""common.fallback_router (раздел 2 ТЗ Senior Backend) — устаревшая/не подходящая
к состоянию кнопка должна ответить понятным текстом, а не быть молча
проигнорирована aiogram'ом (роутер подключается в main.py последним).
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.bot.handlers import common


class _FakeMessage:
    def __init__(self):
        self.answered: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answer_called = False

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answer_called = True


@pytest.mark.asyncio
async def test_photos_done_wrong_state_gives_clear_answer():
    callback = _FakeCallback("photos_done")

    await common.photos_done_wrong_state(callback)

    assert callback.answer_called is True
    assert callback.message.answered == [texts.PHOTOS_NOT_ACTIVE]


@pytest.mark.asyncio
async def test_unhandled_callback_gives_clear_answer_instead_of_silence():
    callback = _FakeCallback("some:stale:callback")

    await common.unhandled_callback(callback)

    assert callback.answer_called is True
    assert callback.message.answered == [texts.STALE_BUTTON]
