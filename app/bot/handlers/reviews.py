from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import reviews_reply_kb
from app.bot.states import ReviewReplyStates
from app.db.models import Review
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.review_service import answer_review, generate_reply, list_unanswered_reviews, sync_ozon_reviews, sync_wb_reviews

logger = logging.getLogger(__name__)
router = Router(name="reviews")

RATING_STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}


def _format_review(review: Review) -> str:
    # review.text — публичный отзыв покупателя с WB/Ozon (см. review_service.py),
    # то есть недоверенный ввод. Бот шлёт сообщения с parse_mode=HTML по
    # умолчанию (app/bot/main.py) — без экранирования отзыв с разметкой вроде
    # <a href="..."> отрендерился бы кликабельной ссылкой в чате админа.
    stars = RATING_STARS.get(review.rating or 0, "—")
    lines = [
        f"💬 <b>Новый отзыв</b> ({review.marketplace.value}, {stars})",
        f"SKU: {review.sku or '—'}",
        f"«{html.escape(review.text) if review.text else '(без текста)'}»",
    ]
    return "\n".join(lines)


@router.message(Command("reviews"))
async def cmd_reviews(message: Message, session) -> None:
    await message.answer("⏳ Проверяю новые отзывы на Wildberries и Ozon...")

    synced = 0
    try:
        synced += len(await sync_wb_reviews(session))
    except MarketplaceAPIError as exc:
        logger.warning("Не удалось получить отзывы WB: %s", exc)
    except KeyError:
        logger.warning("Неожиданный формат ответа WB Feedbacks API")

    try:
        synced += len(await sync_ozon_reviews(session))
    except MarketplaceAPIError as exc:
        logger.warning("Не удалось получить отзывы Ozon: %s", exc)
    except KeyError:
        logger.warning("Неожиданный формат ответа Ozon Reviews API")

    reviews = await list_unanswered_reviews(session)
    if not reviews:
        await message.answer("Неотвеченных отзывов нет 🎉")
        return

    await message.answer(f"Неотвеченных отзывов: {len(reviews)}")
    for review in reviews[:10]:
        await message.answer(_format_review(review), reply_markup=reviews_reply_kb(review.id))


@router.callback_query(F.data.startswith("review_auto:"))
async def review_auto_reply(callback: CallbackQuery, session) -> None:
    review_id = int(callback.data.split(":")[1])
    review = await session.get(Review, review_id)
    if review is None:
        await callback.answer("Отзыв не найден", show_alert=True)
        return

    await callback.answer("Генерирую ответ...")
    reply_text = await generate_reply(review)

    try:
        await answer_review(session, review, reply_text)
    except MarketplaceAPIError as exc:
        await callback.message.answer(f"⚠️ Не удалось отправить ответ: {exc.message}")
        return

    # reply_text сгенерирован AI на основе review.text (недоверенный ввод) —
    # экранируем по той же причине, что и в _format_review.
    await callback.message.answer(f"✅ Ответ отправлен:\n«{html.escape(reply_text)}»")


@router.callback_query(F.data.startswith("review_manual:"))
async def review_manual_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    review_id = int(callback.data.split(":")[1])
    await state.set_state(ReviewReplyStates.entering_reply)
    await state.update_data(review_id=review_id)
    await callback.answer()
    await callback.message.answer("Введите текст ответа на отзыв:")


@router.message(ReviewReplyStates.entering_reply)
async def review_manual_reply_submit(message: Message, state: FSMContext, session) -> None:
    data = await state.get_data()
    review = await session.get(Review, data["review_id"])
    if review is None:
        await message.answer("Отзыв не найден.")
        await state.clear()
        return

    try:
        await answer_review(session, review, message.text.strip())
    except MarketplaceAPIError as exc:
        await message.answer(f"⚠️ Не удалось отправить ответ: {exc.message}")
        return

    await state.clear()
    await message.answer("✅ Ответ отправлен.")
