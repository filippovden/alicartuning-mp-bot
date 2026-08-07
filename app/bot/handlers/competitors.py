from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.services.competitor_analysis import CompetitorAnalysisError, search_wb_competitors, suggest_pricing

logger = logging.getLogger(__name__)
router = Router(name="competitors")


def _format_report(report, cost_price: float | None = None) -> str:
    if not report.items:
        return f"По запросу «{report.query}» конкурентов на Wildberries не нашлось."

    lines = [f"🔍 <b>Анализ конкурентов на Wildberries</b> по запросу «{report.query}»"]
    lines.append(f"Найдено товаров: {len(report.items)}")
    if report.average_price:
        lines.append(
            f"Цена: от {report.min_price:.0f}₽ до {report.max_price:.0f}₽, "
            f"в среднем {report.average_price:.0f}₽"
        )
    keywords = report.top_keywords(8)
    if keywords:
        lines.append(f"Популярные слова в названиях: {', '.join(keywords)}")

    if cost_price:
        pricing = suggest_pricing(report, cost_price)
        lines.append(f"\n💰 Рекомендованная цена (себестоимость {cost_price:.0f}₽ + маржа 35%): "
                      f"{pricing['recommended_price']:.0f}₽")
        if pricing.get("note"):
            lines.append(f"⚠️ {pricing['note']}")

    lines.append("\nТоп-5 карточек конкурентов:")
    for item in report.items[:5]:
        price_part = f"{item.price:.0f}₽" if item.price else "—"
        lines.append(f"• {item.name[:80]} — {price_part}")

    return "\n".join(lines)


@router.message(Command("competitors"))
async def cmd_competitors(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /competitors <ключевое слово>\nНапример: /competitors накладки на зеркала Granta")
        return

    query = args[1].strip()
    await message.answer(f"⏳ Ищу конкурентов на Wildberries по запросу «{query}»...")
    try:
        report = await search_wb_competitors(query, exclude_brand=settings.brand_name)
    except CompetitorAnalysisError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await message.answer(_format_report(report))


@router.callback_query(F.data.startswith("competitors:"))
async def callback_competitors(callback: CallbackQuery, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer("Ищу конкурентов...")

    product = await product_service.get_product(product_id)
    if product is None:
        await callback.message.answer("Товар не найден.")
        return

    query = product.car_model or product.title or (product.category.name if product.category else "")
    if not query:
        await callback.message.answer("Недостаточно данных для поиска (нет ни названия, ни модели авто).")
        return

    await callback.message.answer(f"⏳ Ищу конкурентов на Wildberries по запросу «{query}»...")
    try:
        report = await search_wb_competitors(query, exclude_brand=product.brand or settings.brand_name)
    except CompetitorAnalysisError as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return

    cost_price = float(product.cost_price) if product.cost_price else None
    await callback.message.answer(_format_report(report, cost_price=cost_price))
