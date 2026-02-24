"""
Хранение и проверка паролей, выдача JWT для веб-сессии.

Как работает: пароль не храним в открытом виде — только хэш (bcrypt).
При входе по email сверяем введённый пароль с хэшем. Если верно — выдаём JWT (токен),
который веб-интерфейс сохраняет в cookie и при каждом запросе проверяет.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bcrypt
from jose import JWTError, jwt

# Алгоритм и ключ для JWT (в продакшене ключ из переменной окружения)
SECRET_KEY = os.getenv("WEB_SECRET_KEY", "school-bot-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """Превращает пароль в хэш для хранения в БД (bcrypt напрямую, без passlib)."""
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Проверяет, что введённый пароль совпадает с хэшем из БД."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: int, role: str) -> str:
    """Создаёт JWT с user_id и role для cookie."""
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Читает JWT и возвращает payload (user_id, role) или None если невалидный."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
