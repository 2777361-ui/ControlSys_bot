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
    """Токен бота — обязательная переменная для запуска (из .env или переменных окружения)."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError(
            "BOT_TOKEN не задан. Локально: создайте .env с BOT_TOKEN=.... "
            "На Amvera: задайте переменную BOT_TOKEN в настройках приложения (Environment / Переменные окружения)."
        )
    return token


# --- Платежи в боте (Telegram Stars и платёжные системы) ---

def get_payment_provider_token() -> str | None:
    """Токен платёжного провайдера (от @BotFather). Пусто — только Stars."""
    return os.getenv("PAYMENT_PROVIDER_TOKEN") or None


def get_stars_commission_percent() -> int:
    """Комиссия к сумме оплаты Stars (например 35 → родитель платит сумма + 35%)."""
    try:
        return max(0, min(100, int(os.getenv("PAYMENT_STARS_COMMISSION_PERCENT", "35"))))
    except ValueError:
        return 35


def get_stars_rub_rate() -> float:
    """Курс: сколько рублей за 1 Star (для перевода суммы в звёзды). По умолчанию ~1.5."""
    try:
        return max(0.01, float(os.getenv("PAYMENT_STARS_RUB_RATE", "1.5")))
    except ValueError:
        return 1.5


def get_payment_provider_currency() -> str:
    """Валюта для провайдера (RUB, USD и т.д.)."""
    return (os.getenv("PAYMENT_PROVIDER_CURRENCY") or "RUB").strip().upper() or "RUB"


# --- База данных (Supabase / PostgreSQL) ---

def get_database_url() -> str | None:
    """
    Строка подключения к PostgreSQL (Supabase или свой сервер).
    Если задана — используется вместо локального SQLite (data/school.db).
    Пример для Supabase: Project Settings → Database → Connection string (URI).
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return None
    # Supabase может отдавать postgres:// — psycopg2 принимает и postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[10:]
    return url
