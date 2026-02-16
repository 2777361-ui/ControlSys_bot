"""
Единственная точка запуска бота: инициализация и polling.
Роутеры подключаются здесь.
"""
import asyncio
import logging
import sys
from pathlib import Path

# При прямом запуске main.py папка src должна быть в пути, чтобы находился модуль bot
_src_dir = Path(__file__).resolve().parent.parent
if _src_dir not in sys.path:
    sys.path.insert(0, str(_src_dir))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import get_token
from bot.database import close_db, init_db
from bot.routers import router
from bot.utils.logging import setup_logging

# Логирование включаем при запуске
setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    """Запуск бота: создаём Bot и Dispatcher, подключаем роутеры, запускаем polling."""

    # --- Инициализация базы данных ---
    init_db()
    logger.info("="*60)
    logger.info("БОТ ЗАПУСКАЕТСЯ")
    logger.info("="*60)

    token = get_token()
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Удаляем вебхук, если был (чтобы работал polling)
    await bot.delete_webhook(drop_pending_updates=True)

    # Меню команд: показывается при нажатии «/» в чате
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Приветствие и меню"),
            BotCommand(command="help", description="Справка по командам"),
            BotCommand(command="chat", description="Чат с ИИ (OpenRouter)"),
            BotCommand(command="plus1", description="Прибавить 1 к числу"),
            BotCommand(command="exit", description="Выход из чата с ИИ"),
        ]
    )

    # Получаем информацию о боте (имя, username)
    bot_info = await bot.get_me()
    logger.info("Бот: @%s (id=%s)", bot_info.username, bot_info.id)
    logger.info("Polling запущен — бот готов принимать сообщения")

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал остановки")
    finally:
        # --- Остановка: закрываем БД и логируем ---
        close_db()
        logger.info("="*60)
        logger.info("БОТ ОСТАНОВЛЕН")
        logger.info("="*60)


if __name__ == "__main__":
    asyncio.run(main())
