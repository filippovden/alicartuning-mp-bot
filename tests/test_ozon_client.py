import httpx
import pytest
import respx

from app.services.marketplaces.base import MarketplaceAPIError
from app.services.marketplaces.ozon import OzonClient

BASE_URL = "https://api-seller.ozon.ru"


@pytest.mark.asyncio
@respx.mock
async def test_get_category_tree():
    respx.post(f"{BASE_URL}/v2/category/tree").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "category_id": 100,
                        "category_name": "Автозапчасти",
                        "children": [{"type_id": 200, "type_name": "Тюнинг", "children": []}],
                    }
                ]
            },
        )
    )
    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    nodes = await client.get_category_tree()
    assert len(nodes) == 2
    assert nodes[0].id == 100
    assert nodes[1].parent_id == 100


@pytest.mark.asyncio
@respx.mock
async def test_import_products_success():
    respx.post(f"{BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(200, json={"result": {"task_id": 555}})
    )
    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    result = await client.import_products([{"offer_id": "ART123", "name": "Тест"}])
    assert result.success is True
    assert result.external_id == "555"


@pytest.mark.asyncio
@respx.mock
async def test_import_products_error_raises():
    respx.post(f"{BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(429, json={"message": "Too Many Requests"})
    )
    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    client.max_retries = 0  # без ожидания повторов — здесь проверяем только факт ошибки
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.import_products([{"offer_id": "ART123"}])
    assert exc_info.value.status_code == 429
