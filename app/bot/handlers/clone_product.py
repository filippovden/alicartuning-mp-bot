"""Клонирование карточки под другую модель Lada — одна деталь автотюнинга
часто подходит нескольким моделям, и проще размножить карточку, чем заполнять
её с нуля (см. ProductService.clone_product).

Копирует категорию/бренд/материал/цвет/комплектацию/габариты/вес/цену/фото/
характеристики; НЕ копирует артикул/штрихкод/название/описание/nmID/
ozon_product_id — у клона свой SKU (спрашивается сразу в диалоге) и текст,
сгенерированный заново под новую модель авто. Кнопка «Опубликовать» в превью
появляется только после того, как артикул уже задан — публикация без него
всё равно отклонилась бы валидацией, но так пользователь не тратит на это
лишний шаг.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import confirm_publish_kb, publish_links_kb, skip_kb
from app.bot.states import CloneBatchStates, CloneProductStates

logger = logging.getLogger(__name__)
router = Router(name="clone_product")

MAX_BATCH_CLONE_MODELS = 5
SKIP_TEMPLATE_CALLBACK = "skip:clone_template"


@router.message(Command("clone"))
async def cmd_clone(message: Message, state: FSMContext, product_service) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /clone <ID товара>")
        return
    await _start_clone(message, state, product_service, int(args[1].strip()))


@router.callback_query(F.data.startswith("clone:"))
async def clone_button(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    product_id = int(callback.data.split(":")[1])
    await callback.answer()
    await _start_clone(callback.message, state, product_service, product_id)


async def _start_clone(message: Message, state: FSMContext, product_service, source_id: int) -> None:
    try:
        clone = await product_service.clone_product(source_id)
    except ValueError:
        await message.answer(texts.NOT_FOUND)
        return

    await state.set_state(CloneProductStates.car_model)
    await state.update_data(product_id=clone.id)
    await message.answer(texts.clone_created(clone.id, source_id))
    await message.answer(texts.ASK_CAR_MODEL)


@router.message(CloneProductStates.car_model)
async def clone_car_model(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    car_model = message.text.strip()
    await product_service.update_fields(data["product_id"], car_model=car_model)
    await state.set_state(CloneProductStates.vendor_code)
    await message.answer(texts.ASK_CLONE_VENDOR_CODE)


@router.message(CloneProductStates.vendor_code)
async def clone_vendor_code(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    vendor_code = message.text.strip()
    await product_service.update_fields(data["product_id"], vendor_code=vendor_code)
    await state.clear()

    await message.answer(texts.generating_preview())
    product = await product_service.generate_ai_content(data["product_id"])
    await message.answer(texts.clone_draft_preview(product), reply_markup=confirm_publish_kb(product.id))


@router.callback_query(F.data.startswith("clonebatch:"))
async def clone_batch_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[1])
    await state.set_state(CloneBatchStates.car_models)
    await state.update_data(source_product_id=product_id)
    await callback.answer()
    await callback.message.answer(texts.ask_batch_car_models(MAX_BATCH_CLONE_MODELS))


@router.message(CloneBatchStates.car_models)
async def clone_batch_models(message: Message, state: FSMContext) -> None:
    models = [m.strip() for m in message.text.split(",") if m.strip()]
    if not models:
        await message.answer("Не нашёл ни одной модели — введите через запятую, например: Vesta, Granta")
        return
    if len(models) > MAX_BATCH_CLONE_MODELS:
        await message.answer(texts.too_many_models(MAX_BATCH_CLONE_MODELS, len(models)))
        return

    await state.update_data(car_models=models)
    await state.set_state(CloneBatchStates.vendor_code_template)
    await message.answer(texts.ask_vendor_code_template(), reply_markup=skip_kb("clone_template"))


@router.callback_query(F.data == SKIP_TEMPLATE_CALLBACK)
async def clone_batch_template_skip(callback: CallbackQuery, state: FSMContext, product_service) -> None:
    await callback.answer()
    data = await state.get_data()
    await _create_batch_clones(callback.message, state, product_service, data["source_product_id"], data["car_models"], None)


@router.message(CloneBatchStates.vendor_code_template)
async def clone_batch_template(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    template = message.text.strip() or None
    await _create_batch_clones(message, state, product_service, data["source_product_id"], data["car_models"], template)


def _build_vendor_code(template: str | None, car_model: str, clone_id: int) -> str:
    model_token = car_model.strip().upper().replace(" ", "")
    if not template:
        return f"ART-CLONE-{clone_id}"
    if "{model}" in template:
        return template.replace("{model}", model_token)
    # В шаблоне забыли {model} — добавляем модель сами, иначе все клоны в
    # пакете получили бы один и тот же артикул и не прошли бы валидацию.
    return f"{template}-{model_token}"


async def _create_batch_clones(
    message: Message,
    state: FSMContext,
    product_service,
    source_id: int,
    models: list[str],
    template: str | None,
) -> None:
    await state.clear()
    await message.answer(f"⏳ Создаю {len(models)} черновик(ов)...")

    created = []
    for car_model in models:
        try:
            clone = await product_service.clone_product(source_id)
            vendor_code = _build_vendor_code(template, car_model, clone.id)
            await product_service.update_fields(clone.id, car_model=car_model, vendor_code=vendor_code)
            product = await product_service.generate_ai_content(clone.id)
            created.append(product)
        except Exception:
            logger.warning(
                "Не удалось создать клон для модели «%s» (источник #%s)", car_model, source_id, exc_info=True
            )
            await message.answer(f"⚠️ Не удалось создать черновик для «{car_model}».")

    if not created:
        await message.answer("Не удалось создать ни одного черновика.")
        return

    await message.answer(texts.batch_clone_summary(created), reply_markup=publish_links_kb([p.id for p in created]))
