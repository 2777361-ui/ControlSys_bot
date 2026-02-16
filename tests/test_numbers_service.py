"""
Тесты сервиса чисел (add_one_to_number).
Проверяем прибавление 1 к целым числам и подсказку при неверном вводе.
"""
import pytest

from bot.services.numbers import NOT_A_NUMBER, add_one_to_number


def test_add_one_positive() -> None:
    """Положительное число — результат на 1 больше."""
    assert add_one_to_number("0") == "1"
    assert add_one_to_number("5") == "6"
    assert add_one_to_number("99") == "100"


def test_add_one_negative() -> None:
    """Отрицательное число — результат на 1 больше (ближе к нулю)."""
    assert add_one_to_number("-1") == "0"
    assert add_one_to_number("-10") == "-9"


def test_add_one_with_spaces() -> None:
    """Пробелы по краям обрезаются, число обрабатывается."""
    assert add_one_to_number("  7  ") == "8"
    assert add_one_to_number("\t42\n") == "43"


def test_add_one_empty_returns_hint() -> None:
    """Пустая строка или только пробелы — подсказка."""
    assert add_one_to_number("") == NOT_A_NUMBER
    assert add_one_to_number("   ") == NOT_A_NUMBER
    assert add_one_to_number("\t\n ") == NOT_A_NUMBER


def test_add_one_none_returns_hint() -> None:
    """None — подсказка."""
    assert add_one_to_number(None) == NOT_A_NUMBER


def test_add_one_invalid_returns_hint() -> None:
    """Не число — подсказка."""
    assert add_one_to_number("abc") == NOT_A_NUMBER
    assert add_one_to_number("12.5") == NOT_A_NUMBER
    assert add_one_to_number("5 овец") == NOT_A_NUMBER
    assert add_one_to_number("5 ") == "6"  # пробел в конце — ок после strip


def test_not_a_number_constant() -> None:
    """Подсказка содержит понятный текст."""
    assert "число" in NOT_A_NUMBER.lower()
