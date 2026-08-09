from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import texts
from app.bot.handlers import analytics, list_products, reviews
from app.bot.keyboards import MENU_ANALYTICS, MENU_CLONE, MENU_LIST, MENU_MORE, MENU_NEW_PRODUCT, MENU_REVIEWS, main_menu_kb, new_product_mode_kb

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, product_service, session) -> None:
    await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    await message.answer(texts.WELCOME, reply_markup=main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.WELCOME, reply_markup=main_menu_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED)


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
    await list_products.cmd_list(message, product_service)


@router.message(F.text == MENU_REVIEWS)
async def menu_reviews(message: Message, session) -> None:
    await reviews.cmd_reviews(message, session)


@router.message(F.text == MENU_ANALYTICS)
async def menu_analytics(message: Message, product_service, session) -> None:
    await analytics.cmd_analytics(message, product_service, session)


@router.message(F.text == MENU_MORE)
async def menu_more(message: Message) -> None:
    await message.answer(texts.MORE_MENU)
