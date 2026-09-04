"""Хендлер /shop (раздел 0 и 4 ТЗ v7): публичная витрина WB отдаёт 403/429 с этого
сервера, гарантировать разбор чужого магазина нельзя — ручной /shop (кнопки на
него больше не ведут, см. app/bot/keyboards.py:help_kb) теперь всегда отвечает
одной короткой честной фразой, без сетевого запроса, без URL и без
"Использование: /shop [ссылка]" (см. app/bot/handlers/competitors.py).
"""

from __future__ import annotations

import pytest

from app.bot import texts
from app.bot.handlers import competitors as competitors_handler


class _FakeUser:
    def __init__(self, uid: int):
        self.id = uid


class _FakeMessage:
    def __init__(self, text: str, user: _FakeUser | None = None):
        self.text = text
        self.from_user = user or _FakeUser(1)
        self.answered: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self


@pytest.mark.asyncio
async def test_shop_without_link_shows_showcase_unavailable(session):
    message = _FakeMessage("/shop")
    await competitors_handler.cmd_shop(message, session)
    assert message.answered == [texts.SHOWCASE_UNAVAILABLE]


@pytest.mark.asyncio
async def test_shop_with_link_still_shows_showcase_unavailable_and_no_network_call(session):
    """Раздел 4 ТЗ v7 и смоук-тест: ручной /shop с реальной ссылкой не должен
    показывать URL/403/traceback — только короткую фразу. cmd_shop больше не
    импортирует fetch_wb_shop (см. app/bot/handlers/competitors.py) — сходить
    в сеть отсюда физически нечем."""
    assert not hasattr(competitors_handler, "fetch_wb_shop")

    message = _FakeMessage("/shop https://www.wildberries.ru/seller/445717")
    await competitors_handler.cmd_shop(message, session)

    assert message.answered == [texts.SHOWCASE_UNAVAILABLE]
    assert "catalog.wb.ru" not in message.answered[0]
    assert "wildberries.ru" not in message.answered[0]


@pytest.mark.asyncio
async def test_market_without_query_shows_showcase_unavailable(monkeypatch):
    called = {"n": 0}

    async def fail_if_called(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("cmd_competitors не должен ходить в сеть")

    monkeypatch.setattr(competitors_handler, "search_wb_competitors", fail_if_called)

    message = _FakeMessage("/market")
    await competitors_handler.cmd_competitors(message)

    assert message.answered == [texts.SHOWCASE_UNAVAILABLE]
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_market_with_query_still_shows_showcase_unavailable(monkeypatch):
    called = {"n": 0}

    async def fail_if_called(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("cmd_competitors не должен ходить в сеть")

    monkeypatch.setattr(competitors_handler, "search_wb_competitors", fail_if_called)

    message = _FakeMessage("/market накладки на зеркала Granta")
    await competitors_handler.cmd_competitors(message)

    assert message.answered == [texts.SHOWCASE_UNAVAILABLE]
    assert called["n"] == 0
