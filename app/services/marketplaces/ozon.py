from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.marketplaces.base import (
    BaseMarketplaceClient,
    CategoryAttribute,
    CategoryNode,
    ImageUploadResult,
    PublishResult,
)


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

    async def get_category_tree(self) -> list[CategoryNode]:
        """POST /v2/category/tree — дерево категорий Ozon."""
        response = await self._request("POST", "/v2/category/tree", json_body={})
        result = response.json().get("result", [])

        nodes: list[CategoryNode] = []

        def walk(items: list[dict], parent_id: int | None = None) -> None:
            for item in items:
                node_id = item.get("category_id") or item.get("type_id")
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

    async def get_category_attributes(self, category_id: int, type_id: int) -> list[CategoryAttribute]:
        """POST /v2/category/attribute/values (список атрибутов категории с ID)."""
        response = await self._request(
            "POST",
            "/v3/category/attribute",
            json_body={"category_id": category_id, "type_id": type_id, "language": "RU"},
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
        """POST /v2/category/attribute/values — справочник значений атрибута (если dictionary)."""
        response = await self._request(
            "POST",
            "/v2/category/attribute/values",
            json_body={"attribute_id": attribute_id, "category_id": category_id, "type_id": type_id},
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
