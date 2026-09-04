"""Полный аудит бота по запросу пользователя: «проверь каждый раздел и кнопку,
по 10 раз». Реального Telegram-бота у нас нет, поэтому каждый сценарий
запускается напрямую через реальные хендлеры (как test_simulations.py), с
моками внешних API (WB/Ozon/Anthropic/xAI) — это проверяет логику и
связку кнопок/состояний бота, а не поведение реальных площадок.

Каждый блок гоняется 10 раз с варьируемыми данными и собирает список ошибок
в конце — так один упавший прогон не прячет остальные 9 и видно, если бага
воспроизводится не всегда (edge case на конкретных данных).
"""

from __future__ import annotations

import io
import random

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import texts
from app.bot.handlers import (
    admin,
    analytics as analytics_handler,
    clone_product,
    common,
    competitors as competitors_handler,
    list_products,
    new_product,
    quick_create,
    reviews as reviews_handler,
)
from app.bot.states import QuickCreateStates
from app.db.models import Category, Marketplace, Review, StorageFile
from app.services.ai.client import AIContentGenerationError, AIContentService
from app.services.competitor_analysis import CompetitorItem, CompetitorReport
from app.services.marketplaces.base import CategoryNode, ImageUploadResult, PublishResult
from app.services.marketplaces.wildberries import WildberriesClient
from app.services.product_service import ProductService

N = 10  # «по 10 раз», как попросил пользователь

CAR_MODELS = ["Lada Vesta", "Lada Granta", "Lada Priora", "Lada Niva", "Lada XRAY"]
MATERIALS = ["ABS-пластик", "Стекловолокно", "Полипропилен", "Текстиль"]
COLORS = ["Чёрный глянец", "Карбон", "Чёрный матовый", "Под цвет кузова"]
CATEGORIES = ["Накладки на зеркала", "Спойлер", "Карман двери", "Диффузор", "Утеплитель"]


# --- Общие фейки телеграм-объектов ---------------------------------------------


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
        self.answered_photos: list[tuple[object, str | None]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self

    async def answer_photo(self, photo, caption: str | None = None, **kwargs) -> "_FakeMessage":
        self.answered_photos.append((photo, caption))
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


class _FakeQuickAI:
    """Реализует и parse_quick_description, и generate_full_content — как
    настоящий AIContentService, для сценариев быстрого создания товара."""

    def __init__(self, parsed: dict):
        self._parsed = parsed

    async def parse_quick_description(self, text: str) -> dict:
        return self._parsed

    async def generate_full_content(self, draft) -> dict:
        return {
            "title": f"ALICARTUNING / {draft.draft_title or 'Товар'} для {draft.car_model or 'Lada'}",
            "description": "Качественная деталь автотюнинга, совместима с указанной моделью. " * 3,
            "bullets": ["Прочность", "Простая установка", "Премиум-дизайн"],
            "keywords": ["alicartuning", "тюнинг"],
        }


def _tiny_jpeg_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(200, 200, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _patch_externals(monkeypatch) -> None:
    """Мокает всё, что реально ходит в сеть (WB/Ozon/Anthropic), чтобы гонять
    хендлеры бота изолированно и быстро — 10 итераций * много сценариев не
    должны зависеть от реальных API и их лимитов."""

    async def fake_search_categories(self, name, limit=20):
        return [CategoryNode(id=212, name=f"WB категория: {name}")]

    async def fake_get_category_characteristics(self, subject_id):
        return []

    async def fake_create_card(self, subject_id, variants):
        vendor_code = variants[0].get("vendorCode") if variants else None
        return PublishResult(success=True, external_id=vendor_code, status_code=200, raw={})

    counter = {"n": 500000}

    async def fake_get_cards_list(self, vendor_codes=None, limit=100):
        counter["n"] += 1
        vc = vendor_codes[0] if vendor_codes else "X"
        return [{"vendorCode": vc, "nmID": counter["n"], "mediaFiles": ["https://cdn.example.com/1.jpg"]}]

    async def fake_upload_images(self, nm_id, image_urls):
        return ImageUploadResult(success=True, urls=image_urls)

    monkeypatch.setattr(WildberriesClient, "search_categories", fake_search_categories)
    monkeypatch.setattr(WildberriesClient, "get_category_characteristics", fake_get_category_characteristics)
    monkeypatch.setattr(WildberriesClient, "create_card", fake_create_card)
    monkeypatch.setattr(WildberriesClient, "get_cards_list", fake_get_cards_list)
    monkeypatch.setattr(WildberriesClient, "upload_images", fake_upload_images)

    async def fake_generate_full_content(self, draft):
        return {
            "title": f"ALICARTUNING / {draft.draft_title or 'Товар'} для {draft.car_model or 'Lada'}",
            "description": "Качественная деталь автотюнинга, совместима с указанной моделью. " * 3,
            "bullets": ["Прочность", "Простая установка", "Премиум-дизайн"],
            "keywords": ["alicartuning", "тюнинг"],
        }

    async def fake_generate_bullets(self, title, draft):
        return ["Прочность", "Простая установка", "Премиум-дизайн"]

    monkeypatch.setattr(AIContentService, "generate_full_content", fake_generate_full_content)
    monkeypatch.setattr(AIContentService, "generate_bullets", fake_generate_bullets)

    def fake_report(query: str) -> CompetitorReport:
        items = [
            CompetitorItem(name=f"Похожий товар {i} для {query}", price=float(800 + i * 50), brand="Конкурент")
            for i in range(3)
        ]
        return CompetitorReport(query=query, items=items)

    async def fake_search_wb_competitors(query, limit=20, exclude_brand=None):
        return fake_report(query)

    monkeypatch.setattr(competitors_handler, "search_wb_competitors", fake_search_wb_competitors)
    monkeypatch.setattr(analytics_handler, "search_wb_competitors", fake_search_wb_competitors)

    async def fake_sync_wb_reviews(session):
        return []

    async def fake_sync_ozon_reviews(session):
        return []

    async def fake_generate_reply(review):
        return "Спасибо за отзыв! Мы всегда рады обратной связи."

    async def fake_answer_review(session, review, text):
        review.is_answered = True
        review.reply_text = text

    monkeypatch.setattr(reviews_handler, "sync_wb_reviews", fake_sync_wb_reviews)
    monkeypatch.setattr(reviews_handler, "sync_ozon_reviews", fake_sync_ozon_reviews)
    monkeypatch.setattr(reviews_handler, "generate_reply", fake_generate_reply)
    monkeypatch.setattr(reviews_handler, "answer_review", fake_answer_review)

    async def fake_sync_ozon_category_tree(session):
        return 0

    monkeypatch.setattr(admin, "sync_ozon_category_tree", fake_sync_ozon_category_tree)


async def _make_ready_product(session, service: ProductService, telegram_id: int, vendor_code: str):
    """Товар, готовый к публикации (все обязательные поля + фото)."""
    user = await service.get_or_create_user(telegram_id=telegram_id, username="u", full_name="U")
    category = Category(name=f"Категория {vendor_code}", wb_subject_id=212)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    product = await service.create_draft(user.id)
    await service.update_fields(
        product.id,
        title=f"ALICARTUNING / {vendor_code}",
        description="Качественная деталь автотюнинга. " * 5,
        brand="ALICARTUNING",
        vendor_code=vendor_code,
        price=1500,
        cost_price=800,
        category_id=category.id,
        weight_g=200,
        length_mm=300,
        width_mm=150,
        height_mm=50,
        car_model=random.choice(CAR_MODELS),
    )
    storage_file = StorageFile(path="/tmp/p.jpg", url="https://cdn.example.com/1.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product.id, storage_file.id, image_type="main", position=0)
    return product


def _report(failures: list[str], label: str) -> None:
    assert not failures, f"{label}: {len(failures)}/{N} прогонов упали:\n" + "\n".join(failures)


# --- A. Главное меню -------------------------------------------------------------


@pytest.mark.asyncio
async def test_A_main_menu_buttons_and_start_help(session):
    failures = []
    service = ProductService(session)
    for i in range(N):
        try:
            user = _FakeUser(1000 + i)
            msg = _FakeMessage(user=user)
            await common.cmd_start(msg, service, session, _make_state(user.id))
            assert texts.WELCOME in msg.answered

            msg2 = _FakeMessage(user=user)
            await common.cmd_help(msg2)
            assert texts.WELCOME in msg2.answered

            m = _FakeMessage(user=user)
            await common.menu_new_product(m, _make_state(user.id), service)
            assert m.answered == [texts.QUICK_ASK_PHOTOS]

            m = _FakeMessage(user=user)
            await common.menu_list(m, service)
            assert m.answered  # что-то ответил, не упал

            m = _FakeMessage(user=user)
            await common.menu_clone(m, service)
            assert m.answered

            m = _FakeMessage(user=user)
            await common.menu_reviews(m, session)
            assert m.answered

            m = _FakeMessage(user=user)
            await common.menu_sales(m, service, session)
            assert m.answered

            m = _FakeMessage(user=user)
            await common.menu_help(m)
            assert m.answered == [texts.HELP_TEXT]

            m = _FakeMessage("/cancel", user=user)
            state = _make_state(user.id)
            await common.cmd_cancel(m, state)
            assert m.answered == [texts.CANCELLED]
        except Exception as exc:  # noqa: BLE001 — фиксируем и продолжаем остальные прогоны
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "A. Главное меню")


# --- B. Быстрое создание товара --------------------------------------------------


@pytest.mark.asyncio
async def test_B_quick_create_full_flow(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            guess_dims = i % 2 == 0
            parsed = {
                "draft_title": f"Тестовая деталь {i}",
                "car_model": random.choice(CAR_MODELS),
                "material": random.choice(MATERIALS),
                "color": random.choice(COLORS),
                "price": 500 + i * 10,
                "package_contents": "1 шт.",
                "length_mm": 300 if guess_dims else None,
                "width_mm": 150 if guess_dims else None,
                "height_mm": 50 if guess_dims else None,
                "weight_g": 400 if guess_dims else None,
            }
            service = ProductService(session, ai_service=_FakeQuickAI(parsed))
            user = _FakeUser(2000 + i)
            state = _make_state(user.id)

            cb = _FakeCallback("newmode:quick", user=user)
            await quick_create.start_quick_mode(cb, state, service)
            assert await state.get_state() == "QuickCreateStates:photos"
            data = await state.get_data()
            product_id = data["product_id"]

            for p in range(texts.MIN_PRODUCT_PHOTOS):
                storage_file = StorageFile(path=f"/tmp/q{i}-{p}.jpg", url="https://cdn.example.com/x.jpg", content_type="image/jpeg")
                session.add(storage_file)
                await session.commit()
                await session.refresh(storage_file)
                await service.add_image(product_id, storage_file.id, image_type="main", position=p)
            await state.update_data(photos=[1, 2, 3])

            done_cb = _FakeCallback("photos_done", user=user)
            await quick_create.quick_photos_done(done_cb, state)
            assert await state.get_state() == "QuickCreateStates:description"

            desc_msg = _FakeMessage(f"Деталь {i}, {parsed['car_model']}, {parsed['material']}, {parsed['color']}", user=user)
            await quick_create.quick_description(desc_msg, state, service, session)
            assert await state.get_state() == "QuickCreateStates:vendor_code"

            vc_msg = _FakeMessage(f"ART-QUICK-{i}", user=user)
            await quick_create.quick_vendor_code(vc_msg, state, service)

            if not guess_dims:
                assert await state.get_state() == "QuickCreateStates:dimensions"
                dims_msg = _FakeMessage("300x150x50", user=user)
                await quick_create.quick_dimensions(dims_msg, state, service)
                assert await state.get_state() == "QuickCreateStates:weight"
                weight_msg = _FakeMessage("400", user=user)
                await quick_create.quick_weight(weight_msg, state, service)

            assert await state.get_state() is None
            product = await service.get_product(product_id)
            assert product.vendor_code == f"ART-QUICK-{i}"
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "B. Быстрое создание товара")


@pytest.mark.asyncio
async def test_B2_quick_create_parse_failure_retries(session):
    failures = []
    for i in range(N):
        try:
            service = ProductService(session, ai_service=_FakeQuickAI({}))

            async def _raise(text, self=None):
                raise AIContentGenerationError("не смог разобрать")

            service.ai_service.parse_quick_description = _raise
            user = _FakeUser(2500 + i)
            state = _make_state(user.id)
            await state.set_state(QuickCreateStates.description)
            await state.update_data(product_id=1, photos=[1, 2, 3])

            msg = _FakeMessage("бессвязный текст", user=user)
            await quick_create.quick_description(msg, state, service, session)
            assert msg.answered[-1] == texts.QUICK_PARSE_FAILED
            assert await state.get_state() == "QuickCreateStates:description"
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "B2. Быстрое создание — ошибка разбора")


# --- C. Пошаговый /new + /drafts -------------------------------------------------


@pytest.mark.asyncio
async def test_C_step_by_step_full_dialog_to_publish(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            service = ProductService(session)
            user = _FakeUser(3000 + i)
            state = _make_state(user.id)

            msg = _FakeMessage("/new", user=user)
            await new_product.cmd_new(msg, state, service)
            assert msg.answered[0].startswith("Шаг 1/13")
            data = await state.get_data()
            product_id = data["product_id"]

            msg = _FakeMessage(random.choice(CATEGORIES), user=user)
            await new_product.step_category(msg, state, service)

            cb = _FakeCallback("wbcat:0", user=user)
            await new_product.pick_wb_category(cb, state, service, session)

            msg = _FakeMessage(f"Черновое название {i}", user=user)
            await new_product.step_title(msg, state, service)

            msg = _FakeMessage(f"ART-STEP-{i}", user=user)
            await new_product.step_vendor_code(msg, state, service)

            msg = _FakeMessage(str(random.randint(200, 900)), user=user)
            await new_product.step_cost_price(msg, state, service)

            msg = _FakeMessage(str(random.randint(1000, 3000)), user=user)
            await new_product.step_price(msg, state, service)

            cb = _FakeCallback("skip:barcode", user=user)
            await new_product.skip_barcode(cb, state)

            msg = _FakeMessage("2 шт.", user=user)
            await new_product.step_package_contents(msg, state, service)

            msg = _FakeMessage(random.choice(MATERIALS), user=user)
            await new_product.step_material(msg, state, service)

            msg = _FakeMessage(random.choice(COLORS), user=user)
            await new_product.step_color(msg, state, service)

            msg = _FakeMessage(random.choice(CAR_MODELS), user=user)
            await new_product.step_car_model(msg, state, service)

            msg = _FakeMessage("300x150x50", user=user)
            await new_product.step_dimensions(msg, state, service)

            msg = _FakeMessage("400", user=user)
            await new_product.step_weight(msg, state, service)

            for p in range(texts.MIN_PRODUCT_PHOTOS):
                storage_file = StorageFile(path=f"/tmp/s{i}-{p}.jpg", url="https://cdn.example.com/x.jpg", content_type="image/jpeg")
                session.add(storage_file)
                await session.commit()
                await session.refresh(storage_file)
                await service.add_image(product_id, storage_file.id, image_type="main", position=p)
            await state.update_data(photos=[1, 2, 3])

            cb = _FakeCallback("photos_done", user=user)
            await new_product.photos_done(cb, state, service)
            assert await state.get_state() == "NewProductStates:confirm"

            pub_cb = _FakeCallback(f"publish:{product_id}", user=user)
            await new_product.confirm_publish(pub_cb, state, service)
            assert any(
                "Опубликовано" in t or "фото не ушли" in t or "с ошибками" in t for t in pub_cb.message.answered
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "C. Пошаговый /new до публикации")


@pytest.mark.asyncio
async def test_C2_drafts_list_and_continue(session):
    failures = []
    service = ProductService(session)
    for i in range(N):
        try:
            user = _FakeUser(3500 + i)
            u = await service.get_or_create_user(telegram_id=user.id, username="u", full_name="U")
            category = Category(name=f"Черновик кат {i}")
            session.add(category)
            await session.commit()
            await session.refresh(category)

            draft = await service.create_draft(u.id)
            # Половину черновиков оставляем совсем пустыми, половину — почти готовыми,
            # чтобы проверить оба конца resume_state_for_product.
            if i % 2 == 0:
                await service.update_fields(draft.id, title="Незаконченный")
            else:
                await service.update_fields(
                    draft.id,
                    title="Почти готовый",
                    vendor_code=f"ART-DRAFT-{i}",
                    cost_price=500,
                    price=1000,
                    category_id=category.id,
                    package_contents="1 шт.",
                    material="ABS",
                    color="Чёрный",
                    car_model="Lada Vesta",
                    length_mm=100,
                    width_mm=100,
                    height_mm=100,
                    weight_g=200,
                )

            msg = _FakeMessage(user=user)
            await list_products.cmd_drafts(msg, service)
            assert any(f"#{draft.id}" in t for t in msg.answered)

            state = _make_state(user.id)
            cb = _FakeCallback(f"continuedraft:{draft.id}", user=user)
            await list_products.continue_draft(cb, state, service)
            assert cb.message.answered  # хоть что-то ответил, диалог не завис молча
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "C2. /drafts + продолжить")


# --- D. /list, клон, пакет, «Опубликовать все» -----------------------------------


@pytest.mark.asyncio
async def test_D_list_clone_batch_publish_all(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            service = ProductService(session)
            source = await _make_ready_product(session, service, telegram_id=4000 + i, vendor_code=f"ART-SRC-{i}")
            user = _FakeUser(4000 + i)

            msg = _FakeMessage(user=user)
            await list_products.cmd_list(msg, service)
            assert msg.answered

            # Одиночное клонирование: car_model -> vendor_code -> превью.
            state = _make_state(user.id)
            clone_cb = _FakeCallback(f"clone:{source.id}", user=user)
            await clone_product.clone_button(clone_cb, state, service)
            assert await state.get_state() == "CloneProductStates:car_model"

            cm_msg = _FakeMessage(random.choice(CAR_MODELS), user=user)
            await clone_product.clone_car_model(cm_msg, state, service)
            assert await state.get_state() == "CloneProductStates:vendor_code"

            vc_msg = _FakeMessage(f"ART-CLONE-{i}", user=user)
            await clone_product.clone_vendor_code(vc_msg, state, service)
            assert await state.get_state() is None

            # Пакетное клонирование на несколько моделей + шаблон артикула.
            batch_state = _make_state(user.id)
            batch_cb = _FakeCallback(f"clonebatch:{source.id}", user=user)
            await clone_product.clone_batch_start(batch_cb, batch_state)
            assert await batch_state.get_state() == "CloneBatchStates:car_models"

            models_msg = _FakeMessage("Vesta, Granta, Priora", user=user)
            await clone_product.clone_batch_models(models_msg, batch_state)
            assert await batch_state.get_state() == "CloneBatchStates:vendor_code_template"

            template_msg = _FakeMessage(f"ART-BATCH{i}-{{model}}", user=user)
            await clone_product.clone_batch_template(template_msg, batch_state, service)
            assert await batch_state.get_state() is None
            summary = template_msg.answered[-1]
            assert "Создано черновиков: 3" in summary

            created_ids = [
                int(part.split("#")[1].split(" ·")[0]) for part in summary.splitlines() if part.startswith("•")
            ]
            assert len(created_ids) == 3

            publish_all_cb = _FakeCallback("publishall:" + ",".join(str(pid) for pid in created_ids), user=user)
            await new_product.publish_all(publish_all_cb, service)
            assert "Публикация всех черновиков завершена" in publish_all_cb.message.answered[-1]
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "D. /list, клон, пакет, «Опубликовать все»")


# --- E. Публикация и статусы (edit/status) ----------------------------------------


@pytest.mark.asyncio
async def test_E_status_and_edit(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            service = ProductService(session)
            product = await _make_ready_product(session, service, telegram_id=5000 + i, vendor_code=f"ART-EDIT-{i}")
            user = _FakeUser(5000 + i)

            msg = _FakeMessage(f"/status {product.id}", user=user)
            await list_products.cmd_status(msg, service)
            assert any(f"Товар #{product.id}" in t for t in msg.answered)

            state = _make_state(user.id)
            edit_msg = _FakeMessage(f"/edit {product.id}", user=user)
            await list_products.cmd_edit(edit_msg, state, service)
            assert await state.get_state() == "EditProductStates:choosing_field"

            field_choice = str((i % 9) + 1)
            choose_msg = _FakeMessage(field_choice, user=user)
            await list_products.choose_field(choose_msg, state)
            assert await state.get_state() == "EditProductStates:entering_value"

            data = await state.get_data()
            field_name = data["field_name"]
            new_value = "999" if field_name in ("price", "cost_price") else f"Новое значение {i}"
            value_msg = _FakeMessage(new_value, user=user)
            await list_products.enter_value(value_msg, state, service)
            assert value_msg.answered[-1] == "✅ Обновлено."
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "E. /status и /edit")


# --- F. Цена по рынку --------------------------------------------------------------


@pytest.mark.asyncio
async def test_F_price_check_and_set_price(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            service = ProductService(session)
            product = await _make_ready_product(session, service, telegram_id=6000 + i, vendor_code=f"ART-PRICE-{i}")

            cb = _FakeCallback(f"pricecheck:{product.id}")
            await competitors_handler.price_check(cb, service)
            assert cb.message.answered

            if i % 2 == 0:
                set_cb = _FakeCallback(f"setprice:{product.id}:999.00")
                await competitors_handler.set_price(set_cb, service)
                updated = await service.get_product(product.id)
                assert float(updated.price) == 999.0
            else:
                set_cb = _FakeCallback(f"setprice:{product.id}:keep")
                await competitors_handler.set_price(set_cb, service)
                assert "не изменена" in set_cb.message.answered[-1].lower()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "F. «Цена по рынку»")


# --- G. Анализ конкурентов ---------------------------------------------------------


@pytest.mark.asyncio
async def test_G_competitors_command_and_callback(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            service = ProductService(session)
            product = await _make_ready_product(session, service, telegram_id=7000 + i, vendor_code=f"ART-COMP-{i}")

            msg = _FakeMessage(f"/market накладки {i}", user=_FakeUser(7000 + i))
            await competitors_handler.cmd_competitors(msg)
            assert msg.answered

            cb = _FakeCallback(f"competitors:{product.id}")
            await competitors_handler.callback_competitors(cb, service)
            assert cb.message.answered
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "G. Анализ конкурентов")


# --- H. Аналитика --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H_analytics_summary_and_per_product(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    async def fake_wb_summary(days):
        from app.services.analytics_service import SalesSummary

        return SalesSummary(
            marketplace="wb", period_days=days, total_units=10, total_revenue=10000.0,
            by_sku={"ART-1": {"units": 10, "revenue": 10000.0}},
        )

    async def fake_ozon_summary(days):
        from app.services.analytics_service import SalesSummary

        return SalesSummary(marketplace="ozon", period_days=days, total_units=5, total_revenue=5000.0, by_sku={})

    async def fake_snapshot(session, product):
        return None

    async def fake_revenue_by_date(days, sku=None):
        return {}

    monkeypatch.setattr(analytics_handler, "get_wb_sales_summary", fake_wb_summary)
    monkeypatch.setattr(analytics_handler, "get_ozon_sales_summary", fake_ozon_summary)
    monkeypatch.setattr(analytics_handler, "snapshot_competitor_prices", fake_snapshot)
    monkeypatch.setattr(analytics_handler, "get_wb_revenue_by_date", fake_revenue_by_date)

    for i in range(N):
        try:
            service = ProductService(session)
            product = await _make_ready_product(session, service, telegram_id=8000 + i, vendor_code=f"ART-AN-{i}")

            msg = _FakeMessage("/analytics", user=_FakeUser(8000 + i))
            await analytics_handler.cmd_analytics(msg, service, session)
            assert msg.answered

            msg2 = _FakeMessage(f"/analytics {product.id}", user=_FakeUser(8000 + i))
            await analytics_handler.cmd_analytics(msg2, service, session)
            assert msg2.answered
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "H. Аналитика")


# --- I. Отзывы -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_I_reviews_sync_auto_and_manual_reply(session, monkeypatch):
    _patch_externals(monkeypatch)
    failures = []

    for i in range(N):
        try:
            review = Review(
                marketplace=Marketplace.WB if i % 2 == 0 else Marketplace.OZON,
                external_review_id=f"ext-{i}",
                rating=random.randint(1, 5),
                text=f"Отзыв номер {i}",
                sku=f"ART-REV-{i}",
            )
            session.add(review)
            await session.commit()
            await session.refresh(review)

            msg = _FakeMessage(user=_FakeUser(9000 + i))
            await reviews_handler.cmd_reviews(msg, session)
            assert msg.answered

            auto_cb = _FakeCallback(f"review_auto:{review.id}")
            await reviews_handler.review_auto_reply(auto_cb, session)
            assert auto_cb.message.answered

            review2 = Review(
                marketplace=Marketplace.WB,
                external_review_id=f"ext-manual-{i}",
                rating=3,
                text=f"Отзыв для ручного ответа {i}",
            )
            session.add(review2)
            await session.commit()
            await session.refresh(review2)

            state = _make_state(9000 + i)
            manual_start_cb = _FakeCallback(f"review_manual:{review2.id}")
            await reviews_handler.review_manual_reply_start(manual_start_cb, state)
            assert await state.get_state() == "ReviewReplyStates:entering_reply"

            reply_msg = _FakeMessage(f"Спасибо за отзыв #{i}!", user=_FakeUser(9000 + i))
            await reviews_handler.review_manual_reply_submit(reply_msg, state, session)
            assert await state.get_state() is None
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "I. Отзывы")


# --- J. Обработка фото и инфографика -----------------------------------------------


@pytest.mark.asyncio
async def test_J_process_images_and_infographic(session, monkeypatch):
    _patch_externals(monkeypatch)
    from app.config import settings

    failures = []
    jpeg_bytes = _tiny_jpeg_bytes()

    for i in range(N):
        try:
            service = ProductService(session)
            user = await service.get_or_create_user(telegram_id=9500 + i, username="u", full_name="U")
            product = await service.create_draft(user.id)
            await service.update_fields(product.id, title=f"ALICARTUNING / Тест {i}", brand="ALICARTUNING", car_model="Lada Vesta")

            storage_file = StorageFile(path=f"/tmp/real{i}.jpg", url="https://cdn.example.com/1.jpg", content_type="image/jpeg")
            session.add(storage_file)
            await session.commit()
            await session.refresh(storage_file)
            # Реальные JPEG-байты — process_product_photo реально открывает файл через PIL.
            import pathlib

            pathlib.Path(storage_file.path).write_bytes(jpeg_bytes)
            await service.add_image(product.id, storage_file.id, image_type="main", position=0)

            proc_cb = _FakeCallback(f"processimg:{product.id}")
            await new_product.process_images(proc_cb, service)
            assert proc_cb.message.answered

            monkeypatch.setattr(settings, "xai_api_key", "" if i % 2 == 0 else "test-key")
            graphic_cb = _FakeCallback(f"gengraphic:{product.id}")
            await new_product.generate_graphic(graphic_cb, service)
            assert graphic_cb.message.answered
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "J. Обработка фото / инфографика")


# --- K. Админ: синхронизация категорий Ozon ----------------------------------------


@pytest.mark.asyncio
async def test_K_admin_sync_categories(session, monkeypatch):
    _patch_externals(monkeypatch)
    from app.config import settings

    failures = []
    for i in range(N):
        try:
            monkeypatch.setattr(settings, "telegram_admin_ids", "111")
            msg = _FakeMessage("/synccategories", user=_FakeUser(111))
            await admin.cmd_sync_categories(msg, session)
            assert any("обновлён" in t for t in msg.answered)

            monkeypatch.setattr(settings, "telegram_admin_ids", "111")
            msg2 = _FakeMessage("/synccategories", user=_FakeUser(999))
            await admin.cmd_sync_categories(msg2, session)
            assert any("только администраторам" in t for t in msg2.answered)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{i}: {type(exc).__name__}: {exc}")
    _report(failures, "K. Admin /synccategories")
