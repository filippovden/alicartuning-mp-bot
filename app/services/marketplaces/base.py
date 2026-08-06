from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class MarketplaceAPIError(Exception):
    """Ошибка ответа API маркетплейса (см. раздел 3, 10 ТЗ — «Ошибки и лимиты»)."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


@dataclass
class CategoryNode:
    id: int | str
    name: str
    parent_id: int | str | None = None
    has_children: bool = False


@dataclass
class CategoryAttribute:
    """Характеристика категории, полученная из API маркетплейса."""

    external_id: str
    name: str
    required: bool
    attr_type: str = "string"
    dictionary: list[dict] | None = None


@dataclass
class PublishResult:
    success: bool
    external_id: str | None = None
    status_code: int | None = None
    message: str | None = None
    raw: Any = None


@dataclass
class ImageUploadResult:
    success: bool
    urls: list[str] = field(default_factory=list)
    message: str | None = None


class BaseMarketplaceClient:
    """Общая обёртка над httpx для клиентов WB/Ozon."""

    base_url: str = ""
    request_timeout: float = 20.0

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or self.base_url

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.request_timeout) as client:
            try:
                response = await client.request(
                    method, url, params=params, json=json_body, headers=self._headers()
                )
            except httpx.RequestError as exc:
                raise MarketplaceAPIError(f"Сетевая ошибка при запросе к {url}: {exc}") from exc

        if response.status_code >= 400:
            message = self._extract_error_message(response)
            raise MarketplaceAPIError(message, status_code=response.status_code, payload=self._safe_json(response))

        return response

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text

    def _extract_error_message(self, response: httpx.Response) -> str:
        payload = self._safe_json(response)
        if isinstance(payload, dict):
            for key in ("errorText", "message", "error", "detail"):
                if payload.get(key):
                    return str(payload[key])
        return f"HTTP {response.status_code}: {payload}"
