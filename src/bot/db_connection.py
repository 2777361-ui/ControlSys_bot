"""
Единая точка подключения к БД: SQLite (локально) или PostgreSQL (Supabase/продакшен).

Если задана переменная окружения DATABASE_URL (строка подключения к PostgreSQL),
используется она; иначе — локальный файл data/school.db (SQLite).

Для Supabase: Project Settings → Database → Connection string (URI), скопировать в DATABASE_URL.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _row_to_dict(row: Any) -> dict | None:
    """Превратить строку из PostgreSQL в dict с датами/временем в виде строк (как в SQLite)."""
    if row is None:
        return None
    d = dict(row)
    for k, v in list(d.items()):
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    return d

logger = logging.getLogger(__name__)

# Локальный путь к SQLite (если не используется PostgreSQL)
_DEFAULT_DB_DIR = Path(__file__).resolve().parents[2] / "data"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "school.db"
_DB_DIR = _DEFAULT_DB_DIR
_DB_PATH = _DEFAULT_DB_PATH

_connection: sqlite3.Connection | Any = None  # SQLite или PgConnection


def set_db_path_for_tests(path: str | Path) -> None:
    """Только для тестов: подменить путь к SQLite и закрыть соединение (на PostgreSQL не влияет)."""
    global _connection, _DB_PATH, _DB_DIR
    if _connection is not None:
        close_db()
    _DB_PATH = Path(path) if isinstance(path, str) else path
    _DB_DIR = _DB_PATH.parent


def reset_db_path_to_default() -> None:
    """Восстановить путь к SQLite по умолчанию (после тестов)."""
    global _connection, _DB_PATH, _DB_DIR
    if _connection is not None:
        close_db()
    _DB_PATH = _DEFAULT_DB_PATH
    _DB_DIR = _DEFAULT_DB_DIR


def _get_database_url() -> str | None:
    try:
        from bot.config import get_database_url as _url
        return _url()
    except Exception:
        return None


class _PgCursor:
    """Курсор PostgreSQL с lastrowid (через lastval() после INSERT)."""
    def __init__(self, cursor, conn):
        self._cursor = cursor
        self._conn = conn
        self._lastrowid = None

    def execute(self, sql: str, params: tuple = ()) -> _PgCursor:
        self._cursor.execute(sql, params)
        if sql.strip().upper().startswith("INSERT"):
            self._cursor.execute("SELECT lastval()")
            row = self._cursor.fetchone()
            self._lastrowid = row["lastval"] if row else None
        return self

    @property
    def lastrowid(self):
        return self._lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        return _row_to_dict(row) if row is not None else None

    def fetchall(self):
        return [_row_to_dict(r) for r in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _PgConnection:
    """Обёртка над psycopg2-соединением: тот же интерфейс, что и sqlite3 (execute, commit, ? → %s)."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> _PgCursor:
        from psycopg2.extras import RealDictCursor
        # Совместимость с SQLite-запросами: плейсхолдеры и функция времени
        sql = sql.replace("?", "%s").replace("datetime('now')", "NOW()")
        cursor = self._conn.cursor(cursor_factory=RealDictCursor)
        cur = _PgCursor(cursor, self._conn)
        cur.execute(sql, params)
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def get_connection() -> sqlite3.Connection | _PgConnection:
    """Одно соединение с БД на всё приложение. SQLite или PostgreSQL (Supabase)."""
    global _connection
    if _connection is not None:
        return _connection

    url = _get_database_url()
    if url:
        import psycopg2
        _connection = _PgConnection(psycopg2.connect(url))
        logger.info("PostgreSQL (Supabase/продакшен) подключена по DATABASE_URL")
        return _connection

    _DB_DIR.mkdir(parents=True, exist_ok=True)
    _connection = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    _connection.row_factory = sqlite3.Row
    _connection.execute("PRAGMA foreign_keys = ON")
    logger.info("SQLite (школа) подключена: %s", _DB_PATH)
    return _connection


def close_db() -> None:
    """Закрыть соединение (для тестов и корректного завершения)."""
    global _connection
    if _connection is not None:
        if hasattr(_connection, "close"):
            _connection.close()
        _connection = None


def is_postgres() -> bool:
    """Используется ли PostgreSQL (Supabase)."""
    return _get_database_url() is not None
