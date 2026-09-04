"""ProductService._publish_to_ozon (см. критические исправления, п.5 — Ozon
требует category_id и type_id ВМЕСТЕ, см. app/services/marketplaces/mapping.py).

Раньше отсутствие type_id при заполненном category_id долетало до реального
запроса и падало с непрозрачной ошибкой Ozon API; теперь ловится заранее с
понятным сообщением (см. code-review находку по этому же коммиту).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.db.models import Category, PublishStatus
from app.services.product_service import ProductService

OZON_BASE_URL = "https://api-seller.ozon.ru"


async def _make_product(session, *, telegram_id: int, vendor_code: str, ozon_category_id: int, ozon_type_id: int | None):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=telegram_id, username="u", full_name="U")

    category = Category(name=f"Категория {vendor_code}", ozon_category_id=ozon_category_id, ozon_type_id=ozon_type_id)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    draft = await service.create_draft(user.id)
    await service.update_fields(
        draft.id,
        title=f"ALICARTUNING / {vendor_code}",
        brand="ALICARTUNING",
        vendor_code=vendor_code,
        price=1500,
        category_id=category.id,
    )
    product = await service.get_product(draft.id)
    return service, product


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_ozon_missing_type_id_returns_clear_error_without_api_call(session):
    service, product = await _make_product(
        session, telegram_id=1, vendor_code="OZ-1", ozon_category_id=100, ozon_type_id=None
    )
    # /v2/product/import намеренно не замокан: запрос не должен уйти вообще.

    log = await service._publish_to_ozon(product)

    assert log.status == PublishStatus.ERROR
    assert "ozon_type_id" in log.message
    assert "Донастройте категорию" in log.message
    assert product.ozon_product_id is None


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_ozon_success_with_category_and_type_id(session):
    service, product = await _make_product(
        session, telegram_id=2, vendor_code="OZ-2", ozon_category_id=100, ozon_type_id=200
    )
    respx.post(f"{OZON_BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(200, json={"result": {"task_id": 777}})
    )

    log = await service._publish_to_ozon(product)

    assert log.status == PublishStatus.SUCCESS
    assert log.external_id == "777"
    assert product.ozon_product_id == "777"


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_ozon_api_error_is_reported(session):
    service, product = await _make_product(
        session, telegram_id=3, vendor_code="OZ-3", ozon_category_id=100, ozon_type_id=200
    )
    respx.post(f"{OZON_BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(400, json={"message": "Некорректный category_id"})
    )

    log = await service._publish_to_ozon(product)

    assert log.status == PublishStatus.ERROR
    assert "category_id" in log.message


# --- Мультимагазинность (срез v5): publish_to_shop -----------------------------


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_shop_two_ozon_shops_get_different_offer_id_and_name(session, monkeypatch):
    import json

    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "ozon-salon", "name": "Ozon Салон", "platform": "ozon", "client_id": "1", "api_key": "TOK1"},'
        '{"id": "ozon-kuzov", "name": "Ozon Кузов", "platform": "ozon", "client_id": "2", "api_key": "TOK2"}]',
    )

    service, product = await _make_product(
        session, telegram_id=10, vendor_code="OZ-BASE", ozon_category_id=100, ozon_type_id=200
    )
    await service.update_fields(
        product.id,
        description="Подробное описание товара для прохождения валидации карточки." * 2,
        weight_g=300,
        length_mm=200,
        width_mm=150,
        height_mm=50,
    )
    from app.db.models import StorageFile

    storage_file = StorageFile(path="/tmp/o.jpg", url="https://cdn.example.com/o.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product.id, storage_file.id, image_type="main", position=0)

    import_route = respx.post(f"{OZON_BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(200, json={"result": {"task_id": 555}})
    )

    listing1 = await service.publish_to_shop(product.id, "ozon-salon")
    listing2 = await service.publish_to_shop(product.id, "ozon-kuzov")

    assert listing1.vendor_code != listing2.vendor_code
    assert listing1.title != listing2.title
    assert listing1.status.value == "published"
    assert listing2.status.value == "published"

    body1 = json.loads(import_route.calls[0].request.content)
    body2 = json.loads(import_route.calls[1].request.content)
    assert body1["items"][0]["offer_id"] != body2["items"][0]["offer_id"]
    assert body1["items"][0]["name"] != body2["items"][0]["name"]
    assert body1["items"][0]["offer_id"] != "OZ-BASE"


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_shop_ozon_second_call_does_not_republish(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "ozon-salon", "name": "Ozon Салон", "platform": "ozon", "client_id": "1", "api_key": "TOK1"}]',
    )

    service, product = await _make_product(
        session, telegram_id=11, vendor_code="OZ-BASE2", ozon_category_id=100, ozon_type_id=200
    )
    await service.update_fields(
        product.id,
        description="Подробное описание товара для прохождения валидации карточки." * 2,
        weight_g=300,
        length_mm=200,
        width_mm=150,
        height_mm=50,
    )
    from app.db.models import StorageFile

    storage_file = StorageFile(path="/tmp/o2.jpg", url="https://cdn.example.com/o2.jpg", content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product.id, storage_file.id, image_type="main", position=0)

    import_route = respx.post(f"{OZON_BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(200, json={"result": {"task_id": 111}})
    )

    first = await service.publish_to_shop(product.id, "ozon-salon")
    assert import_route.call_count == 1

    second = await service.publish_to_shop(product.id, "ozon-salon")
    assert import_route.call_count == 1
    assert second.id == first.id
