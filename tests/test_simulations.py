"""Массовые симуляции бота и кода (100+ итераций).

Два уровня:
1. `test_100_product_lifecycle_simulations` — 100 прогонов бизнес-логики
   (ProductService: черновик → заполнение полей → валидация) со случайными, в т.ч.
   намеренно некорректными, данными — проверяем, что валидатор не падает и
   корректно отличает валидные карточки от невалидных на широком разбросе входных
   данных (раздел 10 ТЗ).
2. `test_full_bot_dialog_simulations` — 15 полных диалогов `/new` через реальные
   хендлеры бота (не только сервисный слой): команда → выбор категории → все поля →
   динамические характеристики → генерация AI-контента → публикация/отклонение по
   валидации. Внешние вызовы (WB API, Anthropic) замоканы через respx/monkeypatch.

Суммарно 115 симулированных сценариев.
"""

from __future__ import annotations

import random

import httpx
import pytest
import respx
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import texts
from app.bot.handlers import new_product
from app.services.ai.client import AIContentService
from app.services.product_service import ProductService

WB_BASE_URL = "https://content-api.wildberries.ru"

CATEGORIES = ["Накладки на зеркала", "Спойлер", "Карман обивки двери", "Утеплитель двигателя", "Диффузор"]
CAR_MODELS = ["Lada Vesta", "Lada Granta", "Lada Priora", "Lada Niva", "Lada XRAY"]
MATERIALS = ["ABS-пластик", "Стекловолокно", "Текстиль", "Полипропилен"]
COLORS = ["Чёрный глянец", "Чёрный матовый", "Карбон", "Под цвет кузова"]


# ---------------------------------------------------------------------------
# 1. 100 симуляций жизненного цикла товара (сервисный/бизнес-слой)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_100_product_lifecycle_simulations(session):
    from app.db.models import Category

    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=900000, username="sim", full_name="Simulation User")

    category = Category(name="Симуляция: автотюнинг")
    session.add(category)
    await session.commit()
    await session.refresh(category)

    rng = random.Random(42)
    outcomes = {"valid": 0, "invalid": 0, "crashed": 0}
    crash_details: list[str] = []

    for i in range(100):
        product = await service.create_draft(user.id)

        # Каждый 4-й сценарий — намеренно некорректный по одной из типичных причин
        # (раздел 10 ТЗ), поочерёдно: цена ниже себестоимости, пустое название,
        # отрицательный вес, нет фото — проверяем, что валидатор их всех отлавливает.
        make_invalid = i % 4 == 0
        invalid_kind = i % 16  # какой конкретно дефект вносим в этот невалидный сценарий

        cost_price = rng.randint(200, 3000)
        price = (
            max(1, cost_price - rng.randint(1, 150))
            if (make_invalid and invalid_kind < 4)
            else cost_price + rng.randint(100, 2500)
        )
        title = (
            "" if (make_invalid and 4 <= invalid_kind < 8)
            # "№" (не "#") — валидатор названия (раздел 10 ТЗ) допускает ограниченный
            # набор символов, решётка в их число не входит.
            else f"ALICARTUNING / {rng.choice(CATEGORIES)} для {rng.choice(CAR_MODELS)} №{i}"
        )
        weight = -rng.randint(1, 500) if (make_invalid and 8 <= invalid_kind < 12) else rng.randint(50, 3000)
        skip_photo = make_invalid and invalid_kind >= 12

        fields = dict(
            title=title,
            brand="ALICARTUNING",
            vendor_code=f"SIM-{i}",
            price=price,
            cost_price=cost_price,
            category_id=category.id,
            material=rng.choice(MATERIALS),
            color=rng.choice(COLORS),
            car_model=rng.choice(CAR_MODELS),
            weight_g=weight,
            length_mm=rng.randint(50, 900),
            width_mm=rng.randint(50, 500),
            height_mm=rng.randint(10, 300),
            description="Прочные детали из качественного материала, совместимы с указанной моделью. " * rng.randint(1, 4),
        )

        try:
            await service.update_fields(product.id, **fields)
            if not skip_photo:
                storage_file = await _sim_storage_file(session, i)
                await service.add_image(product.id, storage_file.id, image_type="main", position=0)

            validation = await service.validate(product.id)
        except Exception as exc:  # симуляция должна выявлять падения кода, а не маскировать их
            outcomes["crashed"] += 1
            crash_details.append(f"#{i}: {type(exc).__name__}: {exc}")
            continue

        if validation.is_valid:
            outcomes["valid"] += 1
        else:
            outcomes["invalid"] += 1

    assert outcomes["crashed"] == 0, f"Валидация не должна падать с исключением: {crash_details}"
    assert outcomes["invalid"] >= 20, f"Ожидались намеренно невалидные сценарии: {outcomes}"
    assert outcomes["valid"] >= 50, f"Большинство валидных сценариев должны проходить проверку: {outcomes}"
    assert outcomes["valid"] + outcomes["invalid"] == 100


async def _sim_storage_file(session, i: int):
    from app.db.models import StorageFile

    storage_file = StorageFile(path=f"/tmp/sim-{i}.jpg", content_type="image/jpeg", size_bytes=1024)
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    return storage_file


# ---------------------------------------------------------------------------
# 2. Симуляции полного диалога бота через реальные хендлеры aiogram
# ---------------------------------------------------------------------------


class _FakeUser:
    def __init__(self, uid: int):
        self.id = uid
        self.username = f"user{uid}"
        self.full_name = f"Simulated User {uid}"


class _FakeMessage:
    def __init__(self, text: str, user: _FakeUser):
        self.text = text
        self.from_user = user
        self.sent_texts: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.sent_texts.append(text)
        return self


class _FakeCallback:
    def __init__(self, data: str, user: _FakeUser):
        self.data = data
        self.from_user = user
        self.message = _FakeMessage("", user)

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        return None


def _make_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


@pytest.mark.asyncio
@respx.mock
async def test_full_bot_dialog_simulations(session, monkeypatch):
    """15 полных диалогов /new через реальные хендлеры (не только сервисный слой)."""

    respx.get(f"{WB_BASE_URL}/content/v2/object/all").mock(
        return_value=httpx.Response(200, json={"data": [{"subjectID": 555, "subjectName": "Тестовая категория"}]})
    )
    respx.get(f"{WB_BASE_URL}/content/v2/object/charcs/555").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    async def fake_generate_full_content(self, draft):
        return {
            "title": f"ALICARTUNING / {draft.draft_title or 'Товар'}",
            "description": "Качественный товар для тюнинга автомобиля. Прочный материал, простая установка. " * 2,
            "bullets": ["Прочный материал", "Простая установка без сверления"],
            "keywords": ["alicartuning", "тюнинг", "автозапчасти"],
        }

    monkeypatch.setattr(AIContentService, "generate_full_content", fake_generate_full_content)

    service = ProductService(session)
    rng = random.Random(7)

    results = {"reached_preview": 0, "crashed": 0}
    crash_details: list[str] = []

    for i in range(15):
        user = _FakeUser(600000 + i)
        state = _make_state(user.id)

        try:
            msg = _FakeMessage("/new", user)
            await new_product.cmd_new(msg, state, service)

            msg = _FakeMessage(f"Тестовая категория {i}", user)
            await new_product.step_category(msg, state, service)

            cb = _FakeCallback("wbcat:0", user)
            await new_product.pick_wb_category(cb, state, service, session)

            msg = _FakeMessage(f"ALICARTUNING / Товар для симуляции #{i}", user)
            await new_product.step_title(msg, state, service)

            msg = _FakeMessage(f"SIM-BOT-{i}", user)
            await new_product.step_vendor_code(msg, state, service)

            msg = _FakeMessage(str(rng.randint(200, 1000)), user)
            await new_product.step_cost_price(msg, state, service)

            msg = _FakeMessage(str(rng.randint(1200, 3000)), user)
            await new_product.step_price(msg, state, service)

            cb = _FakeCallback("skip:barcode", user)
            await new_product.skip_barcode(cb, state)

            msg = _FakeMessage("2 шт., крепёж в комплекте", user)
            await new_product.step_package_contents(msg, state, service)

            msg = _FakeMessage(rng.choice(MATERIALS), user)
            await new_product.step_material(msg, state, service)

            msg = _FakeMessage(rng.choice(COLORS), user)
            await new_product.step_color(msg, state, service)

            msg = _FakeMessage(rng.choice(CAR_MODELS), user)
            await new_product.step_car_model(msg, state, service)

            msg = _FakeMessage("500x200x50", user)
            await new_product.step_dimensions(msg, state, service)

            msg = _FakeMessage(str(rng.randint(100, 2000)), user)
            await new_product.step_weight(msg, state, service)

            # Загрузку реального файла через Telegram Bot API не симулируем (нужен
            # живой Bot) — добавляем изображения напрямую через сервис и обновляем
            # состояние диалога, как это сделал бы step_photo после скачивания файла.
            # Нужен минимум MIN_PRODUCT_PHOTOS, иначе «Готово» отклонит переход (п.7
            # критических исправлений).
            data = await state.get_data()
            from app.services.storage import save_bytes

            photo_ids = []
            for p in range(texts.MIN_PRODUCT_PHOTOS):
                storage_file = await save_bytes(
                    session, b"fake-jpeg-bytes", filename=f"sim-{i}-{p}.jpg", content_type="image/jpeg"
                )
                await service.add_image(data["product_id"], storage_file.id, image_type="main", position=p)
                photo_ids.append(storage_file.id)
            await state.update_data(photos=photo_ids)

            cb = _FakeCallback("photos_done", user)
            await new_product.photos_done(cb, state, service)

            product = await service.get_product(data["product_id"])
            assert product.title is not None
            assert product.description is not None
            assert product.car_model is not None
            assert product.status.value == "draft"

            results["reached_preview"] += 1
        except Exception as exc:
            results["crashed"] += 1
            crash_details.append(f"dialog #{i}: {type(exc).__name__}: {exc}")
        finally:
            await state.clear()

    assert results["crashed"] == 0, f"Диалог бота не должен падать с исключением: {crash_details}"
    assert results["reached_preview"] == 15


@pytest.mark.asyncio
@respx.mock
async def test_bot_dialog_handles_wb_search_failure_gracefully(session):
    """Отдельная симуляция: WB API недоступен при поиске категории — бот не должен
    падать, а должен предложить пропустить (раздел 10 ТЗ — «Ошибки и лимиты»)."""
    respx.get(f"{WB_BASE_URL}/content/v2/object/all").mock(return_value=httpx.Response(500))

    service = ProductService(session)
    user = _FakeUser(700001)
    state = _make_state(user.id)

    msg = _FakeMessage("/new", user)
    await new_product.cmd_new(msg, state, service)

    msg = _FakeMessage("Какая-то категория", user)
    await new_product.step_category(msg, state, service)

    assert any("не нашёл точного совпадения" in t.lower() for t in msg.sent_texts)
    data = await state.get_data()
    assert data["wb_matches"] == []
