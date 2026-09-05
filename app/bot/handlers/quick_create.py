"""Быстрое создание товара (раздел B ТЗ) — фото + одно текстовое сообщение вместо
анкеты из 15 вопросов. LLM разбирает описание (тип детали, модель Lada, материал,
цвет, цена), бот сам подбирает категорию WB/Ozon по типу детали и генерирует
title/description, а спрашивает только то, что не удалось угадать: артикул
(всегда, он обязателен и уникален) и размеры/вес упаковки, если их не было в
тексте. Бренд всегда ALICARTUNING — не спрашивается.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers.new_product import (
    DIMENSIONS_RE,
    handle_incoming_photo,
    render_preview,
    resume_state_for_product,
    try_generate_ai_content,
)
from app.bot.keyboards import quick_parse_failed_kb
from app.bot.progress import fail_progress, set_progress, start_progress
from app.bot.states import QuickCreateStates
from app.config import settings
from app.services.ai.client import AIContentGenerationError
from app.services.category_search import ozon_cache_is_empty, search_ozon_categories, search_wb_categories
from app.services.marketplaces.base import MarketplaceAPIError

logger = logging.getLogger(__name__)
router = Router(name="quick_create")


async def start_quick_mode_flow(
    state: FSMContext, product_service, answer, *, telegram_id: int, username: str | None, full_name: str | None
) -> None:
    """Тело быстрого режима, вынесенное отдельно от CallbackQuery — раздел 1 ТЗ
    v7: кнопка «Новый товар» в нижнем меню входит сюда напрямую, без развилки
    Быстро/Пошагово (см. common.menu_new_product)."""
    user = await product_service.get_or_create_user(telegram_id=telegram_id, username=username, full_name=full_name)
    product = await product_service.create_draft(user.id)
    await product_service.update_fields(product.id, brand=settings.brand_name)

    await state.set_state(QuickCreateStates.photos)
    await state.update_data(product_id=product.id, photos=[])
    # Раздел B.2 ТЗ: кнопка «Готово» появляется только после первого фото —
    # нажимать её при пустом черновике бессмысленно.
    await answer(texts.QUICK_ASK_PHOTOS)


@router.callback_query(F.data == "newmode:quick")
async def start_quick_mode(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    await callback.answer()
    await start_quick_mode_flow(
        state,
        product_service,
        callback.message.answer,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )


@router.message(QuickCreateStates.photos, F.photo)
async def quick_photo(message: Message, state: FSMContext, product_service, session) -> None:
    await handle_incoming_photo(message, state, product_service, session)


@router.message(QuickCreateStates.photos)
async def quick_photos_wrong_input(message: Message) -> None:
    # Раздел B.2 ТЗ: пока фото нет ни одного, любое другое сообщение — не
    # молчаливо игнорировать, а подсказать, что делать дальше.
    await message.answer(texts.QUICK_SEND_PHOTOS_FIRST)


@router.callback_query(F.data == "photos_done", QuickCreateStates.photos)
async def quick_photos_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    photos_count = len(data["photos"])
    if photos_count < texts.MIN_PRODUCT_PHOTOS:
        await callback.answer(texts.need_more_photos(photos_count), show_alert=True)
        await callback.message.answer(texts.need_more_photos(photos_count))
        return

    await state.set_state(QuickCreateStates.description)
    await callback.answer()
    await callback.message.answer(texts.QUICK_ASK_DESCRIPTION)


@router.callback_query(F.data == "quickretry", QuickCreateStates.description)
async def quick_retry_description(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(texts.QUICK_ASK_DESCRIPTION)


@router.callback_query(F.data == "quickfallbackstep", QuickCreateStates.description)
async def quick_fallback_to_step(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    """Провал парсинга в быстром режиме → пошаговый режим, но не с нуля: фото
    и бренд уже в черновике, продолжаем с первого незаполненного поля (раздел
    B.2 ТЗ — «пошаговый режим должен подхватить уже загруженные фото»)."""
    data = await state.get_data()
    product_id = data["product_id"]
    photos = data.get("photos", [])
    await callback.answer()

    product = await product_service.get_product(product_id)
    next_state, question = await resume_state_for_product(product)
    await state.update_data(photos=photos, pending_attrs=[])

    if next_state is None:
        await state.clear()
        await callback.message.answer(texts.generating_preview())
        if not await try_generate_ai_content(callback.message.answer, product_service, product_id):
            return
        preview_text, keyboard = await render_preview(product_service, product_id)
        await callback.message.answer(preview_text, reply_markup=keyboard)
        return

    await state.set_state(next_state)
    await callback.message.answer(question)


@router.message(QuickCreateStates.description)
async def quick_description(message: Message, state: FSMContext, product_service, session) -> None:
    """Раздел 2.A ТЗ v8: одна полоска-сообщение вместо пачки отдельных
    «Разбираю...»/«Генерирую...» — редактируется по мере реальных шагов
    (разбор → категория → текст), а не поддельной анимацией."""
    data = await state.get_data()
    product_id = data["product_id"]
    handle = await start_progress(message.answer, step="Разбираю описание")

    try:
        parsed = await product_service.ai_service.parse_quick_description(message.text.strip())
    except AIContentGenerationError:
        logger.warning("Не удалось разобрать быстрое описание товара %s", product_id, exc_info=True)
        await fail_progress(handle, "Не получилось разобрать описание.", reply_markup=quick_parse_failed_kb())
        return

    fields = {}
    for field in ("car_model", "material", "color", "package_contents"):
        value = parsed.get(field)
        if value:
            fields[field] = str(value).strip()

    price = _as_number(parsed.get("price"))
    if price:
        fields["price"] = price

    draft_title = str(parsed.get("draft_title") or "").strip()
    if draft_title:
        fields["title"] = draft_title

    dims_present = all(_as_number(parsed.get(k)) for k in ("length_mm", "width_mm", "height_mm"))
    if dims_present:
        fields["length_mm"] = int(_as_number(parsed["length_mm"]))
        fields["width_mm"] = int(_as_number(parsed["width_mm"]))
        fields["height_mm"] = int(_as_number(parsed["height_mm"]))

    weight = _as_number(parsed.get("weight_g"))
    if weight:
        fields["weight_g"] = int(weight)

    if fields:
        await product_service.update_fields(product_id, **fields)
    await set_progress(handle, 10, "Сохранил данные")

    category_query = draft_title or fields.get("material") or ""
    if category_query:
        try:
            await _auto_pick_category(session, product_service, product_id, category_query)
        except MarketplaceAPIError as exc:
            logger.warning("Не удалось автоподобрать категорию для товара %s: %s", product_id, exc)
    await set_progress(handle, 35, "Категория")

    await set_progress(handle, 55, "Пишу название и текст")
    try:
        await product_service.generate_ai_content(product_id)
    except Exception:
        # Раздел H.6 ТЗ: держим состояние в description, а не переключаем на
        # общий «Повторить» — так те же кнопки («Написать заново»/«Пошагово»)
        # остаются рабочими и не оставляют диалог в непонятном промежуточном шаге.
        # Раздел 2.A ТЗ v8: правим полоску на текст ошибки, не оставляем на 55%.
        logger.warning("Не удалось сгенерировать текст для товара %s", product_id, exc_info=True)
        await fail_progress(handle, "Не получилось собрать текст.", reply_markup=quick_parse_failed_kb())
        return
    await set_progress(handle, 80, "Текст готов")

    await state.update_data(need_dimensions=not dims_present, need_weight=not bool(weight))
    await state.set_state(QuickCreateStates.vendor_code)
    await set_progress(handle, 100, "Карточка готова")
    product = await product_service.get_product(product_id)
    await message.answer(texts.quick_ask_vendor_code(product.car_model or "—"))


def _as_number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _auto_pick_category(session, product_service, product_id: int, query: str) -> None:
    """Подбирает категорию WB/Ozon автоматически по типу детали, без интерактивного
    выбора — раздел B.4 ТЗ: быстрый режим не должен превращаться в ту же анкету."""
    wb_matches = await search_wb_categories(query, limit=1)
    if not wb_matches:
        return

    ozon_category_id = ozon_type_id = None
    if not await ozon_cache_is_empty(session):
        ozon_matches = await search_ozon_categories(session, query, limit=1)
        if ozon_matches:
            ozon_category_id, ozon_type_id = ozon_matches[0].category_id, ozon_matches[0].type_id

    category = await product_service.get_or_fetch_category(
        name=wb_matches[0].name,
        wb_subject_id=wb_matches[0].subject_id,
        ozon_category_id=ozon_category_id,
        ozon_type_id=ozon_type_id,
    )
    await product_service.update_fields(product_id, category_id=category.id)


@router.message(QuickCreateStates.vendor_code)
async def quick_vendor_code(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], vendor_code=message.text.strip())
    await _advance_after_vendor_code(message, state, product_service)


async def _advance_after_vendor_code(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    if data.get("need_dimensions"):
        await state.set_state(QuickCreateStates.dimensions)
        await message.answer(texts.ASK_QUICK_DIMENSIONS)
        return
    if data.get("need_weight"):
        await state.set_state(QuickCreateStates.weight)
        await message.answer(texts.ASK_QUICK_WEIGHT)
        return
    await _finish_quick_flow(message, state, product_service)


@router.message(QuickCreateStates.dimensions)
async def quick_dimensions(message: Message, state: FSMContext, product_service) -> None:
    match = DIMENSIONS_RE.match(message.text)
    if not match:
        await message.answer(texts.INVALID_DIMENSIONS)
        return
    length, width, height = (int(v) for v in match.groups())
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], length_mm=length, width_mm=width, height_mm=height)
    await state.update_data(need_dimensions=False)

    if data.get("need_weight"):
        await state.set_state(QuickCreateStates.weight)
        await message.answer(texts.ASK_QUICK_WEIGHT)
        return
    await _finish_quick_flow(message, state, product_service)


@router.message(QuickCreateStates.weight)
async def quick_weight(message: Message, state: FSMContext, product_service) -> None:
    try:
        weight = int(message.text.strip())
    except ValueError:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], weight_g=weight)
    await _finish_quick_flow(message, state, product_service)


async def _finish_quick_flow(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    product_id = data["product_id"]
    await state.clear()
    preview_text, keyboard = await render_preview(product_service, product_id)
    await message.answer(preview_text, reply_markup=keyboard)
