import httpx
import pytest
import respx

from app.services.analytics_service import get_ozon_sales_summary, get_wb_revenue_by_date, get_wb_sales_summary, recommend_price
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wb_statistics import WbStatisticsClient

WB_STATS_URL = "https://statistics-api.wildberries.ru"
OZON_BASE_URL = "https://api-seller.ozon.ru"


@pytest.mark.asyncio
@respx.mock
async def test_get_wb_sales_summary_excludes_returns():
    respx.get(f"{WB_STATS_URL}/api/v1/supplier/sales").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"saleID": "S1", "supplierArticle": "ART-1", "forPay": 1000.0},
                {"saleID": "S2", "supplierArticle": "ART-1", "forPay": 1000.0},
                {"saleID": "R1", "supplierArticle": "ART-1", "forPay": 1000.0},  # возврат — исключается
            ],
        )
    )
    client = WbStatisticsClient(api_key="test", base_url=WB_STATS_URL)
    summary = await get_wb_sales_summary(30, client=client)
    assert summary.total_units == 2
    assert summary.total_revenue == 2000.0
    assert summary.by_sku["ART-1"]["units"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_get_ozon_sales_summary_aggregates_by_sku():
    respx.post(f"{OZON_BASE_URL}/v1/analytics/data").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "data": [
                        {"dimensions": [{"id": "ART-1"}], "metrics": [1500.0, 3]},
                        {"dimensions": [{"id": "ART-2"}], "metrics": [500.0, 1]},
                    ]
                }
            },
        )
    )
    client = OzonClient(client_id="cid", api_key="key", base_url=OZON_BASE_URL)
    summary = await get_ozon_sales_summary(30, client=client)
    assert summary.total_units == 4
    assert summary.total_revenue == 2000.0
    assert summary.by_sku["ART-1"] == {"units": 3, "revenue": 1500.0}


def test_recommend_price_no_competitor_data():
    result = recommend_price(cost_price=500, target_margin_pct=35)
    assert result["target_price"] == 675.0
    assert result["recommended_price"] == 675.0
    assert result["note"] is None


def test_recommend_price_competitor_below_target_lowers_to_market():
    # target=675, avg=600 -> min_price=575, recommended=max(575,600)=600 (снижена, но не ниже минимума)
    result = recommend_price(cost_price=500, target_margin_pct=35, competitor_avg_price=600)
    assert result["recommended_price"] == 600.0
    assert "снижена до уровня рынка" in result["note"]


def test_recommend_price_competitor_far_below_min_margin():
    # target=675, avg=550 (< min_price=575) -> recommended=575, выше рынка даже по минимальной марже
    result = recommend_price(cost_price=500, target_margin_pct=35, competitor_avg_price=550)
    assert result["recommended_price"] == 575.0
    assert "конкурировать по цене будет сложно" in result["note"]


def test_recommend_price_competitor_above_target_keeps_target():
    result = recommend_price(cost_price=500, target_margin_pct=35, competitor_avg_price=1000)
    assert result["recommended_price"] == 675.0
    assert result["note"] is None


@pytest.mark.asyncio
@respx.mock
async def test_get_wb_revenue_by_date_groups_and_excludes_returns_and_other_sku():
    respx.get(f"{WB_STATS_URL}/api/v1/supplier/sales").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"saleID": "S1", "supplierArticle": "ART-1", "forPay": 1000.0, "date": "2026-08-03T10:00:00"},
                {"saleID": "S2", "supplierArticle": "ART-1", "forPay": 500.0, "date": "2026-08-03T15:00:00"},
                {"saleID": "S3", "supplierArticle": "ART-1", "forPay": 800.0, "date": "2026-08-04T09:00:00"},
                {"saleID": "R1", "supplierArticle": "ART-1", "forPay": 999.0, "date": "2026-08-04T09:00:00"},  # возврат
                {"saleID": "S4", "supplierArticle": "ART-2", "forPay": 5000.0, "date": "2026-08-03T09:00:00"},  # другой SKU
            ],
        )
    )
    client = WbStatisticsClient(api_key="test", base_url=WB_STATS_URL)
    by_date = await get_wb_revenue_by_date(60, sku="ART-1", client=client)

    assert by_date == {"2026-08-03": 1500.0, "2026-08-04": 800.0}


@pytest.mark.asyncio
@respx.mock
async def test_get_wb_revenue_by_date_without_sku_filter_includes_all():
    respx.get(f"{WB_STATS_URL}/api/v1/supplier/sales").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"saleID": "S1", "supplierArticle": "ART-1", "forPay": 1000.0, "date": "2026-08-03T10:00:00"},
                {"saleID": "S4", "supplierArticle": "ART-2", "forPay": 5000.0, "date": "2026-08-03T09:00:00"},
            ],
        )
    )
    client = WbStatisticsClient(api_key="test", base_url=WB_STATS_URL)
    by_date = await get_wb_revenue_by_date(60, client=client)

    assert by_date == {"2026-08-03": 6000.0}
