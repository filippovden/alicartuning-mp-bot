from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def confirm_publish_kb(product_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Опубликовать на WB и Ozon", callback_data=f"publish:{product_id}")
    builder.button(text="✏️ Редактировать", callback_data=f"edit:{product_id}")
    builder.button(text="🗑 Отмена", callback_data=f"cancel:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def yes_no_kb(prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да", callback_data=f"{prefix}:yes")
    builder.button(text="Нет", callback_data=f"{prefix}:no")
    builder.adjust(2)
    return builder.as_markup()


def photos_done_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Фото загружены, дальше", callback_data="photos_done")
    builder.adjust(1)
    return builder.as_markup()


def products_list_kb(products: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id, label in products:
        builder.button(text=label, callback_data=f"show:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def skip_kb(field: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data=f"skip:{field}")
    builder.adjust(1)
    return builder.as_markup()
