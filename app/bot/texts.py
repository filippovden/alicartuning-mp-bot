"""Тексты сообщений бота (раздел 8 ТЗ, стиль ALICARTUNING)."""

WELCOME = (
    "👋 Привет! Я AI-менеджер маркетплейсов ALICARTUNING.\n\n"
    "Помогу быстро создать карточку товара и опубликовать её на Wildberries и Ozon.\n\n"
    "Команды:\n"
    "/new — создать новый товар\n"
    "/list — мои товары и черновики\n"
    "/clone <ID> — клонировать товар под другую модель авто\n"
    "/status — статус публикации\n"
    "/edit <ID> — редактировать товар\n"
    "/competitors <запрос> — анализ конкурентов на Wildberries\n"
    "/analytics — сводка продаж и рекомендации по цене\n"
    "/analytics <ID> — цена + когда лучше продвигаться/менять цену (тайминг-аналитика)\n"
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
ASK_CAR_MODEL = "Модель автомобиля (например, Lada Vesta, Granta, Priora, Niva):"
ASK_DIMENSIONS = "Размеры в упаковке (длина×ширина×высота, мм). Пример: 500x200x50:"
ASK_WEIGHT = "Вес в упаковке (грамм):"
MIN_PRODUCT_PHOTOS = 3

ASK_PHOTOS = (
    f"Загрузите фото товара: минимум {MIN_PRODUCT_PHOTOS}, а лучше 4–5 "
    "(прямоугольное фото 900×1200+, белый фон предпочтителен). "
    "Когда наберётся минимум — нажмите кнопку «Готово»."
)
PHOTO_RECEIVED = "Фото получено ({count})."


def need_more_photos(current: int, minimum: int = MIN_PRODUCT_PHOTOS) -> str:
    missing = minimum - current
    return (
        f"⚠️ Загружено фото: {current}, нужно минимум {minimum}. "
        f"Добавьте ещё {missing} фото, прежде чем нажать «Готово»."
    )

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


def publish_success(
    wb_id: str | None,
    ozon_id: str | None,
    wb_message: str | None = None,
    ozon_message: str | None = None,
) -> str:
    lines = ["✅ <b>Товар успешно опубликован:</b>"]
    lines.append(_publish_success_line("Wildberries", wb_id, wb_message))
    lines.append(_publish_success_line("Ozon", ozon_id, ozon_message))
    return "\n".join(lines)


def _publish_success_line(marketplace: str, external_id: str | None, message: str | None) -> str:
    if not external_id:
        return f"• {marketplace}: —"
    line = f"• {marketplace}: ID {external_id}"
    if message:
        line += f" ({message})"
    return line


def publish_partial(wb_message: str | None, ozon_message: str | None) -> str:
    lines = ["⚠️ <b>Публикация завершена с ошибками:</b>"]
    if wb_message:
        lines.append(f"• Wildberries: {wb_message}")
    if ozon_message:
        lines.append(f"• Ozon: {ozon_message}")
    return "\n".join(lines)


def clone_created(clone_id: int, source_id: int) -> str:
    return (
        f"✅ Черновик #{clone_id} создан на основе товара #{source_id} — категория, материал, "
        "цвет, комплектация, габариты, вес, цена, характеристики и фото скопированы. "
        "Дальше зададим новую модель и новый артикул."
    )


ASK_CLONE_VENDOR_CODE = "Новый уникальный артикул (SKU) для клона:"


def ask_batch_car_models(max_count: int) -> str:
    return f"Введите модели авто через запятую (максимум {max_count}), например:\nVesta, Granta, Priora"


def too_many_models(max_count: int, given: int) -> str:
    return f"⚠️ Указано моделей: {given}, максимум за раз — {max_count}. Сократите список и отправьте ещё раз."


def ask_vendor_code_template() -> str:
    return (
        "Шаблон артикула для каждого клона (используйте {model} — подставится модель "
        "в верхнем регистре без пробелов), например:\nART-{model}\n\n"
        "Или нажмите «Пропустить» — сгенерирую артикул автоматически."
    )


def clone_draft_preview(product) -> str:
    price_part = f"{float(product.price):.0f}₽" if product.price else "—"
    cost_part = f" (себестоимость {float(product.cost_price):.0f}₽)" if product.cost_price else ""
    return (
        "📝 <b>Черновик карточки (клон):</b>\n\n"
        f"<b>Артикул:</b> {product.vendor_code} / <b>Модель:</b> {product.car_model}\n\n"
        f"<b>Название:</b> {product.title}\n\n"
        f"<b>Описание:</b>\n{product.description}\n\n"
        f"<b>Цена:</b> {price_part}{cost_part}\n\n"
        "Проверьте данные выше. Если всё верно — нажмите «Опубликовать». "
        "Если нужно исправить — «Редактировать»."
    )


def batch_clone_summary(products) -> str:
    lines = [f"✅ Создано черновиков: {len(products)}"]
    for product in products:
        lines.append(f"• #{product.id} — артикул {product.vendor_code}, модель {product.car_model}: {product.title}")
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
