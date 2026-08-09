"""Новый UX (раздел A-F ТЗ): постоянное меню, быстрое создание товара,
«Опубликовать все» после batch-клонирования, /drafts.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import texts
from app.bot.handlers import common, list_products, new_product, quick_create
from app.bot.states import QuickCreateStates
from app.db.models import Category, StorageFile
from app.services.ai.client import AIContentGenerationError
from app.services.product_service import ProductService


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = "u"
        self.full_name = "U"


class _FakeMessage:
    def __init__(self, text: str = "", user: _FakeUser | None = None):
        self.text = text
        self.from_user = user or _FakeUser(1)
        self.answered: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self


class _FakeCallback:
    def __init__(self, data: str, user: _FakeUser | None = None):
        self.data = data
        self.from_user = user or _FakeUser(1)
        self.message = _FakeMessage(user=self.from_user)

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        return None


def _make_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


class _FakeAIService:
    def __init__(self, parsed: dict | None = None, raise_parse_error: bool = False):
        self._parsed = parsed or {}
        self._raise_parse_error = raise_parse_error

    async def parse_quick_description(self, text: str) -> dict:
        if self._raise_parse_error:
            raise AIContentGenerationError("boom")
        return self._parsed

    async def generate_full_content(self, draft) -> dict:
        return {
            "title": f"ALICARTUNING / {draft.draft_title or 'Товар'} для {draft.car_model}",
            "description": "Подробное описание. " * 5,
            "bullets": ["Прочность", "Простая установка"],
            "keywords": ["alicartuning"],
        }


async def _seed_photos(session, service: ProductService, product_id: int, count: int) -> list[int]:
    ids = []
    for i in range(count):
        storage_file = StorageFile(path=f"/tmp/p{i}.jpg", url=f"https://cdn.example.com/{i}.jpg", content_type="image/jpeg")
        session.add(storage_file)
        await session.commit()
        await session.refresh(storage_file)
        await service.add_image(product_id, storage_file.id, image_type="main", position=i)
        ids.append(storage_file.id)
    return ids


# --- A. Меню ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_menu_new_product_shows_mode_choice():
    message = _FakeMessage()
    await common.menu_new_product(message)
    assert message.answered == [texts.NEW_PRODUCT_CHOOSE_MODE]


@pytest.mark.asyncio
async def test_menu_list_reuses_cmd_list(session):
    service = ProductService(session)
    await service.get_or_create_user(telegram_id=1, username="u", full_name="U")
    message = _FakeMessage(user=_FakeUser(1))
    await common.menu_list(message, service)
    assert any("нет товаров" in t.lower() for t in message.answered)


@pytest.mark.asyncio
async def test_menu_more_shows_commands():
    message = _FakeMessage()
    await common.menu_more(message)
    assert message.answered == [texts.MORE_MENU]


# --- B. Быстрое создание товара ------------------------------------------------


@pytest.mark.asyncio
async def test_quick_flow_full_happy_path_reaches_checklist_without_extra_questions(session):
    """Все поля, включая размеры/вес, распознаны из текста — бот не должен
    задавать лишних вопросов, кроме обязательного артикула (раздел B ТЗ)."""
    parsed = {
        "draft_title": "Накладки зеркал BMW-стиль",
        "car_model": "Lada Granta",
        "material": "ABS-пластик",
        "color": "Чёрный глянец",
        "price": 990,
        "package_contents": "2 шт.",
        "length_mm": 300,
        "width_mm": 150,
        "height_mm": 50,
        "weight_g": 400,
    }
    service = ProductService(session, ai_service=_FakeAIService(parsed=parsed))
    state = _make_state(1)
    callback = _FakeCallback("newmode:quick")
    await quick_create.start_quick_mode(callback, state, service)
    assert await state.get_state() == "QuickCreateStates:photos"

    data = await state.get_data()
    await _seed_photos(session, service, data["product_id"], texts.MIN_PRODUCT_PHOTOS)
    await state.update_data(photos=[1, 2, 3])

    done_cb = _FakeCallback("photos_done")
    await quick_create.quick_photos_done(done_cb, state)
    assert await state.get_state() == "QuickCreateStates:description"

    message = _FakeMessage("Накладки зеркал BMW-стиль, Lada Granta, ABS, чёрный глянец, цена 990")
    await quick_create.quick_description(message, state, service, session)

    # Размеры/вес угаданы — сразу спрашиваем только артикул.
    assert await state.get_state() == "QuickCreateStates:vendor_code"
    assert any("артикул" in t.lower() for t in message.answered)

    vendor_message = _FakeMessage("ART-GRANTA-990")
    await quick_create.quick_vendor_code(vendor_message, state, service)

    assert await state.get_state() is None
    product = await service.get_product(data["product_id"])
    assert product.vendor_code == "ART-GRANTA-990"
    assert product.car_model == "Lada Granta"
    assert product.price == 990
    assert product.length_mm == 300 and product.weight_g == 400
    checklist = vendor_message.answered[-1]
    assert "Черновик готов" in checklist
    assert "Нет: артикул" not in checklist


@pytest.mark.asyncio
async def test_quick_flow_asks_dimensions_and_weight_when_not_guessed(session):
    parsed = {
        "draft_title": "Карман двери",
        "car_model": "Lada Vesta",
        "material": "Полипропилен",
        "color": "Чёрный",
        "price": None,
        "package_contents": None,
        "length_mm": None,
        "width_mm": None,
        "height_mm": None,
        "weight_g": None,
    }
    service = ProductService(session, ai_service=_FakeAIService(parsed=parsed))
    state = _make_state(2)
    callback = _FakeCallback("newmode:quick", user=_FakeUser(2))
    await quick_create.start_quick_mode(callback, state, service)
    data = await state.get_data()
    await _seed_photos(session, service, data["product_id"], texts.MIN_PRODUCT_PHOTOS)
    await state.update_data(photos=[1, 2, 3])
    await quick_create.quick_photos_done(_FakeCallback("photos_done", user=_FakeUser(2)), state)

    message = _FakeMessage("Карман двери, Lada Vesta, полипропилен, чёрный")
    await quick_create.quick_description(message, state, service, session)
    assert await state.get_state() == "QuickCreateStates:vendor_code"

    vendor_message = _FakeMessage("ART-VESTA-1")
    await quick_create.quick_vendor_code(vendor_message, state, service)
    assert await state.get_state() == "QuickCreateStates:dimensions"

    dims_message = _FakeMessage("300x150x50")
    await quick_create.quick_dimensions(dims_message, state, service)
    assert await state.get_state() == "QuickCreateStates:weight"

    weight_message = _FakeMessage("450")
    await quick_create.quick_weight(weight_message, state, service)
    assert await state.get_state() is None

    product = await service.get_product(data["product_id"])
    assert product.length_mm == 300 and product.weight_g == 450
    checklist = weight_message.answered[-1]
    assert "Черновик готов" in checklist


@pytest.mark.asyncio
async def test_quick_description_parse_failure_asks_to_retry(session):
    service = ProductService(session, ai_service=_FakeAIService(raise_parse_error=True))
    state = _make_state(3)
    callback = _FakeCallback("newmode:quick", user=_FakeUser(3))
    await quick_create.start_quick_mode(callback, state, service)
    await state.set_state(QuickCreateStates.description)

    message = _FakeMessage("что-то нечленораздельное")
    await quick_create.quick_description(message, state, service, session)

    assert message.answered[-1] == texts.QUICK_PARSE_FAILED
    # Состояние не продвинулось — можно ввести описание ещё раз.
    assert await state.get_state() == "QuickCreateStates:description"


# --- D. «Опубликовать все» -----------------------------------------------------


@pytest.mark.asyncio
async def test_publish_all_reports_per_product_results(session, monkeypatch):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=10, username="u", full_name="U")

    category = Category(name="Тест категория", wb_subject_id=1)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    ready = await service.create_draft(user.id)
    await service.update_fields(
        ready.id,
        title="ALICARTUNING / Товар",
        description="Достаточно длинное описание товара для валидации. " * 3,
        brand="ALICARTUNING",
        vendor_code="ART-READY-1",
        price=1000,
        category_id=category.id,
        weight_g=100,
        length_mm=100,
        width_mm=100,
        height_mm=100,
    )
    incomplete = await service.create_draft(user.id)

    callback = _FakeCallback(f"publishall:{ready.id},{incomplete.id}")
    await new_product.publish_all(callback, service)

    summary = callback.message.answered[-1]
    assert f"#{incomplete.id}" in summary
    assert "не прошёл проверку" in summary


# --- C.5 /drafts ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_drafts_lists_only_drafts_and_continue_resumes_missing_field(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=20, username="u", full_name="U")

    category = Category(name="Черновик категория")
    session.add(category)
    await session.commit()
    await session.refresh(category)

    draft = await service.create_draft(user.id)
    await service.update_fields(draft.id, title="Черновик без остального", category_id=category.id)

    message = _FakeMessage(user=_FakeUser(20))
    await list_products.cmd_drafts(message, service)
    assert any(f"#{draft.id}" in t for t in message.answered)

    state = _make_state(20)
    callback = _FakeCallback(f"continuedraft:{draft.id}", user=_FakeUser(20))
    await list_products.continue_draft(callback, state, service)

    # У черновика уже есть title, но нет vendor_code — должны попасть на этот шаг.
    assert await state.get_state() == "NewProductStates:vendor_code"
    assert any("Артикул" in t for t in callback.message.answered)


@pytest.mark.asyncio
async def test_no_drafts_message(session):
    service = ProductService(session)
    await service.get_or_create_user(telegram_id=21, username="u", full_name="U")
    message = _FakeMessage(user=_FakeUser(21))
    await list_products.cmd_drafts(message, service)
    assert message.answered == [texts.NO_DRAFTS]


# --- C.1 Прогресс в пошаговом /new ----------------------------------------------


@pytest.mark.asyncio
async def test_step_by_step_shows_progress_and_skips_brand_question(session):
    service = ProductService(session)
    state = _make_state(30)
    message = _FakeMessage("/new", user=_FakeUser(30))
    await new_product.cmd_new(message, state, service)

    assert message.answered[0].startswith("Шаг 1/13")
    assert await state.get_state() == "NewProductStates:category"

    data = await state.get_data()
    product = await service.get_product(data["product_id"])
    assert product.brand == "ALICARTUNING"
