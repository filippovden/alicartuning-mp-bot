from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.db.models import CompetitorPriceSnapshot, Marketplace, Product, ShopSnapshot, User
from app.services.competitor_analysis import CompetitorItem, CompetitorReport
from app.services.pricing_intelligence import (
    MIN_SNAPSHOTS_FOR_TREND,
    analyze_demand_by_weekday,
    build_recommendation,
    build_timing_report,
    check_significant_price_trends,
    format_trend_digest,
    get_price_trend,
    get_shop_price_trend,
    get_tracked_shop_seller_ids,
    save_shop_snapshot,
    snapshot_competitor_prices,
)

WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"


async def _make_product(session, **overrides) -> Product:
    user = User(telegram_id=overrides.pop("telegram_id", 800001))
    session.add(user)
    await session.commit()
    await session.refresh(user)

    defaults = dict(user_id=user.id, brand="ALICARTUNING", car_model="Lada Vesta", title="Тестовый товар")
    defaults.update(overrides)
    product = Product(**defaults)
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _add_snapshot(session, product_id: int, avg_price: float, days_ago: int) -> CompetitorPriceSnapshot:
    snapshot = CompetitorPriceSnapshot(
        product_id=product_id,
        query="Lada Vesta",
        marketplace=Marketplace.WB,
        avg_price=avg_price,
        min_price=avg_price - 50,
        max_price=avg_price + 50,
        item_count=10,
        captured_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    session.add(snapshot)
    await session.commit()
    return snapshot


# --- snapshot_competitor_prices ---------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_competitor_prices_creates_row(session):
    respx.get(WB_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"products": [{"name": "Товар конкурента", "salePriceU": 100000, "brand": "X"}]}},
        )
    )
    product = await _make_product(session)
    snapshot = await snapshot_competitor_prices(session, product)

    assert snapshot is not None
    assert snapshot.avg_price == 1000.0
    assert snapshot.item_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_competitor_prices_no_query_returns_none(session):
    product = await _make_product(session, car_model=None, title=None)
    snapshot = await snapshot_competitor_prices(session, product)
    assert snapshot is None


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_competitor_prices_api_error_returns_none(session):
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(500))
    product = await _make_product(session)
    snapshot = await snapshot_competitor_prices(session, product)
    assert snapshot is None


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_competitor_prices_empty_results_returns_none(session):
    respx.get(WB_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"products": []}}))
    product = await _make_product(session)
    snapshot = await snapshot_competitor_prices(session, product)
    assert snapshot is None


# --- get_price_trend ---------------------------------------------------------


@pytest.mark.asyncio
async def test_price_trend_insufficient_data(session):
    product = await _make_product(session)
    await _add_snapshot(session, product.id, 1000, days_ago=5)

    trend = await get_price_trend(session, product.id)
    assert trend.direction == "unknown"
    assert trend.snapshots_count == 1


@pytest.mark.asyncio
async def test_price_trend_detects_upward_trend(session):
    product = await _make_product(session)
    await _add_snapshot(session, product.id, 1000, days_ago=20)
    await _add_snapshot(session, product.id, 1050, days_ago=10)
    await _add_snapshot(session, product.id, 1200, days_ago=1)

    trend = await get_price_trend(session, product.id)
    assert trend.direction == "up"
    assert trend.change_pct == 20.0


@pytest.mark.asyncio
async def test_price_trend_detects_downward_trend(session):
    product = await _make_product(session)
    await _add_snapshot(session, product.id, 1200, days_ago=20)
    await _add_snapshot(session, product.id, 1100, days_ago=10)
    await _add_snapshot(session, product.id, 1000, days_ago=1)

    trend = await get_price_trend(session, product.id)
    assert trend.direction == "down"
    assert trend.change_pct < 0


@pytest.mark.asyncio
async def test_price_trend_flat_within_threshold(session):
    product = await _make_product(session)
    await _add_snapshot(session, product.id, 1000, days_ago=20)
    await _add_snapshot(session, product.id, 1010, days_ago=10)
    await _add_snapshot(session, product.id, 1030, days_ago=1)  # +3% — ниже порога значимости

    trend = await get_price_trend(session, product.id)
    assert trend.direction == "flat"


@pytest.mark.asyncio
async def test_price_trend_ignores_old_snapshots_outside_window(session):
    product = await _make_product(session)
    await _add_snapshot(session, product.id, 500, days_ago=90)  # вне окна 30 дней
    await _add_snapshot(session, product.id, 1000, days_ago=20)
    await _add_snapshot(session, product.id, 1010, days_ago=10)
    await _add_snapshot(session, product.id, 1030, days_ago=1)

    trend = await get_price_trend(session, product.id, days=30)
    assert trend.snapshots_count == 3  # старый снимок не учтён


# --- analyze_demand_by_weekday ------------------------------------------------


def test_demand_pattern_insufficient_days_returns_none_best_day():
    revenue = {"2026-08-03": 1000.0, "2026-08-04": 500.0}  # только 2 дня с данными
    pattern = analyze_demand_by_weekday(revenue)
    assert pattern.best_day is None
    assert pattern.worst_day is None


def test_demand_pattern_finds_best_and_worst_day():
    # 2026-08-03 = понедельник; строим 3 недели данных, чтобы был явный паттерн
    revenue = {}
    base = datetime(2026, 8, 3)
    weekday_amounts = [500, 500, 500, 500, 2000, 300, 300]  # пятница (индекс 4) — пик
    for week in range(3):
        for i, amount in enumerate(weekday_amounts):
            d = base + timedelta(days=7 * week + i)
            revenue[d.strftime("%Y-%m-%d")] = amount + week  # небольшой разброс

    pattern = analyze_demand_by_weekday(revenue)
    assert pattern.best_day == "Пятница"
    assert pattern.worst_day in ("Суббота", "Воскресенье")
    assert pattern.days_with_data == 7


# --- build_recommendation / build_timing_report ------------------------------


@pytest.mark.asyncio
async def test_build_recommendation_unknown_trend_mentions_insufficient_data():
    from app.services.pricing_intelligence import PriceTrend

    trend = PriceTrend(direction="unknown", change_pct=None, snapshots_count=1)
    text = build_recommendation(trend, None)
    assert "Недостаточно" in text or "недостаточно" in text.lower() or str(MIN_SNAPSHOTS_FOR_TREND) in text


@pytest.mark.asyncio
async def test_build_recommendation_down_trend_suggests_price_action():
    from app.services.pricing_intelligence import PriceTrend

    trend = PriceTrend(direction="down", change_pct=-15.0, snapshots_count=5, first_avg_price=1200, last_avg_price=1020)
    text = build_recommendation(trend, None)
    assert "снижаются" in text.lower()


@pytest.mark.asyncio
async def test_build_recommendation_flat_trend_suggests_promotion():
    from app.services.pricing_intelligence import PriceTrend

    trend = PriceTrend(direction="flat", change_pct=1.0, snapshots_count=5, first_avg_price=1000, last_avg_price=1010)
    text = build_recommendation(trend, None)
    assert "продвижение" in text.lower()


@pytest.mark.asyncio
async def test_build_timing_report_end_to_end(session):
    product = await _make_product(session)
    await _add_snapshot(session, product.id, 1200, days_ago=20)
    await _add_snapshot(session, product.id, 1100, days_ago=10)
    await _add_snapshot(session, product.id, 1000, days_ago=1)

    report = await build_timing_report(session, product, revenue_by_date=None)
    assert report.trend.direction == "down"
    assert report.demand is None
    assert "снижаются" in report.recommendation.lower()


# --- check_significant_price_trends / format_trend_digest --------------------
# Проактивный дайджест для Celery-задачи snapshot_competitor_prices_task
# (app/worker/celery_app.py) — раньше тренд был виден только через /analytics.


@pytest.mark.asyncio
async def test_check_significant_price_trends_filters_flat_and_unknown(session):
    up_product = await _make_product(session, telegram_id=1, vendor_code="UP-1")
    await _add_snapshot(session, up_product.id, 1000, days_ago=20)
    await _add_snapshot(session, up_product.id, 1100, days_ago=10)
    await _add_snapshot(session, up_product.id, 1200, days_ago=1)  # +20% — значимый рост

    flat_product = await _make_product(session, telegram_id=2, vendor_code="FLAT-1")
    await _add_snapshot(session, flat_product.id, 1000, days_ago=20)
    await _add_snapshot(session, flat_product.id, 1005, days_ago=10)
    await _add_snapshot(session, flat_product.id, 1010, days_ago=1)  # +1% — не значимо

    unknown_product = await _make_product(session, telegram_id=3, vendor_code="UNK-1")
    await _add_snapshot(session, unknown_product.id, 1000, days_ago=1)  # 1 замер — мало для тренда

    alerts = await check_significant_price_trends(session, [up_product, flat_product, unknown_product])

    assert len(alerts) == 1
    assert alerts[0].product_id == up_product.id
    assert alerts[0].trend.direction == "up"
    assert alerts[0].label == "UP-1"


@pytest.mark.asyncio
async def test_check_significant_price_trends_detects_down(session):
    product = await _make_product(session, telegram_id=4, vendor_code="DOWN-1")
    await _add_snapshot(session, product.id, 1200, days_ago=20)
    await _add_snapshot(session, product.id, 1100, days_ago=10)
    await _add_snapshot(session, product.id, 1000, days_ago=1)  # -16.7% — значимое падение

    alerts = await check_significant_price_trends(session, [product])

    assert len(alerts) == 1
    assert alerts[0].trend.direction == "down"


def test_format_trend_digest_lists_all_alerts_with_direction_arrows():
    from app.services.pricing_intelligence import PriceTrend, TrendAlert

    alerts = [
        TrendAlert(
            product_id=1,
            label="ART-UP",
            trend=PriceTrend(direction="up", change_pct=20.0, snapshots_count=3, first_avg_price=1000, last_avg_price=1200),
        ),
        TrendAlert(
            product_id=2,
            label="ART-DOWN",
            trend=PriceTrend(direction="down", change_pct=-15.0, snapshots_count=3, first_avg_price=1200, last_avg_price=1020),
        ),
    ]

    text = format_trend_digest(alerts)

    assert "ART-UP" in text
    assert "ART-DOWN" in text
    assert "📈" in text
    assert "📉" in text
    assert "/analytics" in text


# --- Снимки магазинов-конкурентов (/shop) ---------------------------------------


async def _add_shop_snapshot(session, seller_id: str, avg_price: float, days_ago: int) -> ShopSnapshot:
    snapshot = ShopSnapshot(
        marketplace=Marketplace.WB,
        seller_id=seller_id,
        item_count=10,
        avg_price=avg_price,
        min_price=avg_price - 50,
        max_price=avg_price + 50,
        avg_rating=4.5,
        total_feedbacks=100,
        captured_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    session.add(snapshot)
    await session.commit()
    return snapshot


@pytest.mark.asyncio
async def test_save_shop_snapshot_creates_row(session):
    report = CompetitorReport(
        query="12345",
        items=[
            CompetitorItem(name="a", price=1000, rating=4.5, feedbacks=10),
            CompetitorItem(name="b", price=1200, rating=4.0, feedbacks=20),
        ],
    )
    snapshot = await save_shop_snapshot(session, "12345", report)

    assert snapshot.seller_id == "12345"
    assert snapshot.item_count == 2
    assert float(snapshot.avg_price) == 1100.0
    assert float(snapshot.avg_rating) == 4.25
    assert snapshot.total_feedbacks == 30


@pytest.mark.asyncio
async def test_get_shop_price_trend_unknown_with_single_snapshot(session):
    await _add_shop_snapshot(session, "111", 1000, days_ago=0)

    trend = await get_shop_price_trend(session, "111")

    assert trend.direction == "unknown"
    assert trend.snapshots_count == 1


@pytest.mark.asyncio
async def test_get_shop_price_trend_detects_up(session):
    await _add_shop_snapshot(session, "222", 1000, days_ago=5)
    await _add_shop_snapshot(session, "222", 1200, days_ago=0)

    trend = await get_shop_price_trend(session, "222")

    assert trend.direction == "up"
    assert trend.change_pct == 20.0
    assert trend.snapshots_count == 2


@pytest.mark.asyncio
async def test_get_shop_price_trend_detects_down(session):
    await _add_shop_snapshot(session, "333", 1200, days_ago=5)
    await _add_shop_snapshot(session, "333", 1000, days_ago=0)

    trend = await get_shop_price_trend(session, "333")

    assert trend.direction == "down"


@pytest.mark.asyncio
async def test_get_shop_price_trend_flat_within_threshold(session):
    await _add_shop_snapshot(session, "444", 1000, days_ago=5)
    await _add_shop_snapshot(session, "444", 1010, days_ago=0)

    trend = await get_shop_price_trend(session, "444")

    assert trend.direction == "flat"


@pytest.mark.asyncio
async def test_get_tracked_shop_seller_ids_returns_distinct_ids(session):
    await _add_shop_snapshot(session, "555", 1000, days_ago=1)
    await _add_shop_snapshot(session, "555", 1050, days_ago=0)
    await _add_shop_snapshot(session, "666", 2000, days_ago=0)

    ids = await get_tracked_shop_seller_ids(session)

    assert set(ids) == {"555", "666"}
