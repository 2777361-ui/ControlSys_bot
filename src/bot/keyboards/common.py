"""
Общие клавиатуры для бота.
Меню с кнопками, соответствующими командам.
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Текст кнопки «Помощь» (обрабатывается в help.py)
BTN_HELP = "Помощь"

# Текст кнопки «Плюс 1» (обрабатывается в plus_one.py)
BTN_PLUS1 = "Плюс 1"

# Кнопка чата с ИИ
BTN_CHAT = "Чат с ИИ"


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню: кнопки команд — Старт, Справка, Чат с ИИ, Плюс 1."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="/start"),
                KeyboardButton(text="/help"),
            ],
            [
                KeyboardButton(text=BTN_CHAT),
                KeyboardButton(text=BTN_PLUS1),
            ],
        ],
        resize_keyboard=True,
    )
