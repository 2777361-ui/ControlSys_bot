"""
Обработчик команды /help и кнопки «Помощь».
"""
import logging

from aiogram import Router, F
from aiogram.types import Message

from bot.keyboards.common import BTN_HELP

router = Router(name="help")
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Я эхобот: просто напиши любое сообщение — и я отправлю его обратно.\n\n"
    "Меню команд (кнопка «/» или кнопки под полем ввода):\n"
    "• /start — приветствие и меню\n"
    "• /help — эта справка\n"
    "• /chat — режим чата с ИИ (ответы через OpenRouter), выход: /exit\n"
    "• /plus1 — прибавить 1 к числу (напиши число или /plus1 5)"
)


@router.message(F.text == "/help")
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message) -> None:
    """На /help или кнопку «Помощь» отвечаем текстом справки."""
    user = message.from_user
    logger.info(
        "[HELP] user_id=%s @%s — /help",
        user.id if user else "?",
        user.username if user else "?",
    )
    await message.answer(HELP_TEXT)
