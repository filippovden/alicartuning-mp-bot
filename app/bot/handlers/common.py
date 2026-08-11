from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import analytics, list_products, reviews
from app.bot.keyboards import (
    MENU_ANALYTICS,
    MENU_CLONE,
    MENU_LIST,
    MENU_MORE,
    MENU_NEW_PRODUCT,
    MENU_REVIEWS,
    clone_pick_kb,
    main_menu_kb,
    new_product_mode_kb,
)

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, product_service, session, state: FSMContext) -> None:
    await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    # Раздел H.7 ТЗ: повторный /start посреди диалога не должен молча обнулять
    # состояние (следующий ответ пользователя всё ещё уйдёт в старый диалог) —
    # предупреждаем и предлагаем /cancel, а не тихо ломаем или тихо продолжаем.
    text = texts.WELCOME
    if await state.get_state() is not None:
        text += texts.RESUME_DIALOG_NOTICE
    await message.answer(text, reply_markup=main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.WELCOME, reply_markup=main_menu_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED, reply_markup=main_menu_kb())


# --- Постоянное меню (раздел A ТЗ) — основной путь для обычного пользователя,
# слэш-команды остаются рабочими для тех, кто предпочитает печатать. ------------


@router.message(F.text == MENU_NEW_PRODUCT)
async def menu_new_product(message: Message) -> None:
    await message.answer(texts.NEW_PRODUCT_CHOOSE_MODE, reply_markup=new_product_mode_kb())


@router.message(F.text == MENU_LIST)
async def menu_list(message: Message, product_service) -> None:
    await list_products.cmd_list(message, product_service)


@router.message(F.text == MENU_CLONE)
async def menu_clone(message: Message, product_service) -> None:
    """Раздел A5 ТЗ: отдельный, понятный экран для клонирования — не тот же
    список, что «Мои товары», без объяснения, что тут вообще происходит."""
    user = await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    products = await product_service.list_products(user.id)
    if not products:
        await message.answer("Пока нечего клонировать. Нажмите «📦 Новый товар», чтобы создать первый.")
        return

    labels = []
    for p in products:
        label = f"#{p.id} · {p.title or 'без названия'}"
        if p.car_model:
            label += f" · {p.car_model}"
        labels.append((p.id, label))

    await message.answer("Что клонируем?", reply_markup=clone_pick_kb(labels))


@router.message(F.text == MENU_REVIEWS)
async def menu_reviews(message: Message, session) -> None:
    await reviews.cmd_reviews(message, session)


@router.message(F.text == MENU_ANALYTICS)
async def menu_analytics(message: Message, product_service, session) -> None:
    await analytics.cmd_analytics(message, product_service, session)


@router.message(F.text == MENU_MORE)
async def menu_more(message: Message) -> None:
    await message.answer(texts.MORE_MENU)


# --- Fallback на «мёртвые» кнопки (Senior Backend, п.2 ТЗ) ------------------------
#
# Этот роутер подключается В main.py ПОСЛЕДНИМ (после всех остальных), поэтому
# срабатывает только если ни один другой хендлер не забрал callback — то есть
# кнопка устарела (осталась от старого сообщения) или не подходит текущему
# состоянию FSM. Раньше в этом случае бот молчал: aiogram просто не находил
# хендлер и apdate не обрабатывался, пользователь не понимал, нажалось ли
# вообще. Порядок хендлеров внутри роутера важен: сначала конкретные случаи
# (photos_done — самая частая протухшая кнопка), потом общий catch-all.
fallback_router = Router(name="common_fallback")


@fallback_router.callback_query(F.data == "photos_done")
async def photos_done_wrong_state(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(texts.PHOTOS_NOT_ACTIVE)


@fallback_router.callback_query()
async def unhandled_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(texts.STALE_BUTTON)
