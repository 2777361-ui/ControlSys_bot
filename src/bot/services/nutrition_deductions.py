"""
Ежедневное начисление списаний за питание по планам и ценам.
Запускается раз в день (например в 01:00), создаёт списания за вчерашний день.
"""
import logging
from datetime import date, timedelta

from bot.school_db import process_nutrition_deductions_for_date

logger = logging.getLogger(__name__)


def run_daily_nutrition_deductions() -> None:
    """Создать списания за вчера для всех учеников и родителей по плану и текущим ценам."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    try:
        created = process_nutrition_deductions_for_date(yesterday)
        if created > 0:
            logger.info("Начислены списания за питание за %s: %d записей", yesterday, created)
    except Exception as e:
        logger.exception("Ошибка начисления списаний за питание за %s: %s", yesterday, e)
