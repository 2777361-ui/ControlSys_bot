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


class _RowWrapper:
    """Строка результата PostgreSQL: доступ по имени row['col'] и по индексу row[0], row[1] (как у sqlite3.Row).
    Порядок row[0], row[1], ... совпадает с порядком столбцов в SELECT. dict(row) даёт обычный словарь."""

    __slots__ = ("_d", "_vals")

    def __init__(self, d: dict) -> None:
        self._d = d
        self._vals = list(d.values()) if d else []

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._vals[key] if 0 <= key < len(self._vals) else None
        return self._d.get(key)

    def __bool__(self) -> bool:
        return bool(self._d)

    def keys(self):
        return self._d.keys()

    def values(self):
        return self._d.values()

    def items(self):
        return self._d.items()

    def get(self, key: str | int, default: Any = None) -> Any:
        if isinstance(key, int):
            return self._vals[key] if 0 <= key < len(self._vals) else default
        return self._d.get(key, default)

    def __iter__(self):
        return iter(self._d)

    def __contains__(self, key):
        return key in self._d


def _row_to_dict(row: Any) -> _RowWrapper | None:
    """Превратить строку из PostgreSQL в обёртку: dict + доступ по индексу row[0] (как в SQLite)."""
    if row is None:
        return None
    d = dict(row)
    for k, v in list(d.items()):
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    return _RowWrapper(d)

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


def _is_connection_lost_error(exc: BaseException) -> bool:
    """Проверка: ошибка из-за закрытого/оборванного соединения с PostgreSQL."""
    msg = str(exc).lower()
    return (
        "closed" in msg
        or "connection" in msg and ("terminated" in msg or "unexpectedly" in msg or "refused" in msg)
        or "server closed" in msg
    )


class _PgConnection:
    """Обёртка над psycopg2-соединением: тот же интерфейс, что и sqlite3 (execute, commit, ? → %s). При обрыве соединения — переподключение и повтор запроса."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> _PgCursor:
        from psycopg2 import OperationalError, InterfaceError
        from psycopg2.extras import RealDictCursor
        sql_norm = sql.replace("?", "%s").replace("datetime('now')", "NOW()")
        try:
            cursor = self._conn.cursor(cursor_factory=RealDictCursor)
            cur = _PgCursor(cursor, self._conn)
            cur.execute(sql_norm, params)
            return cur
        except (OperationalError, InterfaceError) as e:
            if _is_connection_lost_error(e):
                logger.warning("Соединение с PostgreSQL оборвано, переподключаемся: %s", e)
                close_db()
                new_conn = get_connection()
                return new_conn.execute(sql, params)
            raise

    def commit(self) -> None:
        from psycopg2 import OperationalError, InterfaceError
        try:
            self._conn.commit()
        except (OperationalError, InterfaceError) as e:
            if _is_connection_lost_error(e):
                logger.warning("Соединение с PostgreSQL оборвано при commit, переподключаемся: %s", e)
                close_db()
                new_conn = get_connection()
                new_conn.commit()
            else:
                raise

    def rollback(self) -> None:
        """Откат транзакции (для обработки ошибок при ALTER и т.д.)."""
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self) -> None:
        self._conn.close()


def _is_pg_connection(conn: Any) -> bool:
    """Является ли соединение обёрткой PostgreSQL."""
    return type(conn).__name__ == "_PgConnection"


def _pg_connection_ok(conn: Any) -> bool:
    """Проверка: живое ли соединение с PostgreSQL (быстрый ping)."""
    if not _is_pg_connection(conn):
        return True
    try:
        cur = conn._conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return True
    except Exception:
        return False


def get_connection() -> sqlite3.Connection | _PgConnection:
    """Одно соединение с БД на всё приложение. SQLite или PostgreSQL (Supabase). Для PostgreSQL при «мёртвом» соединении — переподключение."""
    global _connection
    if _connection is not None:
        if not _is_pg_connection(_connection):
            return _connection
        if _pg_connection_ok(_connection):
            return _connection
        logger.warning("Кэшированное соединение с PostgreSQL недоступно, переподключаемся")
        try:
            _connection._conn.close()
        except Exception:
            pass
        _connection = None

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
