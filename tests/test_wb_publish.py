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
    assert "фото: 1" in log.message
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

    # Карточка создана и nmID подтверждён — но фото не загружали, поэтому
    # PARTIAL, а не полный SUCCESS (см. критические правки).
    assert log.status == PublishStatus.PARTIAL
    assert product.wb_nm_id == "42"
    assert sleeps == [3.0]  # одна пауза перед тем, как nmID нашёлся на второй попытке
    assert "фото не ушли" in log.message  # к товару не добавляли фото


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
    assert "ID ещё не пришёл" in log.message
    assert product.wb_nm_id is None
    assert len(sleeps) == 11  # attempts=12 (settings.wb_nm_id_poll_attempts) → 11 пауз
    assert cards_route.call_count == 12


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

    # nmID подтверждён, но фото к товару не добавляли — PARTIAL, а не SUCCESS.
    assert log.status == PublishStatus.PARTIAL
    assert product.wb_nm_id == "321"
    assert sleeps == [3.0]


@pytest.mark.asyncio
@respx.mock
async def test_wait_for_wb_nm_id_uses_settings_defaults(session, monkeypatch):
    """attempts/delay настраиваются через settings (WB_NM_ID_POLL_ATTEMPTS/
    WB_NM_ID_POLL_DELAY_SECONDS), а не хардкожены — см. критические правки."""
    from app.config import settings
    from app.services.marketplaces.wildberries import WildberriesClient

    monkeypatch.setattr(settings, "wb_nm_id_poll_attempts", 3)
    monkeypatch.setattr(settings, "wb_nm_id_poll_delay_seconds", 7.0)

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.product_service.asyncio.sleep", fake_sleep)

    service, product_id = await _make_product(session, telegram_id=10, vendor_code="ART-10", wb_subject_id=333)
    cards_route = respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": []})
    )

    client = WildberriesClient()
    nm_id = await service._wait_for_wb_nm_id(client, "ART-10")

    assert nm_id is None
    assert cards_route.call_count == 3
    assert sleeps == [7.0, 7.0]


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
    # публикацию в ERROR (карточка живая), но и не полный SUCCESS — PARTIAL,
    # плюс пользователь обязан увидеть понятное сообщение.
    assert log.status == PublishStatus.PARTIAL
    assert "фото не ушли" in log.message
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

    assert log.status == PublishStatus.PARTIAL
    assert "фото не ушли" in log.message
    assert "нужен S3" in log.message
    assert product.wb_nm_id == "111"


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_wb_no_public_url_with_s3_configured_gives_different_reason(session, monkeypatch):
    """Если S3 уже настроен, но конкретный файл всё равно без публичного URL
    (редкий случай — например, апload в S3 не удался для этой фото), сообщение
    не должно советовать «настройте S3», раз он уже настроен."""
    from app.config import settings

    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_endpoint_url", "https://s3.example.com")
    monkeypatch.setattr(settings, "s3_bucket", "bucket")
    monkeypatch.setattr(settings, "s3_access_key", "key")
    monkeypatch.setattr(settings, "s3_secret_key", "secret")

    service, product_id = await _make_product(session, telegram_id=9, vendor_code="ART-9", wb_subject_id=222)
    await _add_image(session, service, product_id, None)  # S3-аплоад для этого файла не удался
    product = await service.get_product(product_id)

    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": [{"vendorCode": "ART-9", "nmID": 222}]})
    )

    log = await service._publish_to_wb(product)

    assert log.status == PublishStatus.PARTIAL
    assert "нет публичной ссылки на файлы" in log.message
    assert "нужен S3" not in log.message


# --- Мультимагазинность (срез v5): publish_to_shop -----------------------------


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_shop_two_wb_shops_get_different_payloads(session, monkeypatch):
    """Раздел 3, 6 ТЗ v5: два магазина одного товара получают РАЗНЫЙ
    vendorCode/title в payload create_card — иначе площадка видит дубль."""
    import json

    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(
        settings,
        "shops_json",
        '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "TOK1"},'
        '{"id": "wb-kuzov", "name": "WB Кузов", "platform": "wb", "api_key": "TOK2"}]',
    )

    service, product_id = await _make_product(session, telegram_id=50, vendor_code="ART-BASE", wb_subject_id=333)
    await _add_image(session, service, product_id, "https://cdn.example.com/base.jpg")

    await service.update_fields(
        product_id,
        description="Подробное описание товара для прохождения валидации карточки." * 2,
        weight_g=300,
        length_mm=200,
        width_mm=150,
        height_mm=50,
    )

    upload_route = respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))

    def cards_list_side_effect(request):
        body = json.loads(request.content)
        vendor_code = body["settings"]["filter"].get("textSearch", "")
        return httpx.Response(200, json={"cards": [{"vendorCode": vendor_code, "nmID": abs(hash(vendor_code)) % 100000}]})

    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(side_effect=cards_list_side_effect)
    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload/images").mock(return_value=httpx.Response(200, json={}))

    listing1 = await service.publish_to_shop(product_id, "wb-salon")
    listing2 = await service.publish_to_shop(product_id, "wb-kuzov")

    assert listing1.vendor_code != listing2.vendor_code
    assert listing1.title != listing2.title
    assert listing1.wb_nm_id is not None
    assert listing2.wb_nm_id is not None
    assert listing1.wb_nm_id != listing2.wb_nm_id
    assert listing1.status.value == "published"
    assert listing2.status.value == "published"

    body1 = json.loads(upload_route.calls[0].request.content)
    body2 = json.loads(upload_route.calls[1].request.content)
    assert body1[0]["variants"][0]["vendorCode"] != body2[0]["variants"][0]["vendorCode"]
    assert body1[0]["variants"][0]["title"] != body2[0]["variants"][0]["title"]

    # Продукт с ключом "ART-BASE" никогда не передаётся ни в один платёж как есть.
    assert body1[0]["variants"][0]["vendorCode"] != "ART-BASE"
    assert body2[0]["variants"][0]["vendorCode"] != "ART-BASE"


@pytest.mark.asyncio
@respx.mock
async def test_publish_to_shop_second_call_same_shop_does_not_republish(session, monkeypatch):
    import json

    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(
        settings, "shops_json", '[{"id": "wb-salon", "name": "WB Салон", "platform": "wb", "api_key": "TOK1"}]'
    )

    service, product_id = await _make_product(session, telegram_id=51, vendor_code="ART-BASE2", wb_subject_id=334)
    await _add_image(session, service, product_id, "https://cdn.example.com/base2.jpg")

    await service.update_fields(
        product_id,
        description="Подробное описание товара для прохождения валидации карточки." * 2,
        weight_g=300,
        length_mm=200,
        width_mm=150,
        height_mm=50,
    )

    upload_route = respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))

    def cards_list_side_effect(request):
        body = json.loads(request.content)
        vendor_code = body["settings"]["filter"].get("textSearch", "")
        return httpx.Response(200, json={"cards": [{"vendorCode": vendor_code, "nmID": 777}]})

    respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(side_effect=cards_list_side_effect)
    respx.post(f"{WB_BASE_URL}/content/v2/cards/upload/images").mock(return_value=httpx.Response(200, json={}))

    first = await service.publish_to_shop(product_id, "wb-salon")
    assert upload_route.call_count == 1

    second = await service.publish_to_shop(product_id, "wb-salon")
    assert upload_route.call_count == 1  # второй раз create_card не звался
    assert second.id == first.id
    assert second.wb_nm_id == first.wb_nm_id


@pytest.mark.asyncio
async def test_publish_to_shop_default_shop_dual_writes_product_wb_nm_id(session, monkeypatch):
    """Обратная совместимость (раздел 2 ТЗ v5): публикация в магазин по
    умолчанию должна дублировать nmID в старую колонку product.wb_nm_id,
    которую читают /list, /status и остальной код, написанный до v5."""
    import json

    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "shops_json", "")
    monkeypatch.setattr(settings, "wb_api_key", "old-single-wb-key")
    monkeypatch.setattr(settings, "ozon_client_id", "")
    monkeypatch.setattr(settings, "ozon_api_key", "")

    with respx.mock:
        service, product_id = await _make_product(session, telegram_id=52, vendor_code="ART-BASE3", wb_subject_id=335)
        await _add_image(session, service, product_id, "https://cdn.example.com/base3.jpg")
        await service.update_fields(
            product_id,
            description="Подробное описание товара для прохождения валидации карточки." * 2,
            weight_g=300,
            length_mm=200,
            width_mm=150,
            height_mm=50,
        )

        respx.post(f"{WB_BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))

        def cards_list_side_effect(request):
            body = json.loads(request.content)
            vendor_code = body["settings"]["filter"].get("textSearch", "")
            return httpx.Response(200, json={"cards": [{"vendorCode": vendor_code, "nmID": 999}]})

        respx.post(f"{WB_BASE_URL}/content/v2/get/cards/list").mock(side_effect=cards_list_side_effect)
        respx.post(f"{WB_BASE_URL}/content/v2/cards/upload/images").mock(return_value=httpx.Response(200, json={}))

        from app.services import shops as shops_service

        listing = await service.publish_to_shop(product_id, shops_service.DEFAULT_WB_SHOP_ID)

        product = await service.get_product(product_id)
        assert product.wb_nm_id == listing.wb_nm_id == "999"
