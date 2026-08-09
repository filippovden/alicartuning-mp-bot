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
from app.bot.handlers.new_product import DIMENSIONS_RE, _photo_size_warning
from app.bot.keyboards import confirm_publish_kb, photos_done_kb
from app.bot.states import QuickCreateStates
from app.config import settings
from app.services.ai.client import AIContentGenerationError
from app.services.category_search import ozon_cache_is_empty, search_ozon_categories, search_wb_categories
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.storage import save_bytes

logger = logging.getLogger(__name__)
router = Router(name="quick_create")


@router.callback_query(F.data == "newmode:quick")
async def start_quick_mode(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    user = await product_service.get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    product = await product_service.create_draft(user.id)
    await product_service.update_fields(product.id, brand=settings.brand_name)

    await state.set_state(QuickCreateStates.photos)
    await state.update_data(product_id=product.id, photos=[])
    await callback.answer()
    await callback.message.answer(texts.QUICK_ASK_PHOTOS, reply_markup=photos_done_kb())


@router.message(QuickCreateStates.photos, F.photo)
async def quick_photo(message: Message, state: FSMContext, product_service, session) -> None:
    data = await state.get_data()
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    buffer = await message.bot.download_file(file.file_path)
    content = buffer.read()
    storage_file = await save_bytes(session, content, filename=file.file_path, content_type="image/jpeg")
    await product_service.add_image(data["product_id"], storage_file.id, image_type="main", position=len(data["photos"]))

    photos = data["photos"] + [storage_file.id]
    await state.update_data(photos=photos)

    size_warning = _photo_size_warning(content)
    if size_warning:
        await message.answer(size_warning)
    await message.answer(texts.PHOTO_RECEIVED.format(count=len(photos)), reply_markup=photos_done_kb())


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


@router.message(QuickCreateStates.description)
async def quick_description(message: Message, state: FSMContext, product_service, session) -> None:
    data = await state.get_data()
    product_id = data["product_id"]
    await message.answer(texts.QUICK_PARSING)

    try:
        parsed = await product_service.ai_service.parse_quick_description(message.text.strip())
    except AIContentGenerationError:
        logger.warning("Не удалось разобрать быстрое описание товара %s", product_id, exc_info=True)
        await message.answer(texts.QUICK_PARSE_FAILED)
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

    category_query = draft_title or fields.get("material") or ""
    if category_query:
        try:
            await _auto_pick_category(session, product_service, product_id, category_query)
        except MarketplaceAPIError as exc:
            logger.warning("Не удалось автоподобрать категорию для товара %s: %s", product_id, exc)

    await message.answer(texts.generating_preview())
    await product_service.generate_ai_content(product_id)

    await state.update_data(need_dimensions=not dims_present, need_weight=not bool(weight))
    await state.set_state(QuickCreateStates.vendor_code)
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
    product = await product_service.get_product(data["product_id"])
    await state.clear()

    missing = []
    if not product.vendor_code:
        missing.append("артикул")
    if not (product.length_mm and product.width_mm and product.height_mm):
        missing.append("размеры упаковки")
    if not product.weight_g:
        missing.append("вес упаковки")
    if not product.category_id:
        missing.append("категория (уточните через «✏️ Править»)")

    await message.answer(texts.quick_checklist(product, missing), reply_markup=confirm_publish_kb(product.id))
