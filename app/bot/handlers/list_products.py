from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers.new_product import resume_state_for_product
from app.bot.keyboards import confirm_publish_kb, drafts_kb, product_actions_kb
from app.bot.states import EditProductStates
from app.db.models import ProductStatus

logger = logging.getLogger(__name__)
router = Router(name="list_products")

EDITABLE_FIELDS = {
    "1": ("title", "Название"),
    "2": ("description", "Описание"),
    "3": ("price", "Цена"),
    "4": ("cost_price", "Себестоимость"),
    "5": ("color", "Цвет"),
    "6": ("material", "Материал"),
    "7": ("vendor_code", "Артикул (SKU)"),
    "8": ("barcode", "Штрихкод"),
    "9": ("brand", "Бренд"),
}


@router.message(Command("list"))
async def cmd_list(message: Message, product_service) -> None:
    user = await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    products = await product_service.list_products(user.id)
    if not products:
        await message.answer("У вас пока нет товаров. Нажмите «📦 Новый товар», чтобы создать первый.")
        return

    lines = [texts.product_list_item(p) for p in products]
    await message.answer("\n".join(lines), reply_markup=product_actions_kb([p.id for p in products]))


@router.message(Command("drafts"))
async def cmd_drafts(message: Message, product_service) -> None:
    """Незаконченные черновики (status=draft) с кнопкой «Продолжить» — раздел C.5
    ТЗ: раньше недописанный товар было легко забросить без единого способа
    вернуться к нему без ручного /edit."""
    user = await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    drafts = await product_service.list_products(user.id, status=ProductStatus.DRAFT)
    if not drafts:
        await message.answer(texts.NO_DRAFTS)
        return

    lines = [texts.draft_list_item(p) for p in drafts]
    await message.answer("\n".join(lines), reply_markup=drafts_kb([p.id for p in drafts]))


@router.callback_query(F.data.startswith("continuedraft:"))
async def continue_draft(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    product = await product_service.get_product(product_id)
    await callback.answer()
    if product is None:
        await callback.message.answer(texts.NOT_FOUND)
        return

    next_state, question = await resume_state_for_product(product)
    await state.update_data(
        product_id=product.id,
        photos=[img.storage_file_id for img in product.images],
        pending_attrs=[],
    )

    if next_state is None:
        await state.clear()
        await callback.message.answer(texts.generating_preview())
        updated = await product_service.generate_ai_content(product.id)
        await callback.message.answer(
            texts.draft_preview(
                updated.title,
                updated.description,
                float(updated.price) if updated.price else None,
                float(updated.cost_price) if updated.cost_price else None,
            ),
            reply_markup=confirm_publish_kb(updated.id),
        )
        return

    await state.set_state(next_state)
    await callback.message.answer(question)


@router.message(Command("status"))
async def cmd_status(message: Message, product_service) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /status <ID товара>")
        return

    product = await product_service.get_product(int(args[1]))
    if product is None:
        await message.answer(texts.NOT_FOUND)
        return

    lines = [f"Товар #{product.id}: {product.title or '(без названия)'}", f"Статус: {product.status.value}"]
    for log in sorted(product.publish_logs, key=lambda x: x.created_at)[-6:]:
        lines.append(f"• {log.marketplace.value}: {log.status.value} — {log.message or ''}")
    await message.answer("\n".join(lines))


@router.message(Command("edit"))
async def cmd_edit(message: Message, state: FSMContext, product_service) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /edit <ID товара>")
        return

    product = await product_service.get_product(int(args[1]))
    if product is None:
        await message.answer(texts.NOT_FOUND)
        return

    field_list = "\n".join(f"{key}. {label}" for key, (_, label) in EDITABLE_FIELDS.items())
    await state.set_state(EditProductStates.choosing_field)
    await state.update_data(product_id=product.id)
    await message.answer(f"Что редактируем у товара #{product.id}?\n\n{field_list}\n\nВведите номер поля:")


@router.message(EditProductStates.choosing_field)
async def choose_field(message: Message, state: FSMContext) -> None:
    key = message.text.strip()
    if key not in EDITABLE_FIELDS:
        await message.answer("Не понял номер поля. Введите число из списка выше:")
        return
    field_name, label = EDITABLE_FIELDS[key]
    await state.update_data(field_name=field_name)
    await state.set_state(EditProductStates.entering_value)
    await message.answer(f"Введите новое значение для «{label}»:")


@router.message(EditProductStates.entering_value)
async def enter_value(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    field_name = data["field_name"]
    value: str | float = message.text.strip()
    if field_name in ("price", "cost_price"):
        try:
            value = float(value.replace(",", "."))
        except ValueError:
            await message.answer(texts.INVALID_NUMBER)
            return

    await product_service.update_fields(data["product_id"], **{field_name: value})
    await state.clear()
    await message.answer("✅ Обновлено.")
