"""
Обработчик команды /start.
Приветствует пользователя и показывает главную клавиатуру.
"""
from aiogram import Router, F
from aiogram.types import Message

from bot.keyboards.common import get_main_keyboard

router = Router(name="start")

WELCOME = (
    "Привет! Я эхобот — повторяю всё, что ты напишешь.\n\n"
    "Меню команд: нажми «/» рядом с полем ввода или используй кнопки ниже.\n"
    "• /start — приветствие\n"
    "• /help — справка\n"
    "• /chat — чат с ИИ (OpenRouter)\n"
    "• /plus1 — прибавить 1 к числу"
)


@router.message(F.text == "/start")
async def cmd_start(message: Message) -> None:
    """На /start отправляем приветствие и клавиатуру."""
    await message.answer(WELCOME, reply_markup=get_main_keyboard())
