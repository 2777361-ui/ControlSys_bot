"""
Модуль работы с SQLite: хранение истории сообщений по каждому пользователю.

Как это работает (для 5-классника):
  - При запуске бота создаётся файл-база данных (как тетрадка).
  - Каждое сообщение пользователя и каждый ответ бота записываются в эту тетрадку.
  - Потом можно открыть тетрадку и посмотреть, кто что писал и когда.

Таблица messages хранит:
  - user_id        — Telegram ID пользователя
  - username       — @username (может быть NULL)
  - full_name      — Имя + фамилия
  - role           — "user" или "assistant"
  - content        — Текст сообщения
  - model          — Какая модель ИИ ответила (NULL для user)
  - raw_response   — Сырой ответ от ИИ (JSON или текст до обработки, NULL для user)
  - created_at     — Когда записано (автоматически)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Путь к файлу базы данных — рядом с корнем проекта, в папке data/
_DB_DIR = Path(__file__).resolve().parents[2] / "data"
_DB_PATH = _DB_DIR / "bot_history.db"

# Соединение создаётся один раз при первом вызове
_connection: sqlite3.Connection | None = None


def _get_connection() -> sqlite3.Connection:
    """Возвращает единственное соединение с БД (создаёт, если ещё нет)."""
    global _connection
    if _connection is None:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row  # чтобы строки были как словари
        logger.info("SQLite подключена: %s", _DB_PATH)
    return _connection


def init_db() -> None:
    """
    Создаёт таблицу messages, если она ещё не существует.
    Вызывается один раз при запуске бота.
    """
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT,
            full_name   TEXT,
            role        TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content     TEXT NOT NULL,
            model       TEXT,
            raw_response TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Индекс для быстрого поиска по пользователю
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id)
    """)
    conn.commit()
    logger.info("Таблица messages готова")


def save_message(
    user_id: int,
    role: str,
    content: str,
    username: str | None = None,
    full_name: str | None = None,
    model: str | None = None,
    raw_response: str | None = None,
) -> None:
    """
    Сохраняет одно сообщение в базу.

    Параметры:
      user_id      — Telegram ID пользователя
      role         — "user" или "assistant"
      content      — Текст сообщения
      username     — @username (опционально)
      full_name    — Имя + фамилия (опционально)
      model        — Имя модели ИИ (опционально, для assistant)
      raw_response — Сырой ответ от API (опционально, для assistant)
    """
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO messages (user_id, username, full_name, role, content, model, raw_response, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            full_name,
            role,
            content,
            model,
            raw_response,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    logger.debug(
        "Сообщение сохранено: user_id=%s, role=%s, len=%d",
        user_id, role, len(content),
    )


def get_user_history(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """
    Возвращает последние `limit` сообщений пользователя (и ответов бота) из БД.
    Результат — список словарей, отсортированных от старых к новым.
    """
    conn = _get_connection()
    cursor = conn.execute(
        """
        SELECT id, user_id, username, full_name, role, content, model, raw_response, created_at
        FROM messages
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cursor.fetchall()
    # Разворачиваем, чтобы самые старые были первыми
    return [dict(row) for row in reversed(rows)]


def close_db() -> None:
    """Закрывает соединение с БД. Вызывается при остановке бота."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("SQLite соединение закрыто")
