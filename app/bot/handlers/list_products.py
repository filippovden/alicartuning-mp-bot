from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import texts
from app.bot.states import EditProductStates

router = Router(name="list_products")

EDITABLE_FIELDS = {
    "1": ("title", "Название"),
    "2": ("description", "Описание"),
    "3": ("price", "Цена"),
    "4": ("cost_price", "Себестоимость"),
    "5": ("color", "Цвет"),
    "6": ("material", "Материал"),
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
        await message.answer("У вас пока нет товаров. Введите /new, чтобы создать первый.")
        return

    lines = [texts.product_list_item(p) for p in products]
    await message.answer("\n".join(lines))


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
