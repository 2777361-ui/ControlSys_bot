"""
Эхо-обработчик: любое текстовое сообщение возвращается пользователю.
Команды /start и /help обрабатываются другими роутерами.
"""
from aiogram import Router, F
from aiogram.types import Message

from bot.services.text import echo_text

router = Router(name="echo")


@router.message(F.text)
async def echo_message(message: Message) -> None:
    """
    На любое текстовое сообщение отвечаем эхом.
    Текст готовит сервис echo_text (пустые сообщения — подсказка).
    """
    reply = echo_text(message.text or "")
    await message.answer(reply)
