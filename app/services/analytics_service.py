"""Аналитика продаж и рекомендация цены (раздел 4, V3 ТЗ — «AI-аналитика»).

Собирает статистику через WB Statistics API и Ozon Analytics API, агрегирует по SKU
и считает рекомендованную розничную цену с учётом себестоимости, целевой маржи и
цен конкурентов (см. app/services/competitor_analysis.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wb_statistics import WbStatisticsClient


@dataclass
class SalesSummary:
    marketplace: str
    period_days: int
    total_revenue: float = 0.0
    total_units: int = 0
    by_sku: dict[str, dict] = field(default_factory=dict)


async def get_wb_sales_summary(period_days: int = 30, client: WbStatisticsClient | None = None) -> SalesSummary:
    client = client or WbStatisticsClient()
    date_from = (date.today() - timedelta(days=period_days)).isoformat()
    sales = await client.get_sales(date_from)

    summary = SalesSummary(marketplace="wildberries", period_days=period_days)
    for sale in sales:
        # Возврат — saleID у WB начинается с "R" (см. документацию Statistics API)
        if str(sale.get("saleID", "")).startswith("R"):
            continue
        sku = str(sale.get("supplierArticle") or sale.get("nmId") or "unknown")
        amount = float(sale.get("forPay") or sale.get("priceWithDisc") or 0)

        bucket = summary.by_sku.setdefault(sku, {"units": 0, "revenue": 0.0})
        bucket["units"] += 1
        bucket["revenue"] += amount
        summary.total_revenue += amount
        summary.total_units += 1

    summary.total_revenue = round(summary.total_revenue, 2)
    return summary


async def get_ozon_sales_summary(period_days: int = 30, client: OzonClient | None = None) -> SalesSummary:
    client = client or OzonClient()
    date_to = date.today().isoformat()
    date_from = (date.today() - timedelta(days=period_days)).isoformat()
    data = await client.get_analytics_data(
        date_from, date_to, metrics=["revenue", "ordered_units"], dimensions=["sku"]
    )

    summary = SalesSummary(marketplace="ozon", period_days=period_days)
    for row in data.get("data", []):
        dims = row.get("dimensions", [])
        sku = str(dims[0]["id"]) if dims else "unknown"
        metrics = row.get("metrics", [])
        revenue = float(metrics[0]) if len(metrics) > 0 else 0.0
        units = int(metrics[1]) if len(metrics) > 1 else 0

        summary.by_sku[sku] = {"units": units, "revenue": revenue}
        summary.total_revenue += revenue
        summary.total_units += units

    summary.total_revenue = round(summary.total_revenue, 2)
    return summary


async def get_wb_revenue_by_date(
    period_days: int = 60,
    sku: str | None = None,
    client: WbStatisticsClient | None = None,
) -> dict[str, float]:
    """Выручка WB по датам (YYYY-MM-DD) за период — сырьё для анализа паттернов
    спроса по дням недели (см. app/services/pricing_intelligence.py). Если указан
    `sku`, учитываются только продажи этого артикула."""
    client = client or WbStatisticsClient()
    date_from = (date.today() - timedelta(days=period_days)).isoformat()
    sales = await client.get_sales(date_from)

    by_date: dict[str, float] = {}
    for sale in sales:
        if str(sale.get("saleID", "")).startswith("R"):
            continue
        if sku and str(sale.get("supplierArticle")) != str(sku):
            continue

        sale_date = str(sale.get("date") or "")[:10]  # "2026-08-06T00:00:00" -> "2026-08-06"
        if not sale_date:
            continue

        amount = float(sale.get("forPay") or sale.get("priceWithDisc") or 0)
        by_date[sale_date] = by_date.get(sale_date, 0.0) + amount

    return by_date


def recommend_price(
    cost_price: float,
    target_margin_pct: float = 35.0,
    competitor_avg_price: float | None = None,
    min_margin_pct: float = 15.0,
) -> dict:
    """Рекомендация цены: себестоимость + целевая маржа, скорректированная по рынку,
    но не ниже минимально допустимой маржи."""
    target_price = cost_price * (1 + target_margin_pct / 100)
    min_price = cost_price * (1 + min_margin_pct / 100)

    recommended = target_price
    note: str | None = None

    if competitor_avg_price and target_price > competitor_avg_price:
        recommended = max(min_price, competitor_avg_price)
        if recommended > competitor_avg_price:
            note = "Даже по минимальной марже цена выше среднерыночной — конкурировать по цене будет сложно"
        elif recommended < target_price:
            note = "Цена снижена до уровня рынка (маржа ниже целевой, но не ниже минимальной)"

    return {
        "cost_price": cost_price,
        "target_margin_pct": target_margin_pct,
        "target_price": round(target_price, 2),
        "min_price": round(min_price, 2),
        "recommended_price": round(recommended, 2),
        "note": note,
    }
