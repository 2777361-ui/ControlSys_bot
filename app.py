"""
Точка входа для деплоя на Amvera.
Запускает Telegram-бота в режиме polling (бот сам опрашивает сервер Telegram).
"""
import asyncio

from src.bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
