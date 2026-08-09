from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.services.analytics_service import get_ozon_sales_summary, get_wb_revenue_by_date, get_wb_sales_summary, recommend_price
from app.services.competitor_analysis import CompetitorAnalysisError, search_wb_competitors
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.pricing_intelligence import build_timing_report, snapshot_competitor_prices

logger = logging.getLogger(__name__)
router = Router(name="analytics")

PERIOD_DAYS = 30
DEMAND_HISTORY_DAYS = 60


@router.message(Command("analytics"))
async def cmd_analytics(message: Message, product_service, session) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip().isdigit():
        await _product_price_recommendation(message, product_service, session, int(args[1].strip()))
        return

    await message.answer(f"⏳ Собираю аналитику продаж за последние {PERIOD_DAYS} дней...")

    lines = [f"📊 <b>Сводка продаж за {PERIOD_DAYS} дней</b>"]

    try:
        wb_summary = await get_wb_sales_summary(PERIOD_DAYS)
        lines.append(f"\n<b>Wildberries:</b> {wb_summary.total_units} продаж на {wb_summary.total_revenue:.0f}₽")
        lines += _top_sku_lines(wb_summary.by_sku)
    except MarketplaceAPIError as exc:
        lines.append(_marketplace_error_line("Wildberries", exc))

    try:
        ozon_summary = await get_ozon_sales_summary(PERIOD_DAYS)
        lines.append(f"\n<b>Ozon:</b> {ozon_summary.total_units} продаж на {ozon_summary.total_revenue:.0f}₽")
        lines += _top_sku_lines(ozon_summary.by_sku)
    except MarketplaceAPIError as exc:
        lines.append(_marketplace_error_line("Ozon", exc))

    lines.append(
        "\nПодсказка: /analytics [ID товара] — рекомендация цены и тайминг "
        "продвижения по конкретному товару."
    )
    await message.answer("\n".join(lines))


def _marketplace_error_line(marketplace: str, exc: MarketplaceAPIError) -> str:
    """Раздел H ТЗ: никакого техжаргона в лицо пользователю. WB Statistics API
    (get_wb_sales_summary → /api/v1/supplier/sales и т.п.) официально ограничен
    1 запросом в минуту на ключ и при превышении отдаёт сырое английское
    сообщение вида «Limited by global limiter, per seller ...» — показываем
    вместо него понятную причину и что делать."""
    message = exc.message or ""
    if exc.status_code == 429 or "limit" in message.lower():
        return (
            f"\n<b>{marketplace}:</b> ⚠️ статистика временно ограничена площадкой "
            "(слишком частые запросы) — повторите через минуту через /analytics."
        )
    return f"\n<b>{marketplace}:</b> ⚠️ не удалось получить данные ({message})"


def _top_sku_lines(by_sku: dict[str, dict], limit: int = 5) -> list[str]:
    if not by_sku:
        return ["  нет данных за период"]
    top = sorted(by_sku.items(), key=lambda kv: kv[1]["revenue"], reverse=True)[:limit]
    return [f"  • {sku}: {info['units']} шт., {info['revenue']:.0f}₽" for sku, info in top]


async def _product_price_recommendation(message: Message, product_service, session, product_id: int) -> None:
    product = await product_service.get_product(product_id)
    if product is None:
        await message.answer("Товар не найден.")
        return
    if not product.cost_price:
        await message.answer("У товара не указана себестоимость — рекомендацию цены посчитать нельзя.")
        return

    competitor_avg = None
    query = product.car_model or product.title
    if query:
        try:
            report = await search_wb_competitors(query, exclude_brand=product.brand or settings.brand_name)
            competitor_avg = report.average_price
        except CompetitorAnalysisError as exc:
            logger.warning("Анализ конкурентов для рекомендации цены не удался: %s", exc)

        # Опортунистический снимок — данные для тренда начинают копиться сразу,
        # не дожидаясь ежесуточной Celery-задачи (см. pricing_intelligence.py).
        try:
            await snapshot_competitor_prices(session, product)
        except Exception:
            logger.warning("Не удалось сохранить снимок цен конкурентов для товара %s", product_id, exc_info=True)

    pricing = recommend_price(float(product.cost_price), competitor_avg_price=competitor_avg)

    lines = [
        f"💰 <b>Рекомендация цены для товара #{product.id}</b>",
        f"Себестоимость: {pricing['cost_price']:.0f}₽",
        f"Целевая цена (маржа {pricing['target_margin_pct']:.0f}%): {pricing['target_price']:.0f}₽",
        f"Минимальная цена (маржа 15%): {pricing['min_price']:.0f}₽",
    ]
    if competitor_avg:
        lines.append(f"Средняя цена конкурентов на WB: {competitor_avg:.0f}₽")
    lines.append(f"\n<b>Рекомендованная цена: {pricing['recommended_price']:.0f}₽</b>")
    if pricing["note"]:
        lines.append(f"⚠️ {pricing['note']}")
    if product.price:
        lines.append(f"\nТекущая цена в карточке: {float(product.price):.0f}₽")

    await message.answer("\n".join(lines))

    await message.answer("⏳ Анализирую тренды цен конкурентов и паттерны спроса...")

    revenue_by_date: dict[str, float] = {}
    if product.vendor_code:
        try:
            revenue_by_date = await get_wb_revenue_by_date(DEMAND_HISTORY_DAYS, sku=product.vendor_code)
        except MarketplaceAPIError as exc:
            logger.warning("Не удалось получить историю продаж WB для тайминга: %s", exc)

    timing = await build_timing_report(session, product, revenue_by_date=revenue_by_date or None)
    await message.answer(f"🧭 <b>Когда продвигаться и когда менять цену</b>\n\n{timing.recommendation}")
