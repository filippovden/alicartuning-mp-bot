"""ProductService.generate_infographic_images: AI-генерация через Grok Imagine
(xAI) с fallback на старый Pillow-рендер (см. app/services/ai/grok_imagine.py,
app/services/image_pipeline.py). Часть 2 критических правок — MVP-инфографика
(Claude пишет буллеты → Pillow рисует текст) заменяется настоящей AI-генерацией
изображения, но без XAI_API_KEY или при сбое API бот не должен падать.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db.models import ImageType
from app.services.ai.client import AIContentService
from app.services.ai.grok_imagine import GrokImagineClient
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.product_service import ProductService


async def _make_product(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=1, username="u", full_name="U")
    product = await service.create_draft(user.id)
    await service.update_fields(
        product.id,
        title="ALICARTUNING / Накладки на зеркала",
        brand="ALICARTUNING",
        car_model="Lada Vesta",
        material="ABS-пластик",
        color="Чёрный глянец",
    )
    return service, product.id


async def _fake_bullets(self, title, draft):
    return ["Прочный материал", "Простая установка", "Премиум-дизайн"]


@pytest.mark.asyncio
async def test_uses_pillow_fallback_when_no_xai_key(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    grok_called = False

    async def fake_grok_generate(self, prompt, **kwargs):
        nonlocal grok_called
        grok_called = True
        return b"SHOULD-NOT-BE-CALLED"

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fake_grok_generate)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    assert grok_called is False
    assert len(images) == 1
    assert images[0].image_type == ImageType.INFOGRAPHIC

    product = await service.get_product(product_id)
    infographic = next(img for img in product.images if img.image_type == ImageType.INFOGRAPHIC)
    assert infographic.storage_file.path.endswith(".png")


@pytest.mark.asyncio
async def test_uses_grok_imagine_when_xai_key_set(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    captured_prompts = []

    async def fake_grok_generate(self, prompt, **kwargs):
        captured_prompts.append(prompt)
        return b"GROK-IMAGE-BYTES"

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fake_grok_generate)

    pillow_called = False

    def fake_pillow_generate(bullets, title=None, **kwargs):
        nonlocal pillow_called
        pillow_called = True
        return b"PILLOW-SHOULD-NOT-RUN"

    from app.services import image_pipeline

    monkeypatch.setattr(image_pipeline, "generate_infographic", fake_pillow_generate)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    assert pillow_called is False
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "ALICARTUNING" in prompt
    assert "Lada Vesta" in prompt
    assert "Прочный материал" in prompt

    product = await service.get_product(product_id)
    infographic = next(img for img in product.images if img.image_type == ImageType.INFOGRAPHIC)
    # bytes сохранены как есть (не через image_pipeline) — читаем напрямую с диска.
    from pathlib import Path

    assert Path(infographic.storage_file.path).read_bytes() == b"GROK-IMAGE-BYTES"
    assert images[0].id == infographic.id


@pytest.mark.asyncio
async def test_falls_back_to_pillow_when_grok_fails(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def failing_grok_generate(self, prompt, **kwargs):
        raise MarketplaceAPIError("xAI недоступен", status_code=500)

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", failing_grok_generate)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    # Не упало — картинка всё равно создана через Pillow-fallback.
    assert len(images) == 1
    assert images[0].image_type == ImageType.INFOGRAPHIC


@pytest.mark.asyncio
async def test_generates_requested_count_of_images(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=3)

    assert len(images) == 3
    assert [img.position for img in images] == [100, 101, 102]
