import httpx
import pytest
import respx

from app.db.models import OzonCategoryNode
from app.services.category_search import (
    ozon_cache_is_empty,
    search_ozon_categories,
    search_wb_categories,
    sync_ozon_category_tree,
)
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wildberries import WildberriesClient

WB_BASE_URL = "https://content-api.wildberries.ru"
OZON_BASE_URL = "https://api-seller.ozon.ru"


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_categories():
    respx.get(f"{WB_BASE_URL}/content/v2/object/all").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"subjectID": 212, "subjectName": "Накладки на зеркала"}]},
        )
    )
    client = WildberriesClient(api_key="test", base_url=WB_BASE_URL)
    matches = await search_wb_categories("накладки на зеркала", client=client)
    assert matches[0].subject_id == 212
    assert matches[0].name == "Накладки на зеркала"


@pytest.mark.asyncio
async def test_ozon_cache_is_empty(session):
    assert await ozon_cache_is_empty(session) is True

    session.add(OzonCategoryNode(category_id=1, type_id=2, name="Тест / Тест", is_leaf=True))
    await session.commit()

    assert await ozon_cache_is_empty(session) is False


@pytest.mark.asyncio
async def test_search_ozon_categories_filters_leaves_only(session):
    session.add_all(
        [
            OzonCategoryNode(category_id=1, type_id=10, name="Автозапчасти / Накладки на зеркала", is_leaf=True),
            OzonCategoryNode(category_id=1, type_id=11, name="Автозапчасти / Спойлеры", is_leaf=True),
            OzonCategoryNode(category_id=1, type_id=None, name="Автозапчасти (раздел)", is_leaf=False),
        ]
    )
    await session.commit()

    # Регистр совпадает с сохранённым в БД: SQLite (в отличие от Postgres ILIKE) не
    # умеет регистронезависимо сравнивать кириллицу, поэтому в тесте матчим точный
    # регистр — сама фильтрация по is_leaf при этом всё равно проверяется.
    matches = await search_ozon_categories(session, "Накладки", limit=10)
    assert len(matches) == 1
    assert matches[0].type_id == 10
    assert matches[0].category_id == 1


@pytest.mark.asyncio
@respx.mock
async def test_sync_ozon_category_tree_flattens_and_stores(session):
    # v6: Ozon отключил /v2/category/tree — актуальный эндпоинт /v1/description-category/tree
    # с полем description_category_id (см. tests/test_ozon_category_tree.py).
    respx.post(f"{OZON_BASE_URL}/v1/description-category/tree").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "description_category_id": 100,
                        "category_name": "Автозапчасти",
                        "disabled": False,
                        "children": [
                            {"type_id": 200, "type_name": "Накладки на зеркала", "disabled": False, "children": []},
                            {"type_id": 201, "type_name": "Спойлеры", "disabled": False, "children": []},
                        ],
                    }
                ]
            },
        )
    )
    client = OzonClient(client_id="cid", api_key="key", base_url=OZON_BASE_URL)
    count = await sync_ozon_category_tree(session, client=client)
    assert count == 2

    matches = await search_ozon_categories(session, "Спойлеры")
    assert len(matches) == 1
    assert matches[0].type_id == 201
    assert matches[0].category_id == 100
