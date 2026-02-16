"""
Сервис для операций с числами.
Логика без привязки к Telegram.
"""
import re

# Сообщение, если введённое значение не похоже на целое число
NOT_A_NUMBER = "Напиши целое число, например: 5 или /plus1 10"


def add_one_to_number(raw: str) -> str:
    """
    Берёт строку с числом и возвращает строку «число + 1».
    Если строка не целое число — возвращает подсказку.
    """
    if raw is None:
        return NOT_A_NUMBER
    stripped = raw.strip()
    if not stripped:
        return NOT_A_NUMBER
    # Разрешаем минус в начале и цифры
    if re.fullmatch(r"-?\d+", stripped):
        return str(int(stripped) + 1)
    return NOT_A_NUMBER
