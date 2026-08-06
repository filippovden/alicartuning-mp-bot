"""Клиент Wildberries Statistics API (раздел 4, V3 ТЗ — «AI-аналитика»).

Документация: https://statistics-api.wildberries.ru — отдельный хост от Content API,
но использует тот же API-ключ (если у него есть категория доступа «Статистика»).
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.marketplaces.base import BaseMarketplaceClient


class WbStatisticsClient(BaseMarketplaceClient):
    base_url = settings.wb_stats_api_base_url

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(base_url)
        self.api_key = api_key or settings.wb_api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.api_key}

    async def get_sales(self, date_from: str, flag: int = 0) -> list[dict[str, Any]]:
        """GET /api/v1/supplier/sales — фактические продажи/возвраты с указанной даты."""
        response = await self._request(
            "GET", "/api/v1/supplier/sales", params={"dateFrom": date_from, "flag": flag}
        )
        return response.json()

    async def get_orders(self, date_from: str, flag: int = 0) -> list[dict[str, Any]]:
        """GET /api/v1/supplier/orders — заказы с указанной даты."""
        response = await self._request(
            "GET", "/api/v1/supplier/orders", params={"dateFrom": date_from, "flag": flag}
        )
        return response.json()

    async def get_stocks(self, date_from: str) -> list[dict[str, Any]]:
        """GET /api/v1/supplier/stocks — остатки на складах WB."""
        response = await self._request("GET", "/api/v1/supplier/stocks", params={"dateFrom": date_from})
        return response.json()
