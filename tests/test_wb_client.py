import httpx
import pytest
import respx

from app.services.marketplaces.base import MarketplaceAPIError
from app.services.marketplaces.wildberries import WildberriesClient

BASE_URL = "https://content-api.wildberries.ru"


@pytest.mark.asyncio
@respx.mock
async def test_get_parent_categories():
    respx.get(f"{BASE_URL}/content/v2/object/parent/all").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 1, "name": "Автозапчасти"}]})
    )
    client = WildberriesClient(api_key="test-key", base_url=BASE_URL)
    categories = await client.get_parent_categories()
    assert len(categories) == 1
    assert categories[0].name == "Автозапчасти"


@pytest.mark.asyncio
@respx.mock
async def test_get_category_characteristics():
    respx.get(f"{BASE_URL}/content/v2/object/charcs/212").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"charcID": 14177449, "name": "Цвет", "required": True}]},
        )
    )
    client = WildberriesClient(api_key="test-key", base_url=BASE_URL)
    attrs = await client.get_category_characteristics(212)
    assert attrs[0].external_id == "14177449"
    assert attrs[0].required is True


@pytest.mark.asyncio
@respx.mock
async def test_create_card_success():
    respx.post(f"{BASE_URL}/content/v2/cards/upload").mock(return_value=httpx.Response(200, json={}))
    client = WildberriesClient(api_key="test-key", base_url=BASE_URL)
    variants = [{"vendorCode": "ART123", "title": "Тест", "description": "d", "brand": "ALICARTUNING", "characteristics": []}]
    result = await client.create_card(212, variants)
    assert result.success is True
    assert result.external_id == "ART123"


@pytest.mark.asyncio
@respx.mock
async def test_create_card_error_raises():
    respx.post(f"{BASE_URL}/content/v2/cards/upload").mock(
        return_value=httpx.Response(400, json={"errorText": "Некорректный subjectID"})
    )
    client = WildberriesClient(api_key="test-key", base_url=BASE_URL)
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.create_card(999999, [{"vendorCode": "X"}])
    assert exc_info.value.status_code == 400
    assert "subjectID" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_get_cards_list_returns_cards():
    respx.post(f"{BASE_URL}/content/v2/get/cards/list").mock(
        return_value=httpx.Response(200, json={"cards": [{"vendorCode": "ART1", "nmID": 123}]})
    )
    client = WildberriesClient(api_key="test-key", base_url=BASE_URL)
    cards = await client.get_cards_list(vendor_codes=["ART1"])
    assert cards == [{"vendorCode": "ART1", "nmID": 123}]


@pytest.mark.asyncio
@respx.mock
async def test_upload_images_success():
    respx.post(f"{BASE_URL}/content/v2/cards/upload/images").mock(return_value=httpx.Response(200, json={}))
    client = WildberriesClient(api_key="test-key", base_url=BASE_URL)
    result = await client.upload_images(123, ["https://cdn.example.com/1.jpg"])
    assert result.success is True
    assert result.urls == ["https://cdn.example.com/1.jpg"]
