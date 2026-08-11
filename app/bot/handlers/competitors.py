from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import price_suggestion_kb
from app.config import settings
from app.services.competitor_analysis import (
    CompetitorAnalysisError,
    CompetitorReport,
    fetch_wb_shop,
    parse_wb_seller_id,
    search_wb_competitors,
    suggest_pricing,
)
from app.services.pricing_intelligence import PriceTrend, get_shop_price_trend, save_shop_snapshot

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


def _format_shop_report(seller_id: str, report: CompetitorReport, trend: PriceTrend) -> str:
    lines = [f"🏪 <b>Магазин WB</b> (ID {html.escape(seller_id)})"]
    lines.append(f"Товаров в ассортименте: {len(report.items)}")
    if report.average_price:
        lines.append(
            f"Цена: от {report.min_price:.0f}₽ до {report.max_price:.0f}₽, "
            f"в среднем {report.average_price:.0f}₽"
        )
    if report.average_rating:
        rating_line = f"Рейтинг: {report.average_rating}"
        if report.total_feedbacks:
            rating_line += f" (всего отзывов: {report.total_feedbacks})"
        lines.append(rating_line)

    lines.append("")
    if trend.direction == "unknown":
        lines.append(
            "📈 Это первый снимок по этому магазину — тренд цены появится при "
            "следующих проверках (бот сам обновляет данные раз в сутки)."
        )
    elif trend.direction == "flat" and trend.change_pct is not None:
        lines.append(f"📈 Цена стабильна с начала отслеживания ({trend.change_pct:+.1f}%).")
    elif trend.direction == "up" and trend.change_pct is not None:
        lines.append(f"📈 Цена выросла на {trend.change_pct:.1f}% с начала отслеживания.")
    elif trend.direction == "down" and trend.change_pct is not None:
        lines.append(f"📉 Цена снизилась на {abs(trend.change_pct):.1f}% с начала отслеживания.")

    lines.append("\nТоп-5 товаров:")
    for item in report.items[:5]:
        price_part = f"{item.price:.0f}₽" if item.price else "—"
        lines.append(f"• {html.escape(item.name[:80])} — {price_part}")

    return "\n".join(lines)


@router.message(Command("shop"))
async def cmd_shop(message: Message, session) -> None:
    """Разбор магазина-конкурента по ссылке (только Wildberries — у Ozon нет
    публичного доступа к чужому каталогу, см. app/services/competitor_analysis.py).
    История цены копится с первого запроса — ретроактивных данных не бывает."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: /shop [ссылка на магазин WB]\n"
            "Например: /shop https://www.wildberries.ru/seller/12345\n\n"
            "Пока поддерживается только Wildberries — у Ozon нет публичного доступа "
            "к чужому каталогу."
        )
        return

    seller_id = parse_wb_seller_id(args[1])
    if seller_id is None:
        await message.answer(
            "Не нашёл ID продавца в ссылке. Пришлите ссылку вида "
            "https://www.wildberries.ru/seller/12345 или сам числовой ID."
        )
        return

    await message.answer(f"⏳ Собираю данные по магазину (ID {seller_id})...")
    try:
        report = await fetch_wb_shop(seller_id)
    except CompetitorAnalysisError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await save_shop_snapshot(session, seller_id, report)
    trend = await get_shop_price_trend(session, seller_id)

    await message.answer(_format_shop_report(seller_id, report, trend))


@router.message(Command("market"))
async def cmd_competitors(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /market [ключевое слово]\nНапример: /market накладки на зеркала Granta")
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


@router.callback_query(F.data.startswith("pricecheck:"))
async def price_check(callback: CallbackQuery, product_service) -> None:
    """«Цена по рынку» в превью (раздел F ТЗ) — быстрый взгляд на мин/среднюю/макс
    цену конкурентов с готовым предложением поставить рекомендованную цену в один клик."""
    product_id = int(callback.data.split(":")[1])
    await callback.answer("Проверяю цены конкурентов...")

    product = await product_service.get_product(product_id)
    if product is None:
        await callback.message.answer(texts.NOT_FOUND)
        return

    query = product.car_model or product.title or (product.category.name if product.category else "")
    if not query:
        await callback.message.answer("Недостаточно данных для поиска (нет ни названия, ни модели авто).")
        return

    try:
        report = await search_wb_competitors(query, exclude_brand=product.brand or settings.brand_name)
    except CompetitorAnalysisError as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return

    cost_price = float(product.cost_price) if product.cost_price else None
    pricing = suggest_pricing(report, cost_price) if report.items else None
    await callback.message.answer(texts.price_check_report(report, pricing))

    if pricing and pricing.get("recommended_price"):
        await callback.message.answer(
            "Обновить цену товара?", reply_markup=price_suggestion_kb(product_id, pricing["recommended_price"])
        )


@router.callback_query(F.data.startswith("setprice:"))
async def set_price(callback: CallbackQuery, product_service) -> None:
    _, product_id_raw, value = callback.data.split(":")
    product_id = int(product_id_raw)
    await callback.answer()

    if value == "keep":
        await callback.message.answer("Цена не изменена.")
        return

    price = float(value)
    await product_service.update_fields(product_id, price=price)
    await callback.message.answer(texts.price_set(product_id, price))
