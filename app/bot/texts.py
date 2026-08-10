"""Тексты сообщений бота (раздел 8 ТЗ, стиль ALICARTUNING)."""

import html

WELCOME = (
    "ALICARTUNING — карточки для Wildberries и Ozon.\n\n"
    "Создавай товары, клонируй на другие модели Lada и публикуй из этого чата.\n\n"
    "Выбери действие в меню ниже."
)

RESUME_DIALOG_NOTICE = (
    "\n\n⚠️ У вас есть незавершённый диалог. Можно продолжить отвечать на "
    "последний вопрос или нажать /cancel, чтобы начать заново."
)

MORE_MENU = (
    "⚙️ Ещё команды:\n\n"
    "/drafts — незаконченные черновики\n"
    "/edit [ID] — править товар\n"
    "/status [ID] — статус публикации\n"
    "/competitors [запрос] — анализ конкурентов на Wildberries\n"
    "/synccategories — (админ) обновить справочник категорий Ozon\n"
    "/cancel — отменить текущий диалог"
)

NEW_PRODUCT_CHOOSE_MODE = "Как создаём карточку?"

STEP_TOTAL = 13


def step(n: int, label: str) -> str:
    """Прогресс в пошаговом диалоге /new — чтобы было видно, сколько ещё осталось
    (раздел C ТЗ: длинная анкета без ориентиров отпугивает)."""
    return f"Шаг {n}/{STEP_TOTAL} · {label}\n\n"


ASK_CATEGORY = "Создаём новый товар.\n\nВведите категорию товара (например, «Тюнинг салона» или «Карман обивки дверей»):"
ASK_TITLE = "Черновое название (можно коротко, AI потом улучшит):"
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


CANCELLED = "Отменено."
NOT_FOUND = "Товар не найден."
INVALID_NUMBER = "Нужно ввести число. Попробуйте ещё раз:"
INVALID_DIMENSIONS = "Формат неверный. Введите как 500x200x50 (длина×ширина×высота в мм):"

PUBLISHING = "⏳ Публикую на Wildberries и Ozon..."

DESCRIPTION_PREVIEW_MAX_LEN = 700


def generating_preview() -> str:
    return "🤖 Генерирую SEO-название, описание и ключевые слова в стиле ALICARTUNING..."


def product_preview(product, validation=None) -> str:
    """Единый формат превью карточки — раздел C1 ТЗ: один и тот же экран для
    пошагового режима, быстрого создания и клонирования, а не три разных.

    Значения полей экранируются от HTML: car_model/vendor_code вводит человек
    руками, а сообщение отправляется с parse_mode=HTML — без экранирования
    случайный «<» в артикуле уронил бы отправку (та же категория бага, что
    уже случалась с плейсхолдерами в WELCOME, см. test_html_safety.py)."""
    title = html.escape(product.title) if product.title else "(без названия)"
    car_model = html.escape(product.car_model) if product.car_model else "—"
    vendor_code = html.escape(product.vendor_code) if product.vendor_code else "—"
    price_part = f"{float(product.price):.0f}" if product.price else "—"
    photos_count = len(product.images)

    lines = [
        f"📦 <b>{title}</b>",
        f"Модель: {car_model} · Артикул: {vendor_code}",
        f"Цена: {price_part}₽ · Фото: {photos_count}",
    ]

    description = product.description or ""
    if description:
        description = html.escape(description)
        if len(description) > DESCRIPTION_PREVIEW_MAX_LEN:
            description = description[:DESCRIPTION_PREVIEW_MAX_LEN].rstrip() + "…"
        lines.append("")
        lines.append(description)

    errors = validation.errors() if validation is not None else []
    lines.append("")
    if errors:
        lines.append("⚠️ <b>Нужно исправить:</b>")
        lines += [f"• {issue.message}" for issue in errors]
    else:
        lines.append("✅ Можно публиковать")

    return "\n".join(lines)


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
    return f"🧬 Клон #{clone_id} от товара #{source_id} — фото и характеристики скопированы. Модель авто?"


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


def batch_clone_summary(products) -> str:
    lines = [f"✅ Создано черновиков: {len(products)}"]
    for product in products:
        lines.append(f"• #{product.id} · {product.car_model} · {product.vendor_code}")
    return "\n".join(lines)


QUICK_ASK_PHOTOS = (
    f"⚡ Быстрое создание.\n\n"
    f"Пришлите от {MIN_PRODUCT_PHOTOS} фото товара — можно подряд или альбомом."
)
QUICK_ASK_DESCRIPTION = (
    "Теперь одним сообщением опишите товар: тип детали, модель Lada, материал, "
    "цвет, цену — как удобно, я разберу текст сам.\n\n"
    "Пример: «Накладки зеркал BMW-стиль, Lada Granta, ABS, чёрный глянец, цена 990»"
)
QUICK_PARSING = "🤖 Разбираю описание..."
QUICK_PARSE_FAILED = "⚠️ Не смог разобрать описание. Можно попробовать ещё раз или заполнить пошагово:"
QUICK_SEND_PHOTOS_FIRST = "Сначала пришлите хотя бы одно фото."


def quick_ask_vendor_code(car_model: str) -> str:
    return f"Модель определена как «{car_model}». Теперь новый уникальный артикул (SKU):"


ASK_QUICK_DIMENSIONS = "Размеры в упаковке не удалось определить из текста — укажите (длина×ширина×высота, мм). Пример: 500x200x50:"
ASK_QUICK_WEIGHT = "Вес в упаковке не удалось определить из текста — укажите (грамм):"


def draft_list_item(product) -> str:
    parts = [f"📝 #{product.id}"]
    if product.title:
        parts.append(product.title)
    if product.car_model:
        parts.append(f"({product.car_model})")
    return " ".join(parts)


NO_DRAFTS = "Незаконченных черновиков нет. Нажмите «📦 Новый товар», чтобы начать."


def price_check_report(report, pricing: dict | None) -> str:
    if not report.items:
        return f"По запросу «{report.query}» конкурентов на Wildberries не нашлось — сравнить цену не с чем."

    lines = [f"💰 <b>Цена по рынку</b> (запрос «{report.query}»)"]
    lines.append(
        f"Конкуренты: мин {report.min_price:.0f}₽ / средняя {report.average_price:.0f}₽ / макс {report.max_price:.0f}₽"
    )
    if pricing and pricing.get("recommended_price"):
        lines.append(f"\nРекомендованная цена: {pricing['recommended_price']:.0f}₽")
        if pricing.get("note"):
            lines.append(f"⚠️ {pricing['note']}")
    return "\n".join(lines)


def price_set(product_id: int, price: float) -> str:
    return f"✅ Цена товара #{product_id} обновлена: {price:.0f}₽"


def publish_all_summary(results: list[tuple[int, bool, str]]) -> str:
    lines = ["📦 <b>Публикация всех черновиков завершена:</b>"]
    for product_id, ok, note in results:
        emoji = "✅" if ok else "⚠️"
        lines.append(f"{emoji} #{product_id}: {note}")
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
    parts = [f"{status_emoji} #{product.id}", product.title or "(без названия)"]
    if product.car_model:
        parts.append(product.car_model)
    if product.price:
        parts.append(f"{float(product.price):.0f}₽")
    return " · ".join(parts)
