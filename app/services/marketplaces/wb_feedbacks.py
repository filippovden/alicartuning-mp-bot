"""Клиент Wildberries Feedbacks API (раздел 4, V3 ТЗ — «Работа с отзывами»).

Документация: https://feedbacks-api.wildberries.ru
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.marketplaces.base import BaseMarketplaceClient


class WbFeedbacksClient(BaseMarketplaceClient):
    base_url = settings.wb_feedbacks_api_base_url

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(base_url)
        self.api_key = api_key or settings.wb_api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.api_key, "Content-Type": "application/json"}

    async def list_feedbacks(self, is_answered: bool = False, take: int = 20, skip: int = 0) -> list[dict[str, Any]]:
        """GET /api/v1/feedbacks — список отзывов покупателей."""
        response = await self._request(
            "GET",
            "/api/v1/feedbacks",
            params={"isAnswered": str(is_answered).lower(), "take": take, "skip": skip},
        )
        return response.json().get("data", {}).get("feedbacks", [])

    async def answer_feedback(self, feedback_id: str, text: str) -> dict[str, Any]:
        """PATCH /api/v1/feedbacks — ответ на отзыв."""
        response = await self._request(
            "PATCH", "/api/v1/feedbacks", json_body={"id": feedback_id, "text": text}
        )
        return self._safe_json(response) if response.content else {}
