"""
Настройка логирования для бота.
Логи идут в консоль с уровнем INFO.
"""
import logging

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Включает логирование с заданным уровнем."""
    logging.basicConfig(level=level, format=LOG_FORMAT)
