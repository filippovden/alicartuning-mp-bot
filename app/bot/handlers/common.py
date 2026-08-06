from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import texts

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, product_service, session) -> None:
    await product_service.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    await message.answer(texts.WELCOME)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.WELCOME)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED)
