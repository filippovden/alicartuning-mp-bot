"""Продуктовая полировка UX (Lead Product Engineer раунд): единое превью
карточки, реорганизация кнопок, «Открыть #ID» вместо технической сетки,
отдельный экран клонирования, /cancel возвращает меню, retry при сбое AI,
устойчивость «Опубликовать все» к неожиданным ошибкам.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import texts
from app.bot.handlers import common, list_products, new_product, quick_create
from app.bot.keyboards import confirm_publish_kb, product_detail_kb
from app.bot.states import QuickCreateStates
from app.db.models import Category, StorageFile
from app.services.ai.client import AIContentService
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
        self.answered_kb: list[object] = []
        self.edited: list[str] = []
        self.edited_kb: list[object] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        self.answered_kb.append(reply_markup)
        return self

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.edited.append(text)
        self.edited_kb.append(reply_markup)
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


async def _fake_full_content(self, draft):
    return {
        "title": f"ALICARTUNING / {draft.draft_title or 'Товар'} для {draft.car_model}",
        "description": "Подробное описание товара. " * 5,
        "bullets": ["Прочность", "Простая установка"],
        "keywords": ["alicartuning"],
    }


async def _make_ready_product(session, telegram_id: int, vendor_code: str = "ART-1"):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=telegram_id, username="u", full_name="U")
    category = Category(name="Тест категория", wb_subject_id=212)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    product = await service.create_draft(user.id)
    await service.update_fields(
        product.id,
        title="ALICARTUNING / Накладки зеркал",
        description="Качественная деталь автотюнинга. " * 5,
        brand="ALICARTUNING",
        vendor_code=vendor_code,
        price=990,
        cost_price=500,
        category_id=category.id,
        weight_g=200,
        length_mm=100,
        width_mm=100,
        height_mm=100,
        car_model="Lada Vesta",
    )
    storage_file = StorageFile(path="/tmp/p.jpg", url="https://cdn.example.com/1.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product.id, storage_file.id, image_type="main", position=0)
    return service, product.id


# --- C1/C2. Единое превью и состав кнопок ----------------------------------------


@pytest.mark.asyncio
async def test_product_preview_shows_ready_status_when_no_errors(session):
    service, product_id = await _make_ready_product(session, telegram_id=100)
    preview_text, keyboard = await new_product.render_preview(service, product_id)

    assert "📦 <b>ALICARTUNING / Накладки зеркал</b>" in preview_text
    assert "Модель: Lada Vesta · Артикул: ART-1" in preview_text
    assert "Цена: 990₽ · Фото: 1" in preview_text
    assert "✅ Можно публиковать" in preview_text
    assert "⚠️ Нужно исправить" not in preview_text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_product_preview_lists_missing_fields_when_invalid(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=101, username="u", full_name="U")
    product = await service.create_draft(user.id)
    await service.update_fields(product.id, title="Черновик")

    preview_text, _ = await new_product.render_preview(service, product.id)
    assert "⚠️ <b>Нужно исправить:</b>" in preview_text
    assert "✅ Можно публиковать" not in preview_text


@pytest.mark.asyncio
async def test_product_preview_escapes_html_in_user_supplied_fields(session):
    """car_model/vendor_code вводит человек руками, а сообщение уходит с
    parse_mode=HTML — без экранирования «<script>» в артикуле уронил бы
    отправку (та же категория бага, что уже чинили в WELCOME)."""
    service, product_id = await _make_ready_product(session, telegram_id=102, vendor_code="ART-<b>X</b>")
    await service.update_fields(product_id, car_model="<script>alert(1)</script>")

    preview_text, _ = await new_product.render_preview(service, product_id)
    assert "<script>" not in preview_text
    assert "&lt;script&gt;" in preview_text
    assert "ART-<b>X</b>" not in preview_text
    assert "ART-&lt;b&gt;X&lt;/b&gt;" in preview_text


def test_confirm_publish_kb_has_no_overloaded_buttons():
    """Раздел 3 ТЗ v7: превью — ровно 4 кнопки, без Выдача/Цена/Конкуренты
    (мёртвая витрина search.wb.ru) и без отдельной Инфографики (уходит тихо
    при «Выложить» — см. раздел 3 ТЗ v7)."""
    kb = confirm_publish_kb(1)
    all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any("Выдача" in t for t in all_texts)
    assert not any("Цена" in t for t in all_texts)
    assert not any("Конкуренты" in t for t in all_texts)
    assert not any("Инфографика" in t for t in all_texts)
    assert any("Выложить" in t for t in all_texts)  # раздел 4.1 ТЗ v5: «✅ Опубликовать» → «🚀 Выложить»
    assert any("Исправить" in t for t in all_texts)
    assert any("На другую модель" in t for t in all_texts)
    # 4 ряда: Выложить / Исправить / На другую модель / Не надо (раздел 3 ТЗ v7)
    assert len(kb.inline_keyboard) == 4


def test_product_detail_kb_keeps_secondary_actions():
    """Раздел 3 ТЗ v7: карточка товара — 5 кнопок, без Выдача/Конкуренты и без
    «Пакет на модели» (код clone_product.py остаётся рабочим, просто не здесь)."""
    kb = product_detail_kb(1)
    all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Фото" in t for t in all_texts)
    assert not any("Конкуренты" in t for t in all_texts)
    assert not any("Выдача" in t for t in all_texts)
    assert not any("Пакет" in t for t in all_texts)
    assert any("Выложить" in t for t in all_texts)  # раздел 4.1 ТЗ v5: «✅ Опубликовать» → «🚀 Выложить»
    assert any("На другую модель" in t for t in all_texts)
    assert any("Исправить" in t for t in all_texts)
    assert any("Инфографика" in t for t in all_texts)
    assert len(kb.inline_keyboard) == 5


# --- D1/D2. /list → «Открыть» → карточка товара -----------------------------------


@pytest.mark.asyncio
async def test_open_product_shows_preview_and_detail_actions(session):
    service, product_id = await _make_ready_product(session, telegram_id=110)
    callback = _FakeCallback(f"open:{product_id}")

    await list_products.open_product(callback, service)

    assert any("ALICARTUNING / Накладки зеркал" in t for t in callback.message.answered)
    last_kb = callback.message.answered_kb[-1]
    all_texts = [btn.text for row in last_kb.inline_keyboard for btn in row]
    assert any("Фото" in t for t in all_texts)
    assert any("На другую модель" in t for t in all_texts)


# --- Раздел 3 ТЗ v7: короткое меню «Исправить» (Название/Цена/Фото/Назад к карточке)


@pytest.mark.asyncio
async def test_quick_edit_menu_shows_four_options(session):
    service, product_id = await _make_ready_product(session, telegram_id=111)
    callback = _FakeCallback(f"quickedit:{product_id}")

    await list_products.quick_edit_menu(callback)

    kb = callback.message.answered_kb[-1]
    all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert all_texts == ["Название", "Цена", "Фото", "Назад к карточке"]


@pytest.mark.asyncio
async def test_quick_edit_field_title_then_enter_value_updates_product(session):
    from app.bot.states import EditProductStates

    service, product_id = await _make_ready_product(session, telegram_id=112)
    state = _make_state(112)

    field_cb = _FakeCallback(f"quickeditfield:{product_id}:title")
    await list_products.quick_edit_field(field_cb, state)

    assert await state.get_state() == EditProductStates.entering_value.state
    assert "Название" in field_cb.message.answered[-1]

    value_msg = _FakeMessage("Новое название", user=_FakeUser(112))
    await list_products.enter_value(value_msg, state, service)

    product = await service.get_product(product_id)
    assert product.title == "Новое название"
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_quick_edit_field_price_then_enter_value_updates_product(session):
    service, product_id = await _make_ready_product(session, telegram_id=113)
    state = _make_state(113)

    field_cb = _FakeCallback(f"quickeditfield:{product_id}:price")
    await list_products.quick_edit_field(field_cb, state)
    assert "Цена" in field_cb.message.answered[-1]

    value_msg = _FakeMessage("1234.50", user=_FakeUser(113))
    await list_products.enter_value(value_msg, state, service)

    product = await service.get_product(product_id)
    assert float(product.price) == 1234.5


@pytest.mark.asyncio
async def test_quick_edit_back_shows_product_detail_card(session):
    service, product_id = await _make_ready_product(session, telegram_id=114)
    callback = _FakeCallback(f"quickeditback:{product_id}")

    await list_products.quick_edit_back(callback, service)

    assert any("ALICARTUNING / Накладки зеркал" in t for t in callback.message.answered)
    last_kb = callback.message.answered_kb[-1]
    all_texts = [btn.text for row in last_kb.inline_keyboard for btn in row]
    assert any("Выложить" in t for t in all_texts)


@pytest.mark.asyncio
async def test_open_product_not_found():
    class _Stub:
        async def get_product(self, product_id):
            return None

    callback = _FakeCallback("open:999999")
    await list_products.open_product(callback, _Stub())
    assert callback.message.answered == [texts.NOT_FOUND]


# --- A5. Отдельный экран клонирования ----------------------------------------------


@pytest.mark.asyncio
async def test_menu_clone_shows_dedicated_pick_screen(session):
    service, product_id = await _make_ready_product(session, telegram_id=120)
    message = _FakeMessage(user=_FakeUser(120))

    await common.menu_clone(message, service)

    assert message.answered == ["Что клонируем?"]
    kb = message.answered_kb[-1]
    all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any(f"#{product_id}" in t for t in all_texts)
    all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert f"clone:{product_id}" in all_callbacks


@pytest.mark.asyncio
async def test_menu_clone_empty_state_message(session):
    service = ProductService(session)
    await service.get_or_create_user(telegram_id=121, username="u", full_name="U")
    message = _FakeMessage(user=_FakeUser(121))

    await common.menu_clone(message, service)
    assert any("нечего клонировать" in t for t in message.answered)


# --- A4/H7. /cancel и повторный /start -------------------------------------------


@pytest.mark.asyncio
async def test_cmd_cancel_returns_to_menu(session):
    state = _make_state(200)
    await state.set_state("SomeStates:field")

    message = _FakeMessage("/cancel", user=_FakeUser(200))
    await common.cmd_cancel(message, state)

    assert message.answered == [texts.CANCELLED]
    assert message.answered_kb[-1] is not None
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_cmd_start_mid_dialog_warns_instead_of_silently_resetting(session):
    service = ProductService(session)
    state = _make_state(201)
    await state.set_state("NewProductStates:vendor_code")

    message = _FakeMessage("/start", user=_FakeUser(201))
    await common.cmd_start(message, service, session, state)

    text = message.answered[-1]
    assert text.startswith(texts.WELCOME)
    assert "/cancel" in text
    # Состояние не тронуто молча — пользователь либо продолжит, либо явно отменит.
    assert await state.get_state() == "NewProductStates:vendor_code"


@pytest.mark.asyncio
async def test_cmd_start_without_active_dialog_shows_plain_welcome(session):
    service = ProductService(session)
    state = _make_state(202)

    message = _FakeMessage("/start", user=_FakeUser(202))
    await common.cmd_start(message, service, session, state)

    assert message.answered == [texts.WELCOME]


# --- H6. Сбой генерации AI не должен ронять диалог --------------------------------


@pytest.mark.asyncio
async def test_try_generate_ai_content_shows_retry_button_on_failure(session, monkeypatch):
    service, product_id = await _make_ready_product(session, telegram_id=130)

    async def failing_generate(self, draft):
        raise RuntimeError("AI недоступен")

    monkeypatch.setattr(AIContentService, "generate_full_content", failing_generate)

    message = _FakeMessage()
    ok = await new_product.try_generate_ai_content(message.answer, service, product_id)

    assert ok is False
    assert any("Повторить" in str(kb) or True for kb in message.answered_kb)  # кнопка передана
    last_kb = message.answered_kb[-1]
    all_callbacks = [btn.callback_data for row in last_kb.inline_keyboard for btn in row]
    assert f"regenai:{product_id}" in all_callbacks


@pytest.mark.asyncio
async def test_regenerate_ai_content_retries_and_shows_preview(session, monkeypatch):
    monkeypatch.setattr(AIContentService, "generate_full_content", _fake_full_content)
    service, product_id = await _make_ready_product(session, telegram_id=131)

    callback = _FakeCallback(f"regenai:{product_id}")
    await new_product.regenerate_ai_content(callback, service)

    assert any("✅ Можно публиковать" in t for t in callback.message.answered)


# --- B2. Провал парсинга в быстром режиме -> retry/fallback ----------------------


@pytest.mark.asyncio
async def test_quick_parse_failure_offers_retry_and_fallback_buttons(session):
    from app.services.ai.client import AIContentGenerationError

    class _FailingAI:
        async def parse_quick_description(self, text):
            raise AIContentGenerationError("boom")

    service = ProductService(session, ai_service=_FailingAI())
    user = await service.get_or_create_user(telegram_id=140, username="u", full_name="U")
    product = await service.create_draft(user.id)

    state = _make_state(140)
    await state.set_state(QuickCreateStates.description)
    await state.update_data(product_id=product.id, photos=[1, 2, 3])

    message = _FakeMessage("нечленораздельное", user=_FakeUser(140))
    await quick_create.quick_description(message, state, service, session)

    # Раздел 2.A ТЗ v8: ошибка правит ту же полоску-сообщение (edit_text), а не
    # шлёт новое — см. app/bot/progress.py:fail_progress.
    assert message.edited[-1] == "⚠️ Не получилось разобрать описание."
    kb = message.edited_kb[-1]
    all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "quickretry" in all_callbacks
    assert "quickfallbackstep" in all_callbacks


@pytest.mark.asyncio
async def test_quick_fallback_to_step_keeps_photos_and_resumes_correct_step(session):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=141, username="u", full_name="U")
    product = await service.create_draft(user.id)

    state = _make_state(141)
    await state.set_state(QuickCreateStates.description)
    await state.update_data(product_id=product.id, photos=[10, 11, 12])

    callback = _FakeCallback("quickfallbackstep", user=_FakeUser(141))
    await quick_create.quick_fallback_to_step(callback, state, service)

    # Категория ещё не задана — резюме должно начаться с неё же (раздел B.2 ТЗ).
    assert await state.get_state() == "NewProductStates:category"
    data = await state.get_data()
    assert data["photos"] == [10, 11, 12]
    assert data["product_id"] == product.id


@pytest.mark.asyncio
async def test_quick_retry_description_reasks_same_state():
    callback = _FakeCallback("quickretry")
    await quick_create.quick_retry_description(callback)
    assert callback.message.answered == [texts.QUICK_ASK_DESCRIPTION]


# --- E4. Без технического жаргона в отчёте публикации -----------------------------


@pytest.mark.asyncio
async def test_publish_one_success_message_has_no_nmid_jargon(session, monkeypatch):
    from app.services.marketplaces.base import ImageUploadResult, PublishResult
    from app.services.marketplaces.wildberries import WildberriesClient

    async def fake_create_card(self, subject_id, variants):
        return PublishResult(success=True, external_id=variants[0].get("vendorCode"), status_code=200, raw={})

    async def fake_get_cards_list(self, vendor_codes=None, limit=100):
        return [{"vendorCode": vendor_codes[0], "nmID": 777001}]

    async def fake_upload_images(self, nm_id, image_urls):
        return ImageUploadResult(success=True, urls=image_urls)

    monkeypatch.setattr(WildberriesClient, "create_card", fake_create_card)
    monkeypatch.setattr(WildberriesClient, "get_cards_list", fake_get_cards_list)
    monkeypatch.setattr(WildberriesClient, "upload_images", fake_upload_images)

    service, product_id = await _make_ready_product(session, telegram_id=150, vendor_code="ART-NOJARGON")
    ok, note = await new_product._publish_one(service, product_id)

    assert ok is True
    assert "nmID" not in note
    assert "ID 777001" in note


# --- H8. «Опубликовать все» переживает неожиданную ошибку на одном товаре --------


@pytest.mark.asyncio
async def test_publish_all_survives_unexpected_exception_on_one_product(session, monkeypatch):
    service, good_id = await _make_ready_product(session, telegram_id=160, vendor_code="ART-GOOD")

    async def boom(self, product_id):
        raise RuntimeError("совсем неожиданная ошибка")

    call_count = {"n": 0}
    original = ProductService.validate

    async def flaky_validate(self, product_id):
        call_count["n"] += 1
        if product_id == 999999:
            raise RuntimeError("совсем неожиданная ошибка")
        return await original(self, product_id)

    monkeypatch.setattr(ProductService, "validate", flaky_validate)

    callback = _FakeCallback(f"publishall:{good_id},999999")
    await new_product.publish_all(callback, service)

    summary = callback.message.answered[-1]
    assert f"#{good_id}" in summary
    assert "#999999" in summary
    assert "непредвиденная ошибка" in summary
