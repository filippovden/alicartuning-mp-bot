"""Клонирование карточки под другую модель Lada — одна деталь автотюнинга
часто подходит нескольким моделям, и проще размножить карточку, чем заполнять
её с нуля (см. ProductService.clone_product).

Копирует категорию/бренд/материал/цвет/комплектацию/габариты/вес/цену/фото;
НЕ копирует артикул/штрихкод/название/описание/nmID/ozon_product_id — у
клона свой SKU (заполняется через /edit) и текст, сгенерированный заново под
новую модель авто.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards import confirm_publish_kb, publish_links_kb
from app.bot.states import CloneBatchStates, CloneProductStates

logger = logging.getLogger(__name__)
router = Router(name="clone_product")

MAX_BATCH_CLONE_MODELS = 5


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
    await state.clear()

    await message.answer(texts.generating_preview())
    product = await product_service.generate_ai_content(data["product_id"])
    await message.answer(
        texts.draft_preview(
            product.title,
            product.description,
            float(product.price) if product.price else None,
            float(product.cost_price) if product.cost_price else None,
        ),
        reply_markup=confirm_publish_kb(product.id),
    )


@router.callback_query(F.data.startswith("clonebatch:"))
async def clone_batch_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[1])
    await state.set_state(CloneBatchStates.car_models)
    await state.update_data(source_product_id=product_id)
    await callback.answer()
    await callback.message.answer(texts.ask_batch_car_models(MAX_BATCH_CLONE_MODELS))


@router.message(CloneBatchStates.car_models)
async def clone_batch_models(message: Message, state: FSMContext, product_service) -> None:
    data = await state.get_data()
    source_id = data["source_product_id"]

    models = [m.strip() for m in message.text.split(",") if m.strip()]
    if not models:
        await message.answer("Не нашёл ни одной модели — введите через запятую, например: Vesta, Granta")
        return
    if len(models) > MAX_BATCH_CLONE_MODELS:
        await message.answer(texts.too_many_models(MAX_BATCH_CLONE_MODELS, len(models)))
        return

    await state.clear()
    await message.answer(f"⏳ Создаю {len(models)} черновик(ов)...")

    created = []
    for car_model in models:
        try:
            clone = await product_service.clone_product(source_id)
            await product_service.update_fields(clone.id, car_model=car_model)
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
