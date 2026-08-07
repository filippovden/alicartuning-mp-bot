"""Клиент xAI Grok Imagine — AI-генерация инфографики карточек (раздел 11, 12
ТЗ). Заменяет MVP-рендер (Claude пишет буллеты → Pillow рисует текст на белом
фоне, см. app/services/image_pipeline.py) на настоящую генерацию изображения;
Pillow остаётся как fallback, если XAI_API_KEY не задан или запрос не удался
(см. ProductService.generate_infographic_images).

xAI отдаёт OpenAI-совместимый REST API (https://docs.x.ai). Используем прямой
httpx через общий BaseMarketplaceClient (тот же retry/429-механизм, что у
клиентов WB/Ozon — app/services/marketplaces/base.py), а не SDK `openai`,
чтобы не тянуть отдельную тяжёлую зависимость ради одного эндпоинта.
MarketplaceAPIError — тот же класс ошибок, что и у остальных внешних API
клиентов проекта (несмотря на название модуля, это общий тип ошибок HTTP-слоя,
не специфичный для маркетплейсов).
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.config import settings
from app.services.marketplaces.base import BaseMarketplaceClient, MarketplaceAPIError

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"
FALLBACK_IMAGE_MODEL = "grok-imagine-image"


class GrokImagineClient(BaseMarketplaceClient):
    base_url = XAI_BASE_URL
    request_timeout = 60.0  # генерация изображения может занимать десятки секунд

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        super().__init__(base_url)
        self.api_key = api_key or settings.xai_api_key
        self.model = model or settings.xai_image_model

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _extract_error_message(self, response: httpx.Response) -> str:
        # OpenAI-совместимые API отдают ошибку как {"error": {"message": ...}},
        # а не плоской строкой — база (marketplaces/base.py) этого не знает.
        payload = self._safe_json(response)
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        return super()._extract_error_message(response)

    async def generate_infographic(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "3:4",
        response_format: str = "url",
    ) -> bytes:
        """Генерирует изображение по промпту и возвращает его байты.

        aspect_ratio="3:4" — под требования WB (900×1200) и Ozon по кадру.
        response_format="url" скачивает картинку отдельным запросом;
        "b64_json" получает байты прямо в ответе API (без второго запроса).
        """
        payload = await self._create_image(prompt, aspect_ratio=aspect_ratio, response_format=response_format)
        return await self._payload_to_bytes(payload, response_format)

    async def edit_infographic(
        self,
        prompt: str,
        image_url: str,
        *,
        response_format: str = "url",
    ) -> bytes:
        """Правка уже сгенерированного/загруженного изображения по промпту
        (POST /images/edits) — например, доработать инфографику с фото товара
        как референсом, если xAI API это поддерживает."""
        body = {
            "model": self.model,
            "prompt": prompt,
            "image": image_url,
            "response_format": response_format,
        }
        response = await self._request("POST", "/images/edits", json_body=body)
        return await self._payload_to_bytes(response.json(), response_format)

    async def _create_image(self, prompt: str, *, aspect_ratio: str, response_format: str) -> dict:
        body = {
            "model": self.model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "response_format": response_format,
        }
        try:
            response = await self._request("POST", "/images/generations", json_body=body)
        except MarketplaceAPIError as exc:
            if exc.status_code == 404 and self.model != FALLBACK_IMAGE_MODEL:
                logger.warning(
                    "Grok Imagine: модель %s недоступна (%s), пробую fallback-модель %s",
                    self.model,
                    exc.message,
                    FALLBACK_IMAGE_MODEL,
                )
                body["model"] = FALLBACK_IMAGE_MODEL
                response = await self._request("POST", "/images/generations", json_body=body)
            else:
                raise
        return response.json()

    async def _payload_to_bytes(self, payload: dict, response_format: str) -> bytes:
        data = payload.get("data") or []
        if not data:
            raise MarketplaceAPIError("Grok Imagine не вернул изображений (пустой data[] в ответе)")

        item = data[0]
        if response_format == "b64_json":
            b64 = item.get("b64_json")
            if not b64:
                raise MarketplaceAPIError("Grok Imagine: в ответе нет поля b64_json")
            return base64.b64decode(b64)

        url = item.get("url")
        if not url:
            raise MarketplaceAPIError("Grok Imagine: в ответе нет поля url")
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                image_response = await client.get(url)
                image_response.raise_for_status()
                return image_response.content
        except httpx.HTTPError as exc:
            raise MarketplaceAPIError(f"Не удалось скачать сгенерированное изображение: {exc}") from exc
