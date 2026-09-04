from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

MENU_NEW_PRODUCT = "📦 Новый товар"
MENU_LIST = "📋 Мои товары"
MENU_CLONE = "🧬 На другую модель"
MENU_CLONE_OLD = "🧬 Клонировать"
MENU_REVIEWS = "⭐ Отзывы"
MENU_SALES = "📊 Продажи"
MENU_SALES_OLD = "📊 Аналитика"
MENU_HELP = "❓ Помощь"
MENU_HELP_OLD = "⚙️ Ещё"


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянное меню бота — раздел 1 ТЗ v7: ровно 6 кнопок, без слэшей и без
    «Ещё» как единственного входа в остальные функции. Старые подписи
    («Клонировать»/«Аналитика»/«Ещё») остаются рабочими синонимами на один
    релиз (см. common.py) — старая клавиатура на телефоне заказчика, которая
    не обновилась мгновенно, не должна «молчать»."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=MENU_NEW_PRODUCT), KeyboardButton(text=MENU_LIST))
    builder.row(KeyboardButton(text=MENU_CLONE), KeyboardButton(text=MENU_REVIEWS))
    builder.row(KeyboardButton(text=MENU_SALES), KeyboardButton(text=MENU_HELP))
    return builder.as_markup(resize_keyboard=True)


def new_product_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ Быстро (рекомендуется)", callback_data="newmode:quick")
    builder.button(text="📝 Пошагово", callback_data="newmode:step")
    builder.adjust(1)
    return builder.as_markup()


def confirm_publish_kb(product_id: int) -> InlineKeyboardMarkup:
    """Превью карточки — раздел 3 ТЗ v7: ровно 4 кнопки, ничего, что заказчик
    не может гарантированно довести до конца с этого сервера (Выдача/Цена/
    Конкуренты убраны — они зовут мёртвую витрину search.wb.ru, см. раздел 0
    и 4 ТЗ v7). Инфографика уходит тихо при «Выложить» и отдельной кнопкой
    сюда не выносится — она есть в карточке товара (см. product_detail_kb)."""
    builder = InlineKeyboardBuilder()
    # Раздел 4.1 ТЗ v5: «Выложить» ведёт на экран выбора магазинов (если их
    # больше одного на платформу), а не сразу публикует — см. new_product.confirm_publish.
    builder.row(_btn("🚀 Выложить", f"publish:{product_id}"))
    builder.row(_btn("✏️ Исправить", f"quickedit:{product_id}"))
    builder.row(_btn("🧬 На другую модель", f"clone:{product_id}"))
    builder.row(_btn("❌ Не надо", f"cancel:{product_id}"))
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
    """Карточка товара после «Открыть» — раздел 3 ТЗ v7: ровно 5 кнопок, без
    Выдача/Конкуренты (мёртвая витрина search.wb.ru, см. раздел 0 и 4 ТЗ v7)
    и без «Пакет на модели» (код у clone_product.py остаётся рабочим, просто
    не рекламируется здесь)."""
    builder = InlineKeyboardBuilder()
    builder.row(_btn("🚀 Выложить", f"publish:{product_id}"))
    builder.row(_btn("✏️ Исправить", f"quickedit:{product_id}"))
    builder.row(_btn("🧬 На другую модель", f"clone:{product_id}"))
    builder.row(_btn("🎨 Инфографика", f"gengraphic:{product_id}"))
    builder.row(_btn("🖼 Фото", f"processimg:{product_id}"))
    return builder.as_markup()


def quickedit_kb(product_id: int) -> InlineKeyboardMarkup:
    """«✏️ Исправить» — раздел 3 ТЗ v7: короткое меню на 4 пункта вместо полного
    списка из 9 полей (см. list_products.EDITABLE_FIELDS/start_edit, который
    остаётся рабочим для тех, кто печатает /edit [ID] руками)."""
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Название", f"quickeditfield:{product_id}:title"))
    builder.row(_btn("Цена", f"quickeditfield:{product_id}:price"))
    builder.row(_btn("Фото", f"processimg:{product_id}"))
    builder.row(_btn("Назад к карточке", f"quickeditback:{product_id}"))
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


def shop_picker_kb(product_id: int, wb_shops, ozon_shops, selected_ids) -> InlineKeyboardMarkup:
    """Экран «Куда выложить?» — раздел 4.2 ТЗ v5. Переключатели (☑/☐) на
    каждый магазин, плюс «Все WB»/«Все Ozon» и «Дальше»."""
    builder = InlineKeyboardBuilder()
    for shop in wb_shops:
        mark = "☑" if shop.id in selected_ids else "☐"
        builder.row(_btn(f"{mark} {shop.name}", f"shoppick:{product_id}:{shop.id}"))
    for shop in ozon_shops:
        mark = "☑" if shop.id in selected_ids else "☐"
        builder.row(_btn(f"{mark} {shop.name}", f"shoppick:{product_id}:{shop.id}"))

    toggle_row = []
    if len(wb_shops) > 1:
        toggle_row.append(_btn("Все Wildberries", f"shoppickall:{product_id}:wb"))
    if len(ozon_shops) > 1:
        toggle_row.append(_btn("Все Ozon", f"shoppickall:{product_id}:ozon"))
    if toggle_row:
        builder.row(*toggle_row)

    builder.row(_btn("Дальше", f"shopgo:{product_id}"))
    return builder.as_markup()


def shop_confirm_kb(product_id: int) -> InlineKeyboardMarkup:
    """Подтверждение перед публикацией — раздел 4.3 ТЗ v5."""
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Выкладывать", f"shopconfirm:{product_id}"), _btn("Назад", f"shopback:{product_id}"))
    return builder.as_markup()


def seo_actions_kb(product_id: int, suggested_title: str | None, suggested_price: float | None) -> InlineKeyboardMarkup:
    """Клавиатура под экраном «📈 Выдача» — кнопки правки показываются, только
    если реально есть что предложить (раздел 3.2 ТЗ v4)."""
    builder = InlineKeyboardBuilder()
    if suggested_title:
        builder.button(text="✍️ Подставить название", callback_data=f"seotitle:{product_id}")
    if suggested_price is not None:
        builder.button(text=f"💰 Поставить {suggested_price:.0f}₽", callback_data=f"seoprice:{product_id}:{suggested_price:.2f}")
    builder.button(text="🎨 Инфографика", callback_data=f"gengraphic:{product_id}")
    builder.button(text="🔍 Топ выдачи", callback_data=f"competitors:{product_id}")
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


def help_kb() -> InlineKeyboardMarkup:
    """Экран «❓ Помощь» — раздел 2 ТЗ v7: заказчик не айтишник, поэтому вместо
    простыни слэш-команд — три кнопки на самое нужное. /market и /shop сюда
    сознательно не выносятся, пока витрина WB отдаёт 403/429 с сервера."""
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Начать заново", "helpcancel"))
    builder.row(_btn("Черновики", "helpdrafts"))
    builder.row(_btn("Категории Ozon", "helpsynccategories"))
    return builder.as_markup()


def reviews_reply_kb(review_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🤖 Ответить автоматически", callback_data=f"review_auto:{review_id}")
    builder.button(text="✍️ Ответить вручную", callback_data=f"review_manual:{review_id}")
    builder.adjust(1)
    return builder.as_markup()
