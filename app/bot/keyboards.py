from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

MENU_NEW_PRODUCT = "📦 Новый товар"
MENU_LIST = "📋 Мои товары"
MENU_CLONE = "🧬 Клонировать"
MENU_REVIEWS = "⭐ Отзывы"
MENU_ANALYTICS = "📊 Аналитика"
MENU_MORE = "⚙️ Ещё"


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


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
    """Превью карточки — раздел C2 ТЗ: одно главное действие сверху, дальше
    самое нужное в паре рядов, «Обработать фото»/«Анализ конкурентов» убраны
    отсюда (перегружали главный экран) и живут в карточке товара (см.
    product_detail_kb) — там, где до них реально доходит очередь."""
    builder = InlineKeyboardBuilder()
    builder.row(_btn("✅ Опубликовать на WB и Ozon", f"publish:{product_id}"))
    builder.row(
        _btn("🎨 Инфографика", f"gengraphic:{product_id}"),
        _btn("💰 Цена", f"pricecheck:{product_id}"),
    )
    builder.row(
        _btn("🧬 Другие модели", f"clone:{product_id}"),
        _btn("✏️ Править", f"edit:{product_id}"),
    )
    builder.row(_btn("❌ Отмена", f"cancel:{product_id}"))
    return builder.as_markup()


def open_product_kb(product_ids: list[int]) -> InlineKeyboardMarkup:
    """/list — раздел D1 ТЗ: одна кнопка «Открыть» на товар вместо сетки из
    трёх технически звучащих кнопок (Клон/Пакет/Публикация) на каждую строку."""
    builder = InlineKeyboardBuilder()
    for product_id in product_ids:
        builder.button(text=f"Открыть #{product_id}", callback_data=f"open:{product_id}")
    builder.adjust(2)
    return builder.as_markup()


def product_detail_kb(product_id: int) -> InlineKeyboardMarkup:
    """Карточка товара после «Открыть» — раздел D2 ТЗ: короткое превью + весь
    набор действий по конкретному товару в одном месте."""
    builder = InlineKeyboardBuilder()
    builder.row(_btn("✅ Опубликовать", f"publish:{product_id}"))
    builder.row(_btn("🧬 Клон", f"clone:{product_id}"), _btn("📦 Пакет на модели", f"clonebatch:{product_id}"))
    builder.row(_btn("✏️ Править", f"edit:{product_id}"), _btn("🎨 Инфографика", f"gengraphic:{product_id}"))
    builder.row(_btn("🖼 Фото", f"processimg:{product_id}"), _btn("🔍 Конкуренты", f"competitors:{product_id}"))
    return builder.as_markup()


def clone_pick_kb(products: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """«Что клонируем?» — раздел A5 ТЗ: отдельный, понятный список для
    клонирования вместо переиспользования общего /list без пояснения."""
    builder = InlineKeyboardBuilder()
    for product_id, label in products:
        text = label if len(label) <= 60 else label[:57] + "…"
        builder.button(text=f"🧬 {text}", callback_data=f"clone:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def retry_ai_kb(product_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Повторить", callback_data=f"regenai:{product_id}")
    builder.adjust(1)
    return builder.as_markup()


def quick_parse_failed_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Написать заново", callback_data="quickretry")
    builder.button(text="📝 Заполнить пошагово", callback_data="quickfallbackstep")
    builder.adjust(1)
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
