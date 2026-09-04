"""Дерево категорий Ozon (срез v6): Ozon отключил /v2/category/tree в пользу
/v1/description-category/tree — старый URL отдаёт 404 на весь путь целиком
("404 page not found", не JSON), из-за чего /synccategories падал с сырой
HTTP-ошибкой в чат вместо человеческого текста.

См. app/services/marketplaces/ozon.py: OzonClient._fetch_category_tree_result
(новый URL первым, один запасной заход на старый при 404 нового) и
app/bot/handlers/admin.py: cmd_sync_categories (маппинг ошибки на человеческий
текст, без URL/HTTP-кода в чат).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import settings
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.marketplaces.ozon import OzonClient

BASE_URL = "https://api-seller.ozon.ru"
NEW_TREE_URL = f"{BASE_URL}/v1/description-category/tree"
OLD_TREE_URL = f"{BASE_URL}/v2/category/tree"

# Компактная фикстура дерева новой схемы (раздел 4 ТЗ v6): категория → подкатегория → лист с type_id.
NEW_SCHEMA_TREE = {
    "result": [
        {
            "description_category_id": 17027900,
            "category_name": "Автотовары",
            "disabled": False,
            "children": [
                {
                    "description_category_id": 17027949,
                    "category_name": "Экстерьер и тюнинг",
                    "disabled": False,
                    "children": [
                        {
                            "type_id": 91565,
                            "type_name": "Накладка на зеркало",
                            "disabled": False,
                            "children": [],
                        }
                    ],
                }
            ],
        }
    ]
}

OLD_SCHEMA_TREE = {
    "result": [
        {
            "category_id": 17027949,
            "category_name": "Экстерьер и тюнинг",
            "children": [{"type_id": 91565, "type_name": "Накладка на зеркало", "children": []}],
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_get_category_leaves_uses_new_endpoint_and_old_not_called():
    new_route = respx.post(NEW_TREE_URL).mock(return_value=httpx.Response(200, json=NEW_SCHEMA_TREE))
    old_route = respx.post(OLD_TREE_URL).mock(return_value=httpx.Response(200, json=OLD_SCHEMA_TREE))

    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    leaves = await client.get_category_leaves()

    assert new_route.call_count == 1
    assert old_route.call_count == 0
    assert len(leaves) == 1
    assert leaves[0]["category_id"] == 17027949
    assert leaves[0]["type_id"] == 91565
    assert leaves[0]["category_name"] == "Экстерьер и тюнинг"
    assert leaves[0]["type_name"] == "Накладка на зеркало"


@pytest.mark.asyncio
@respx.mock
async def test_get_category_leaves_falls_back_to_old_endpoint_on_404():
    respx.post(NEW_TREE_URL).mock(return_value=httpx.Response(404, text="404 page not found"))
    respx.post(OLD_TREE_URL).mock(return_value=httpx.Response(200, json=OLD_SCHEMA_TREE))

    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    leaves = await client.get_category_leaves()

    assert len(leaves) == 1
    assert leaves[0]["category_id"] == 17027949
    assert leaves[0]["type_id"] == 91565


@pytest.mark.asyncio
@respx.mock
async def test_get_category_leaves_both_dead_raises_without_raw_404_text_leaking_to_handler():
    """Хендлер (admin.py) сам мапит status_code на человеческий текст — здесь
    только проверяем, что оба эндпоинта действительно вызваны и что итоговая
    ошибка несёт status_code=404 (по нему хендлер и определяет ситуацию)."""
    new_route = respx.post(NEW_TREE_URL).mock(return_value=httpx.Response(404, text="404 page not found"))
    old_route = respx.post(OLD_TREE_URL).mock(return_value=httpx.Response(404, text="404 page not found"))

    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.get_category_leaves()

    assert new_route.call_count == 1
    assert old_route.call_count == 1
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_category_leaves_without_key_does_not_touch_network(monkeypatch):
    """Раздел 1.3 ТЗ v6: пустые Client-Id/Api-Key — сразу ошибка, без сетевого
    запроса (respx не замокан вообще — если бы клиент постучался в сеть, тест
    упал бы с ConnectionError/сетевой ошибкой, а не с MarketplaceAPIError).

    Конструктор OzonClient использует `client_id or settings.ozon_client_id` —
    явная пустая строка "" в аргументе ложна для `or` и молча подменяется
    значением из settings (реальным ключом, если он есть в окружении). Поэтому
    "нет ключа" нужно симулировать через settings, а не через аргументы конструктора."""
    monkeypatch.setattr(settings, "ozon_client_id", "")
    monkeypatch.setattr(settings, "ozon_api_key", "")
    client = OzonClient(base_url=BASE_URL)
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.get_category_leaves()
    assert "ключ" in exc_info.value.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_get_category_leaves_skips_disabled_nodes():
    tree = {
        "result": [
            {
                "description_category_id": 1,
                "category_name": "Категория",
                "disabled": False,
                "children": [
                    {"type_id": 10, "type_name": "Живой лист", "disabled": False, "children": []},
                    {"type_id": 20, "type_name": "Отключённый лист", "disabled": True, "children": []},
                ],
            }
        ]
    }
    respx.post(NEW_TREE_URL).mock(return_value=httpx.Response(200, json=tree))

    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    leaves = await client.get_category_leaves()

    assert len(leaves) == 1
    assert leaves[0]["type_id"] == 10


@pytest.mark.asyncio
@respx.mock
async def test_sync_ozon_category_tree_writes_rows(session):
    from app.db.models import OzonCategoryNode
    from app.services.category_search import sync_ozon_category_tree
    from sqlalchemy import select

    respx.post(NEW_TREE_URL).mock(return_value=httpx.Response(200, json=NEW_SCHEMA_TREE))

    client = OzonClient(client_id="cid", api_key="key", base_url=BASE_URL)
    count = await sync_ozon_category_tree(session, client=client)

    assert count == 1
    rows = (await session.execute(select(OzonCategoryNode))).scalars().all()
    assert len(rows) == 1
    assert rows[0].category_id == 17027949
    assert rows[0].type_id == 91565

    # Идемпотентность: повторный вызов обновляет, не плодит дубли.
    count2 = await sync_ozon_category_tree(session, client=client)
    assert count2 == 1
    rows_after = (await session.execute(select(OzonCategoryNode))).scalars().all()
    assert len(rows_after) == 1
