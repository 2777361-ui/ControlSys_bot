"""
Ежемесячное начисление за обучение (1-го числа каждого учебного месяца).
Учебный год: сентябрь — июнь (учёба); в июле и августе не учатся — начисление не делаем.
"""
import logging
from datetime import date

from bot.school_db import process_education_charges_for_month

logger = logging.getLogger(__name__)


def run_monthly_education_charges() -> None:
    """Начислить обучение за текущий месяц (1-е число). В июле и августе не начисляем (каникулы)."""
    today = date.today()
    if today.month in (7, 8):  # июль и август — не учатся
        logger.debug("Июль/август: начисление обучения не выполняется")
        return
    month_date = today.replace(day=1).isoformat()
    try:
        created = process_education_charges_for_month(month_date)
        if created > 0:
            logger.info("Начислено обучение за %s: %d учеников", month_date, created)
    except Exception as e:
        logger.exception("Ошибка начисления обучения за %s: %s", month_date, e)
