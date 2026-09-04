"""Разные карточки из одной основы товара под разные магазины — раздел 3 ТЗ v5.

Без ANTHROPIC_API_KEY — детерминированные шаблоны (тестируем именно этот путь,
он же обязателен: «без ключа бот всё равно должен уметь опубликовать»)."""

from __future__ import annotations

import pytest

from app.config import settings
from app.db.models import ShopListing
from app.services.ai.client import AIContentService
from app.services.listing_variation import build_listing_variation
from app.services.product_service import ProductService
from app.services.shops import Shop, Marketplace


async def _make_product(session, **fields) -> tuple[ProductService, int]:
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=1, username="u", full_name="U")
    product = await service.create_draft(user.id)
    defaults = dict(
        title="ALICARTUNING / Фары",
        brand="ALICARTUNING",
        car_model="Lada Granta",
        material="ABS-пластик",
        color="чёрный",
        price=1000,
        cost_price=400,
    )
    defaults.update(fields)
    await service.update_fields(product.id, **defaults)
    return service, product.id


WB_SALON = Shop(id="wb-salon", name="WB Салон", platform=Marketplace.WB, api_key="x")
WB_KUZOV = Shop(id="wb-kuzov", name="WB Кузов", platform=Marketplace.WB, api_key="y")


@pytest.mark.asyncio
async def test_two_shops_get_different_title_and_vendor_code(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    v1 = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)
    listing1 = ShopListing(
        product_id=product_id, shop_id=WB_SALON.id, platform=Marketplace.WB,
        title=v1.title, description=v1.description, bullets=v1.bullets, vendor_code=v1.vendor_code,
    )
    session.add(listing1)
    await session.commit()

    v2 = await build_listing_variation(product, WB_KUZOV, variant_index=1, session=session)

    assert v1.title != v2.title
    assert v1.vendor_code != v2.vendor_code
    assert v1.vendor_code.endswith("-01")


@pytest.mark.asyncio
async def test_vendor_code_never_copies_product_vendor_code_verbatim(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    service, product_id = await _make_product(session, vendor_code="ART-BASE-1")
    product = await service.get_product(product_id)

    v = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)

    assert v.vendor_code != "ART-BASE-1"
    assert v.vendor_code.startswith("ФАРЫ-")
    assert v.vendor_code.endswith("-01")


@pytest.mark.asyncio
async def test_no_anthropic_key_uses_templates_and_does_not_crash(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    call_count = {"n": 0}

    async def counting_generate_full_content(self, draft):
        call_count["n"] += 1
        return {"title": "should not be called", "bullets": [], "description": ""}

    monkeypatch.setattr(AIContentService, "generate_full_content", counting_generate_full_content)

    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    v = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)

    assert call_count["n"] == 0
    assert v.title.startswith("ALICARTUNING /")
    assert len(v.bullets) == 3


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_template(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def failing_generate(self, draft):
        raise RuntimeError("Anthropic недоступен")

    monkeypatch.setattr(AIContentService, "generate_full_content", failing_generate)

    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    v = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)

    assert v.title.startswith("ALICARTUNING /")
    assert len(v.bullets) == 3


@pytest.mark.asyncio
async def test_ai_success_is_used_when_available(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def fake_generate(self, draft):
        return {
            "title": "ALICARTUNING / AI-сгенерированные фары для Lada Granta",
            "bullets": ["AI буллет 1", "AI буллет 2", "AI буллет 3"],
            "description": "AI-описание товара.",
        }

    monkeypatch.setattr(AIContentService, "generate_full_content", fake_generate)

    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    v = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)

    assert "AI-сгенерированные" in v.title
    assert v.bullets == ["AI буллет 1", "AI буллет 2", "AI буллет 3"]


@pytest.mark.asyncio
async def test_forbidden_words_stripped_from_ai_output(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def fake_generate(self, draft):
        return {
            "title": "ALICARTUNING / Хит продаж, оригинал фары для Lada Granta",
            "bullets": ["Скидка на установку", "Материал ABS", "Для Granta"],
            "description": "Акция, только сейчас.",
        }

    monkeypatch.setattr(AIContentService, "generate_full_content", fake_generate)

    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    v = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)

    forbidden = {"хит", "оригинал", "скидка", "акция"}
    assert not any(w in v.title.casefold() for w in forbidden)
    assert not any(w in " ".join(v.bullets).casefold() for w in forbidden)
    assert not any(w in v.description.casefold() for w in forbidden)


@pytest.mark.asyncio
async def test_duplicate_title_gets_distinguisher_not_variant_suffix(session, monkeypatch):
    """Если генерация даёт одинаковый title на два магазина — добавляем факт
    (цвет/материал/модель), а не «вариант 2» (раздел 3.2 ТЗ)."""
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def same_title_generate(self, draft):
        return {"title": "ALICARTUNING / Фары для Lada Granta", "bullets": ["a", "b", "c"], "description": "d"}

    monkeypatch.setattr(AIContentService, "generate_full_content", same_title_generate)

    service, product_id = await _make_product(session, color="чёрный", material="ABS-пластик")
    product = await service.get_product(product_id)

    v1 = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)
    listing1 = ShopListing(
        product_id=product_id, shop_id=WB_SALON.id, platform=Marketplace.WB,
        title=v1.title, description=v1.description, bullets=v1.bullets, vendor_code=v1.vendor_code,
    )
    session.add(listing1)
    await session.commit()

    v2 = await build_listing_variation(product, WB_KUZOV, variant_index=1, session=session)

    assert v1.title != v2.title
    assert "вариант" not in v2.title.casefold()


@pytest.mark.asyncio
async def test_vendor_code_unique_within_shop_increments(session, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    service, product_id = await _make_product(session)
    product = await service.get_product(product_id)

    v1 = await build_listing_variation(product, WB_SALON, variant_index=0, session=session)
    session.add(
        ShopListing(
            product_id=product_id, shop_id=WB_SALON.id, platform=Marketplace.WB,
            title=v1.title, description=v1.description, bullets=v1.bullets, vendor_code=v1.vendor_code,
        )
    )
    await session.commit()

    # Второй товар, тот же магазин — база артикула та же (одна модель/деталь),
    # но vendor_code должен вырасти на -02, а не конфликтовать.
    service2, product_id2 = await _make_product(session, vendor_code=None)
    product2 = await service2.get_product(product_id2)
    v2 = await build_listing_variation(product2, WB_SALON, variant_index=0, session=session)

    assert v2.vendor_code != v1.vendor_code
    assert v2.vendor_code.endswith("-02")
