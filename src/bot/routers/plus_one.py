"""
Команда /plus1 и ответ «число + 1» на ввод числа.
Если пользователь вводит число — бот отвечает числом на 1 больше.
"""
from aiogram import Router, F
from aiogram.types import Message

from bot.keyboards.common import BTN_PLUS1
from bot.services.numbers import add_one_to_number

router = Router(name="plus_one")

PLUS1_HINT = (
    "Введи число после команды, например: /plus1 5\n"
    "Или просто отправь число — я прибавлю 1."
)

# Целое число (опционально с минусом)
NUMBER_PATTERN = r"^-?\d+$"
# Команда /plus1 с числом в том же сообщении
CMD_WITH_NUMBER = r"^/plus1\s+(-?\d+)$"


@router.message(F.text.regexp(CMD_WITH_NUMBER))
async def cmd_plus_one_with_number(message: Message) -> None:
    """Команда вида /plus1 5 — отвечаем 6."""
    match = message.text and message.text.strip()
    if not match:
        return
    # Достаём число после /plus1
    parts = match.split()
    if len(parts) >= 2:
        num_str = parts[1]
        reply = add_one_to_number(num_str)
        await message.answer(reply)


@router.message(F.text == "/plus1")
@router.message(F.text == BTN_PLUS1)
async def cmd_plus_one_no_number(message: Message) -> None:
    """Команда /plus1 или кнопка «Плюс 1» — подсказка."""
    await message.answer(PLUS1_HINT)


@router.message(F.text.regexp(NUMBER_PATTERN))
async def message_is_number(message: Message) -> None:
    """Сообщение — просто число (например 5) — отвечаем числом +1."""
    reply = add_one_to_number(message.text or "")
    await message.answer(reply)
