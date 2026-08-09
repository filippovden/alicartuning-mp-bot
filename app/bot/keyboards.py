from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

MENU_NEW_PRODUCT = "📦 Новый товар"
MENU_LIST = "📋 Мои товары"
MENU_CLONE = "🧬 Клонировать"
MENU_REVIEWS = "⭐ Отзывы"
MENU_ANALYTICS = "📊 Аналитика"
MENU_MORE = "⚙️ Ещё"


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянное меню бота — основной путь для обычного пользователя, слэш-команды
    остаются как power-user способ (см. раздел A ТЗ про удобство ежедневной выкладки)."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=MENU_NEW_PRODUCT), KeyboardButton(text=MENU_LIST))
    builder.row(KeyboardButton(text=MENU_CLONE), KeyboardButton(text=MENU_REVIEWS))
    builder.row(KeyboardButton(text=MENU_ANALYTICS), KeyboardButton(text=MENU_MORE))
    return builder.as_markup(resize_keyboard=True)


def new_product_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ Быстро (рекомендуется)", callback_data="newmode:quick")
    builder.button(text="📝 Пошагово", callback_data="newmode:step")
    builder.adjust(1)
    return builder.as_markup()


def confirm_publish_kb(product_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать на WB и Ozon", callback_data=f"publish:{product_id}")
    builder.button(text="🎨 Инфографика", callback_data=f"gengraphic:{product_id}")
    builder.button(text="🖼 Обработать фото (убрать фон)", callback_data=f"processimg:{product_id}")
    builder.button(text="💰 Цена по рынку", callback_data=f"pricecheck:{product_id}")
    builder.button(text="🔍 Анализ конкурентов", callback_data=f"competitors:{product_id}")
    builder.button(text="🧬 Другие модели Lada", callback_data=f"clone:{product_id}")
    builder.button(text="✏️ Править", callback_data=f"edit:{product_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancel:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def product_actions_kb(product_ids: list[int]) -> InlineKeyboardMarkup:
    """Три кнопки на каждый товар в /list: клон, пакет на несколько моделей,
    публикация — без этого приходилось помнить и вводить /clone <ID> вручную."""
    builder = InlineKeyboardBuilder()
    for product_id in product_ids:
        builder.button(text=f"🧬 Клон #{product_id}", callback_data=f"clone:{product_id}")
        builder.button(text=f"📦 Пакет #{product_id}", callback_data=f"clonebatch:{product_id}")
        builder.button(text=f"🚀 Публикация #{product_id}", callback_data=f"publish:{product_id}")
    builder.adjust(3)
    return builder.as_markup()


def publish_links_kb(product_ids: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id in product_ids:
        builder.button(text=f"🚀 Опубликовать #{product_id}", callback_data=f"publish:{product_id}")
    if len(product_ids) > 1:
        ids_csv = ",".join(str(pid) for pid in product_ids)
        builder.button(text="✅ Опубликовать все", callback_data=f"publishall:{ids_csv}")
    builder.adjust(1)
    return builder.as_markup()


def drafts_kb(product_ids: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id in product_ids:
        builder.button(text=f"▶️ Продолжить #{product_id}", callback_data=f"continuedraft:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def price_suggestion_kb(product_id: int, suggested_price: float) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Поставить {suggested_price:.0f}₽", callback_data=f"setprice:{product_id}:{suggested_price:.2f}")
    builder.button(text="Оставить как есть", callback_data=f"setprice:{product_id}:keep")
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
