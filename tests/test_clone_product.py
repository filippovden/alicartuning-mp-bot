"""Клонирование карточки под другую модель авто (см. ProductService.clone_product,
app/bot/handlers/clone_product.py) — одна деталь автотюнинга часто подходит
нескольким моделям Lada, и проще размножить карточку, чем заполнять с нуля.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import texts
from app.bot.handlers.clone_product import (
    MAX_BATCH_CLONE_MODELS,
    _build_vendor_code,
    clone_batch_models,
    clone_batch_start,
    clone_batch_template,
    clone_batch_template_skip,
    clone_button,
    clone_car_model,
    clone_vendor_code,
    cmd_clone,
)
from app.bot.states import CloneBatchStates, CloneProductStates
from app.db.models import Attribute, Category, CategoryAttr, ImageType, ProductStatus, StorageFile
from app.services.ai.client import AIContentService
from app.services.product_service import ProductService


class _FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.answered: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        return None


def _make_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


async def _fake_full_content(self, draft):
    return {
        "title": f"ALICARTUNING / Тест для {draft.car_model}",
        "description": "Описание.",
        "bullets": ["Раз", "Два", "Три"],
        "keywords": ["тест"],
    }


async def _make_source_product(session, **overrides):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=overrides.pop("telegram_id", 1), username="u", full_name="U")
    category = Category(name="Накладки на зеркала", wb_subject_id=212)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    product = await service.create_draft(user.id)
    defaults = dict(
        title="ALICARTUNING / Накладки Vesta",
        description="Старое описание",
        vendor_code="ART-SRC-1",
        barcode="4600000000001",
        brand="ALICARTUNING",
        material="ABS-пластик",
        color="Чёрный глянец",
        car_model="Lada Vesta",
        package_contents="2 шт.",
        cost_price=500,
        price=1200,
        weight_g=300,
        length_mm=500,
        width_mm=200,
        height_mm=50,
        category_id=category.id,
        wb_nm_id="123456",
        ozon_product_id="987654",
    )
    defaults.update(overrides)
    await service.update_fields(product.id, **defaults)

    storage_file = StorageFile(path="/tmp/photo.jpg", url="https://cdn.example.com/1.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product.id, storage_file.id, image_type="main", position=0)

    category_attr = CategoryAttr(
        category_id=category.id, marketplace="wildberries", external_attr_id="1", name="Материал"
    )
    session.add(category_attr)
    await session.commit()
    await session.refresh(category_attr)
    session.add(Attribute(product_id=product.id, category_attr_id=category_attr.id, value="ABS-пластик"))
    await session.commit()

    return service, product.id


# --- ProductService.clone_product ---------------------------------------------


@pytest.mark.asyncio
async def test_clone_product_copies_expected_fields(session):
    service, source_id = await _make_source_product(session)
    source = await service.get_product(source_id)
    clone = await service.clone_product(source_id)

    assert clone.id != source_id
    assert clone.category_id == source.category_id
    assert clone.brand == source.brand
    assert clone.material == source.material
    assert clone.color == source.color
    assert clone.package_contents == source.package_contents
    assert clone.cost_price == source.cost_price
    assert clone.price == source.price
    assert clone.weight_g == source.weight_g
    assert clone.length_mm == source.length_mm
    assert clone.width_mm == source.width_mm
    assert clone.height_mm == source.height_mm


@pytest.mark.asyncio
async def test_clone_product_does_not_copy_identity_fields(session):
    service, source_id = await _make_source_product(session)
    clone = await service.clone_product(source_id)

    assert clone.vendor_code is None
    assert clone.barcode is None
    assert clone.title is None
    assert clone.description is None
    assert clone.wb_nm_id is None
    assert clone.ozon_product_id is None
    assert clone.car_model is None


@pytest.mark.asyncio
async def test_clone_product_status_is_draft(session):
    service, source_id = await _make_source_product(session)
    clone = await service.clone_product(source_id)
    assert clone.status == ProductStatus.DRAFT


@pytest.mark.asyncio
async def test_clone_product_copies_images(session):
    service, source_id = await _make_source_product(session)
    source = await service.get_product(source_id)
    clone = await service.clone_product(source_id)

    assert len(clone.images) == 1
    assert clone.images[0].storage_file_id == source.images[0].storage_file_id
    assert clone.images[0].image_type == ImageType.MAIN


@pytest.mark.asyncio
async def test_clone_product_not_found_raises(session):
    service = ProductService(session)
    with pytest.raises(ValueError):
        await service.clone_product(999999)


# --- Хендлеры: одиночное клонирование ------------------------------------------


@pytest.mark.asyncio
async def test_cmd_clone_starts_car_model_flow(session):
    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    message = _FakeMessage(text=f"/clone {source_id}")

    await cmd_clone(message, state, service)

    assert any("создан на основе" in t for t in message.answered)
    assert any(t == texts.ASK_CAR_MODEL for t in message.answered)
    assert await state.get_state() == "CloneProductStates:car_model"


@pytest.mark.asyncio
async def test_cmd_clone_without_args_shows_usage(session):
    service = ProductService(session)
    state = _make_state(1)
    message = _FakeMessage(text="/clone")

    await cmd_clone(message, state, service)

    assert any("Использование" in t for t in message.answered)


@pytest.mark.asyncio
async def test_cmd_clone_not_found(session):
    service = ProductService(session)
    state = _make_state(1)
    message = _FakeMessage(text="/clone 999999")

    await cmd_clone(message, state, service)

    assert any(t == texts.NOT_FOUND for t in message.answered)


@pytest.mark.asyncio
async def test_clone_button_starts_flow(session):
    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    callback = _FakeCallback(f"clone:{source_id}")

    await clone_button(callback, state, service)

    assert await state.get_state() == "CloneProductStates:car_model"


@pytest.mark.asyncio
async def test_clone_product_copies_attributes(session):
    service, source_id = await _make_source_product(session)
    source = await service.get_product(source_id)
    clone = await service.clone_product(source_id)

    assert len(source.attributes) == 1
    assert len(clone.attributes) == 1
    assert clone.attributes[0].category_attr_id == source.attributes[0].category_attr_id
    assert clone.attributes[0].value == source.attributes[0].value
    assert clone.attributes[0].variant_id is None


@pytest.mark.asyncio
async def test_clone_car_model_asks_vendor_code_without_generating_content(session, monkeypatch):
    generated = False

    async def _spy(self, draft):
        nonlocal generated
        generated = True
        return await _fake_full_content(self, draft)

    monkeypatch.setattr(AIContentService, "generate_full_content", _spy)

    service, source_id = await _make_source_product(session)
    clone = await service.clone_product(source_id)

    state = _make_state(1)
    await state.set_state(CloneProductStates.car_model)
    await state.update_data(product_id=clone.id)

    message = _FakeMessage(text="Lada Granta")
    await clone_car_model(message, state, service)

    product = await service.get_product(clone.id)
    assert product.car_model == "Lada Granta"
    assert product.title is None  # текст ещё не сгенерирован — нет vendor_code
    assert not generated
    assert await state.get_state() == "CloneProductStates:vendor_code"
    assert any(t == texts.ASK_CLONE_VENDOR_CODE for t in message.answered)


@pytest.mark.asyncio
async def test_clone_vendor_code_generates_content_and_shows_preview_with_publish_button(session, monkeypatch):
    monkeypatch.setattr(AIContentService, "generate_full_content", _fake_full_content)

    service, source_id = await _make_source_product(session)
    clone = await service.clone_product(source_id)
    await service.update_fields(clone.id, car_model="Lada Granta")

    state = _make_state(1)
    await state.set_state(CloneProductStates.vendor_code)
    await state.update_data(product_id=clone.id)

    message = _FakeMessage(text="ART-GRANTA-1")
    await clone_vendor_code(message, state, service)

    product = await service.get_product(clone.id)
    assert product.vendor_code == "ART-GRANTA-1"
    assert "Granta" in product.title
    assert await state.get_state() is None

    preview = next(t for t in message.answered if "Черновик карточки" in t)
    assert "Артикул:</b> ART-GRANTA-1" in preview
    assert "Модель:</b> Lada Granta" in preview


# --- Хендлеры: пакетное клонирование --------------------------------------------


@pytest.mark.asyncio
async def test_clone_batch_start_asks_for_models(session):
    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    callback = _FakeCallback(f"clonebatch:{source_id}")

    await clone_batch_start(callback, state)

    assert await state.get_state() == "CloneBatchStates:car_models"
    assert any(str(MAX_BATCH_CLONE_MODELS) in t for t in callback.message.answered)


@pytest.mark.asyncio
async def test_clone_batch_models_asks_for_vendor_code_template(session):
    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    await state.set_state(CloneBatchStates.car_models)
    await state.update_data(source_product_id=source_id)

    message = _FakeMessage(text="Vesta, Granta, Priora")
    await clone_batch_models(message, state)

    assert await state.get_state() == "CloneBatchStates:vendor_code_template"
    assert any("Шаблон артикула" in t for t in message.answered)


@pytest.mark.asyncio
async def test_clone_batch_template_creates_drafts_with_substituted_sku(session, monkeypatch):
    monkeypatch.setattr(AIContentService, "generate_full_content", _fake_full_content)

    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    await state.set_state(CloneBatchStates.vendor_code_template)
    await state.update_data(source_product_id=source_id, car_models=["Vesta", "Granta", "Priora"])

    message = _FakeMessage(text="ART-{model}")
    await clone_batch_template(message, state, service)

    assert await state.get_state() is None
    summary = message.answered[-1]
    assert "Создано черновиков: 3" in summary
    assert "ART-VESTA" in summary
    assert "ART-GRANTA" in summary
    assert "ART-PRIORA" in summary


@pytest.mark.asyncio
async def test_clone_batch_template_skip_generates_fallback_sku(session, monkeypatch):
    monkeypatch.setattr(AIContentService, "generate_full_content", _fake_full_content)

    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    await state.set_state(CloneBatchStates.vendor_code_template)
    await state.update_data(source_product_id=source_id, car_models=["Vesta"])

    callback = _FakeCallback("skip:clone_template")
    await clone_batch_template_skip(callback, state, service)

    assert await state.get_state() is None
    summary = callback.message.answered[-1]
    assert "Создано черновиков: 1" in summary
    assert "ART-CLONE-" in summary


def test_build_vendor_code_substitutes_model_uppercase_no_spaces():
    assert _build_vendor_code("ART-{model}", "Lada Vesta", clone_id=1) == "ART-LADAVESTA"


def test_build_vendor_code_appends_model_when_placeholder_missing():
    assert _build_vendor_code("ART", "Granta", clone_id=1) == "ART-GRANTA"


def test_build_vendor_code_fallback_when_no_template():
    assert _build_vendor_code(None, "Granta", clone_id=42) == "ART-CLONE-42"


@pytest.mark.asyncio
async def test_clone_batch_models_rejects_more_than_max(session):
    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    await state.set_state(CloneBatchStates.car_models)
    await state.update_data(source_product_id=source_id)

    message = _FakeMessage(text="Vesta, Granta, Priora, Niva, XRAY, Kalina")
    await clone_batch_models(message, state)

    assert any("максимум" in t.lower() for t in message.answered)
    source = await service.get_product(source_id)
    remaining = await service.list_products(source.user_id)
    assert len(remaining) == 1  # ни одного клона не создалось


@pytest.mark.asyncio
async def test_clone_batch_models_empty_input(session):
    service, source_id = await _make_source_product(session)
    state = _make_state(1)
    await state.set_state(CloneBatchStates.car_models)
    await state.update_data(source_product_id=source_id)

    message = _FakeMessage(text="   ")
    await clone_batch_models(message, state)

    assert any("Не нашёл ни одной модели" in t for t in message.answered)
