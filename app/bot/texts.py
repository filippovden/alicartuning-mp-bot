"""Тексты сообщений бота (раздел 8 ТЗ, стиль ALICARTUNING)."""

WELCOME = (
    "👋 Привет! Я AI-менеджер маркетплейсов ALICARTUNING.\n\n"
    "Помогу быстро создать карточку товара и опубликовать её на Wildberries и Ozon.\n\n"
    "Команды:\n"
    "/new — создать новый товар\n"
    "/list — мои товары и черновики\n"
    "/status — статус публикации\n"
    "/edit <ID> — редактировать товар\n"
    "/competitors <запрос> — анализ конкурентов на Wildberries\n"
    "/analytics — сводка продаж и рекомендации по цене\n"
    "/reviews — новые отзывы и автоответы\n"
    "/synccategories — (админ) обновить справочник категорий Ozon\n"
    "/cancel — отменить текущий диалог"
)

ASK_CATEGORY = "Создаём новый товар.\n\nВведите категорию товара (например, «Тюнинг салона» или «Карман обивки дверей»):"
ASK_TITLE = "Укажите точное название товара (коротко, как на WB):"
ASK_BRAND = f"Бренд товара (без ИП/ООО):"
ASK_VENDOR_CODE = "Артикул в вашем магазине (SKU):"
ASK_COST_PRICE = "Себестоимость (руб. без НДС):"
ASK_PRICE = "Розничная цена (руб.):"
ASK_BARCODE = "Штрихкод (EAN, 13 цифр) — или нажмите «Пропустить», если нет маркировки:"
ASK_PACKAGE_CONTENTS = "Укажите комплектность (что входит в набор):"
ASK_MATERIAL = "Материал:"
ASK_COLOR = "Цвет:"
ASK_DIMENSIONS = "Размеры в упаковке (длина×ширина×высота, мм). Пример: 500x200x50:"
ASK_WEIGHT = "Вес в упаковке (грамм):"
ASK_PHOTOS = "Загрузите 4–5 фото товара (прямоугольное фото 900×1200+, белый фон предпочтителен). Когда закончите — нажмите кнопку ниже."
PHOTO_RECEIVED = "Фото получено ({count})."

CANCELLED = "Диалог отменён. Введите /new, чтобы начать заново."
NOT_FOUND = "Товар не найден."
INVALID_NUMBER = "Нужно ввести число. Попробуйте ещё раз:"
INVALID_DIMENSIONS = "Формат неверный. Введите как 500x200x50 (длина×ширина×высота в мм):"

PUBLISHING = "⏳ Публикую карточку на Wildberries и Ozon..."


def generating_preview() -> str:
    return "🤖 Генерирую SEO-название, описание и ключевые слова в стиле ALICARTUNING..."


def draft_preview(title: str, description: str, price: float | None, cost_price: float | None) -> str:
    price_part = f"{price:.0f}₽" if price else "—"
    cost_part = f" (себестоимость {cost_price:.0f}₽)" if cost_price else ""
    return (
        "📝 <b>Черновик карточки:</b>\n\n"
        f"<b>Название:</b> {title}\n\n"
        f"<b>Описание:</b>\n{description}\n\n"
        f"<b>Цена:</b> {price_part}{cost_part}\n\n"
        "Проверьте данные выше. Если всё верно — нажмите «Опубликовать». "
        "Если нужно исправить — «Редактировать»."
    )


def validation_errors(text: str) -> str:
    return f"⚠️ Карточку нельзя опубликовать, пока не исправлены проблемы:\n\n{text}"


def publish_success(wb_id: str | None, ozon_id: str | None) -> str:
    lines = ["✅ <b>Товар успешно опубликован:</b>"]
    lines.append(f"• Wildberries: {'ID ' + wb_id if wb_id else '—'}")
    lines.append(f"• Ozon: {'ID ' + ozon_id if ozon_id else '—'}")
    return "\n".join(lines)


def publish_partial(wb_message: str | None, ozon_message: str | None) -> str:
    lines = ["⚠️ <b>Публикация завершена с ошибками:</b>"]
    if wb_message:
        lines.append(f"• Wildberries: {wb_message}")
    if ozon_message:
        lines.append(f"• Ozon: {ozon_message}")
    return "\n".join(lines)


def product_list_item(product) -> str:
    status_emoji = {
        "draft": "📝",
        "ready": "🟡",
        "publishing": "⏳",
        "published": "✅",
        "partially_published": "⚠️",
        "error": "❌",
    }.get(product.status.value, "•")
    return f"{status_emoji} #{product.id} {product.title or '(без названия)'}"
