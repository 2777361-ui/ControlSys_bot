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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import get_token
from bot.services.reminders import send_payment_reminders
from bot.services.broadcast import process_broadcast_queue
from bot.services.nutrition_deductions import run_daily_nutrition_deductions
from bot.services.education_charges import run_monthly_education_charges
from bot.school_db import close_db as close_school_db, init_db as init_school_db
from bot.middleware.auth import SchoolAuthMiddleware
from bot.routers import router
from bot.utils.logging import setup_logging

# Логирование включаем при запуске
setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    """Запуск бота: создаём Bot и Dispatcher, подключаем роутеры, запускаем polling."""

    # --- Инициализация базы данных школы ---
    init_school_db()
    logger.info("="*60)
    logger.info("БОТ ЗАПУСКАЕТСЯ")
    logger.info("="*60)

    token = get_token()
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(SchoolAuthMiddleware())
    dp.callback_query.middleware(SchoolAuthMiddleware())
    dp.include_router(router)

    # Удаляем вебхук, если был (чтобы работал polling)
    await bot.delete_webhook(drop_pending_updates=True)

    # Меню команд: показывается при нажатии «/» в чате
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Приветствие и меню"),
            BotCommand(command="help", description="Справка по командам"),
            BotCommand(command="plus1", description="Прибавить 1 к числу"),
        ]
    )

    # Планировщик: напоминания о платежах с 1 по 10 число каждого месяца (9:00 МСК)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_payment_reminders, "cron", hour=6, minute=0, args=[bot])  # 6:00 UTC = 9:00 МСК
    scheduler.add_job(process_broadcast_queue, "interval", seconds=30, args=[bot])
    scheduler.add_job(run_daily_nutrition_deductions, "cron", hour=1, minute=0)
    scheduler.add_job(run_monthly_education_charges, "cron", day=1, hour=2, minute=0)  # 1-го числа каждого месяца в 02:00 (кроме августа — внутри не начисляем)
    scheduler.start()
    logger.info("Планировщик напоминаний о платежах запущен (ежедневно 9:00 МСК)")
    logger.info("Планировщик рассылок запущен (каждые 30 сек)")

    # Получаем информацию о боте (имя, username)
    bot_info = await bot.get_me()
    logger.info("Бот: @%s (id=%s)", bot_info.username, bot_info.id)
    logger.info("Polling запущен — бот готов принимать сообщения")

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал остановки")
    finally:
        scheduler.shutdown(wait=False)
        # --- Остановка: закрываем БД школы и логируем ---
        close_school_db()
        logger.info("="*60)
        logger.info("БОТ ОСТАНОВЛЕН")
        logger.info("="*60)


if __name__ == "__main__":
    asyncio.run(main())
