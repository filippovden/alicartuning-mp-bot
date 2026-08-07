"""Хендлер «Сгенерировать AI-инфографику» (gengraphic:) — сообщения о процессе
и ошибке, отправка фото в чат после успеха (см. критические правки, часть 2.4).
"""

from __future__ import annotations

import pytest

from app.bot.handlers.new_product import generate_graphic
from app.config import settings
from app.services.ai.client import AIContentService
from app.services.ai.grok_imagine import GrokImagineClient
from app.services.product_service import ProductService


class _FakeUser:
    def __init__(self, uid: int):
        self.id = uid


class _FakeMessage:
    def __init__(self):
        self.answered: list[str] = []
        self.answered_photos: list[tuple[object, str | None]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self

    async def answer_photo(self, photo, caption: str | None = None, **kwargs) -> "_FakeMessage":
        self.answered_photos.append((photo, caption))
        return self


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        return None


async def _make_product(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=1, username="u", full_name="U")
    product = await service.create_draft(user.id)
    await service.update_fields(product.id, title="ALICARTUNING / Тест", brand="ALICARTUNING", car_model="Lada Vesta")
    return service, product.id


async def _fake_bullets(self, title, draft):
    return ["Раз", "Два", "Три"]


@pytest.mark.asyncio
async def test_gengraphic_without_key_uses_fallback_wording_and_sends_photo(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    service, product_id = await _make_product(session)
    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert any("XAI_API_KEY не задан" in t for t in callback.message.answered)
    assert len(callback.message.answered_photos) == 1
    _, caption = callback.message.answered_photos[0]
    assert caption == "✅ Инфографика добавлена к карточке."


@pytest.mark.asyncio
async def test_gengraphic_with_key_uses_grok_wording_and_sends_photo(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def fake_generate(self, prompt, **kwargs):
        return b"GROK-BYTES"

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fake_generate)

    service, product_id = await _make_product(session)
    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert any("через Grok Imagine" in t for t in callback.message.answered)
    assert len(callback.message.answered_photos) == 1


@pytest.mark.asyncio
async def test_gengraphic_shows_clear_error_on_unexpected_failure(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "")

    async def failing_bullets(self, title, draft):
        raise RuntimeError("AI недоступен")

    monkeypatch.setattr(AIContentService, "generate_bullets", failing_bullets)

    service, product_id = await _make_product(session)
    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert any("Не удалось сгенерировать инфографику" in t for t in callback.message.answered)
    assert callback.message.answered_photos == []
