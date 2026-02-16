"""
Общие клавиатуры для бота.

Как это работает (для 5-классника):
  - ReplyKeyboardMarkup — кнопки ВНИЗУ экрана (под полем ввода).
  - InlineKeyboardMarkup — кнопки ВНУТРИ сообщения (нажимаешь — бот получает callback).
  - Когда пользователь нажимает /chat, появляется инлайн-меню с тремя режимами.
"""
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Текст кнопки «Помощь» (обрабатывается в help.py)
BTN_HELP = "Помощь"

# Текст кнопки «Плюс 1» (обрабатывается в plus_one.py)
BTN_PLUS1 = "Плюс 1"

# Кнопка чата с ИИ
BTN_CHAT = "Чат с ИИ"

# --- Идентификаторы режимов чата (callback_data) ---
MODE_ASSISTANT = "chat_mode:assistant"
MODE_ASCII = "chat_mode:ascii"
MODE_TRANSLATE = "chat_mode:translate"


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


def get_chat_mode_keyboard() -> InlineKeyboardMarkup:
    """
    Инлайн-клавиатура выбора режима чата с ИИ.
    Три кнопки — три режима работы.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ассистент", callback_data=MODE_ASSISTANT)],
            [InlineKeyboardButton(text="🎨 ASCII-арт", callback_data=MODE_ASCII)],
            [InlineKeyboardButton(text="🌐 Переводчик (RU → EN)", callback_data=MODE_TRANSLATE)],
        ]
    )
