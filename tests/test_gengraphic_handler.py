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
async def test_gengraphic_without_key_sends_photo(session, monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    service, product_id = await _make_product(session)
    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert len(callback.message.answered_photos) == 1
    _, caption = callback.message.answered_photos[0]
    assert caption == "✅ Инфографика добавлена к карточке."


@pytest.mark.asyncio
async def test_gengraphic_with_key_uses_grok_wording_and_sends_photo(session, monkeypatch):
    """С ключом xAI хендлер показывает 2 варианта (раздел 7 ТЗ) — по одной
    фотографии на каждый, с подписями «Вариант 1»/«Вариант 2», плюс итоговое
    сообщение, что оба варианта добавлены."""
    monkeypatch.setattr(settings, "xai_api_key", "test-key")
    monkeypatch.setattr(AIContentService, "generate_bullets", _fake_bullets)

    async def fake_generate(self, prompt, **kwargs):
        return b"GROK-BYTES"

    monkeypatch.setattr(GrokImagineClient, "generate_infographic", fake_generate)

    service, product_id = await _make_product(session)
    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert any("через Grok Imagine" in t for t in callback.message.answered)
    assert len(callback.message.answered_photos) == 2
    captions = [caption for _, caption in callback.message.answered_photos]
    assert captions == ["✅ Вариант 1", "✅ Вариант 2"]
    assert any("Оба варианта добавлены" in t for t in callback.message.answered)


@pytest.mark.asyncio
async def test_gengraphic_shows_clear_error_on_unexpected_failure(session, monkeypatch):
    """Буллеты теперь падают в fallback (не роняют инфографику), поэтому чтобы
    проверить обработку реального сбоя, ломаем сам product_service.generate_infographic_images —
    хендлер должен показать короткий понятный текст, а не стектрейс, и не отправлять фото."""

    async def failing_generate_infographic_images(self, product_id, count=1):
        raise RuntimeError("неожиданный сбой сервиса")

    monkeypatch.setattr(
        ProductService, "generate_infographic_images", failing_generate_infographic_images
    )

    service, product_id = await _make_product(session)
    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert any("Не удалось сгенерировать инфографику" in t for t in callback.message.answered)
    assert callback.message.answered_photos == []


class _FakeStorageFile:
    def __init__(self, path: str):
        self.path = path


class _FakeImage:
    def __init__(self, image_id: int, path: str):
        self.id = image_id
        self.storage_file = _FakeStorageFile(path)


class _FakeProduct:
    def __init__(self, images: list[_FakeImage]):
        self.images = images


@pytest.mark.asyncio
async def test_gengraphic_reports_missing_file_instead_of_crashing(session, monkeypatch, tmp_path):
    """Раздел 4 ТЗ: если storage_file.path есть в БД, но файла нет на диске
    (например, volume очищен) — хендлер не должен падать на Path.read_bytes,
    а обязан явно сообщить, что файл не найден, и не пытаться отправить фото."""
    monkeypatch.setattr(settings, "xai_api_key", "")

    service, product_id = await _make_product(session)

    missing_path = str(tmp_path / "does-not-exist.png")
    fake_image = _FakeImage(image_id=999, path=missing_path)

    async def fake_generate_infographic_images(self, product_id, count=1):
        return [fake_image]

    async def fake_get_product(self, product_id):
        return _FakeProduct(images=[fake_image])

    monkeypatch.setattr(ProductService, "generate_infographic_images", fake_generate_infographic_images)
    monkeypatch.setattr(ProductService, "get_product", fake_get_product)

    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert any("не найден на диске" in t for t in callback.message.answered)
    assert callback.message.answered_photos == []


@pytest.mark.asyncio
async def test_gengraphic_sends_in_memory_bytes_if_disk_missing(session, monkeypatch, tmp_path):
    """Раздел 8 ТЗ: если path на диске битый, но байты только что сгенерированной
    картинки лежат прямо на объекте (_preview_bytes) — хендлер всё равно должен
    отправить фото, не читая (и не спотыкаясь о) диск."""
    monkeypatch.setattr(settings, "xai_api_key", "")

    service, product_id = await _make_product(session)

    missing_path = str(tmp_path / "does-not-exist.png")
    fake_image = _FakeImage(image_id=999, path=missing_path)
    fake_image._preview_bytes = b"IN-MEMORY-BYTES"

    async def fake_generate_infographic_images(self, product_id, count=1):
        return [fake_image]

    monkeypatch.setattr(ProductService, "generate_infographic_images", fake_generate_infographic_images)

    callback = _FakeCallback(f"gengraphic:{product_id}")

    await generate_graphic(callback, service)

    assert len(callback.message.answered_photos) == 1
    _, caption = callback.message.answered_photos[0]
    assert caption == "✅ Инфографика добавлена к карточке."
