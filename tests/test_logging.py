"""
Тесты утилиты логирования (setup_logging).
Проверяем, что функция не падает и выставляет уровень.
"""
import logging

from bot.utils.logging import setup_logging


def test_setup_logging_does_not_raise() -> None:
    """Вызов setup_logging не вызывает исключений."""
    setup_logging(level=logging.WARNING)


def test_setup_logging_sets_level() -> None:
    """После вызова корневой логгер имеет заданный уровень."""
    setup_logging(level=logging.DEBUG)
    assert logging.getLogger().level == logging.DEBUG
