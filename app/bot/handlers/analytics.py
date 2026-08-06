from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services.analytics_service import get_ozon_sales_summary, get_wb_sales_summary, recommend_price
from app.services.competitor_analysis import CompetitorAnalysisError, search_wb_competitors
from app.services.marketplaces.base import MarketplaceAPIError

logger = logging.getLogger(__name__)
router = Router(name="analytics")

PERIOD_DAYS = 30


@router.message(Command("analytics"))
async def cmd_analytics(message: Message, product_service) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip().isdigit():
        await _product_price_recommendation(message, product_service, int(args[1].strip()))
        return

    await message.answer(f"⏳ Собираю аналитику продаж за последние {PERIOD_DAYS} дней...")

    lines = [f"📊 <b>Сводка продаж за {PERIOD_DAYS} дней</b>"]

    try:
        wb_summary = await get_wb_sales_summary(PERIOD_DAYS)
        lines.append(f"\n<b>Wildberries:</b> {wb_summary.total_units} продаж на {wb_summary.total_revenue:.0f}₽")
        lines += _top_sku_lines(wb_summary.by_sku)
    except MarketplaceAPIError as exc:
        lines.append(f"\n<b>Wildberries:</b> ⚠️ не удалось получить данные ({exc.message})")

    try:
        ozon_summary = await get_ozon_sales_summary(PERIOD_DAYS)
        lines.append(f"\n<b>Ozon:</b> {ozon_summary.total_units} продаж на {ozon_summary.total_revenue:.0f}₽")
        lines += _top_sku_lines(ozon_summary.by_sku)
    except MarketplaceAPIError as exc:
        lines.append(f"\n<b>Ozon:</b> ⚠️ не удалось получить данные ({exc.message})")

    lines.append("\nПодсказка: /analytics <ID товара> — рекомендация цены по конкретному товару.")
    await message.answer("\n".join(lines))


def _top_sku_lines(by_sku: dict[str, dict], limit: int = 5) -> list[str]:
    if not by_sku:
        return ["  нет данных за период"]
    top = sorted(by_sku.items(), key=lambda kv: kv[1]["revenue"], reverse=True)[:limit]
    return [f"  • {sku}: {info['units']} шт., {info['revenue']:.0f}₽" for sku, info in top]


async def _product_price_recommendation(message: Message, product_service, product_id: int) -> None:
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
            report = await search_wb_competitors(query)
            competitor_avg = report.average_price
        except CompetitorAnalysisError as exc:
            logger.warning("Анализ конкурентов для рекомендации цены не удался: %s", exc)

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
