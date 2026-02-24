"""
Суперлогирование для бота.

Как это работает (для 5-классника):
  - Логи — это записи о том, что происходит внутри бота (как дневник).
  - Мы пишем их одновременно в два места:
    1) В терминал (консоль) — чтобы видеть прямо сейчас.
    2) В файл logs/bot.log — чтобы потом открыть и посмотреть историю.
  - Каждая запись содержит: дату, время, уровень важности, модуль и само сообщение.
"""
import logging
import sys
from pathlib import Path

# Подробный формат: дата-время | уровень | модуль:строка | сообщение
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Папка для лог-файлов — в корне проекта
_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_LOG_FILE = _LOG_DIR / "bot.log"


def setup_logging(level: int = logging.DEBUG) -> None:
    """
    Настраивает логирование:
      - В терминал (stdout) с уровнем INFO.
      - В файл logs/bot.log с уровнем DEBUG (самый подробный).

    Формат: дата-время | уровень | модуль:строка | сообщение
    """
    # Создаём папку для логов, если её нет
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Корневой логгер — ловит всё
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Убираем старые обработчики (если setup_logging вызвали повторно)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # --- Обработчик 1: терминал (stdout) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # --- Обработчик 2: файл (с ротацией по размеру) ---
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        str(_LOG_FILE),
        maxBytes=5 * 1024 * 1024,  # 5 МБ максимум на один файл
        backupCount=3,              # хранить до 3 старых файлов
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Приглушаем слишком болтливые библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    root_logger.info("Логирование настроено: терминал=INFO, файл=%s=DEBUG", _LOG_FILE)


# --- Супер-логирование: кто что нажал / что сделал (аудит и отладка) ---

def audit_log(
    logger: logging.Logger,
    action: str,
    *,
    user_id: int | None = None,
    role: str | None = None,
    extra: dict | None = None,
    message: str = "",
) -> None:
    """
    Записать действие в лог в едином формате: кто (user_id, role), что сделал (action), доп. данные (extra).
    Так в логах видно, кто что нажимал и где что сломалось.
    """
    parts = [f"action={action}"]
    if user_id is not None:
        parts.append(f"user_id={user_id}")
    if role:
        parts.append(f"role={role}")
    if extra:
        for k, v in extra.items():
            if k in ("password", "password_hash", "token"):
                parts.append(f"{k}=***")
            else:
                parts.append(f"{k}={v}")
    line = " | ".join(parts)
    if message:
        line = f"{line} | {message}"
    logger.info("AUDIT | %s", line)


def log_exception(
    logger: logging.Logger,
    message: str,
    *,
    user_id: int | None = None,
    path: str | None = None,
    exc: BaseException | None = None,
) -> None:
    """Записать ошибку с контекстом (кто, какой путь), чтобы понять, где сломалось."""
    ctx = []
    if user_id is not None:
        ctx.append(f"user_id={user_id}")
    if path:
        ctx.append(f"path={path}")
    prefix = " | ".join(ctx) + " | " if ctx else ""
    logger.exception("%s%s", prefix, message, exc_info=exc is not None)
