"""Минимум MIN_PRODUCT_PHOTOS фото перед «Готово» в диалоге /new (критические
исправления, п.7) — см. app/bot/handlers/new_product.photos_done.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import texts
from app.bot.handlers.new_product import photos_done
from app.services.ai.client import AIContentService
from app.services.product_service import ProductService


class _FakeMessage:
    def __init__(self):
        self.sent_texts: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.sent_texts.append(text)
        return self


class _FakeCallback:
    def __init__(self):
        self.message = _FakeMessage()
        self.alerts: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.alerts.append((text, show_alert))


def _make_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


async def _make_draft(session) -> tuple[ProductService, int]:
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=1, username="u", full_name="U")
    product = await service.create_draft(user.id)
    return service, product.id


@pytest.mark.asyncio
async def test_photos_done_blocks_below_minimum(session):
    service, product_id = await _make_draft(session)
    state = _make_state(1)
    await state.update_data(product_id=product_id, photos=[101, 102], pending_attrs=[])

    cb = _FakeCallback()
    await photos_done(cb, state, service)

    assert cb.alerts == [(texts.need_more_photos(2), True)]
    assert cb.message.sent_texts == [texts.need_more_photos(2)]
    # Диалог не продвинулся дальше — pending_attrs не пересчитывался.
    assert (await state.get_data())["pending_attrs"] == []


@pytest.mark.asyncio
async def test_photos_done_blocks_with_zero_photos(session):
    service, product_id = await _make_draft(session)
    state = _make_state(2)
    await state.update_data(product_id=product_id, photos=[], pending_attrs=[])

    cb = _FakeCallback()
    await photos_done(cb, state, service)

    assert "минимум 3" in cb.alerts[0][0]
    assert "Добавьте ещё 3" in cb.alerts[0][0]


@pytest.mark.asyncio
async def test_photos_done_proceeds_at_exact_minimum(session, monkeypatch):
    async def fake_generate_full_content(self, draft):
        return {
            "title": "ALICARTUNING / Тест",
            "description": "Описание.",
            "bullets": ["Плюс"],
            "keywords": ["тест"],
        }

    monkeypatch.setattr(AIContentService, "generate_full_content", fake_generate_full_content)

    service, product_id = await _make_draft(session)
    state = _make_state(3)
    await state.update_data(product_id=product_id, photos=[101, 102, 103], pending_attrs=[])

    cb = _FakeCallback()
    await photos_done(cb, state, service)

    assert cb.alerts == [(None, False)]  # обычный callback.answer(), без блокирующего алерта
    assert any("успешно" not in t and t for t in cb.message.sent_texts)  # дошли до генерации превью
    current_state = await state.get_state()
    assert current_state == "NewProductStates:confirm"
