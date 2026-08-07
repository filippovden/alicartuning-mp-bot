"""Надёжная публикация в WB: карточка → опрос nmID → фото (см. критические
исправления, п.6: «Сделай публикацию на WB надёжнее»).

POST /content/v2/cards/upload создаёт карточку АСИНХРОННО и не возвращает
настоящий nmID синхронно — ProductService._publish_to_wb опрашивает
/content/v2/get/cards/list и только после подтверждения nmID грузит фото
через /content/v2/cards/upload/images (app/services/product_service.py).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.db.models import Category, PublishStatus, StorageFile
from app.services.product_service import ProductService

WB_BASE_URL = "https://content-api.wildberries.ru"


async def _make_product(session, *, telegram_id: int, vendor_code: str, wb_subject_id: int):
    service = ProductService(session)
    user = await service.get_or_create_user(telegram_id=telegram_id, username="u", full_name="U")

    category = Category(name=f"Категория {vendor_code}", wb_subject_id=wb_subject_id)
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
    return service, draft.id


async def _add_image(session, service: ProductService, product_id: int, url: str | None) -> None:
    storage_file = StorageFile(path="/tmp/photo.jpg", url=url, content_type="image/jpeg")
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    await service.add_image(product_id, storage_file.id, image_type="main", position=0)


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_success_with_photos(session):
    service, product_id = await _make_product(session, telegram_id=1, vendor_code="ART-1", wb_subject_id=212)
    await _add_image(session, service, product_id, "https://cdn.example.com/1.jpg")
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": [{"vendorCode": "ART-1", "nmID": 555777}]})
    )
    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload/images").mock(return_value=httpx.Response(200, json={}))

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.SUCCESS
    assert log.external_id == "555777"
    assert "загружено фото: 1" in log.message
    assert product.wb_nm_id == "555777"


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_polls_until_nm_id_appears(session, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.product_service.asyncio.sleep", fake_sleep)

    service, product_id = await _make_product(session, telegram_id=2, vendor_code="ART-2", wb_subject_id=333)
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    cards_route = respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list")
    cards_route.side_effect = [
        httpx.Response(200, json={"cards": []}),
        httpx.Response(200, json={"cards": [{"vendorCode": "ART-2", "nmID": 42}]}),
    ]

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.SUCCESS
    assert product.wb_nm_id == "42"
    assert sleeps == [2.0]  # одна пауза перед тем, как nmID нашёлся на второй попытке
    assert "фото не загружены" in log.message  # к товару не добавляли фото


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_nm_id_never_appears(session, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.product_service.asyncio.sleep", fake_sleep)

    service, product_id = await _make_product(session, telegram_id=3, vendor_code="ART-3", wb_subject_id=444)
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    cards_route = respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": []})
    )

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.ERROR
    assert "не подтверждён" in log.message
    assert product.wb_nm_id is None
    assert len(sleeps) == 4  # attempts=5 → 4 паузы между попытками
    assert cards_route.call_count == 5


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_nm_id_poll_always_errors_surfaces_real_error(session, monkeypatch):
    """Если /get/cards/list не отвечает успешно НИ РАЗУ (например, невалидный
    WB_API_KEY), пользователь должен увидеть реальную причину, а не общее
    «nmID не подтверждён — проверьте позже» (см. code-review находку)."""

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr("app.services.product_service.asyncio.sleep", fake_sleep)

    service, product_id = await _make_product(session, telegram_id=7, vendor_code="ART-7", wb_subject_id=888)
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(401, json={"errorText": "Невалидный токен"})
    )

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.ERROR
    assert "Невалидный токен" in log.message
    assert "не подтверждён" not in log.message  # не маскируем реальную причину
    assert product.wb_nm_id is None


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_nm_id_poll_transient_error_then_recovers(session, monkeypatch):
    """Единичный сбой опроса (например, 500) не должен считаться фатальным —
    как только WB ответил успешно, ищем nmID дальше как обычно."""
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.product_service.asyncio.sleep", fake_sleep)

    service, product_id = await _make_product(session, telegram_id=8, vendor_code="ART-8", wb_subject_id=999)
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    cards_route = respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list")
    cards_route.side_effect = [
        httpx.Response(500, json={"errorText": "Временная ошибка"}),
        httpx.Response(200, json={"cards": [{"vendorCode": "ART-8", "nmID": 321}]}),
    ]

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.SUCCESS
    assert product.wb_nm_id == "321"
    assert sleeps == [2.0]


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_create_card_fails(session):
    service, product_id = await _make_product(session, telegram_id=4, vendor_code="ART-4", wb_subject_id=555)
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(
        return_value=httpx.Response(400, json={"errorText": "Некорректный subjectID"})
    )
    # /get/cards/list намеренно не замокан: код не должен опрашивать nmID,
    # если создание карточки уже провалилось.

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.ERROR
    assert "Ошибка создания карточки" in log.message
    assert "subjectID" in log.message
    assert product.wb_nm_id is None


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_photo_upload_failure_is_not_blocking(session):
    service, product_id = await _make_product(session, telegram_id=5, vendor_code="ART-5", wb_subject_id=666)
    await _add_image(session, service, product_id, "https://cdn.example.com/5.jpg")
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": [{"vendorCode": "ART-5", "nmID": 999}]})
    )
    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload/images").mock(
        return_value=httpx.Response(400, json={"errorText": "Файл повреждён"})
    )

    log = await service._publish_to_wb(product)

    # Карточка уже создана в WB — сбой загрузки фото не должен превращать
    # публикацию в ошибку, но пользователь обязан увидеть понятное сообщение.
    assert log.status == PublishStatus.SUCCESS
    assert "но фото не загрузились" in log.message
    assert "Файл повреждён" in log.message
    assert product.wb_nm_id == "999"


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_local_storage_has_no_public_url(session):
    service, product_id = await _make_product(session, telegram_id=6, vendor_code="ART-6", wb_subject_id=777)
    await _add_image(session, service, product_id, None)  # локальный backend — URL не задан
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": [{"vendorCode": "ART-6", "nmID": 111}]})
    )
    # /cards/upload/images намеренно не замокан: без публичных URL грузить нечего.

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.SUCCESS
    assert "фото не загружены" in log.message
    assert "объектного хранилища" in log.message
    assert product.wb_nm_id == "111"
