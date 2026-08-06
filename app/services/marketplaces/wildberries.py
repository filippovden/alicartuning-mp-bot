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


class WildberriesClient(BaseMarketplaceClient):
    """Клиент Wildberries Content API (см. раздел 3 и 7 ТЗ).

    Документация: https://content-api.wildberries.ru
    """

    base_url = settings.wb_api_base_url

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(base_url)
        self.api_key = api_key or settings.wb_api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    async def get_parent_categories(self) -> list[CategoryNode]:
        """GET /content/v2/object/parent/all — список разделов."""
        response = await self._request("GET", "/content/v2/object/parent/all")
        data = response.json().get("data", [])
        return [CategoryNode(id=item["id"], name=item["name"]) for item in data]

    async def get_subcategories(self, parent_id: int, limit: int = 1000) -> list[CategoryNode]:
        """GET /content/v2/object/all?parentID={ID} — подкатегории (до 1000 за раз)."""
        response = await self._request(
            "GET", "/content/v2/object/all", params={"parentID": parent_id, "limit": limit}
        )
        data = response.json().get("data", [])
        return [
            CategoryNode(id=item["subjectID"], name=item["subjectName"], parent_id=parent_id) for item in data
        ]

    async def get_category_characteristics(self, subject_id: int) -> list[CategoryAttribute]:
        """GET /content/v2/object/charcs/{subjectID} — обязательные характеристики категории."""
        response = await self._request("GET", f"/content/v2/object/charcs/{subject_id}")
        data = response.json().get("data", [])
        return [
            CategoryAttribute(
                external_id=str(item["charcID"]),
                name=item["name"],
                required=bool(item.get("required", False)),
                attr_type="dictionary" if item.get("dictionary") else "string",
                dictionary=item.get("dictionary"),
            )
            for item in data
        ]

    async def create_card(self, subject_id: int, variants: list[dict[str, Any]]) -> PublishResult:
        """POST /content/v2/cards/upload — создание/обновление карточки товара."""
        body = [{"subjectID": subject_id, "variants": variants}]
        response = await self._request("POST", "/content/v2/cards/upload", json_body=body)
        payload = response.json() if response.content else {}
        vendor_code = variants[0].get("vendorCode") if variants else None
        return PublishResult(success=True, external_id=vendor_code, status_code=response.status_code, raw=payload)

    async def upload_images(self, nm_id: int, image_urls: list[str]) -> ImageUploadResult:
        """POST /content/v2/cards/upload/images — загрузка фото по ссылкам."""
        body = {"nmID": nm_id, "data": image_urls}
        response = await self._request("POST", "/content/v2/cards/upload/images", json_body=body)
        return ImageUploadResult(success=True, urls=image_urls, message=None) if response else ImageUploadResult(
            success=False
        )

    async def get_cards_list(self, vendor_codes: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """POST /content/v2/get/cards/list — список карточек / статус публикации."""
        body: dict[str, Any] = {"settings": {"cursor": {"limit": limit}, "filter": {"withPhoto": -1}}}
        if vendor_codes:
            body["settings"]["filter"]["textSearch"] = " ".join(vendor_codes)
        response = await self._request("POST", "/content/v2/get/cards/list", json_body=body)
        return response.json().get("cards", [])
