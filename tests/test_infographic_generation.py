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


async def _add_main_photo(session, service, product_id: int, url: str) -> None:
    """Добавляет товару главное фото с заданным storage_file.url — референс для
    Grok Imagine edit_infographic (раздел 5 ТЗ). url задаём напрямую, а не через
    save_bytes, чтобы контролировать локальный/публичный http(s) случай."""
    from app.db.models import StorageFile

    storage_file = StorageFile(path="/tmp/main-photo.jpg", url=url, content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product_id, storage_file.id, image_type="main", position=0)


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
    """С ключом xAI сервис всегда отдаёт минимум 2 варианта (акцент на материал
    и на совместимость с моделью) — с одной картинкой продавцу выбирать не из
    чего (раздел 7 ТЗ)."""
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")
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
    assert len(images) == 2
    assert len(captured_prompts) == 2
    # Оба варианта используют одни и те же факты о товаре, но разный акцент.
    for prompt in captured_prompts:
        assert "ALICARTUNING" in prompt
        assert "Lada Vesta" in prompt
        assert "Прочный материал" in prompt
    assert captured_prompts[0] != captured_prompts[1]

    product = await service.get_product(product_id)
    infographics = [img for img in product.images if img.image_type == ImageType.INFOGRAPHIC]
    assert len(infographics) == 2
    from pathlib import Path

    for infographic in infographics:
        # bytes сохранены как есть (не через image_pipeline) — читаем напрямую с диска.
        assert Path(infographic.storage_file.path).read_bytes() == b"GROK-IMAGE-BYTES"
    assert {img.id for img in images} == {img.id for img in infographics}


@pytest.mark.asyncio
async def test_falls_back_to_pillow_when_grok_fails(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def failing_grok_generate(self, prompt, **kwargs):
        raise MarketplaceAPIError("xAI недоступен", status_code=500)

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", failing_grok_generate)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    # Не упало — обе картинки всё равно созданы через Pillow-fallback
    # (с ключом xAI сервис всегда пытается отдать минимум 2 варианта).
    assert len(images) == 2
    assert all(img.image_type == ImageType.INFOGRAPHIC for img in images)


@pytest.mark.asyncio
async def test_falls_back_to_pillow_when_grok_raises_unexpected_exception(session, monkeypatch):
    """Раньше _render_infographic ловил только MarketplaceAPIError — любая другая
    ошибка Grok (сеть, неожиданный формат ответа) роняла инфографику целиком."""
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def failing_grok_generate(self, prompt, **kwargs):
        raise RuntimeError("неожиданный сбой, не MarketplaceAPIError")

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", failing_grok_generate)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    assert len(images) == 2
    assert all(img.image_type == ImageType.INFOGRAPHIC for img in images)


@pytest.mark.asyncio
async def test_fallback_bullets_used_when_ai_bullets_generation_fails(session, monkeypatch):
    """Раздел 1 ТЗ: сбой Claude при генерации буллетов не должен ронять
    инфографику — буллеты собираются из полей товара без обращения к AI.
    Ключ Anthropic задан намеренно (непустой), чтобы упасть именно в except,
    а не в guard «ключа нет совсем» — см. test_does_not_call_claude_when_anthropic_key_missing
    для этого отдельного случая."""
    monkeypatch.setattr(settings, "xai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")

    async def failing_bullets(self, title, draft):
        raise RuntimeError("Anthropic недоступен")

    monkeypatch.setattr(AIContentService, "generate_bullets", failing_bullets)

    service, product_id = await _make_product(session)
    bullets = await service._safe_generate_bullets(await service.get_product(product_id))

    assert len(bullets) >= 3
    joined = " ".join(bullets)
    assert "ABS-пластик" in joined or "Чёрный глянец" in joined or "Vesta" in joined

    images = await service.generate_infographic_images(product_id, count=1)
    assert len(images) == 1
    assert images[0].image_type == ImageType.INFOGRAPHIC


@pytest.mark.asyncio
async def test_infographic_works_without_any_ai_keys(session, monkeypatch):
    """Приёмочный сценарий: без ANTHROPIC (буллеты падают) и без XAI_API_KEY —
    инфографика всё равно возвращает валидные PNG-байты через чистый Pillow."""
    monkeypatch.setattr(settings, "xai_api_key", "")

    async def failing_bullets(self, title, draft):
        raise RuntimeError("нет ключа Anthropic")

    monkeypatch.setattr(AIContentService, "generate_bullets", failing_bullets)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    assert len(images) == 1
    product = await service.get_product(product_id)
    infographic = next(img for img in product.images if img.image_type == ImageType.INFOGRAPHIC)

    from pathlib import Path

    png_bytes = Path(infographic.storage_file.path).read_bytes()
    assert png_bytes.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_generates_requested_count_of_images(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=3)

    assert len(images) == 3
    assert [img.position for img in images] == [100, 101, 102]


# --- Раздел 3 ТЗ: пустой ANTHROPIC_API_KEY — без сетевого запроса вообще -------


@pytest.mark.asyncio
async def test_does_not_call_claude_when_anthropic_key_missing(session, monkeypatch):
    """Пустой ANTHROPIC_API_KEY — буллеты сразу из полей товара, БЕЗ единого
    вызова generate_bullets (не таймаут/ошибка авторизации, а полное отсутствие
    сетевого запроса — раньше кнопка «Инфографика» без ключа всё равно уходила
    в AsyncAnthropic.messages.create и ждала таймаут)."""
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "xai_api_key", "")

    call_count = {"n": 0}

    async def counting_generate_bullets(self, title, draft):
        call_count["n"] += 1
        return ["не должно быть вызвано"]

    monkeypatch.setattr(AIContentService, "generate_bullets", counting_generate_bullets)

    service, product_id = await _make_product(session)
    images = await service.generate_infographic_images(product_id, count=1)

    assert call_count["n"] == 0
    assert len(images) == 1
    product = await service.get_product(product_id)
    infographic = next(img for img in product.images if img.image_type == ImageType.INFOGRAPHIC)

    from pathlib import Path

    assert Path(infographic.storage_file.path).read_bytes().startswith(b"\x89PNG")


# --- Раздел 5 ТЗ: референс-фото товара для Grok Imagine edit_infographic ------


@pytest.mark.asyncio
async def test_uses_edit_when_main_photo_has_https_url(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    edit_calls: list[tuple[str, str]] = []

    async def fake_edit(self, prompt, image_url, **kwargs):
        edit_calls.append((prompt, image_url))
        return b"EDIT-BYTES"

    async def fail_generate(self, prompt, **kwargs):
        raise AssertionError("generate_infographic не должен вызываться, если edit сработал")

    monkeypatch.setattr(GrokImagineClient, "edit_infographic", fake_edit)
    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fail_generate)

    service, product_id = await _make_product(session)
    await _add_main_photo(session, service, product_id, "https://cdn.example.com/product.jpg")

    images = await service.generate_infographic_images(product_id, count=1)

    assert len(edit_calls) == 2  # с ключом xAI всегда минимум 2 варианта
    assert all(url == "https://cdn.example.com/product.jpg" for _, url in edit_calls)

    from pathlib import Path

    for image in images:
        assert Path(image.storage_file.path).read_bytes() == b"EDIT-BYTES"


@pytest.mark.asyncio
async def test_does_not_call_edit_when_photo_url_is_local(session, monkeypatch):
    """Без S3 storage_file.url — локальный путь на диске контейнера, xAI по
    нему ничего не скачает — штатный случай без S3, не ошибка."""
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def fail_edit(self, prompt, image_url, **kwargs):
        raise AssertionError("edit_infographic не должен вызываться для локального пути")

    async def fake_generate(self, prompt, **kwargs):
        return b"GEN-BYTES"

    monkeypatch.setattr(GrokImagineClient, "edit_infographic", fail_edit)
    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fake_generate)

    service, product_id = await _make_product(session)
    await _add_main_photo(session, service, product_id, "/app/storage/local-photo.jpg")

    images = await service.generate_infographic_images(product_id, count=1)

    from pathlib import Path

    for image in images:
        assert Path(image.storage_file.path).read_bytes() == b"GEN-BYTES"


@pytest.mark.asyncio
async def test_edit_failure_then_generate_then_ok(session, monkeypatch):
    """edit по референсу не обязан быть поддержан провайдером — сбой edit не
    должен сразу сдаваться на Pillow, сначала пробуем generate без референса."""
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def failing_edit(self, prompt, image_url, **kwargs):
        raise RuntimeError("edit endpoint недоступен")

    async def fake_generate(self, prompt, **kwargs):
        return b"GEN-AFTER-EDIT-FAIL"

    monkeypatch.setattr(GrokImagineClient, "edit_infographic", failing_edit)
    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fake_generate)

    service, product_id = await _make_product(session)
    await _add_main_photo(session, service, product_id, "https://cdn.example.com/product.jpg")

    images = await service.generate_infographic_images(product_id, count=1)

    from pathlib import Path

    for image in images:
        assert Path(image.storage_file.path).read_bytes() == b"GEN-AFTER-EDIT-FAIL"


@pytest.mark.asyncio
async def test_edit_and_generate_fail_goes_to_pillow(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def failing_edit(self, prompt, image_url, **kwargs):
        raise RuntimeError("edit endpoint недоступен")

    async def failing_generate(self, prompt, **kwargs):
        raise RuntimeError("generate endpoint тоже недоступен")

    monkeypatch.setattr(GrokImagineClient, "edit_infographic", failing_edit)
    monkeypatch.setattr(GrokImagineClient, "generate_infographic", failing_generate)

    service, product_id = await _make_product(session)
    await _add_main_photo(session, service, product_id, "https://cdn.example.com/product.jpg")

    images = await service.generate_infographic_images(product_id, count=1)

    from pathlib import Path

    for image in images:
        assert Path(image.storage_file.path).read_bytes().startswith(b"\x89PNG")
