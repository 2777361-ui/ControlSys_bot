"""
Эхо-обработчик: любое текстовое сообщение возвращается пользователю.
Команды /start и /help обрабатываются другими роутерами.
"""
import logging

from aiogram import Router, F
from aiogram.types import Message

from bot.services.text import echo_text

router = Router(name="echo")
logger = logging.getLogger(__name__)


@router.message(F.text)
async def echo_message(message: Message) -> None:
    """
    На любое текстовое сообщение отвечаем эхом.
    Текст готовит сервис echo_text (пустые сообщения — подсказка).
    """
    user = message.from_user
    logger.info(
        "[ECHO] user_id=%s @%s — текст: %s",
        user.id if user else "?",
        user.username if user else "?",
        (message.text or "")[:200],
    )
    reply = echo_text(message.text or "")
    await message.answer(reply)
