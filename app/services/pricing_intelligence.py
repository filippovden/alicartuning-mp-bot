"""Тайминг-аналитика: когда лучше продвигаться и когда менять цену.

Идея: разовая цена конкурентов (см. competitor_analysis.py) отвечает на вопрос
«сколько сейчас стоит рынок», но не отвечает на «что делать дальше». Этот модуль
накапливает историю цен конкурентов (снимки — раз в сутки через Celery, плюс
опортунистически при вызове бот-команд) и сопоставляет её с паттерном спроса по
дням недели из собственных продаж (WB Statistics API), чтобы дать конкретную
рекомендацию по времени: держать/менять цену сейчас или вложиться в продвижение.

Это не «предсказание будущего» — это трендовый анализ на исторических данных,
честно помечающий случаи недостатка данных вместо того, чтобы выдумывать сигнал.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CompetitorPriceSnapshot, Marketplace, Product
from app.services.competitor_analysis import CompetitorAnalysisError, search_wb_competitors

MIN_SNAPSHOTS_FOR_TREND = 3
TREND_SIGNIFICANT_PCT = 5.0

WEEKDAY_NAMES_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


@dataclass
class PriceTrend:
    direction: str  # "up" | "down" | "flat" | "unknown"
    change_pct: float | None
    snapshots_count: int
    first_avg_price: float | None = None
    last_avg_price: float | None = None


@dataclass
class DemandPattern:
    best_day: str | None
    worst_day: str | None
    by_weekday: dict[str, float] = field(default_factory=dict)
    days_with_data: int = 0


@dataclass
class PricingTimingReport:
    trend: PriceTrend
    demand: DemandPattern | None
    recommendation: str


async def snapshot_competitor_prices(session: AsyncSession, product: Product) -> CompetitorPriceSnapshot | None:
    """Снимает текущие цены конкурентов и сохраняет как точку истории. Вызывается
    как из периодической Celery-задачи, так и опортунистически при обращении к
    /analytics или /competitors, чтобы данные начинали копиться сразу."""
    query = product.car_model or product.title
    if not query:
        return None

    try:
        report = await search_wb_competitors(query)
    except CompetitorAnalysisError:
        return None

    if not report.items or report.average_price is None:
        return None

    snapshot = CompetitorPriceSnapshot(
        product_id=product.id,
        query=query,
        marketplace=Marketplace.WB,
        avg_price=report.average_price,
        min_price=report.min_price,
        max_price=report.max_price,
        item_count=len(report.items),
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def get_price_trend(session: AsyncSession, product_id: int, days: int = 30) -> PriceTrend:
    """Тренд средней цены конкурентов за период по накопленным снимкам."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(CompetitorPriceSnapshot)
        .where(CompetitorPriceSnapshot.product_id == product_id, CompetitorPriceSnapshot.captured_at >= since)
        .order_by(CompetitorPriceSnapshot.captured_at.asc())
    )
    snapshots = list((await session.execute(stmt)).scalars().all())

    if len(snapshots) < MIN_SNAPSHOTS_FOR_TREND:
        return PriceTrend(direction="unknown", change_pct=None, snapshots_count=len(snapshots))

    first_price = float(snapshots[0].avg_price)
    last_price = float(snapshots[-1].avg_price)
    if first_price == 0:
        return PriceTrend(direction="unknown", change_pct=None, snapshots_count=len(snapshots))

    change_pct = round((last_price - first_price) / first_price * 100, 2)
    if change_pct > TREND_SIGNIFICANT_PCT:
        direction = "up"
    elif change_pct < -TREND_SIGNIFICANT_PCT:
        direction = "down"
    else:
        direction = "flat"

    return PriceTrend(
        direction=direction,
        change_pct=change_pct,
        snapshots_count=len(snapshots),
        first_avg_price=first_price,
        last_avg_price=last_price,
    )


def analyze_demand_by_weekday(revenue_by_date: dict[str, float]) -> DemandPattern:
    """revenue_by_date: {"2026-08-01": выручка, ...} -> лучший/худший день недели.

    Требует данных минимум за 2 недели, иначе один нетипичный день перевесит
    статистику — в таком случае возвращается пустой паттерн (best_day=None)."""
    buckets: dict[str, list[float]] = {name: [] for name in WEEKDAY_NAMES_RU}
    for date_str, revenue in revenue_by_date.items():
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        buckets[WEEKDAY_NAMES_RU[dt.weekday()]].append(revenue)

    days_with_data = sum(1 for values in buckets.values() if values)
    averages = {day: round(mean(values), 2) if values else 0.0 for day, values in buckets.items()}

    if days_with_data < 5 or not any(averages.values()):
        return DemandPattern(best_day=None, worst_day=None, by_weekday=averages, days_with_data=days_with_data)

    non_zero = {day: v for day, v in averages.items() if v > 0}
    best_day = max(non_zero, key=non_zero.get)
    worst_day = min(non_zero, key=non_zero.get)
    return DemandPattern(best_day=best_day, worst_day=worst_day, by_weekday=averages, days_with_data=days_with_data)


def build_recommendation(trend: PriceTrend, demand: DemandPattern | None) -> str:
    parts: list[str] = []

    if trend.direction == "unknown":
        parts.append(
            f"📉 Пока накоплено только {trend.snapshots_count} замер(ов) цен конкурентов "
            f"(нужно минимум {MIN_SNAPSHOTS_FOR_TREND}, снимаются раз в сутки). Рекомендация по тренду "
            "появится через несколько дней — карточка уже поставлена на отслеживание."
        )
    elif trend.direction == "down":
        parts.append(
            f"📉 Цены конкурентов снижаются (в среднем {trend.first_avg_price:.0f}₽ → "
            f"{trend.last_avg_price:.0f}₽, {trend.change_pct:+.1f}% за период). Рынок дешевеет — "
            "в ближайшие дни стоит либо скорректировать цену вниз, либо усилить карточку "
            "(фото/описание/инфографику), чтобы удержать позиции без демпинга."
        )
    elif trend.direction == "up":
        parts.append(
            f"📈 Цены конкурентов растут (в среднем {trend.first_avg_price:.0f}₽ → "
            f"{trend.last_avg_price:.0f}₽, {trend.change_pct:+.1f}% за период). Можно аккуратно "
            "поднять цену и вам — рынок это выдержит, дополнительная маржа не ударит по спросу."
        )
    else:
        parts.append(
            f"➡️ Цены конкурентов стабильны ({trend.change_pct:+.1f}% за период). Сейчас не время "
            "конкурировать ценой — лучше вложиться в продвижение (реклама, буст, обновление контента)."
        )

    if demand is None or demand.best_day is None:
        parts.append(
            "🗓 Недостаточно истории продаж для анализа по дням недели (нужно данных минимум за "
            "1-2 недели активных продаж)."
        )
    else:
        parts.append(
            f"🗓 По истории продаж лучший день — {demand.best_day} "
            f"({demand.by_weekday[demand.best_day]:.0f}₽ в среднем), худший — {demand.worst_day} "
            f"({demand.by_weekday[demand.worst_day]:.0f}₽). Рекомендуем запускать/усиливать рекламу "
            f"за 1-2 дня до пикового дня, чтобы алгоритмы площадки успели раскрутить карточку к пику спроса."
        )

    return "\n\n".join(parts)


async def build_timing_report(
    session: AsyncSession,
    product: Product,
    revenue_by_date: dict[str, float] | None = None,
) -> PricingTimingReport:
    trend = await get_price_trend(session, product.id)
    demand = analyze_demand_by_weekday(revenue_by_date) if revenue_by_date else None
    recommendation = build_recommendation(trend, demand)
    return PricingTimingReport(trend=trend, demand=demand, recommendation=recommendation)
