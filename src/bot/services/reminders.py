"""
Напоминания о платежах по расписанию:

- Обычные месяцы: посты 3, 7 и 10 числа.
- Январь: посты 10, 13 и 15 числа.
- Июль и август: напоминаний нет.
- Если по ученику нет платежа за месяц: дополнительно 11 число, затем каждые 3 дня
  (11, 14, 17, 20, 23, 26, 29…) до конца месяца, пока не появится платёж.
"""
import calendar
import logging
from datetime import datetime

from aiogram import Bot

from bot.school_db import (
    balance_canteen_for_student,
    format_class_grade,
    has_food_payment_in_month,
    parents_by_student_id,
    reminder_mark_sent_on_date,
    reminder_was_sent_on_date,
    students_all,
)

logger = logging.getLogger(__name__)


def _allowed_days_for_month(year: int, month: int) -> tuple[list[int], list[int]]:
    """
    Возвращает (базовые_дни, дни_эскалации_если_нет_платежа).
    Июль (7) и август (8): ([], []).
    Январь: ([10, 13, 15], [11, 14, 17, 20, 23, 26, 29] + до конца месяца каждые 3 дня).
    Остальные: ([3, 7, 10], [11, 14, 17, 20, 23, 26, 29] + до конца месяца).
    """
    if month in (7, 8):
        return [], []

    last_day = calendar.monthrange(year, month)[1]
    escalation = []
    d = 11
    while d <= last_day:
        escalation.append(d)
        d += 3

    if month == 1:
        return [10, 13, 15], escalation
    return [3, 7, 10], escalation


def _should_send_today(student_id: int, year: int, month: int, day: int) -> bool:
    """Нужно ли сегодня отправить напоминание этому ученику."""
    base_days, escalation_days = _allowed_days_for_month(year, month)
    if not base_days and not escalation_days:
        return False

    if day in base_days:
        return True
    if day in escalation_days and not has_food_payment_in_month(student_id, year, month):
        return True
    return False


async def send_payment_reminders(bot: Bot) -> None:
    """
    Отправляет напоминания по расписанию: 3, 7, 10 (январь: 10, 13, 15);
    июль/август — пропуск; при отсутствии платежа — 11 и каждые 3 дня.
    """
    now = datetime.utcnow()
    year, month, day = now.year, now.month, now.day
    sent_date = now.strftime("%Y-%m-%d")

    base_days, escalation_days = _allowed_days_for_month(year, month)
    if not base_days and not escalation_days:
        return

    students = students_all()
    sent_count = 0
    for s in students:
        student_id = s["id"]
        if reminder_was_sent_on_date(student_id, sent_date):
            continue
        if not _should_send_today(student_id, year, month, day):
            continue

        parents = parents_by_student_id(student_id)
        balance = balance_canteen_for_student(student_id)
        text = (
            f"Напоминание: внесите средства на баланс ученика.\n\n"
            f"Ученик: {s['full_name']} ({format_class_grade(s['class_grade'])}).\n"
            f"Текущий баланс: {balance:.2f} ₽."
        )
        any_sent = False
        for parent in parents:
            tid = parent.get("telegram_id")
            if not tid:
                continue
            try:
                await bot.send_message(tid, text)
                any_sent = True
            except Exception as e:
                logger.warning("Не удалось отправить напоминание user_id=%s: %s", parent.get("user_id"), e)
        if any_sent:
            reminder_mark_sent_on_date(student_id, sent_date)
            sent_count += 1

    if sent_count:
        logger.info("Отправлено напоминаний о платежах: %d (дата %s)", sent_count, sent_date)
