from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import category_match_kb, confirm_publish_kb, photos_done_kb, skip_kb
from app.bot.states import NewProductStates
from app.services.category_search import (
    ozon_cache_is_empty,
    search_ozon_categories,
    search_wb_categories,
)
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.storage import save_bytes

logger = logging.getLogger(__name__)
router = Router(name="new_product")

DIMENSIONS_RE = re.compile(r"^\s*(\d+)\s*[xXхХ×]\s*(\d+)\s*[xXхХ×]\s*(\d+)\s*$")


def _parse_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return None


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext, product_service) -> None:
    user = await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    product = await product_service.create_draft(user.id)
    await state.set_state(NewProductStates.category)
    await state.update_data(product_id=product.id, photos=[], pending_attrs=[])
    await message.answer(texts.ASK_CATEGORY)


@router.message(NewProductStates.category)
async def step_category(message: Message, state: FSMContext, product_service) -> None:
    category_name = message.text.strip()
    await state.update_data(category_name=category_name)

    try:
        wb_matches = await search_wb_categories(category_name, limit=8)
    except MarketplaceAPIError as exc:
        logger.warning("Поиск категорий WB не удался: %s", exc)
        wb_matches = []

    await state.update_data(
        wb_matches=[{"subject_id": m.subject_id, "name": m.name} for m in wb_matches]
    )
    await state.set_state(NewProductStates.category_pick_wb)

    if wb_matches:
        labels = [m.name for m in wb_matches]
        await message.answer(
            "Нашёл подходящие категории на <b>Wildberries</b>, выберите нужную:",
            reply_markup=category_match_kb("wbcat", labels),
        )
    else:
        await message.answer(
            "Не нашёл точного совпадения на Wildberries по этому названию — можно "
            "пропустить и указать характеристики вручную позже.",
            reply_markup=category_match_kb("wbcat", []),
        )


@router.callback_query(F.data.startswith("wbcat:"), NewProductStates.category_pick_wb)
async def pick_wb_category(callback: CallbackQuery, state: FSMContext, product_service, session) -> None:
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if choice == "manual":
        await state.update_data(wb_subject_id=None, wb_category_name=None)
    else:
        match = data["wb_matches"][int(choice)]
        await state.update_data(wb_subject_id=match["subject_id"], wb_category_name=match["name"])

    await callback.answer()

    if await ozon_cache_is_empty(session):
        await state.update_data(ozon_matches=[])
        await callback.message.answer(
            "Справочник категорий Ozon ещё не синхронизирован администратором "
            "(команда /synccategories) — пропускаю подбор Ozon-категории, "
            "её можно будет указать позже через /edit.",
        )
        await _finish_category_pick(callback.message, state, product_service, None, None)
        return

    ozon_matches = await search_ozon_categories(session, data["category_name"], limit=8)
    await state.update_data(
        ozon_matches=[
            {"category_id": m.category_id, "type_id": m.type_id, "full_name": m.full_name} for m in ozon_matches
        ]
    )
    await state.set_state(NewProductStates.category_pick_ozon)

    if ozon_matches:
        labels = [m.full_name for m in ozon_matches]
        await callback.message.answer(
            "Теперь найдите категорию на <b>Ozon</b>:", reply_markup=category_match_kb("ozcat", labels)
        )
    else:
        await callback.message.answer(
            "На Ozon точных совпадений не нашлось — можно пропустить.",
            reply_markup=category_match_kb("ozcat", []),
        )


@router.callback_query(F.data.startswith("ozcat:"), NewProductStates.category_pick_ozon)
async def pick_ozon_category(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()

    ozon_category_id = ozon_type_id = None
    if choice != "manual":
        match = data["ozon_matches"][int(choice)]
        ozon_category_id, ozon_type_id = match["category_id"], match["type_id"]

    await callback.answer()
    await _finish_category_pick(callback.message, state, product_service, ozon_category_id, ozon_type_id)


async def _finish_category_pick(
    message: Message,
    state: FSMContext,
    product_service,
    ozon_category_id: int | None,
    ozon_type_id: int | None,
) -> None:
    data = await state.get_data()
    category = await product_service.get_or_fetch_category(
        name=data.get("wb_category_name") or data["category_name"],
        wb_subject_id=data.get("wb_subject_id"),
        ozon_category_id=ozon_category_id,
        ozon_type_id=ozon_type_id,
    )
    await product_service.update_fields(data["product_id"], category_id=category.id)

    await state.set_state(NewProductStates.title)
    await message.answer(texts.ASK_TITLE)


@router.message(NewProductStates.title)
async def step_title(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], title=message.text.strip())
    await state.set_state(NewProductStates.brand)
    await message.answer(texts.ASK_BRAND)


@router.message(NewProductStates.brand)
async def step_brand(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], brand=message.text.strip())
    await state.set_state(NewProductStates.vendor_code)
    await message.answer(texts.ASK_VENDOR_CODE)


@router.message(NewProductStates.vendor_code)
async def step_vendor_code(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], vendor_code=message.text.strip())
    await state.set_state(NewProductStates.cost_price)
    await message.answer(texts.ASK_COST_PRICE)


@router.message(NewProductStates.cost_price)
async def step_cost_price(message: Message, state: FSMContext, product_service) -> None:
    value = _parse_float(message.text)
    if value is None:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], cost_price=value)
    await state.set_state(NewProductStates.price)
    await message.answer(texts.ASK_PRICE)


@router.message(NewProductStates.price)
async def step_price(message: Message, state: FSMContext, product_service) -> None:
    value = _parse_float(message.text)
    if value is None:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], price=value)
    await state.set_state(NewProductStates.barcode)
    await message.answer(texts.ASK_BARCODE, reply_markup=skip_kb("barcode"))


@router.callback_query(F.data == "skip:barcode")
async def skip_barcode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewProductStates.package_contents)
    await callback.message.answer(texts.ASK_PACKAGE_CONTENTS)
    await callback.answer()


@router.message(NewProductStates.barcode)
async def step_barcode(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], barcode=message.text.strip())
    await state.set_state(NewProductStates.package_contents)
    await message.answer(texts.ASK_PACKAGE_CONTENTS)


@router.message(NewProductStates.package_contents)
async def step_package_contents(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], package_contents=message.text.strip())
    await state.set_state(NewProductStates.material)
    await message.answer(texts.ASK_MATERIAL)


@router.message(NewProductStates.material)
async def step_material(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], material=message.text.strip())
    await state.set_state(NewProductStates.color)
    await message.answer(texts.ASK_COLOR)


@router.message(NewProductStates.color)
async def step_color(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], color=message.text.strip())
    await state.set_state(NewProductStates.dimensions)
    await message.answer(texts.ASK_DIMENSIONS)


@router.message(NewProductStates.dimensions)
async def step_dimensions(message: Message, state: FSMContext, product_service) -> None:
    match = DIMENSIONS_RE.match(message.text)
    if not match:
        await message.answer(texts.INVALID_DIMENSIONS)
        return
    length, width, height = (int(v) for v in match.groups())
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], length_mm=length, width_mm=width, height_mm=height)
    await state.set_state(NewProductStates.weight)
    await message.answer(texts.ASK_WEIGHT)


@router.message(NewProductStates.weight)
async def step_weight(message: Message, state: FSMContext, product_service) -> None:
    try:
        weight = int(message.text.strip())
    except ValueError:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], weight_g=weight)
    await state.set_state(NewProductStates.photos)
    await message.answer(texts.ASK_PHOTOS, reply_markup=photos_done_kb())


@router.message(NewProductStates.photos, F.photo)
async def step_photo(message: Message, state: FSMContext, product_service, session) -> None:
    data = await state.get_data()
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    buffer = await message.bot.download_file(file.file_path)
    storage_file = await save_bytes(session, buffer.read(), filename=file.file_path, content_type="image/jpeg")
    await product_service.add_image(data["product_id"], storage_file.id, image_type="main", position=len(data["photos"]))

    photos = data["photos"] + [storage_file.id]
    await state.update_data(photos=photos)
    await message.answer(texts.PHOTO_RECEIVED.format(count=len(photos)), reply_markup=photos_done_kb())


@router.callback_query(F.data == "photos_done", NewProductStates.photos)
async def photos_done(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    pending_attrs = await _collect_pending_attributes(product_service, data["product_id"])
    await state.update_data(pending_attrs=pending_attrs)
    await callback.answer()
    await _ask_next_attribute_or_generate(callback.message, state, product_service)


async def _collect_pending_attributes(product_service, product_id: int) -> list[dict]:
    product = await product_service.get_product(product_id)
    if product.category is None:
        return []
    try:
        await product_service.sync_category_attributes(product.category)
    except MarketplaceAPIError as exc:
        logger.warning("Не удалось получить характеристики категории: %s", exc)

    # Перечитываем товар, чтобы подтянуть свежесинхронизированные category.attrs
    product = await product_service.get_product(product_id)
    filled_ids = {attr.category_attr_id for attr in product.attributes}
    pending = [
        {"id": attr.id, "name": attr.name, "marketplace": attr.marketplace.value}
        for attr in product.category.attrs
        if attr.required and attr.id not in filled_ids
    ]
    return pending


async def _ask_next_attribute_or_generate(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    pending = data.get("pending_attrs", [])
    if pending:
        next_attr = pending[0]
        await state.set_state(NewProductStates.dynamic_attribute)
        await message.answer(f"Укажите «{next_attr['name']}» ({next_attr['marketplace']}):")
        return

    await state.set_state(NewProductStates.confirm)
    await message.answer(texts.generating_preview())
    product = await product_service.generate_ai_content(data["product_id"])
    await message.answer(
        texts.draft_preview(product.title, product.description, float(product.price) if product.price else None, float(product.cost_price) if product.cost_price else None),
        reply_markup=confirm_publish_kb(product.id),
    )


@router.message(NewProductStates.dynamic_attribute)
async def step_dynamic_attribute(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    pending = data.get("pending_attrs", [])
    if not pending:
        await _ask_next_attribute_or_generate(message, state, product_service)
        return

    current = pending[0]
    await product_service.set_attribute_value(data["product_id"], current["id"], message.text.strip())
    remaining = pending[1:]
    await state.update_data(pending_attrs=remaining)
    await _ask_next_attribute_or_generate(message, state, product_service)


@router.callback_query(F.data.startswith("processimg:"))
async def process_images(callback: CallbackQuery, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer("Обрабатываю фото...")
    await callback.message.answer("⏳ Убираю фон и привожу фото к единому шаблону...")
    processed = await product_service.process_product_images(product_id)
    if processed:
        await callback.message.answer(f"✅ Обработано фото: {len(processed)}. Новые версии добавлены к товару.")
    else:
        await callback.message.answer("⚠️ Не нашёл исходных фото для обработки.")


@router.callback_query(F.data.startswith("gengraphic:"))
async def generate_graphic(callback: CallbackQuery, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer("Генерирую инфографику...")
    await callback.message.answer("🎨 Генерирую инфографику преимуществ...")
    images = await product_service.generate_infographic_images(product_id)
    await callback.message.answer(f"✅ Готово: {len(images)} изображение(й) добавлено к товару.")


@router.callback_query(F.data.startswith("publish:"))
async def confirm_publish(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer()
    await callback.message.answer(texts.PUBLISHING)

    validation = await product_service.validate(product_id)
    if not validation.is_valid:
        await callback.message.answer(texts.validation_errors(validation.as_text()))
        return

    try:
        summary = await product_service.publish(product_id)
    except ValueError as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return

    if summary.all_succeeded:
        await callback.message.answer(
            texts.publish_success(
                summary.wb.external_id if summary.wb else None,
                summary.ozon.external_id if summary.ozon else None,
            )
        )
    else:
        await callback.message.answer(
            texts.publish_partial(
                summary.wb.message if summary.wb else None,
                summary.ozon.message if summary.ozon else None,
            )
        )
    await state.clear()


@router.callback_query(F.data.startswith("edit:"))
async def edit_from_preview(callback: CallbackQuery) -> None:
    product_id = callback.data.split(":")[1]
    await callback.answer()
    await callback.message.answer(f"Чтобы отредактировать поля — используйте /edit {product_id}")


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_from_preview(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer(texts.CANCELLED)
