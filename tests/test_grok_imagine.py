"""GrokImagineClient (xAI) — AI-генерация инфографики (см. app/services/ai/grok_imagine.py).

Клиент наследует BaseMarketplaceClient (retry на 429, единый httpx-слой), но
работает с OpenAI-совместимым API xAI, а не WB/Ozon.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.services.ai.grok_imagine import FALLBACK_IMAGE_MODEL, XAI_BASE_URL, GrokImagineClient
from app.services.marketplaces.base import MarketplaceAPIError


@pytest.mark.asyncio
@respx.mock
async def test_generate_infographic_downloads_url_response():
    respx.post(f"{XAI_BASE_URL}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.x.ai/img/1.png"}]})
    )
    respx.get("https://cdn.x.ai/img/1.png").mock(return_value=httpx.Response(200, content=b"PNGDATA"))

    client = GrokImagineClient(api_key="test-key")
    result = await client.generate_infographic("нарисуй инфографику")

    assert result == b"PNGDATA"


@pytest.mark.asyncio
@respx.mock
async def test_generate_infographic_decodes_b64_json_without_extra_request():
    encoded = base64.b64encode(b"RAWBYTES").decode()
    route = respx.post(f"{XAI_BASE_URL}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": encoded}]})
    )

    client = GrokImagineClient(api_key="test-key")
    result = await client.generate_infographic("промпт", response_format="b64_json")

    assert result == b"RAWBYTES"
    assert route.call_count == 1  # без второго запроса на скачивание


@pytest.mark.asyncio
@respx.mock
async def test_generate_infographic_sends_aspect_ratio_and_model():
    route = respx.post(f"{XAI_BASE_URL}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"x").decode()}]})
    )

    client = GrokImagineClient(api_key="test-key", model="grok-imagine-image-quality")
    await client.generate_infographic("промпт", aspect_ratio="3:4", response_format="b64_json")

    sent_body = route.calls[0].request.content
    import json

    body = json.loads(sent_body)
    assert body["model"] == "grok-imagine-image-quality"
    assert body["aspect_ratio"] == "3:4"
    assert body["prompt"] == "промпт"


@pytest.mark.asyncio
@respx.mock
async def test_falls_back_to_secondary_model_on_404():
    route = respx.post(f"{XAI_BASE_URL}/images/generations")
    route.side_effect = [
        httpx.Response(404, json={"error": {"message": "model not found"}}),
        httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"OK").decode()}]}),
    ]

    client = GrokImagineClient(api_key="test-key", model="grok-imagine-image-quality")
    result = await client.generate_infographic("промпт", response_format="b64_json")

    assert result == b"OK"
    assert route.call_count == 2
    import json

    first_body = json.loads(route.calls[0].request.content)
    second_body = json.loads(route.calls[1].request.content)
    assert first_body["model"] == "grok-imagine-image-quality"
    assert second_body["model"] == FALLBACK_IMAGE_MODEL


@pytest.mark.asyncio
@respx.mock
async def test_non_404_error_does_not_fall_back(monkeypatch):
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr("app.services.marketplaces.base.asyncio.sleep", fake_sleep)

    route = respx.post(f"{XAI_BASE_URL}/images/generations").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid API key"}})
    )

    client = GrokImagineClient(api_key="bad-key")
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.generate_infographic("промпт")

    assert "Invalid API key" in exc_info.value.message
    assert route.call_count == 1  # 401 — не повторяем и не переключаем модель


@pytest.mark.asyncio
@respx.mock
async def test_empty_data_raises_clear_error():
    respx.post(f"{XAI_BASE_URL}/images/generations").mock(return_value=httpx.Response(200, json={"data": []}))

    client = GrokImagineClient(api_key="test-key")
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.generate_infographic("промпт")

    assert "не вернул изображений" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.marketplaces.base.asyncio.sleep", fake_sleep)

    route = respx.post(f"{XAI_BASE_URL}/images/generations")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"ok").decode()}]}),
    ]

    client = GrokImagineClient(api_key="test-key")
    result = await client.generate_infographic("промпт", response_format="b64_json")

    assert result == b"ok"
    assert sleeps == [2.0]
