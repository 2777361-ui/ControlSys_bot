"""
Роутеры бота: команды и обработчики сообщений.
Подключаются в main.py.
"""
from aiogram import Router

from . import chat, echo, start
from . import help as help_router  # help — встроенная функция Python, импортируем модуль с псевдонимом
from . import plus_one

# Общий роутер: порядок важен — chat до echo (в чате текст уходит в LLM)
router = Router(name="main")
router.include_router(start.router)
router.include_router(help_router.router)
router.include_router(chat.router)
router.include_router(plus_one.router)
router.include_router(echo.router)
