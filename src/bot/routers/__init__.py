"""
Роутеры бота: команды и обработчики сообщений.
Подключаются в main.py.
"""
from aiogram import Router

from . import echo, school, start, tasks
from . import help as help_router  # help — встроенная функция Python, импортируем модуль с псевдонимом
from . import plus_one

# Общий роутер: порядок важен. school первым обрабатывает /start (родитель/админ/гость)
router = Router(name="main")
router.include_router(school.router)
router.include_router(tasks.router)  # Текущие дела — для сотрудников с доступом
router.include_router(start.router)
router.include_router(help_router.router)
router.include_router(plus_one.router)
router.include_router(echo.router)
