"""Тесты retry/backoff при 429 в BaseMarketplaceClient (см. app/services/marketplaces/base.py).

По результатам ресёрча открытых аналогов: Ozon Seller API возвращает 429 с телом
{"error_type": "rate_limit_exceeded", "retry_after_seconds": N} — клиент должен
уважать это поле, а не просто фиксированный backoff.
"""

import httpx
import pytest
import respx

from app.services.marketplaces.base import MarketplaceAPIError
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wildberries import WildberriesClient

OZON_BASE_URL = "https://api-seller.ozon.ru"
WB_BASE_URL = "https://content-api.wildberries.ru"


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.marketplaces.base.asyncio.sleep", fake_sleep)

    route = respx.post(f"{OZON_BASE_URL}/v2/product/import")
    route.side_effect = [
        httpx.Response(429, json={"error_type": "rate_limit_exceeded", "retry_after_seconds": 2}),
        httpx.Response(200, json={"result": {"task_id": 999}}),
    ]

    client = OzonClient(client_id="cid", api_key="key", base_url=OZON_BASE_URL)
    result = await client.import_products([{"offer_id": "ART1"}])

    assert result.success is True
    assert result.external_id == "999"
    assert sleeps == [2.0]  # уважает retry_after_seconds из тела ответа Ozon


@pytest.mark.asyncio
@respx.mock
async def test_exhausts_retries_and_raises(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.marketplaces.base.asyncio.sleep", fake_sleep)

    respx.get(f"{WB_BASE_URL}/content/v2/object/parent/all").mock(
        return_value=httpx.Response(429, json={"errorText": "Too Many Requests"})
    )

    client = WildberriesClient(api_key="test", base_url=WB_BASE_URL)
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.get_parent_categories()

    assert exc_info.value.status_code == 429
    # 3 повтора (max_retries по умолчанию) — экспоненциальный backoff 1с, 2с, 4с
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
@respx.mock
async def test_respects_retry_after_header(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.marketplaces.base.asyncio.sleep", fake_sleep)

    route = respx.post(f"{OZON_BASE_URL}/v2/product/import")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "5"}),
        httpx.Response(200, json={"result": {"task_id": 1}}),
    ]

    client = OzonClient(client_id="cid", api_key="key", base_url=OZON_BASE_URL)
    await client.import_products([{"offer_id": "ART1"}])

    assert sleeps == [5.0]


@pytest.mark.asyncio
@respx.mock
async def test_non_429_error_does_not_retry(monkeypatch):
    calls = {"count": 0}

    async def fake_sleep(seconds):
        raise AssertionError("sleep не должен вызываться для не-429 ошибок")

    monkeypatch.setattr("app.services.marketplaces.base.asyncio.sleep", fake_sleep)

    respx.post(f"{OZON_BASE_URL}/v2/product/import").mock(
        return_value=httpx.Response(400, json={"message": "Bad request"})
    )

    client = OzonClient(client_id="cid", api_key="key", base_url=OZON_BASE_URL)
    with pytest.raises(MarketplaceAPIError) as exc_info:
        await client.import_products([{"offer_id": "ART1"}])
    assert exc_info.value.status_code == 400
