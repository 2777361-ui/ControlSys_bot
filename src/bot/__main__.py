"""
Позволяет запускать бота командой: python -m bot
из папки src (или с правильным PYTHONPATH).
"""
from bot.main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
