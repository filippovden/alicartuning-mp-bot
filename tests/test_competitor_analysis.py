import httpx
import pytest
import respx

from app.services.competitor_analysis import (
    CompetitorAnalysisError,
    CompetitorItem,
    CompetitorReport,
    search_ozon_competitors,
    search_wb_competitors,
    suggest_pricing,
)

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_parses_prices():
    respx.get(WB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "products": [
                        {"name": "Накладки на зеркала Lada Vesta", "salePriceU": 120000, "brand": "Other", "reviewRating": 4.5, "feedbacks": 10},
                        {"name": "Накладки на зеркала Granta BMW стиль", "priceU": 90000, "brand": "AnotherBrand"},
                    ]
                }
            },
        )
    )
    report = await search_wb_competitors("накладки на зеркала")
    assert len(report.items) == 2
    assert report.items[0].price == 1200.0
    assert report.items[1].price == 900.0
    assert report.average_price == 1050.0
    assert report.min_price == 900.0
    assert report.max_price == 1200.0


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_network_error_raises():
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(CompetitorAnalysisError):
        await search_wb_competitors("что-то")


@pytest.mark.asyncio
async def test_search_ozon_competitors_not_supported():
    with pytest.raises(CompetitorAnalysisError):
        await search_ozon_competitors("что-то")


def test_top_keywords_excludes_stopwords():
    report = CompetitorReport(
        query="накладки",
        items=[
            CompetitorItem(name="Накладки на зеркала для Lada Vesta", price=1000),
            CompetitorItem(name="Накладки зеркал Vesta черные", price=1100),
        ],
    )
    keywords = report.top_keywords(5)
    assert "накладки" in keywords
    assert "для" not in keywords  # стоп-слово
    assert "на" not in keywords  # стоп-слово


def test_suggest_pricing_no_cost_price_returns_avg():
    report = CompetitorReport(query="x", items=[CompetitorItem(name="a", price=1000), CompetitorItem(name="b", price=1200)])
    result = suggest_pricing(report, cost_price=None)
    assert result["recommended_price"] == 1100.0
    assert result["margin_based_price"] is None


def test_suggest_pricing_margin_within_market():
    # margin_based_price=675 попадает в диапазон [avg*0.85, avg*1.15] = [595, 805] при avg=700
    report = CompetitorReport(query="x", items=[CompetitorItem(name="a", price=700)])
    result = suggest_pricing(report, cost_price=500, target_margin_pct=35)
    assert result["margin_based_price"] == 675.0
    assert result["recommended_price"] == 675.0
    assert result.get("note") is None


def test_suggest_pricing_flags_price_far_above_market():
    report = CompetitorReport(query="x", items=[CompetitorItem(name="a", price=500)])
    result = suggest_pricing(report, cost_price=500, target_margin_pct=35)
    assert result["margin_based_price"] == 675.0
    assert "выше среднего" in result["note"]


def test_suggest_pricing_flags_price_far_below_market():
    report = CompetitorReport(query="x", items=[CompetitorItem(name="a", price=2000)])
    result = suggest_pricing(report, cost_price=100, target_margin_pct=35)
    assert result["margin_based_price"] == 135.0
    assert "ниже конкурентов" in result["note"]
