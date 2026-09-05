from __future__ import annotations

import asyncio
import logging
import re
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import (
    category_match_kb,
    confirm_publish_kb,
    main_menu_kb,
    photos_done_kb,
    retry_ai_kb,
    shop_confirm_kb,
    shop_picker_kb,
    skip_kb,
)
from app.bot.progress import set_progress, start_progress
from app.bot.states import NewProductStates, ShopPickStates
from app.config import settings
from app.db.models import ListingStatus, Marketplace
from app.services.category_search import (
    ozon_cache_is_empty,
    search_ozon_categories,
    search_wb_categories,
)
from app.services.marketplaces.base import MarketplaceAPIError
from app.services.storage import save_bytes
from app.services.validation import check_image_dimensions

logger = logging.getLogger(__name__)
router = Router(name="new_product")

DIMENSIONS_RE = re.compile(r"^\s*(\d+)\s*[xXхХ×]\s*(\d+)\s*[xXхХ×]\s*(\d+)\s*$")


def _parse_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return None


def _photo_size_warning(image_bytes: bytes) -> str | None:
    """Проверяет фото сразу при загрузке (раздел C.4 ТЗ) — раньше маленькое фото
    всплывало только на этапе публикации, когда переснимать уже неудобно.
    Переиспользует пороги WB/Ozon из app.services.validation, а не заводит
    отдельное магическое число."""
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
    except Exception:
        logger.warning("Не удалось прочитать размеры фото", exc_info=True)
        return None

    issues = check_image_dimensions(width, height)
    if not issues:
        return None
    return "\n".join(str(issue) for issue in issues)


async def render_preview(product_service, product_id: int):
    """Единый экран превью — раздел C1 ТЗ: один и тот же формат для пошагового
    режима, быстрого создания и клонирования (см. texts.product_preview),
    вместо трёх похожих, но чуть разных текстов."""
    product = await product_service.get_product(product_id)
    validation = await product_service.validate(product_id)
    return texts.product_preview(product, validation), confirm_publish_kb(product.id)


async def try_generate_ai_content(answer, product_service, product_id: int) -> bool:
    """Раздел H.6 ТЗ: сбой генерации AI-текста не должен ронять диалог —
    показываем понятную ошибку с кнопкой «Повторить» вместо необработанного
    исключения, из-за которого бот молча переставал бы отвечать."""
    try:
        await product_service.generate_ai_content(product_id)
        return True
    except Exception:
        logger.warning("Не удалось сгенерировать текст карточки #%s", product_id, exc_info=True)
        await answer(
            "⚠️ Не получилось сгенерировать текст карточки — AI недоступен. Попробуйте ещё раз:",
            reply_markup=retry_ai_kb(product_id),
        )
        return False


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext, product_service) -> None:
    await start_step_by_step(
        state, product_service, message.answer,
        telegram_id=message.from_user.id, username=message.from_user.username, full_name=message.from_user.full_name,
    )


@router.callback_query(F.data == "newmode:step")
async def start_step_by_step_callback(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    await callback.answer()
    await start_step_by_step(
        state, product_service, callback.message.answer,
        telegram_id=callback.from_user.id, username=callback.from_user.username, full_name=callback.from_user.full_name,
    )


async def start_step_by_step(state: FSMContext, product_service, answer, *, telegram_id: int, username: str | None, full_name: str | None) -> None:
    user = await product_service.get_or_create_user(telegram_id=telegram_id, username=username, full_name=full_name)
    product = await product_service.create_draft(user.id)
    await product_service.update_fields(product.id, brand=settings.brand_name)
    await state.set_state(NewProductStates.category)
    await state.update_data(product_id=product.id, photos=[], pending_attrs=[])
    await answer(texts.step(1, "Категория") + texts.ASK_CATEGORY)


@router.message(NewProductStates.category)
async def step_category(message: Message, state: FSMContext, product_service) -> None:
    category_name = message.text.strip()
    await state.update_data(category_name=category_name)

    try:
        wb_matches = await search_wb_categories(category_name, limit=8)
    except MarketplaceAPIError as exc:
        logger.warning("Поиск категорий WB не удался: %s", exc)
        wb_matches = []

    await state.update_data(
        wb_matches=[{"subject_id": m.subject_id, "name": m.name} for m in wb_matches]
    )
    await state.set_state(NewProductStates.category_pick_wb)

    if wb_matches:
        labels = [m.name for m in wb_matches]
        await message.answer(
            "Нашёл подходящие категории на <b>Wildberries</b>, выберите нужную:",
            reply_markup=category_match_kb("wbcat", labels),
        )
    else:
        await message.answer(
            "Не нашёл точного совпадения на Wildberries по этому названию — можно "
            "пропустить и указать характеристики вручную позже.",
            reply_markup=category_match_kb("wbcat", []),
        )


@router.callback_query(F.data.startswith("wbcat:"), NewProductStates.category_pick_wb)
async def pick_wb_category(callback: CallbackQuery, state: FSMContext, product_service, session) -> None:
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if choice == "manual":
        await state.update_data(wb_subject_id=None, wb_category_name=None)
    else:
        match = data["wb_matches"][int(choice)]
        await state.update_data(wb_subject_id=match["subject_id"], wb_category_name=match["name"])

    await callback.answer()

    if await ozon_cache_is_empty(session):
        await state.update_data(ozon_matches=[])
        await callback.message.answer(
            "Справочник категорий Ozon ещё не синхронизирован администратором "
            "(команда /synccategories) — пропускаю подбор Ozon-категории, "
            "её можно будет указать позже через /edit.",
        )
        await _finish_category_pick(callback.message, state, product_service, None, None)
        return

    ozon_matches = await search_ozon_categories(session, data["category_name"], limit=8)
    await state.update_data(
        ozon_matches=[
            {"category_id": m.category_id, "type_id": m.type_id, "full_name": m.full_name} for m in ozon_matches
        ]
    )
    await state.set_state(NewProductStates.category_pick_ozon)

    if ozon_matches:
        labels = [m.full_name for m in ozon_matches]
        await callback.message.answer(
            "Теперь найдите категорию на <b>Ozon</b>:", reply_markup=category_match_kb("ozcat", labels)
        )
    else:
        await callback.message.answer(
            "На Ozon точных совпадений не нашлось — можно пропустить.",
            reply_markup=category_match_kb("ozcat", []),
        )


@router.callback_query(F.data.startswith("ozcat:"), NewProductStates.category_pick_ozon)
async def pick_ozon_category(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()

    ozon_category_id = ozon_type_id = None
    if choice != "manual":
        match = data["ozon_matches"][int(choice)]
        ozon_category_id, ozon_type_id = match["category_id"], match["type_id"]

    await callback.answer()
    await _finish_category_pick(callback.message, state, product_service, ozon_category_id, ozon_type_id)


async def _finish_category_pick(
    message: Message,
    state: FSMContext,
    product_service,
    ozon_category_id: int | None,
    ozon_type_id: int | None,
) -> None:
    data = await state.get_data()
    category = await product_service.get_or_fetch_category(
        name=data.get("wb_category_name") or data["category_name"],
        wb_subject_id=data.get("wb_subject_id"),
        ozon_category_id=ozon_category_id,
        ozon_type_id=ozon_type_id,
    )
    await product_service.update_fields(data["product_id"], category_id=category.id)

    await state.set_state(NewProductStates.title)
    await message.answer(texts.step(2, "Название") + texts.ASK_TITLE)


@router.message(NewProductStates.title)
async def step_title(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], title=message.text.strip())
    await state.set_state(NewProductStates.vendor_code)
    await message.answer(texts.step(3, "Артикул") + texts.ASK_VENDOR_CODE)


@router.message(NewProductStates.vendor_code)
async def step_vendor_code(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], vendor_code=message.text.strip())
    await state.set_state(NewProductStates.cost_price)
    await message.answer(texts.step(4, "Себестоимость") + texts.ASK_COST_PRICE)


@router.message(NewProductStates.cost_price)
async def step_cost_price(message: Message, state: FSMContext, product_service) -> None:
    value = _parse_float(message.text)
    if value is None:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], cost_price=value)
    await state.set_state(NewProductStates.price)
    await message.answer(texts.step(5, "Цена") + texts.ASK_PRICE)


@router.message(NewProductStates.price)
async def step_price(message: Message, state: FSMContext, product_service) -> None:
    value = _parse_float(message.text)
    if value is None:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], price=value)
    await state.set_state(NewProductStates.barcode)
    await message.answer(texts.step(6, "Штрихкод") + texts.ASK_BARCODE, reply_markup=skip_kb("barcode"))


@router.callback_query(F.data == "skip:barcode")
async def skip_barcode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewProductStates.package_contents)
    await callback.message.answer(texts.step(7, "Комплектация") + texts.ASK_PACKAGE_CONTENTS)
    await callback.answer()


@router.message(NewProductStates.barcode)
async def step_barcode(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], barcode=message.text.strip())
    await state.set_state(NewProductStates.package_contents)
    await message.answer(texts.step(7, "Комплектация") + texts.ASK_PACKAGE_CONTENTS)


@router.message(NewProductStates.package_contents)
async def step_package_contents(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], package_contents=message.text.strip())
    await state.set_state(NewProductStates.material)
    await message.answer(texts.step(8, "Материал") + texts.ASK_MATERIAL)


@router.message(NewProductStates.material)
async def step_material(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], material=message.text.strip())
    await state.set_state(NewProductStates.color)
    await message.answer(texts.step(9, "Цвет") + texts.ASK_COLOR)


@router.message(NewProductStates.color)
async def step_color(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], color=message.text.strip())
    await state.set_state(NewProductStates.car_model)
    await message.answer(texts.step(10, "Модель авто") + texts.ASK_CAR_MODEL)


@router.message(NewProductStates.car_model)
async def step_car_model(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], car_model=message.text.strip())
    await state.set_state(NewProductStates.dimensions)
    await message.answer(texts.step(11, "Размеры") + texts.ASK_DIMENSIONS)


@router.message(NewProductStates.dimensions)
async def step_dimensions(message: Message, state: FSMContext, product_service) -> None:
    match = DIMENSIONS_RE.match(message.text)
    if not match:
        await message.answer(texts.INVALID_DIMENSIONS)
        return
    length, width, height = (int(v) for v in match.groups())
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], length_mm=length, width_mm=width, height_mm=height)
    await state.set_state(NewProductStates.weight)
    await message.answer(texts.step(12, "Вес") + texts.ASK_WEIGHT)


@router.message(NewProductStates.weight)
async def step_weight(message: Message, state: FSMContext, product_service) -> None:
    try:
        weight = int(message.text.strip())
    except ValueError:
        await message.answer(texts.INVALID_NUMBER)
        return
    data = await state.get_data()
    await product_service.update_fields(data["product_id"], weight_g=weight)
    await state.set_state(NewProductStates.photos)
    await message.answer(texts.step(13, "Фото") + texts.ASK_PHOTOS, reply_markup=photos_done_kb())


ALBUM_DEBOUNCE_SECONDS = 0.8

# Отложенные ответы «Фото получено (N)» на альбомы — по одному на (chat_id,
# media_group_id), см. handle_incoming_photo.
_album_debounce_tasks: dict[tuple[int, str], asyncio.Task] = {}


async def _answer_photos_received(message: Message, state: FSMContext, delay: float = 0.0) -> None:
    if delay:
        await asyncio.sleep(delay)
    data = await state.get_data()
    photos = data.get("photos", [])
    await message.answer(texts.PHOTO_RECEIVED.format(count=len(photos)), reply_markup=photos_done_kb())


async def handle_incoming_photo(
    message: Message,
    state: FSMContext,
    product_service,
    session,
    *,
    debounce_seconds: float = ALBUM_DEBOUNCE_SECONDS,
) -> None:
    """Сохраняет пришедшее фото как главное и отвечает пользователю «Фото
    получено (N)» (раздел 6 ТЗ v3, общая логика для пошагового и быстрого
    режимов). Каждый кадр альбома (message.media_group_id задан) сохраняется
    сразу — не теряем ни одного, но ответ пользователю шлём только один раз,
    после последнего кадра: новый кадр того же альбома отменяет предыдущий
    отложенный ответ и планирует новый (debounce). Раньше каждый кадр альбома
    рождал свой ответ и клавиатуру — спамило и гоняло state.update_data
    неатомарно. Одиночное фото (без media_group_id) отвечает сразу, как раньше."""
    data = await state.get_data()
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    buffer = await message.bot.download_file(file.file_path)
    content = buffer.read()
    storage_file = await save_bytes(session, content, filename=file.file_path, content_type="image/jpeg")
    await product_service.add_image(data["product_id"], storage_file.id, image_type="main", position=len(data["photos"]))

    photos = data["photos"] + [storage_file.id]
    await state.update_data(photos=photos)

    media_group_id = message.media_group_id
    if media_group_id is None:
        size_warning = _photo_size_warning(content)
        if size_warning:
            await message.answer(size_warning)
        await _answer_photos_received(message, state)
        return

    # Варнинг о маленьком размере — максимум один на альбом, даже если
    # несколько кадров одного альбома оказались маленькими.
    warned_groups = data.get("warned_album_groups", [])
    size_warning = _photo_size_warning(content)
    if size_warning and media_group_id not in warned_groups:
        await message.answer(size_warning)
        await state.update_data(warned_album_groups=warned_groups + [media_group_id])

    key = (message.chat.id, media_group_id)
    existing_task = _album_debounce_tasks.get(key)
    if existing_task is not None:
        existing_task.cancel()
    _album_debounce_tasks[key] = asyncio.create_task(_answer_photos_received(message, state, delay=debounce_seconds))


@router.message(NewProductStates.photos, F.photo)
async def step_photo(message: Message, state: FSMContext, product_service, session) -> None:
    await handle_incoming_photo(message, state, product_service, session)


@router.callback_query(F.data == "photos_done", NewProductStates.photos)
async def photos_done(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    photos_count = len(data["photos"])
    if photos_count < texts.MIN_PRODUCT_PHOTOS:
        await callback.answer(texts.need_more_photos(photos_count), show_alert=True)
        await callback.message.answer(texts.need_more_photos(photos_count))
        return

    pending_attrs = await _collect_pending_attributes(product_service, data["product_id"])
    await state.update_data(pending_attrs=pending_attrs)
    await callback.answer()
    await _ask_next_attribute_or_generate(callback.message, state, product_service)


async def _collect_pending_attributes(product_service, product_id: int) -> list[dict]:
    product = await product_service.get_product(product_id)
    if product.category is None:
        return []
    try:
        await product_service.sync_category_attributes(product.category)
    except MarketplaceAPIError as exc:
        logger.warning("Не удалось получить характеристики категории: %s", exc)

    # Перечитываем товар, чтобы подтянуть свежесинхронизированные category.attrs
    product = await product_service.get_product(product_id)
    filled_ids = {attr.category_attr_id for attr in product.attributes}
    pending = [
        {"id": attr.id, "name": attr.name, "marketplace": attr.marketplace.value}
        for attr in product.category.attrs
        if attr.required and attr.id not in filled_ids
    ]
    return pending


async def _ask_next_attribute_or_generate(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    pending = data.get("pending_attrs", [])
    if pending:
        next_attr = pending[0]
        await state.set_state(NewProductStates.dynamic_attribute)
        await message.answer(f"Укажите «{next_attr['name']}» ({next_attr['marketplace']}):")
        return

    await state.set_state(NewProductStates.confirm)
    await message.answer(texts.generating_preview())
    product_id = data["product_id"]
    if not await try_generate_ai_content(message.answer, product_service, product_id):
        return
    preview_text, keyboard = await render_preview(product_service, product_id)
    await message.answer(preview_text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("regenai:"))
async def regenerate_ai_content(callback: CallbackQuery, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer()
    if not await try_generate_ai_content(callback.message.answer, product_service, product_id):
        return
    preview_text, keyboard = await render_preview(product_service, product_id)
    await callback.message.answer(preview_text, reply_markup=keyboard)


@router.message(NewProductStates.dynamic_attribute)
async def step_dynamic_attribute(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    pending = data.get("pending_attrs", [])
    if not pending:
        await _ask_next_attribute_or_generate(message, state, product_service)
        return

    current = pending[0]
    await product_service.set_attribute_value(data["product_id"], current["id"], message.text.strip())
    remaining = pending[1:]
    await state.update_data(pending_attrs=remaining)
    await _ask_next_attribute_or_generate(message, state, product_service)


@router.callback_query(F.data.startswith("processimg:"))
async def process_images(callback: CallbackQuery, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer("Обрабатываю фото...")
    await callback.message.answer("⏳ Убираю фон и привожу фото к единому шаблону...")
    processed = await product_service.process_product_images(product_id)
    if processed:
        await callback.message.answer(f"✅ Обработано фото: {len(processed)}. Новые версии добавлены к товару.")
    else:
        await callback.message.answer("⚠️ Не нашёл исходных фото для обработки.")


@router.callback_query(F.data.startswith("gengraphic:"))
async def generate_graphic(callback: CallbackQuery, product_service) -> None:
    """Кнопка «🎨 Инфографика» — вся цепочка (буллеты → Grok Imagine с
    референс-фото/без него → Pillow) обёрнута так, чтобы недоступность любого
    внешнего сервиса не оставляла пользователя без ответа и без stacktrace.
    С XAI_API_KEY отдаём 2 варианта (акцент на материал / на совместимость с
    моделью) — с одной картинкой выбирать не из чего; без ключа — один
    Pillow-рендер, дублировать одинаковые Pillow-картинки незачем."""
    product_id = int(callback.data.split(":")[1])
    await callback.answer()

    if settings.xai_api_key:
        await callback.message.answer("⏳ Генерирую инфографику через Grok Imagine...")
        count = 2
    else:
        await callback.message.answer("⏳ Генерирую инфографику...")
        count = 1

    try:
        images = await product_service.generate_infographic_images(product_id, count=count)
    except Exception:
        logger.warning("Не удалось сгенерировать инфографику для товара %s", product_id, exc_info=True)
        await callback.message.answer("⚠️ Не удалось сгенерировать инфографику. Попробуйте ещё раз чуть позже.")
        return

    if not images:
        await callback.message.answer("⚠️ Инфографика не создана.")
        return

    from pathlib import Path

    sent = 0
    for idx, image in enumerate(images):
        # Байты только что сгенерированной картинки лежат прямо на объекте
        # (см. ProductService.generate_infographic_images) — читаем диск только
        # если их почему-то нет, а не наоборот, чтобы гонка/проблема с volume
        # не превращалась в «файл не найден» для картинки, которая только что
        # реально была создана.
        photo_bytes = getattr(image, "_preview_bytes", None)
        if photo_bytes is None and image.storage_file:
            source_path = Path(image.storage_file.path)
            if source_path.exists():
                photo_bytes = source_path.read_bytes()

        if photo_bytes is None:
            continue

        caption = f"✅ Вариант {idx + 1}" if len(images) > 1 else "✅ Инфографика добавлена к карточке."
        await callback.message.answer_photo(
            BufferedInputFile(photo_bytes, filename="infographic.png"),
            caption=caption,
        )
        sent += 1

    if sent == 0:
        await callback.message.answer("⚠️ Файл инфографики не найден на диске.")
    elif sent > 1:
        await callback.message.answer("Оба варианта добавлены к карточке.")


@router.callback_query(F.data.startswith("publish:"))
async def confirm_publish(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    """«🚀 Выложить» — раздел 4.1-4.2 ТЗ v5. Если магазинов больше одного на
    платформу — сначала экран «Куда выложить?» (ShopPickStates.picking), а
    сама публикация начинается только после подтверждения (shopconfirm:).
    Если в системе ровно один WB и один Ozon (или меньше) — экран не нужен,
    поведение как раньше: публикуем сразу в магазин(ы) по умолчанию."""
    from app.services import shops as shops_service

    product_id = int(callback.data.split(":")[1])
    await callback.answer()

    validation = await product_service.validate(product_id)
    if not validation.is_valid:
        await callback.message.answer(texts.validation_errors(validation.as_text()))
        return

    wb_shops = shops_service.list_shops(platform=Marketplace.WB)
    ozon_shops = shops_service.list_shops(platform=Marketplace.OZON)

    if len(wb_shops) <= 1 and len(ozon_shops) <= 1:
        # Раздел 2.B ТЗ v8: одна полоска-сообщение вместо голого «⏳ Публикую...».
        # Инфографику в publish() не зовём (см. product_service.publish) —
        # поэтому шага «Картинка» из таблицы ТЗ здесь нет, только 15/70/100.
        handle = await start_progress(callback.message.answer, "Публикую карточку")
        await set_progress(handle, 15, "Готовлю выкладку")
        await set_progress(handle, 70, "Отправляю на площадки")
        try:
            summary = await product_service.publish(product_id)
        except ValueError as exc:
            await set_progress(handle, 100, "Не получилось")
            await callback.message.answer(f"⚠️ {exc}")
            return
        await set_progress(handle, 100, "Готово")
        await callback.message.answer(texts.publish_result(summary.wb, summary.ozon))
        await state.clear()
        return

    await state.update_data(shoppick_product_id=product_id, shoppick_selected=[])
    await state.set_state(ShopPickStates.picking)
    await callback.message.answer(
        texts.SHOP_PICK_INTRO, reply_markup=shop_picker_kb(product_id, wb_shops, ozon_shops, set())
    )


@router.callback_query(F.data.startswith("shoppick:"), ShopPickStates.picking)
async def shop_pick_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    from app.services import shops as shops_service

    _, product_id_raw, shop_id = callback.data.split(":")
    product_id = int(product_id_raw)
    await callback.answer()

    data = await state.get_data()
    selected = set(data.get("shoppick_selected", []))
    if shop_id in selected:
        selected.discard(shop_id)
    else:
        selected.add(shop_id)
    await state.update_data(shoppick_selected=list(selected))

    wb_shops = shops_service.list_shops(platform=Marketplace.WB)
    ozon_shops = shops_service.list_shops(platform=Marketplace.OZON)
    await callback.message.edit_reply_markup(reply_markup=shop_picker_kb(product_id, wb_shops, ozon_shops, selected))


@router.callback_query(F.data.startswith("shoppickall:"), ShopPickStates.picking)
async def shop_pick_all(callback: CallbackQuery, state: FSMContext) -> None:
    from app.services import shops as shops_service

    _, product_id_raw, platform_raw = callback.data.split(":")
    product_id = int(product_id_raw)
    await callback.answer()

    platform = Marketplace.WB if platform_raw == "wb" else Marketplace.OZON
    wb_shops = shops_service.list_shops(platform=Marketplace.WB)
    ozon_shops = shops_service.list_shops(platform=Marketplace.OZON)
    platform_shops = wb_shops if platform == Marketplace.WB else ozon_shops

    data = await state.get_data()
    selected = set(data.get("shoppick_selected", []))
    selected.update(s.id for s in platform_shops)
    await state.update_data(shoppick_selected=list(selected))

    await callback.message.edit_reply_markup(reply_markup=shop_picker_kb(product_id, wb_shops, ozon_shops, selected))


@router.callback_query(F.data.startswith("shopgo:"), ShopPickStates.picking)
async def shop_pick_go(callback: CallbackQuery, state: FSMContext) -> None:
    from app.services import shops as shops_service

    product_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected = data.get("shoppick_selected", [])
    if not selected:
        await callback.answer(texts.NEED_AT_LEAST_ONE_SHOP, show_alert=True)
        return
    await callback.answer()

    shop_objs = [s for s in (shops_service.get_shop(sid) for sid in selected) if s is not None]
    await state.set_state(ShopPickStates.confirming)
    await callback.message.answer(
        texts.shop_confirm_screen([s.name for s in shop_objs]), reply_markup=shop_confirm_kb(product_id)
    )


@router.callback_query(F.data.startswith("shopback:"), ShopPickStates.confirming)
async def shop_pick_back(callback: CallbackQuery, state: FSMContext) -> None:
    from app.services import shops as shops_service

    product_id = int(callback.data.split(":")[1])
    await callback.answer()

    data = await state.get_data()
    selected = set(data.get("shoppick_selected", []))
    await state.set_state(ShopPickStates.picking)

    wb_shops = shops_service.list_shops(platform=Marketplace.WB)
    ozon_shops = shops_service.list_shops(platform=Marketplace.OZON)
    await callback.message.answer(
        texts.SHOP_PICK_INTRO, reply_markup=shop_picker_kb(product_id, wb_shops, ozon_shops, selected)
    )


@router.callback_query(F.data.startswith("shopconfirm:"), ShopPickStates.confirming)
async def shop_confirm_publish(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    """Публикует по очереди в каждый выбранный магазин (раздел 4.4 ТЗ v5) —
    не параллельно, чтобы ошибка одного магазина не мешала диагностировать
    остальные, и чтобы каждый шаг был виден отдельной строкой в чате."""
    from app.services import shops as shops_service

    product_id = int(callback.data.split(":")[1])
    await callback.answer()

    data = await state.get_data()
    selected = data.get("shoppick_selected", [])
    await state.clear()

    # Раздел 2.B ТЗ v8: «и после выбора магазинов, когда публикация реально
    # пошла» — та же полоска 15/70/100, что и в confirm_publish, вокруг всего
    # цикла по магазинам (без деления на проценты по магазинам — детальный
    # итог всё равно приходит следующим сообщением, построчно по магазинам).
    handle = await start_progress(callback.message.answer, "Публикую карточку")
    await set_progress(handle, 15, "Готовлю выкладку")
    await set_progress(handle, 70, "Отправляю на площадки")

    lines: list[str] = []
    for shop_id in selected:
        shop = shops_service.get_shop(shop_id)
        if shop is None:
            continue

        existing = await product_service.get_listing(product_id, shop_id)
        already_live = existing is not None and (existing.wb_nm_id or existing.ozon_product_id)
        if already_live:
            lines.append(texts.shop_publish_line(shop.name, "уже выложено"))
            continue

        try:
            listing = await product_service.publish_to_shop(product_id, shop_id)
        except ValueError as exc:
            lines.append(texts.shop_publish_line(shop.name, f"не выложилось — {exc}"))
            continue

        listing_id = listing.wb_nm_id or listing.ozon_product_id
        if listing.status == ListingStatus.PUBLISHED and listing_id:
            lines.append(texts.shop_publish_line(shop.name, f"готово, номер {listing_id}"))
        elif listing.status == ListingStatus.PARTIAL:
            lines.append(texts.shop_publish_line(shop.name, listing.publish_message or "карточка есть, фото не ушли"))
        else:
            lines.append(texts.shop_publish_line(shop.name, f"не выложилось — {listing.publish_message or 'ошибка'}"))

    if not lines:
        await set_progress(handle, 100, "Не получилось")
        await callback.message.answer(texts.NEED_AT_LEAST_ONE_SHOP)
        return

    await set_progress(handle, 100, "Готово")
    await callback.message.answer("\n".join(f"• {line}" for line in lines))


async def _publish_one(product_service, product_id: int) -> tuple[bool, str]:
    """Публикует один товар и возвращает короткий человекочитаемый итог — общая
    логика для одиночной и пакетной («Опубликовать все») публикации."""
    validation = await product_service.validate(product_id)
    if not validation.is_valid:
        return False, "не прошёл проверку — " + "; ".join(i.message for i in validation.errors())

    try:
        summary = await product_service.publish(product_id)
    except ValueError as exc:
        return False, str(exc)

    parts = []
    if summary.wb:
        parts.append(f"WB: {summary.wb.message}")
    if summary.ozon:
        parts.append(f"Ozon: {summary.ozon.message}")
    note = "; ".join(parts) or "опубликован"

    return summary.all_succeeded, note


@router.callback_query(F.data.startswith("publishall:"))
async def publish_all(callback: CallbackQuery, product_service) -> None:
    """«Опубликовать все» после пакетного клонирования (раздел D ТЗ) — публикует
    черновики последовательно и присылает один отчёт по каждому, вместо того
    чтобы нажимать «Опубликовать» на каждую карточку по отдельности."""
    product_ids = [int(pid) for pid in callback.data.split(":", 1)[1].split(",") if pid]
    await callback.answer()
    await callback.message.answer(f"⏳ Публикую {len(product_ids)} товар(ов)...")

    results = []
    for product_id in product_ids:
        try:
            ok, note = await _publish_one(product_service, product_id)
        except Exception as exc:
            # Раздел H.8 ТЗ: неожиданная ошибка на одном товаре не должна молча
            # оборвать пакет — остальные товары всё равно должны попасть в отчёт.
            logger.warning("Публикация товара #%s в пакете упала неожиданно", product_id, exc_info=True)
            ok, note = False, f"непредвиденная ошибка — {exc}"
        results.append((product_id, ok, note))

    await callback.message.answer(texts.publish_all_summary(results))


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_from_preview(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer(texts.CANCELLED, reply_markup=main_menu_kb())


# --- Возобновление черновика (/drafts, раздел C.5 ТЗ) -----------------------


async def resume_state_for_product(product) -> tuple[object | None, str]:
    """Определяет, с какого шага диалога /new продолжить недозаполненный черновик,
    и текст вопроса для этого шага. Возвращает (None, "") если все обязательные
    поля уже заполнены — тогда черновик можно сразу отправить на генерацию превью."""
    if product.category_id is None:
        return NewProductStates.category, texts.step(1, "Категория") + texts.ASK_CATEGORY
    if not product.title:
        return NewProductStates.title, texts.step(2, "Название") + texts.ASK_TITLE
    if not product.vendor_code:
        return NewProductStates.vendor_code, texts.step(3, "Артикул") + texts.ASK_VENDOR_CODE
    if product.cost_price is None:
        return NewProductStates.cost_price, texts.step(4, "Себестоимость") + texts.ASK_COST_PRICE
    if product.price is None:
        return NewProductStates.price, texts.step(5, "Цена") + texts.ASK_PRICE
    if not product.package_contents:
        return NewProductStates.package_contents, texts.step(7, "Комплектация") + texts.ASK_PACKAGE_CONTENTS
    if not product.material:
        return NewProductStates.material, texts.step(8, "Материал") + texts.ASK_MATERIAL
    if not product.color:
        return NewProductStates.color, texts.step(9, "Цвет") + texts.ASK_COLOR
    if not product.car_model:
        return NewProductStates.car_model, texts.step(10, "Модель авто") + texts.ASK_CAR_MODEL
    if not (product.length_mm and product.width_mm and product.height_mm):
        return NewProductStates.dimensions, texts.step(11, "Размеры") + texts.ASK_DIMENSIONS
    if not product.weight_g:
        return NewProductStates.weight, texts.step(12, "Вес") + texts.ASK_WEIGHT
    if len(product.images) < texts.MIN_PRODUCT_PHOTOS:
        return NewProductStates.photos, texts.step(13, "Фото") + texts.ASK_PHOTOS
    return None, ""
