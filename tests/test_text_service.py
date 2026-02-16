"""
Тесты сервиса текста (эхо-логика).
Проверяем, что echo_text возвращает переданный текст или подсказку для пустоты.
"""
import pytest

from bot.services.text import echo_text


def test_echo_returns_same_text() -> None:
    """Обычный текст возвращается без изменений."""
    assert echo_text("Привет") == "Привет"
    assert echo_text("  пробелы  ") == "пробелы"


def test_echo_empty_returns_hint() -> None:
    """Пустая или только пробелы — подсказка."""
    assert "повторю" in echo_text("")
    assert "повторю" in echo_text("   ")


def test_echo_none_like_returns_hint() -> None:
    """Пустая строка после strip даёт подсказку."""
    assert "повторю" in echo_text("\t\n ")
