"""Понятные сообщения об ошибках маркетплейсов в /analytics (раздел H ТЗ:
никакого техжаргона в лицо пользователю). WB Statistics API официально
ограничивает некоторые эндпоинты (/api/v1/supplier/sales и т.п.) 1 запросом
в минуту на ключ и при превышении отдаёт сырое сообщение вида «Limited by
global limiter, per seller <uuid>» — пользователь не должен видеть эту фразу
как есть, только понятную причину и что делать (см. app/bot/handlers/analytics.py).
"""

from __future__ import annotations

import pytest

from app.bot.handlers import analytics as analytics_handler
from app.services.marketplaces.base import MarketplaceAPIError


class _FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.answered: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self


def test_marketplace_error_line_rewords_global_limiter_message():
    exc = MarketplaceAPIError(
        "Limited by global limiter, per seller d5b4a219-f702-4730-97be-6a90d4c7dadf; "
        "See https://dev.wildberries.ru/openapi/api-information",
        status_code=429,
    )
    line = analytics_handler._marketplace_error_line("Wildberries", exc)
    assert "Limited by global limiter" not in line
    assert "per seller" not in line
    assert "минуту" in line


def test_marketplace_error_line_rewords_by_keyword_even_without_429_status():
    exc = MarketplaceAPIError("Request limit exceeded for this key", status_code=None)
    line = analytics_handler._marketplace_error_line("Ozon", exc)
    assert "минуту" in line


def test_marketplace_error_line_keeps_other_errors_as_is():
    exc = MarketplaceAPIError("Неверный API-ключ", status_code=401)
    line = analytics_handler._marketplace_error_line("Wildberries", exc)
    assert "Неверный API-ключ" in line


@pytest.mark.asyncio
async def test_cmd_analytics_shows_friendly_message_on_rate_limit(monkeypatch):
    async def fake_wb_summary(days):
        raise MarketplaceAPIError(
            "Limited by global limiter, per seller d5b4a219-f702-4730-97be-6a90d4c7dadf",
            status_code=429,
        )

    async def fake_ozon_summary(days):
        from app.services.analytics_service import SalesSummary

        return SalesSummary(marketplace="ozon", period_days=days, total_units=0, total_revenue=0.0, by_sku={})

    monkeypatch.setattr(analytics_handler, "get_wb_sales_summary", fake_wb_summary)
    monkeypatch.setattr(analytics_handler, "get_ozon_sales_summary", fake_ozon_summary)

    class _FakeService:
        pass

    message = _FakeMessage("/analytics")
    await analytics_handler.cmd_analytics(message, _FakeService(), session=None)

    summary_text = message.answered[-1]
    assert "Limited by global limiter" not in summary_text
    assert "per seller" not in summary_text
    assert "минуту" in summary_text
