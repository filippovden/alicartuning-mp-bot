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
from app.bot.states import NewProductStates, QuickCreateStates
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
        self.answered_markups: list[object] = []
        self.edited_markups: list[object] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        self.answered_markups.append(reply_markup)
        return self

    async def edit_reply_markup(self, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.edited_markups.append(reply_markup)
        return self


class _FakeCallback:
    def __init__(self, data: str, user: _FakeUser | None = None):
        self.data = data
        self.from_user = user or _FakeUser(1)
        self.message = _FakeMessage(user=self.from_user)
        self.alerts: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.alerts.append((text, show_alert))


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
async def test_quick_flow_full_happy_path_reaches_checklist_without_extra_questions(session, monkeypatch):
    """Все поля, включая размеры/вес, распознаны из текста — бот не должен
    задавать лишних вопросов, кроме обязательного артикула (раздел B ТЗ)."""
    from app.services.category_search import WbCategoryMatch

    async def fake_search_wb_categories(query, limit=1):
        return [WbCategoryMatch(subject_id=212, name="Накладки на зеркала")]

    monkeypatch.setattr(quick_create, "search_wb_categories", fake_search_wb_categories)

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
    preview = vendor_message.answered[-1]
    assert "Артикул: ART-GRANTA-990" in preview
    assert "✅ Можно публиковать" in preview


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
    preview = weight_message.answered[-1]
    assert "Артикул: ART-VESTA-1" in preview


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


# --- Раздел 6 ТЗ v3: альбом фото — один ответ на группу, а не на каждый кадр ---


class _FakePhotoSize:
    def __init__(self, file_id: str):
        self.file_id = file_id


class _FakeTgFile:
    def __init__(self, file_path: str):
        self.file_path = file_path


class _FakeBot:
    def __init__(self, image_size: tuple[int, int] = (900, 1200)):
        self._image_size = image_size

    async def get_file(self, file_id: str) -> _FakeTgFile:
        return _FakeTgFile(file_path=f"{file_id}.jpg")

    async def download_file(self, file_path: str):
        import io

        from PIL import Image as PILImage

        buf = io.BytesIO()
        PILImage.new("RGB", self._image_size, (10, 20, 30)).save(buf, format="JPEG")
        buf.seek(0)
        return buf


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class _FakePhotoMessage:
    def __init__(
        self,
        file_id: str,
        chat_id: int = 1,
        media_group_id: str | None = None,
        image_size: tuple[int, int] = (900, 1200),
    ):
        self.photo = [_FakePhotoSize(file_id)]
        self.bot = _FakeBot(image_size=image_size)
        self.chat = _FakeChat(chat_id)
        self.media_group_id = media_group_id
        self.answered: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakePhotoMessage":
        self.answered.append((text, reply_markup))
        return self


async def _make_product_for_photos(session, chat_id: int) -> tuple[ProductService, int, FSMContext]:
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=chat_id, username="u", full_name="U")
    product = await service.create_draft(user.id)
    state = _make_state(chat_id)
    await state.update_data(product_id=product.id, photos=[])
    await state.set_state(NewProductStates.photos)
    return service, product.id, state


@pytest.mark.asyncio
async def test_album_of_three_photos_answers_once(session, monkeypatch):
    """Раздел 6 ТЗ v3: 3 кадра одного альбома → одно «Фото получено (3)»,
    а не три, все 3 фото при этом сохранены."""
    monkeypatch.setattr(new_product, "ALBUM_DEBOUNCE_SECONDS", 0)
    service, product_id, state = await _make_product_for_photos(session, chat_id=41)

    group_id = "album-1"
    msg1 = _FakePhotoMessage("f1", chat_id=41, media_group_id=group_id)
    msg2 = _FakePhotoMessage("f2", chat_id=41, media_group_id=group_id)
    msg3 = _FakePhotoMessage("f3", chat_id=41, media_group_id=group_id)

    await new_product.step_photo(msg1, state, service, session)
    await new_product.step_photo(msg2, state, service, session)
    await new_product.step_photo(msg3, state, service, session)

    task = new_product._album_debounce_tasks[(41, group_id)]
    await task

    all_texts = [t for t, _ in msg1.answered + msg2.answered + msg3.answered]
    photo_received = [t for t in all_texts if t.startswith("Фото получено")]
    assert photo_received == ["Фото получено (3)."]

    data = await state.get_data()
    assert len(data["photos"]) == 3

    product = await service.get_product(product_id)
    assert len(product.images) == 3


@pytest.mark.asyncio
async def test_single_photo_answers_immediately(session):
    """Одиночное фото (без media_group_id) не должно ждать debounce — как и
    раньше, ответ уходит сразу же."""
    service, product_id, state = await _make_product_for_photos(session, chat_id=42)
    msg = _FakePhotoMessage("solo", chat_id=42, media_group_id=None)

    await new_product.step_photo(msg, state, service, session)

    assert msg.answered
    assert msg.answered[-1][0] == "Фото получено (1)."
    assert (42, None) not in new_product._album_debounce_tasks


@pytest.mark.asyncio
async def test_album_small_photos_warn_only_once(session, monkeypatch):
    """_photo_size_warning должен сработать максимум раз на альбом, даже
    если несколько кадров подряд оказались маленькими."""
    monkeypatch.setattr(new_product, "ALBUM_DEBOUNCE_SECONDS", 0)
    service, product_id, state = await _make_product_for_photos(session, chat_id=43)

    group_id = "album-small"
    small = (100, 100)
    msg1 = _FakePhotoMessage("s1", chat_id=43, media_group_id=group_id, image_size=small)
    msg2 = _FakePhotoMessage("s2", chat_id=43, media_group_id=group_id, image_size=small)
    msg3 = _FakePhotoMessage("s3", chat_id=43, media_group_id=group_id, image_size=small)

    await new_product.step_photo(msg1, state, service, session)
    await new_product.step_photo(msg2, state, service, session)
    await new_product.step_photo(msg3, state, service, session)

    task = new_product._album_debounce_tasks[(43, group_id)]
    await task

    all_texts = [t for t, _ in msg1.answered + msg2.answered + msg3.answered]
    warnings = [t for t in all_texts if "меньше минимума" in t]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_quick_create_album_of_three_photos_answers_once(session, monkeypatch):
    """Тот же debounce работает и в быстром режиме (quick_create), не только
    в пошаговом /new — обе ветки переиспользуют handle_incoming_photo."""
    monkeypatch.setattr(new_product, "ALBUM_DEBOUNCE_SECONDS", 0)
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=44, username="u", full_name="U")
    product = await service.create_draft(user.id)
    state = _make_state(44)
    await state.update_data(product_id=product.id, photos=[])
    await state.set_state(QuickCreateStates.photos)

    group_id = "quick-album"
    msg1 = _FakePhotoMessage("q1", chat_id=44, media_group_id=group_id)
    msg2 = _FakePhotoMessage("q2", chat_id=44, media_group_id=group_id)
    msg3 = _FakePhotoMessage("q3", chat_id=44, media_group_id=group_id)

    await quick_create.quick_photo(msg1, state, service, session)
    await quick_create.quick_photo(msg2, state, service, session)
    await quick_create.quick_photo(msg3, state, service, session)

    task = new_product._album_debounce_tasks[(44, group_id)]
    await task

    all_texts = [t for t, _ in msg1.answered + msg2.answered + msg3.answered]
    photo_received = [t for t in all_texts if t.startswith("Фото получено")]
    assert photo_received == ["Фото получено (3)."]

    data = await state.get_data()
    assert len(data["photos"]) == 3


# --- Раздел 4 ТЗ v5: экран выбора магазинов перед публикацией ------------------


async def _make_publishable_product(session, telegram_id: int, vendor_code: str) -> tuple[ProductService, int]:
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=telegram_id, username="u", full_name="U")
    category = Category(name=f"Категория {vendor_code}", wb_subject_id=100, ozon_category_id=200, ozon_type_id=300)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    product = await service.create_draft(user.id)
    await service.update_fields(
        product.id,
        title=f"ALICARTUNING / {vendor_code}",
        brand="ALICARTUNING",
        vendor_code=vendor_code,
        price=1500,
        category_id=category.id,
        description="Подробное описание товара для прохождения валидации." * 2,
        weight_g=300,
        length_mm=200,
        width_mm=150,
        height_mm=50,
    )
    storage_file = StorageFile(path="/tmp/p.jpg", url="https://cdn.example.com/p.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product.id, storage_file.id, image_type="main", position=0)
    return service, product.id


@pytest.mark.asyncio
async def test_confirm_publish_skips_shop_picker_with_single_shop_per_platform(session, monkeypatch):
    """Раздел 4.2 ТЗ v5: не больше одного WB и одного Ozon в системе —
    публикуем сразу, без экрана выбора (как раньше)."""
    from app.config import settings

    monkeypatch.setattr(settings, "shops_json", "")
    monkeypatch.setattr(settings, "wb_api_key", "")
    monkeypatch.setattr(settings, "ozon_client_id", "")
    monkeypatch.setattr(settings, "ozon_api_key", "")

    async def fake_publish(self, product_id):
        from app.services.product_service import PublishSummary

        return PublishSummary(wb=None, ozon=None)

    monkeypatch.setattr(ProductService, "publish", fake_publish)

    service, product_id = await _make_publishable_product(session, 60, "ART-SKIP")
    state = _make_state(60)
    fake_cb = _FakeCallback(f"publish:{product_id}")

    await new_product.confirm_publish(fake_cb, state, service)

    all_texts = fake_cb.message.answered
    assert not any("Куда выложить" in t for t in all_texts)
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_confirm_publish_shows_shop_picker_with_multiple_shops(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "T1"},'
        '{"id": "wb-kuzov", "name": "WB Кузов", "platform": "wb", "api_key": "T2"}]',
    )

    service, product_id = await _make_publishable_product(session, 61, "ART-MULTI")
    state = _make_state(61)
    fake_cb = _FakeCallback(f"publish:{product_id}")

    await new_product.confirm_publish(fake_cb, state, service)

    assert any("Куда выложить" in t for t in fake_cb.message.answered)
    assert await state.get_state() == "ShopPickStates:picking"
    data = await state.get_data()
    assert data["shoppick_product_id"] == product_id
    assert data["shoppick_selected"] == []


@pytest.mark.asyncio
async def test_shop_pick_toggle_updates_selection(session, monkeypatch):
    from app.config import settings
    from app.bot.states import ShopPickStates

    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "T1"},'
        '{"id": "wb-kuzov", "name": "WB Кузов", "platform": "wb", "api_key": "T2"}]',
    )

    state = _make_state(62)
    await state.set_state(ShopPickStates.picking)
    await state.update_data(shoppick_product_id=1, shoppick_selected=[])

    cb1 = _FakeCallback("shoppick:1:wb-salon")
    await new_product.shop_pick_toggle(cb1, state)
    data = await state.get_data()
    assert data["shoppick_selected"] == ["wb-salon"]
    assert len(cb1.message.edited_markups) == 1

    cb2 = _FakeCallback("shoppick:1:wb-salon")
    await new_product.shop_pick_toggle(cb2, state)
    data = await state.get_data()
    assert data["shoppick_selected"] == []


@pytest.mark.asyncio
async def test_shop_pick_go_without_selection_shows_alert(session, monkeypatch):
    from app.config import settings
    from app.bot.states import ShopPickStates

    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "T1"},'
        '{"id": "wb-kuzov", "name": "WB Кузов", "platform": "wb", "api_key": "T2"}]',
    )

    state = _make_state(63)
    await state.set_state(ShopPickStates.picking)
    await state.update_data(shoppick_product_id=1, shoppick_selected=[])

    cb = _FakeCallback("shopgo:1")
    await new_product.shop_pick_go(cb, state)

    assert cb.alerts == [(texts.NEED_AT_LEAST_ONE_SHOP, True)]
    assert await state.get_state() == "ShopPickStates:picking"


@pytest.mark.asyncio
async def test_shop_pick_go_with_selection_moves_to_confirm(session, monkeypatch):
    from app.config import settings
    from app.bot.states import ShopPickStates

    monkeypatch.setattr(
        settings, "shops_json", '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "T1"}]'
    )

    state = _make_state(64)
    await state.set_state(ShopPickStates.picking)
    await state.update_data(shoppick_product_id=1, shoppick_selected=["wb-salon"])

    cb = _FakeCallback("shopgo:1")
    await new_product.shop_pick_go(cb, state)

    assert await state.get_state() == "ShopPickStates:confirming"
    assert any("Выкладываю" in t for t in cb.message.answered)
    assert any("WB Салон" in t for t in cb.message.answered)


@pytest.mark.asyncio
async def test_shop_confirm_publish_reports_per_shop_lines(session, monkeypatch):
    from app.config import settings
    from app.bot.states import ShopPickStates
    from app.services.product_service import ProductService as PS

    monkeypatch.setattr(
        settings, "shops_json", '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "T1"}]'
    )

    async def fake_publish_to_shop(self, product_id, shop_id):
        from app.db.models import ListingStatus, ShopListing

        return ShopListing(
            id=1, product_id=product_id, shop_id=shop_id, platform="wildberries",
            vendor_code="ART-1", wb_nm_id="12345", status=ListingStatus.PUBLISHED,
            publish_message="карточка (ID 12345), фото: 1",
        )

    async def fake_get_listing(self, product_id, shop_id):
        return None

    monkeypatch.setattr(PS, "publish_to_shop", fake_publish_to_shop)
    monkeypatch.setattr(PS, "get_listing", fake_get_listing)

    service = ProductService(session)
    state = _make_state(65)
    await state.set_state(ShopPickStates.confirming)
    await state.update_data(shoppick_product_id=1, shoppick_selected=["wb-salon"])

    cb = _FakeCallback("shopconfirm:1")
    await new_product.shop_confirm_publish(cb, state, service)

    assert await state.get_state() is None
    joined = " ".join(cb.message.answered)
    assert "WB Салон" in joined
    assert "готово, номер 12345" in joined


@pytest.mark.asyncio
async def test_shop_confirm_publish_skips_already_live_listing(session, monkeypatch):
    from app.config import settings
    from app.bot.states import ShopPickStates
    from app.services.product_service import ProductService as PS

    monkeypatch.setattr(
        settings, "shops_json", '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "T1"}]'
    )

    call_count = {"n": 0}

    async def fake_publish_to_shop(self, product_id, shop_id):
        call_count["n"] += 1
        raise AssertionError("не должен вызываться повторно для уже живого listing")

    async def fake_get_listing(self, product_id, shop_id):
        from app.db.models import ListingStatus, ShopListing

        return ShopListing(
            id=1, product_id=product_id, shop_id=shop_id, platform="wildberries",
            vendor_code="ART-1", wb_nm_id="999", status=ListingStatus.PUBLISHED,
        )

    monkeypatch.setattr(PS, "publish_to_shop", fake_publish_to_shop)
    monkeypatch.setattr(PS, "get_listing", fake_get_listing)

    service = ProductService(session)
    state = _make_state(66)
    await state.set_state(ShopPickStates.confirming)
    await state.update_data(shoppick_product_id=1, shoppick_selected=["wb-salon"])

    cb = _FakeCallback("shopconfirm:1")
    await new_product.shop_confirm_publish(cb, state, service)

    assert call_count["n"] == 0
    assert any("уже выложено" in t for t in cb.message.answered)
