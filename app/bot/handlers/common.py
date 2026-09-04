from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import admin, analytics, list_products, quick_create, reviews
from app.bot.keyboards import (
    MENU_CLONE,
    MENU_CLONE_OLD,
    MENU_HELP,
    MENU_HELP_OLD,
    MENU_LIST,
    MENU_NEW_PRODUCT,
    MENU_REVIEWS,
    MENU_SALES,
    MENU_SALES_OLD,
    clone_pick_kb,
    help_kb,
    main_menu_kb,
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


@router.message(F.text.lower() == "отмена")
async def text_cancel(message: Message, state: FSMContext) -> None:
    """Текстовый синоним /cancel — раздел 5 ТЗ v6: заказчик не обязан помнить
    слэш-команды, чтобы выйти из текущего шага."""
    await cmd_cancel(message, state)


# --- Постоянное меню (раздел A ТЗ) — основной путь для обычного пользователя,
# слэш-команды остаются рабочими для тех, кто предпочитает печатать. ------------


@router.message(F.text == MENU_NEW_PRODUCT)
async def menu_new_product(message: Message, state: FSMContext, product_service) -> None:
    """«📦 Новый товар» — раздел 1 ТЗ v7: сразу быстрый режим (фото), без
    развилки Быстро/Пошагово — она перегружала первый шаг лишним вопросом.
    Пошаговый режим остаётся рабочим по /new для тех, кто печатает команды."""
    await quick_create.start_quick_mode_flow(
        state,
        product_service,
        message.answer,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )


@router.message(F.text == MENU_LIST)
async def menu_list(message: Message, product_service) -> None:
    await list_products.cmd_list(message, product_service)


@router.message(F.text.in_({MENU_CLONE, MENU_CLONE_OLD}))
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


@router.message(F.text.in_({MENU_SALES, MENU_SALES_OLD}))
async def menu_sales(message: Message, product_service, session) -> None:
    """«📊 Продажи» — раздел 1 и 5 ТЗ v7: аналитика кабинета (WB Statistics +
    Ozon Analytics), без обращения к витрине search.wb.ru с этого экрана —
    /analytics без ID (см. analytics.cmd_analytics) ровно то и делает."""
    await analytics.cmd_analytics(message, product_service, session)


@router.message(F.text.in_({MENU_HELP, MENU_HELP_OLD}))
async def menu_help(message: Message) -> None:
    await message.answer(texts.HELP_TEXT, reply_markup=help_kb())


@router.callback_query(F.data == "helpcancel")
async def help_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """«Начать заново» на экране «Помощь» — тот же код, что /cancel (раздел 2 ТЗ v7)."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(texts.CANCELLED, reply_markup=main_menu_kb())


@router.callback_query(F.data == "helpdrafts")
async def help_drafts(callback: CallbackQuery, product_service) -> None:
    """«Черновики» на экране «Помощь» — тот же код, что /drafts (раздел 2 ТЗ v7)."""
    await callback.answer()
    await list_products.send_drafts(callback.message.answer, callback.from_user, product_service)


@router.callback_query(F.data == "helpsynccategories")
async def help_sync_categories(callback: CallbackQuery, session) -> None:
    """«Категории Ozon» на экране «Помощь» — тот же код, что /synccategories,
    админская операция под кнопкой (раздел 2 ТЗ v7): админу можно, не-админу —
    честная фраза, а не «доступна только администраторам» (это делает тот,
    кто ставил бота, а не сам заказчик)."""
    await callback.answer()
    if not admin.is_admin_id(callback.from_user.id):
        await callback.message.answer("Это делает тот, кто ставил бота.")
        return
    await admin.sync_categories(callback.message.answer, session)


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
