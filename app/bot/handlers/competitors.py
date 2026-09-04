from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import price_suggestion_kb, seo_actions_kb
from app.config import settings
from app.db.models import ProductStatus
from app.services.competitor_analysis import CompetitorAnalysisError, search_wb_competitors, suggest_pricing
from app.services.seo_coach import SEOReport, build_seo_report, suggested_title_for_product

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


@router.message(Command("shop"))
async def cmd_shop(message: Message, session) -> None:
    """Разбор магазина-конкурента по ссылке — раздел 0 и 4 ТЗ v7: публичная
    витрина WB с этого сервера отдаёт 403/429, гарантировать результат нельзя,
    поэтому ручной /shop (кнопки на него больше не ведут) отвечает коротко и
    честно, не пытаясь сходить в сеть и не показывая URL/traceback."""
    await message.answer(texts.SHOWCASE_UNAVAILABLE)


@router.message(Command("market"))
async def cmd_competitors(message: Message) -> None:
    """См. cmd_shop выше — тот же принцип для /market (раздел 0 и 4 ТЗ v7)."""
    await message.answer(texts.SHOWCASE_UNAVAILABLE)


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


def _format_seo_report(report: SEOReport) -> str:
    """Экран «📈 Выдача» (раздел 3.4 ТЗ v4) — вывод и до 5 конкретных действий,
    не простыня из 20 карточек конкурентов. query — текст, который ввёл
    пользователь в товар (title/car_model), поэтому экранируем от HTML."""
    query = html.escape(report.query) if report.query else "—"

    if report.items_count == 0:
        lines = [f"📈 <b>Выдача по запросу «{query}»</b>"]
        for action in report.actions:
            lines.append(f"⚠️ {html.escape(action.text)}")
        return "\n".join(lines)

    lines = [f"📈 <b>Выдача Wildberries</b> по запросу «{query}» (найдено {report.items_count})"]

    if report.price_rank is not None:
        vs_median = ""
        if report.price_vs_median_pct:
            direction = "дороже" if report.price_vs_median_pct > 0 else "дешевле"
            vs_median = f" ({direction} медианы на {abs(report.price_vs_median_pct):.0f}%)"
        lines.append(f"Твоя позиция по цене: {report.price_rank} из {report.items_count + 1}{vs_median}")

    if report.missing_in_our_title:
        words = ", ".join(html.escape(w) for w in report.missing_in_our_title)
        lines.append(f"В топе есть слова, которых нет в названии: {words}")

    lines.append(f"Фото в топе обычно 5+, у тебя {report.our_photo_count}")

    if report.typical_top_feedbacks is not None:
        lines.append(f"Отзывов у топа в среднем {report.typical_top_feedbacks:.0f}")

    if report.actions:
        lines.append("")
        lines.append("Что сделать сейчас:")
        for action in report.actions:
            lines.append(f"{action.priority}. {html.escape(action.text)}")

    return "\n".join(lines)


@router.callback_query(F.data.startswith("seo:"))
async def seo_report(callback: CallbackQuery, product_service) -> None:
    """«📈 Выдача» — раздел 3.2 ТЗ v4: выводы по живой позиции в поиске WB и
    конкретные правки, а не сырой топ-5 (это остаётся в «🔍 Конкуренты»/`/market`)."""
    product_id = int(callback.data.split(":")[1])
    await callback.answer("Смотрю выдачу WB...")

    product = await product_service.get_product(product_id)
    if product is None:
        await callback.message.answer(texts.NOT_FOUND)
        return

    report = await build_seo_report(product)
    await callback.message.answer(
        _format_seo_report(report), reply_markup=seo_actions_kb(product_id, report.suggested_title, report.suggested_price)
    )


@router.callback_query(F.data.startswith("seotitle:"))
async def seo_apply_title(callback: CallbackQuery, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer()

    product = await product_service.get_product(product_id)
    if product is None:
        await callback.message.answer(texts.NOT_FOUND)
        return

    suggested = suggested_title_for_product(product)
    if not suggested:
        await callback.message.answer("Название уже в нужном формате — предлагать нечего.")
        return

    await product_service.update_fields(product_id, title=suggested)

    note = "✅ Название в боте обновлено."
    if product.status in (ProductStatus.PUBLISHED, ProductStatus.PARTIALLY_PUBLISHED):
        # Раздел 3.3 ТЗ v4: не обещаем то, чего бот пока не умеет — синхрона
        # названия на уже опубликованную карточку WB/Ozon в этом срезе нет.
        note += " Чтобы сменить на WB/Ozon — опубликуй ещё раз или поправь в кабинете."
    await callback.message.answer(note)

    from app.bot.handlers.new_product import render_preview

    preview_text, keyboard = await render_preview(product_service, product_id)
    await callback.message.answer(preview_text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("seoprice:"))
async def seo_apply_price(callback: CallbackQuery, product_service) -> None:
    _, product_id_raw, value = callback.data.split(":")
    product_id = int(product_id_raw)
    await callback.answer()

    product = await product_service.get_product(product_id)
    if product is None:
        await callback.message.answer(texts.NOT_FOUND)
        return

    price = float(value)
    cost_price = float(product.cost_price) if product.cost_price else None
    if cost_price is not None and price < cost_price:
        # Повторная проверка на случай устаревшего callback_data — предложение
        # уже не должно было прийти ниже себестоимости (см. seo_coach._suggest_price).
        await callback.message.answer("Эта цена ниже себестоимости — не ставлю.")
        return

    await product_service.update_fields(product_id, price=price)
    await callback.message.answer(texts.price_set(product_id, price))
