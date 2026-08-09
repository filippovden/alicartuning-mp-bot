from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def confirm_publish_kb(product_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Опубликовать на WB и Ozon", callback_data=f"publish:{product_id}")
    builder.button(text="🖼 Обработать фото (убрать фон)", callback_data=f"processimg:{product_id}")
    builder.button(text="🎨 Сгенерировать инфографику", callback_data=f"gengraphic:{product_id}")
    builder.button(text="🔍 Анализ конкурентов", callback_data=f"competitors:{product_id}")
    builder.button(text="🧬 Создать похожую (другая модель)", callback_data=f"clone:{product_id}")
    builder.button(text="📦 Сделать для нескольких моделей", callback_data=f"clonebatch:{product_id}")
    builder.button(text="✏️ Редактировать", callback_data=f"edit:{product_id}")
    builder.button(text="🗑 Отмена", callback_data=f"cancel:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def clone_from_list_kb(product_ids: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id in product_ids:
        builder.button(text=f"🧬 Похожая #{product_id}", callback_data=f"clone:{product_id}")
    builder.adjust(2)
    return builder.as_markup()


def publish_links_kb(product_ids: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id in product_ids:
        builder.button(text=f"🚀 Опубликовать #{product_id}", callback_data=f"publish:{product_id}")
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


def category_match_kb(prefix: str, labels: list[str]) -> InlineKeyboardMarkup:
    """Кнопки выбора найденной категории по индексу + запасной вариант «не найдено»."""
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(labels):
        text = label if len(label) <= 60 else label[:57] + "…"
        builder.button(text=text, callback_data=f"{prefix}:{idx}")
    builder.button(text="🚫 Ничего не подходит / пропустить", callback_data=f"{prefix}:manual")
    builder.adjust(1)
    return builder.as_markup()


def reviews_reply_kb(review_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🤖 Ответить автоматически", callback_data=f"review_auto:{review_id}")
    builder.button(text="✍️ Ответить вручную", callback_data=f"review_manual:{review_id}")
    builder.adjust(1)
    return builder.as_markup()
