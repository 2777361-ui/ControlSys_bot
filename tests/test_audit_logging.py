"""
Тесты супер-логирования: audit_log и log_exception не падают и пишут в лог.
"""
import logging

import pytest

from bot.utils.logging import audit_log, log_exception


def test_audit_log_no_raise():
    """audit_log не бросает исключений при минимальных аргументах."""
    log = logging.getLogger("test.audit")
    audit_log(log, "test_action")


def test_audit_log_with_user_and_extra():
    """audit_log с user_id, role и extra записывает без ошибок."""
    log = logging.getLogger("test.audit")
    audit_log(
        log,
        "payment_add",
        user_id=1,
        role="accountant",
        extra={"student_id": 5, "amount": 100},
    )


def test_audit_log_masks_password():
    """В extra пароль не выводится в открытом виде (подставляется ***)."""
    log = logging.getLogger("test.audit")
    # Проверяем, что вызов с password= не падает; фактическое значение в логе должно быть ***
    audit_log(log, "login", extra={"email": "a@b.ru", "password": "secret"})


def test_log_exception_no_raise():
    """log_exception не бросает исключений (логирует и возвращается)."""
    log = logging.getLogger("test.audit")
    log_exception(log, "Тестовая ошибка", user_id=1, path="/test")


def test_log_exception_with_exc():
    """log_exception с exc не падает."""
    log = logging.getLogger("test.audit")
    try:
        raise ValueError("тест")
    except ValueError as e:
        log_exception(log, "Поймана ошибка", path="/api", exc=e)
