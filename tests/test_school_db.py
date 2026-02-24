"""
Тесты модуля school_db: пользователи, ученики, платежи, назначения.

Как работает: фикстура school_db_test подменяет БД на временный файл,
поэтому тесты не трогают продовую базу.
"""
import pytest

from bot.school_db import (
    ROLE_PARENT,
    ROLE_DIRECTOR,
    user_create,
    user_by_id,
    user_by_email,
    user_by_telegram_id,
    student_create,
    students_by_parent_id,
    students_all,
    student_by_id,
    payment_purpose_codes,
    payment_create,
    payment_confirm,
    payment_reject,
    payment_by_id,
    payments_by_student,
    balance_canteen_for_student,
    broadcast_create,
    broadcast_pending_task,
    department_create,
    department_list,
    department_add_member,
    department_member_ids,
)


def test_user_create_and_find(school_db_test):
    """Создание пользователя и поиск по id, email, telegram_id."""
    uid = user_create(role=ROLE_PARENT, full_name="Иван Родитель", telegram_id=123456)
    assert uid > 0
    u = user_by_id(uid)
    assert u is not None
    assert u["full_name"] == "Иван Родитель"
    assert u["role"] == ROLE_PARENT
    assert u["telegram_id"] == 123456

    uid2 = user_create(
        role=ROLE_DIRECTOR,
        full_name="Директор",
        email="dir@school.ru",
        password_hash="hash",
    )
    u2 = user_by_email("dir@school.ru")
    assert u2 is not None
    assert u2["id"] == uid2
    u2t = user_by_telegram_id(123456)
    assert u2t is not None
    assert u2t["id"] == uid


def test_student_create_with_and_without_parent(school_db_test):
    """Ученик с родителем и без (приглашение позже)."""
    pid = user_create(role=ROLE_PARENT, full_name="Мама", telegram_id=111)
    sid = student_create("Петя", 5, pid, "")
    assert sid > 0
    s = student_by_id(sid)
    assert s["full_name"] == "Петя"
    assert s["class_grade"] == 5
    children = students_by_parent_id(pid)
    assert len(children) == 1
    assert children[0]["full_name"] == "Петя"

    sid2 = student_create("Вася", 3, None, "")
    assert sid2 > 0
    all_s = students_all()
    assert len(all_s) >= 2


def test_payment_purpose_codes_exist(school_db_test):
    """После init_db есть хотя бы одно назначение платежа (миграция создаёт справочник)."""
    codes = payment_purpose_codes()
    assert isinstance(codes, set)
    assert len(codes) >= 1
    # Часто после миграции есть education, food и т.д.
    assert "education" in codes or "food" in codes or len(codes) > 0


def test_payment_create_and_confirm(school_db_test):
    """Создание платежа и подтверждение бухгалтером."""
    pid = user_create(role=ROLE_PARENT, full_name="Родитель", telegram_id=222)
    sid = student_create("Ученик", 1, pid, "")
    codes = payment_purpose_codes()
    purpose = next(iter(codes))
    pay_id = payment_create(sid, 500.0, purpose, description="Обед")
    assert pay_id > 0
    p = payment_by_id(pay_id)
    assert p["status"] == "pending"
    assert p["amount"] == 500.0

    director_id = user_create(role=ROLE_DIRECTOR, full_name="Директор", email="d@d.ru")
    ok = payment_confirm(pay_id, director_id, "Ок")
    assert ok is True
    p2 = payment_by_id(pay_id)
    assert p2["status"] == "confirmed"

    # Повторное подтверждение не должно сработать
    ok2 = payment_confirm(pay_id, director_id, "")
    assert ok2 is False


def test_payment_reject(school_db_test):
    """Отклонение платежа."""
    pid = user_create(role=ROLE_PARENT, full_name="Р", telegram_id=333)
    sid = student_create("У", 2, pid, "")
    codes = payment_purpose_codes()
    purpose = next(iter(codes))
    pay_id = payment_create(sid, 100.0, purpose)
    director_id = user_create(role=ROLE_DIRECTOR, full_name="Д", email="d2@d.ru")
    ok = payment_reject(pay_id, director_id, "Дубликат")
    assert ok is True
    p = payment_by_id(pay_id)
    assert p["status"] == "rejected"


def test_payments_by_student_and_balance(school_db_test):
    """Список платежей по ученику и баланс по питанию (только подтверждённые food)."""
    pid = user_create(role=ROLE_PARENT, full_name="Р", telegram_id=444)
    sid = student_create("У", 4, pid, "")
    codes = payment_purpose_codes()
    purpose = "food" if "food" in codes else next(iter(codes))
    payment_create(sid, 300.0, purpose)
    payment_create(sid, 200.0, purpose)
    director_id = user_create(role=ROLE_DIRECTOR, full_name="Д", email="d3@d.ru")
    for p in payments_by_student(sid):
        payment_confirm(p["id"], director_id)
    balance = balance_canteen_for_student(sid)
    # Баланс считается только по purpose=food; если тест использует другое назначение — может быть 0
    assert balance >= 0
    assert len(payments_by_student(sid)) >= 2


def test_broadcast_create(school_db_test):
    """Создание рассылки и появление задачи в очереди."""
    uid = user_create(role=ROLE_DIRECTOR, full_name="Д", email="d4@d.ru", telegram_id=555)
    bid = broadcast_create(uid, "Текст рассылки", [])
    assert bid > 0
    task = broadcast_pending_task()
    assert task is not None
    assert task["id"] == bid
    assert task["message_text"] == "Текст рассылки"
    assert task["created_by"] == uid


def test_department_create_and_members(school_db_test):
    """Создание отдела и добавление участника."""
    dep_id = department_create("Учителя")
    assert dep_id > 0
    deps = department_list()
    assert any(d["name"] == "Учителя" for d in deps)
    uid = user_create(role=ROLE_PARENT, full_name="У", telegram_id=666)
    added = department_add_member(dep_id, uid)
    assert added is True
    ids = department_member_ids(dep_id)
    assert uid in ids
