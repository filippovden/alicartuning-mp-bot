from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.marketplaces.base import (
    BaseMarketplaceClient,
    CategoryAttribute,
    CategoryNode,
    ImageUploadResult,
    MarketplaceAPIError,
    PublishResult,
)

# Ozon отключил /v2/category/tree в пользу /v1/description-category/tree
# (актуально на 2026) — старый эндпоинт отдаёт 404 на весь путь целиком.
# Раздел 1 ТЗ v6: новый эндпоинт первым, один запасной заход на старый при 404.
NEW_CATEGORY_TREE_PATH = "/v1/description-category/tree"
OLD_CATEGORY_TREE_PATH = "/v2/category/tree"
NO_API_KEY_STATUS_CODE = 0  # сентинел: настоящих HTTP-статусов 0 не бывает


class OzonClient(BaseMarketplaceClient):
    """Клиент Ozon Seller API (см. раздел 3 и 7 ТЗ).

    Документация: https://docs.ozon.ru/api/seller/
    """

    base_url = settings.ozon_api_base_url

    def __init__(self, client_id: str | None = None, api_key: str | None = None, base_url: str | None = None):
        super().__init__(base_url)
        self.client_id = client_id or settings.ozon_client_id
        self.api_key = api_key or settings.ozon_api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def _fetch_category_tree_result(self) -> list[dict]:
        """Тело result[] дерева категорий — общая точка для get_category_tree и
        get_category_leaves (раздел 1.1, 1.3 ТЗ v6). Ozon отключил /v2/category/tree
        в 2026 — сначала актуальный /v1/description-category/tree, и только если
        он вернул 404 (эндпоинт не подключён для этого продавца/региона), один
        запасной заход на старый URL. Без ключа — сразу понятная ошибка, без
        похода в сеть."""
        if not self.client_id or not self.api_key:
            raise MarketplaceAPIError("Нет ключа Ozon (Client-Id/Api-Key).", status_code=NO_API_KEY_STATUS_CODE)

        try:
            response = await self._request("POST", NEW_CATEGORY_TREE_PATH, json_body={"language": "RU"})
            return response.json().get("result", [])
        except MarketplaceAPIError as exc:
            if exc.status_code != 404:
                raise
            response = await self._request("POST", OLD_CATEGORY_TREE_PATH, json_body={})
            return response.json().get("result", [])

    async def get_category_tree(self) -> list[CategoryNode]:
        """Дерево категорий Ozon — плоский список узлов для админки/отладки
        (раздел 1.1 ТЗ v6). Полноценный подбор категории при создании товара
        идёт через get_category_leaves, не через этот метод."""
        result = await self._fetch_category_tree_result()

        nodes: list[CategoryNode] = []

        def walk(items: list[dict], parent_id: int | None = None) -> None:
            for item in items:
                if item.get("disabled"):
                    continue
                node_id = item.get("description_category_id", item.get("category_id")) or item.get("type_id")
                nodes.append(
                    CategoryNode(
                        id=node_id,
                        name=item.get("category_name") or item.get("type_name", ""),
                        parent_id=parent_id,
                        has_children=bool(item.get("children")),
                    )
                )
                if item.get("children"):
                    walk(item["children"], parent_id=node_id)

        walk(result)
        return nodes

    async def get_category_leaves(self) -> list[dict[str, Any]]:
        """Плоский список листьев дерева: пары (category_id, type_id), которые нужно
        указывать при создании товара в POST /v2/product/import. Ozon не даёт поиск
        категорий по имени, поэтому листья кэшируются в БД (OzonCategoryNode) и
        фильтруются локально — см. app/services/category_search.py.

        Ключ словаря остаётся "category_id" (так называется и колонка БД —
        раздел 1.1 ТЗ v6: колонки не переименовываем), но его значение теперь
        берётся из description_category_id актуального дерева. Отключённые
        (disabled: true) узлы пропускаются целиком — в них товар не создать.
        """
        result = await self._fetch_category_tree_result()

        leaves: list[dict[str, Any]] = []

        def node_id(item: dict) -> int | str | None:
            return item.get("description_category_id", item.get("category_id"))

        def walk(items: list[dict], category_id: int | None, category_name: str | None) -> None:
            for item in items:
                if item.get("disabled"):
                    continue
                if item.get("type_id"):
                    leaves.append(
                        {
                            "category_id": category_id,
                            "category_name": category_name or "",
                            "type_id": item["type_id"],
                            "type_name": item.get("type_name", ""),
                        }
                    )
                    if item.get("children"):
                        walk(item["children"], category_id, category_name)
                else:
                    next_category_id = node_id(item) if node_id(item) is not None else category_id
                    next_category_name = item.get("category_name", category_name)
                    if item.get("children"):
                        walk(item["children"], next_category_id, next_category_name)

        walk(result, None, None)
        return leaves

    async def get_category_attributes(self, category_id: int, type_id: int) -> list[CategoryAttribute]:
        """POST /v1/description-category/attribute — атрибуты категории
        (актуальный эндпоинт на 2026, раздел 1.2 ТЗ v6; /v3/category/attribute
        отключён Ozon вместе со старым деревом)."""
        response = await self._request(
            "POST",
            "/v1/description-category/attribute",
            json_body={"description_category_id": category_id, "type_id": type_id, "language": "RU"},
        )
        result = response.json().get("result", [])
        return [
            CategoryAttribute(
                external_id=str(item["id"]),
                name=item["name"],
                required=bool(item.get("is_required", False)),
                attr_type=item.get("type", "string"),
                dictionary=None,
            )
            for item in result
        ]

    async def get_attribute_values(self, attribute_id: int, category_id: int, type_id: int) -> list[dict[str, Any]]:
        """POST /v1/description-category/attribute/values — справочник значений
        атрибута (актуальный эндпоинт на 2026, раздел 1.2 ТЗ v6)."""
        response = await self._request(
            "POST",
            "/v1/description-category/attribute/values",
            json_body={
                "attribute_id": attribute_id,
                "description_category_id": category_id,
                "type_id": type_id,
                "limit": 100,
                "language": "RU",
            },
        )
        return response.json().get("result", [])

    async def import_products(self, items: list[dict[str, Any]]) -> PublishResult:
        """POST /v2/product/import — создание/обновление товара (до 1000 за раз)."""
        response = await self._request("POST", "/v2/product/import", json_body={"items": items})
        payload = response.json()
        task_id = payload.get("result", {}).get("task_id")
        return PublishResult(success=True, external_id=str(task_id) if task_id else None, status_code=response.status_code, raw=payload)

    async def upload_pictures(self, offer_id: str, image_urls: list[str]) -> ImageUploadResult:
        """POST /v1/product/pictures/import — загрузка фото по offer_id."""
        body = {"offer_id": offer_id, "images": image_urls}
        response = await self._request("POST", "/v1/product/pictures/import", json_body=body)
        return ImageUploadResult(success=True, urls=image_urls) if response else ImageUploadResult(success=False)

    async def update_stocks(self, stocks: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /v2/products/stocks — обновление остатков."""
        response = await self._request("POST", "/v2/products/stocks", json_body={"stocks": stocks})
        return response.json()

    async def update_prices(self, prices: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /v1/product/import/prices — обновление цен."""
        response = await self._request("POST", "/v1/product/import/prices", json_body={"prices": prices})
        return response.json()

    async def get_product_info(self, offer_id: str) -> dict[str, Any]:
        """GET /v2/product/info — информация о товаре по offer_id."""
        response = await self._request("GET", "/v2/product/info", params={"offer_id": offer_id})
        return response.json().get("result", {})

    async def get_import_status(self, task_id: str) -> dict[str, Any]:
        """POST /v1/product/import/info — статус асинхронной задачи импорта товаров."""
        response = await self._request("POST", "/v1/product/import/info", json_body={"task_id": task_id})
        return response.json().get("result", {})

    # --- Аналитика (раздел 4, V3 ТЗ) ------------------------------------------

    async def get_analytics_data(
        self,
        date_from: str,
        date_to: str,
        metrics: list[str] | None = None,
        dimensions: list[str] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """POST /v1/analytics/data — аналитика продаж (выручка, заказы, конверсия)."""
        body = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": metrics or ["revenue", "ordered_units"],
            "dimension": dimensions or ["sku"],
            "limit": limit,
            "offset": 0,
        }
        response = await self._request("POST", "/v1/analytics/data", json_body=body)
        return response.json().get("result", {})

    # --- Отзывы (раздел 4, V3 ТЗ — «Работа с отзывами») ------------------------

    async def list_reviews(self, status: str = "UNPROCESSED", limit: int = 20, last_id: str = "") -> dict[str, Any]:
        """POST /v1/review/list — список отзывов покупателей."""
        body = {"status": status, "limit": limit, "last_id": last_id}
        response = await self._request("POST", "/v1/review/list", json_body=body)
        return response.json()

    async def comment_review(self, review_id: str, text: str) -> dict[str, Any]:
        """POST /v1/review/comment/create — ответ на отзыв."""
        body = {"review_id": review_id, "text": text, "mark_review_as_processed": True}
        response = await self._request("POST", "/v1/review/comment/create", json_body=body)
        return response.json()
