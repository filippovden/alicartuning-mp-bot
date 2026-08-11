"""Хендлер /shop — разбор магазина-конкурента на WB по ссылке, с сохранением
снимка и трендом цены с первого запроса (см. app/bot/handlers/competitors.py,
app/services/pricing_intelligence.py: save_shop_snapshot/get_shop_price_trend).
"""

from __future__ import annotations

import pytest

from app.bot.handlers import competitors as competitors_handler
from app.services.competitor_analysis import CompetitorAnalysisError, CompetitorItem, CompetitorReport


class _FakeUser:
    def __init__(self, uid: int):
        self.id = uid


class _FakeMessage:
    def __init__(self, text: str, user: _FakeUser | None = None):
        self.text = text
        self.from_user = user or _FakeUser(1)
        self.answered: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self


@pytest.mark.asyncio
async def test_shop_without_link_shows_usage(session):
    message = _FakeMessage("/shop")
    await competitors_handler.cmd_shop(message, session)
    assert "Использование" in message.answered[0]
    assert "/shop" in message.answered[0]


@pytest.mark.asyncio
async def test_shop_with_unparseable_link_asks_for_valid_one(session):
    message = _FakeMessage("/shop не ссылка")
    await competitors_handler.cmd_shop(message, session)
    assert any("Не нашёл ID продавца" in t for t in message.answered)


@pytest.mark.asyncio
async def test_shop_first_query_saves_snapshot_and_shows_unknown_trend(session, monkeypatch):
    async def fake_fetch_wb_shop(seller_id, max_pages=3, limit=100):
        return CompetitorReport(
            query=seller_id,
            items=[
                CompetitorItem(name="Накладки Vesta", price=1000, rating=4.5, feedbacks=10),
                CompetitorItem(name="Накладки Granta", price=1200, rating=4.0, feedbacks=20),
            ],
        )

    monkeypatch.setattr(competitors_handler, "fetch_wb_shop", fake_fetch_wb_shop)

    message = _FakeMessage("/shop https://www.wildberries.ru/seller/12345")
    await competitors_handler.cmd_shop(message, session)

    report_text = message.answered[-1]
    assert "Магазин WB" in report_text
    assert "12345" in report_text
    assert "Товаров в ассортименте: 2" in report_text
    assert "первый снимок" in report_text


@pytest.mark.asyncio
async def test_shop_second_query_shows_price_trend(session, monkeypatch):
    reports = iter(
        [
            CompetitorReport(query="777", items=[CompetitorItem(name="a", price=1000, rating=4.5, feedbacks=10)]),
            CompetitorReport(query="777", items=[CompetitorItem(name="a", price=1300, rating=4.5, feedbacks=10)]),
        ]
    )

    async def fake_fetch_wb_shop(seller_id, max_pages=3, limit=100):
        return next(reports)

    monkeypatch.setattr(competitors_handler, "fetch_wb_shop", fake_fetch_wb_shop)

    await competitors_handler.cmd_shop(_FakeMessage("/shop 777"), session)
    message2 = _FakeMessage("/shop 777")
    await competitors_handler.cmd_shop(message2, session)

    report_text = message2.answered[-1]
    assert "выросла" in report_text
    assert "30.0%" in report_text


@pytest.mark.asyncio
async def test_shop_reports_friendly_error_on_failure(session, monkeypatch):
    async def failing_fetch(seller_id, max_pages=3, limit=100):
        raise CompetitorAnalysisError("По этой ссылке не нашлось товаров — проверьте ссылку на магазин WB.")

    monkeypatch.setattr(competitors_handler, "fetch_wb_shop", failing_fetch)

    message = _FakeMessage("/shop 999")
    await competitors_handler.cmd_shop(message, session)

    assert any("не нашлось товаров" in t for t in message.answered)
