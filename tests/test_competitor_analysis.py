import httpx
import pytest
import respx

from app.services.competitor_analysis import (
    CompetitorAnalysisError,
    CompetitorItem,
    CompetitorReport,
    fetch_wb_shop,
    parse_wb_seller_id,
    search_ozon_competitors,
    search_wb_competitors,
    suggest_pricing,
)

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"
WB_SELLER_CATALOG_URL = "https://catalog.wb.ru/sellers/catalog"


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
async def test_search_wb_competitors_sends_browser_like_headers():
    """catalog.wb.ru/search.wb.ru отдают 403 без «браузерных» заголовков —
    дефолтный httpx User-Agent ("python-httpx/...") WB распознаёт как бота."""
    route = respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": []}}))
    await search_wb_competitors("что-то")
    sent_headers = route.calls[0].request.headers
    assert "python-httpx" not in sent_headers["user-agent"]
    assert "Mozilla" in sent_headers["user-agent"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_wb_shop_sends_browser_like_headers():
    route = respx.get(WB_SELLER_CATALOG_URL).mock(
        return_value=httpx.Response(200, json={"data": {"products": [{"name": "x", "salePriceU": 100000}]}})
    )
    await fetch_wb_shop("12345", max_pages=1)
    sent_headers = route.calls[0].request.headers
    assert "python-httpx" not in sent_headers["user-agent"]
    assert "Mozilla" in sent_headers["user-agent"]


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_retries_on_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.competitor_analysis.asyncio.sleep", fake_sleep)

    route = respx.get(WB_SEARCH_URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"data": {"products": [{"name": "x", "salePriceU": 100000}]}}),
    ]

    report = await search_wb_competitors("что-то")

    assert len(report.items) == 1
    assert sleeps == [2.0]  # уважает Retry-After


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_exhausts_retries_and_raises(monkeypatch):
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr("app.services.competitor_analysis.asyncio.sleep", fake_sleep)
    route = respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(CompetitorAnalysisError):
        await search_wb_competitors("что-то")

    assert route.call_count == 4  # 1 попытка + 3 повтора


@pytest.mark.asyncio
@respx.mock
async def test_fetch_wb_shop_retries_on_429_then_succeeds(monkeypatch):
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr("app.services.competitor_analysis.asyncio.sleep", fake_sleep)

    route = respx.get(WB_SELLER_CATALOG_URL)
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(200, json={"data": {"products": [{"name": "x", "salePriceU": 100000}]}}),
        httpx.Response(200, json={"data": {"products": []}}),
    ]

    report = await fetch_wb_shop("12345", max_pages=5)

    assert len(report.items) == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_network_error_raises():
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(CompetitorAnalysisError):
        await search_wb_competitors("что-то")


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_excludes_own_brand():
    """После публикации карточка продавца часто сама попадает в выдачу по
    своему названию/модели — без фильтрации она бы искажала «среднюю цену
    конкурентов» собственной ценой (см. app/services/pricing_intelligence.py)."""
    respx.get(WB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "products": [
                        {"name": "Накладки Lada Vesta", "salePriceU": 100000, "brand": "ALICARTUNING"},
                        {"name": "Накладки Lada Vesta", "salePriceU": 120000, "brand": "Other"},
                    ]
                }
            },
        )
    )
    report = await search_wb_competitors("накладки Lada Vesta", exclude_brand="ALICARTUNING")
    assert len(report.items) == 1
    assert report.items[0].brand == "Other"
    assert report.average_price == 1200.0


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_brand_exclusion_is_case_insensitive():
    respx.get(WB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"products": [{"name": "x", "salePriceU": 100000, "brand": "alicartuning"}]}},
        )
    )
    report = await search_wb_competitors("x", exclude_brand="ALICARTUNING")
    assert report.items == []


@pytest.mark.asyncio
@respx.mock
async def test_search_wb_competitors_exclusion_does_not_shrink_limit():
    """Фильтрация применяется до limit — исключённый бренд не должен уменьшать
    итоговое количество результатов, если в выдаче есть больше товаров."""
    products = [{"name": "own", "salePriceU": 100000, "brand": "ALICARTUNING"}] + [
        {"name": f"comp{i}", "salePriceU": 100000, "brand": "Other"} for i in range(3)
    ]
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": products}}))
    report = await search_wb_competitors("x", limit=3, exclude_brand="ALICARTUNING")
    assert len(report.items) == 3
    assert all(item.brand == "Other" for item in report.items)


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


def test_average_rating_and_total_feedbacks():
    report = CompetitorReport(
        query="x",
        items=[
            CompetitorItem(name="a", price=100, rating=4.0, feedbacks=10),
            CompetitorItem(name="b", price=200, rating=5.0, feedbacks=20),
            CompetitorItem(name="c", price=300),  # без рейтинга/отзывов — не должен ломать среднее
        ],
    )
    assert report.average_rating == 4.5
    assert report.total_feedbacks == 30


def test_average_rating_and_total_feedbacks_empty():
    report = CompetitorReport(query="x", items=[])
    assert report.average_rating is None
    assert report.total_feedbacks is None


# --- parse_wb_seller_id -------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.wildberries.ru/seller/12345", "12345"),
        ("wildberries.ru/seller/998877", "998877"),
        ("https://www.wildberries.ru/seller/555?utm_source=x", "555"),
        ("42", "42"),
        ("  42  ", "42"),
    ],
)
def test_parse_wb_seller_id_valid(text, expected):
    assert parse_wb_seller_id(text) == expected


@pytest.mark.parametrize("text", ["не ссылка", "https://www.wildberries.ru/catalog/123", ""])
def test_parse_wb_seller_id_invalid(text):
    assert parse_wb_seller_id(text) is None


# --- fetch_wb_shop -------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_wb_shop_paginates_until_empty_page():
    page1 = [{"name": f"item{i}", "salePriceU": 100000, "reviewRating": 4.5, "feedbacks": 5} for i in range(2)]
    respx.get(WB_SELLER_CATALOG_URL, params={"page": "1"}).mock(
        return_value=httpx.Response(200, json={"data": {"products": page1}})
    )
    respx.get(WB_SELLER_CATALOG_URL, params={"page": "2"}).mock(
        return_value=httpx.Response(200, json={"data": {"products": []}})
    )
    report = await fetch_wb_shop("12345", max_pages=5)
    assert len(report.items) == 2
    assert report.average_price == 1000.0
    assert report.average_rating == 4.5
    assert report.total_feedbacks == 10


@pytest.mark.asyncio
@respx.mock
async def test_fetch_wb_shop_stops_at_max_pages():
    page = [{"name": "x", "salePriceU": 100000}]
    respx.get(WB_SELLER_CATALOG_URL).mock(return_value=httpx.Response(200, json={"data": {"products": page}}))
    report = await fetch_wb_shop("12345", max_pages=2, limit=1000)
    assert len(report.items) == 2  # ровно по одной странице * max_pages


@pytest.mark.asyncio
@respx.mock
async def test_fetch_wb_shop_no_products_raises():
    respx.get(WB_SELLER_CATALOG_URL).mock(return_value=httpx.Response(200, json={"data": {"products": []}}))
    with pytest.raises(CompetitorAnalysisError):
        await fetch_wb_shop("99999999")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_wb_shop_network_error_raises():
    respx.get(WB_SELLER_CATALOG_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(CompetitorAnalysisError):
        await fetch_wb_shop("12345")
