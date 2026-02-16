"""
Загрузка настроек из переменных окружения.
Все секреты и параметры бота берутся из .env.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта (родитель папки src)
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)


def get_token() -> str:
    """Токен бота — обязательная переменная для запуска."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не задан в .env")
    return token


def get_openrouter_api_key() -> str | None:
    """Ключ OpenRouter для режима чата с ИИ. Если не задан — /chat не работает."""
    return os.getenv("OPENROUTER_API_KEY") or None
