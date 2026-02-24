"""
База данных школьной системы: пользователи, роли, ученики, платежи, мероприятия.

Как это работает (простыми словами):
  - users — все люди в системе: директор, бухгалтер, родители. У каждого роль и способ входа (Telegram или email).
  - students — ученики (Детский сад = 0, 1–11 класс), привязаны к родителю.
  - payments — все платежи (столовая, мероприятия, прочее). Статус меняет только бухгалтер.
  - events — мероприятия школы (экскурсии, праздники и т.д.).
"""
from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def format_class_grade(class_grade: int) -> str:
    """Текст для отображения класса: 0 → «Детский сад», иначе «N класс»."""
    if class_grade == 0:
        return "Детский сад"
    return f"{class_grade} класс"


# Подключение к БД: SQLite (локально) или PostgreSQL (Supabase) через db_connection
from bot.db_connection import (
    close_db as _close_db,
    get_connection,
    is_postgres,
    reset_db_path_to_default,
    set_db_path_for_tests,
)

# Роли в системе (администратор — первый, раздаёт роли и пароли)
ROLE_ADMINISTRATOR = "administrator"
ROLE_DIRECTOR = "director"
ROLE_DEPUTY_DIRECTOR = "deputy_director"
ROLE_ACCOUNTANT = "accountant"
ROLE_PARENT = "parent"
ROLE_TEACHER = "teacher"
ROLE_CANTEEN = "canteen"

# Ключи прав для заместителя директора (директор отмечает, что видит заместитель)
# Порядок ключей задаёт порядок чекбоксов в форме редактирования (Пользователи → Редактировать)
DEPUTY_PERMISSION_KEYS = {
    "dashboard": "Дашборд",
    "accounting": "Бухгалтерия",
    "payments": "Платежи и внесение платежа",
    "payment_purposes": "Назначения платежей",
    "students": "Ученики",
    "users": "Пользователи",
    "events": "Мероприятия",
    "nutrition": "Питание",
    "report": "Отчёт",
    "departments": "Отделы",
    "teacher_classes": "Классные руководители",
    "broadcast": "Рассылки и каналы",
    "feedback_inbox": "Входящие обращения",
    "tasks": "Текущие дела и группы",
}

# Те же ключи для настройки доступа бухгалтера (директор/админ отмечают разделы)
ACCOUNTANT_PERMISSION_KEYS = {
    "dashboard": "Дашборд",
    "accounting": "Бухгалтерия",
    "payments": "Платежи и внесение платежа",
    "payment_purposes": "Назначения платежей",
    "students": "Ученики",
    "users": "Пользователи",
    "events": "Мероприятия",
    "nutrition": "Питание",
    "report": "Отчёт",
    "departments": "Отделы",
    "teacher_classes": "Классные руководители",
    "broadcast": "Рассылки и каналы",
    "feedback_inbox": "Входящие обращения",
    "tasks": "Текущие дела и группы",
}

# Роли родителя в связке с учеником (мама, папа и т.д.)
PARENT_ROLE_MOM = "mom"
PARENT_ROLE_DAD = "dad"
PARENT_ROLE_GUARDIAN = "guardian"
PARENT_ROLE_PRIMARY = "primary"

# Назначение платежа
# Назначения платежей: Обучение, Питание, Расходники, Доп занятия
PAYMENT_PURPOSE_EDUCATION = "education"
PAYMENT_PURPOSE_FOOD = "food"
PAYMENT_PURPOSE_CONSUMABLES = "consumables"
PAYMENT_PURPOSE_EXTRA_CLASSES = "extra_classes"
# Старые значения (миграция в food/consumables/extra_classes)
PAYMENT_PURPOSE_CANTEEN = "canteen"
PAYMENT_PURPOSE_EVENT = "event"
PAYMENT_PURPOSE_OTHER = "other"

# Статус платежа (подтверждает только бухгалтер)
PAYMENT_STATUS_PENDING = "pending"
PAYMENT_STATUS_CONFIRMED = "confirmed"
PAYMENT_STATUS_REJECTED = "rejected"


def _init_db_postgres() -> None:
    """Создание таблиц в PostgreSQL (Supabase). Схема в финальном виде, без миграций SQLite."""
    conn = get_connection()
    # users — все колонки после миграций (profile, deputy и т.д.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              SERIAL PRIMARY KEY,
            telegram_id     BIGINT UNIQUE,
            email           TEXT UNIQUE,
            password_hash   TEXT,
            role            TEXT NOT NULL CHECK(role IN ('administrator', 'director', 'deputy_director', 'accountant', 'parent', 'teacher', 'canteen')),
            full_name       TEXT NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            avatar_path     TEXT,
            theme_preference TEXT DEFAULT 'system',
            whatsapp_contact TEXT,
            max_contact     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id              SERIAL PRIMARY KEY,
            full_name       TEXT NOT NULL,
            class_grade     INTEGER NOT NULL CHECK(class_grade >= 0 AND class_grade <= 11),
            class_letter    TEXT NOT NULL DEFAULT 'А',
            parent_id       INTEGER REFERENCES users(id),
            archived        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_parents (
            id              SERIAL PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            parent_role     TEXT NOT NULL DEFAULT 'primary' CHECK(parent_role IN ('primary', 'mom', 'dad', 'guardian')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(student_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_nutrition (
            id              SERIAL PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            nutrition_date  DATE NOT NULL,
            entered_by      INTEGER NOT NULL REFERENCES users(id),
            had_breakfast   INTEGER NOT NULL DEFAULT 0,
            had_lunch       INTEGER NOT NULL DEFAULT 0,
            had_snack       INTEGER NOT NULL DEFAULT 0,
            comment         TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(student_id, nutrition_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id              SERIAL PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            amount          DOUBLE PRECISION NOT NULL CHECK(amount > 0),
            purpose         TEXT NOT NULL,
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'rejected')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            confirmed_by    INTEGER REFERENCES users(id),
            confirmed_at    TIMESTAMPTZ,
            comment         TEXT,
            payment_type    TEXT DEFAULT 'cash',
            bank_commission DOUBLE PRECISION DEFAULT 0,
            amount_received DOUBLE PRECISION
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id              SERIAL PRIMARY KEY,
            name            TEXT NOT NULL,
            description     TEXT,
            event_date      TEXT,
            amount_required DOUBLE PRECISION,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_reminders (
            id              SERIAL PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            reminder_year   INTEGER NOT NULL,
            reminder_month  INTEGER NOT NULL,
            sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(student_id, reminder_year, reminder_month)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_reminder_sent_date (
            student_id      INTEGER NOT NULL REFERENCES students(id),
            sent_date       DATE NOT NULL,
            sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (student_id, sent_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invitation_links (
            token           TEXT PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            parent_role     TEXT NOT NULL DEFAULT 'primary' CHECK(parent_role IN ('primary', 'mom', 'dad', 'guardian')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at      TIMESTAMPTZ,
            used_by_user_id INTEGER REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parent_payment_reports (
            id              SERIAL PRIMARY KEY,
            parent_id       INTEGER NOT NULL REFERENCES users(id),
            student_id      INTEGER NOT NULL REFERENCES students(id),
            reported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            amount          DOUBLE PRECISION,
            comment         TEXT,
            status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'entered', 'dismissed')),
            resolved_by     INTEGER REFERENCES users(id),
            resolved_at     TIMESTAMPTZ
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id              SERIAL PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS department_members (
            department_id   INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            PRIMARY KEY (department_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_tasks (
            id              SERIAL PRIMARY KEY,
            created_by      INTEGER NOT NULL REFERENCES users(id),
            message_text    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sending', 'sent', 'partial')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            scheduled_at    TIMESTAMPTZ
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_recipients (
            id              SERIAL PRIMARY KEY,
            broadcast_id    INTEGER NOT NULL REFERENCES broadcast_tasks(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            telegram_id     BIGINT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
            sent_at         TIMESTAMPTZ,
            error_message   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_channels (
            id                  SERIAL PRIMARY KEY,
            name                TEXT NOT NULL,
            channel_type        TEXT NOT NULL CHECK(channel_type IN ('telegram', 'whatsapp', 'max')),
            channel_identifier  TEXT NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_channel_sends (
            id              SERIAL PRIMARY KEY,
            broadcast_id    INTEGER NOT NULL REFERENCES broadcast_tasks(id) ON DELETE CASCADE,
            channel_id      INTEGER NOT NULL REFERENCES broadcast_channels(id),
            status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
            sent_at         TIMESTAMPTZ,
            error_message   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_purposes (
            id              SERIAL PRIMARY KEY,
            code            TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            price           DOUBLE PRECISION NOT NULL DEFAULT 0,
            charge_frequency TEXT NOT NULL DEFAULT 'manual',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meal_prices (
            meal_type       TEXT PRIMARY KEY CHECK(meal_type IN ('breakfast', 'lunch', 'dinner')),
            price           DOUBLE PRECISION NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_meal_plan (
            student_id      INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
            has_breakfast   INTEGER NOT NULL DEFAULT 1,
            has_lunch       INTEGER NOT NULL DEFAULT 1,
            has_dinner      INTEGER NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parent_meal_plan (
            user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            has_breakfast   INTEGER NOT NULL DEFAULT 0,
            has_lunch       INTEGER NOT NULL DEFAULT 0,
            has_dinner      INTEGER NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_deductions (
            id                  SERIAL PRIMARY KEY,
            student_id          INTEGER REFERENCES students(id) ON DELETE CASCADE,
            user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
            charge_to_student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
            deduction_date      DATE NOT NULL,
            amount              DOUBLE PRECISION NOT NULL DEFAULT 0,
            breakfast_amt       DOUBLE PRECISION DEFAULT 0,
            lunch_amt           DOUBLE PRECISION DEFAULT 0,
            dinner_amt          DOUBLE PRECISION DEFAULT 0,
            is_manual           INTEGER NOT NULL DEFAULT 0,
            reason              TEXT,
            created_by          INTEGER REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK((student_id IS NOT NULL) OR (user_id IS NOT NULL))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_charges (
            id              SERIAL PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            purpose_code    TEXT NOT NULL,
            amount          DOUBLE PRECISION NOT NULL CHECK(amount > 0),
            charge_date     DATE NOT NULL,
            created_by      INTEGER REFERENCES users(id),
            description     TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id              SERIAL PRIMARY KEY,
            from_user_id    INTEGER NOT NULL REFERENCES users(id),
            message_text    TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status          TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'read', 'replied', 'deleted', 'moved')),
            admin_reply     TEXT,
            replied_at      TIMESTAMPTZ,
            replied_by_id   INTEGER REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id                  SERIAL PRIMARY KEY,
            title               TEXT NOT NULL,
            description         TEXT,
            contact_info        TEXT,
            status              TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'done')),
            created_by_id       INTEGER NOT NULL REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source_feedback_id  INTEGER REFERENCES feedback(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_groups (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_group_members (
            group_id     INTEGER NOT NULL REFERENCES task_groups(id) ON DELETE CASCADE,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_assignments (
            id          SERIAL PRIMARY KEY,
            task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            group_id    INTEGER REFERENCES task_groups(id) ON DELETE CASCADE,
            user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
            can_view    INTEGER NOT NULL DEFAULT 1,
            is_executor INTEGER NOT NULL DEFAULT 0,
            CHECK((group_id IS NOT NULL AND user_id IS NULL) OR (group_id IS NULL AND user_id IS NOT NULL))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_comments (
            id          SERIAL PRIMARY KEY,
            task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            comment_text TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_class (
            class_grade     INTEGER NOT NULL PRIMARY KEY CHECK(class_grade >= 0 AND class_grade <= 11),
            teacher_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_substitute (
            id                  SERIAL PRIMARY KEY,
            class_grade         INTEGER NOT NULL CHECK(class_grade >= 0 AND class_grade <= 11),
            main_teacher_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            substitute_teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            valid_until         TIMESTAMPTZ,
            UNIQUE(class_grade, main_teacher_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deputy_permissions (
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission_key  TEXT NOT NULL,
            PRIMARY KEY (user_id, permission_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accountant_permissions (
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission_key  TEXT NOT NULL,
            PRIMARY KEY (user_id, permission_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            expires_at  TIMESTAMPTZ NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_opening_balance (
            period_key     TEXT PRIMARY KEY,
            balance        DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_expense (
            id             SERIAL PRIMARY KEY,
            amount         DOUBLE PRECISION NOT NULL CHECK(amount > 0),
            reason         TEXT NOT NULL,
            expense_date   DATE NOT NULL,
            created_by     INTEGER REFERENCES users(id),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_income_extra (
            id             SERIAL PRIMARY KEY,
            amount         DOUBLE PRECISION NOT NULL CHECK(amount > 0),
            comment        TEXT NOT NULL DEFAULT '',
            income_date    DATE NOT NULL,
            created_by     INTEGER REFERENCES users(id),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounting_income_extra_date ON accounting_income_extra(income_date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('nutrition_cutoff_hour', '8'), ('nutrition_cutoff_minute', '0'), ('nutrition_cutoff_timezone', 'Asia/Yekaterinburg') ON CONFLICT (key) DO NOTHING")
    # Индексы
    for idx, stmt in [
        ("idx_users_telegram", "CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)"),
        ("idx_users_email", "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"),
        ("idx_students_parent", "CREATE INDEX IF NOT EXISTS idx_students_parent ON students(parent_id)"),
        ("idx_student_parents_student", "CREATE INDEX IF NOT EXISTS idx_student_parents_student ON student_parents(student_id)"),
        ("idx_student_parents_user", "CREATE INDEX IF NOT EXISTS idx_student_parents_user ON student_parents(user_id)"),
        ("idx_daily_nutrition_date", "CREATE INDEX IF NOT EXISTS idx_daily_nutrition_date ON daily_nutrition(nutrition_date)"),
        ("idx_daily_nutrition_student", "CREATE INDEX IF NOT EXISTS idx_daily_nutrition_student ON daily_nutrition(student_id)"),
        ("idx_payments_student", "CREATE INDEX IF NOT EXISTS idx_payments_student ON payments(student_id)"),
        ("idx_payments_status", "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)"),
        ("idx_payments_created", "CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at)"),
        ("idx_reminder_sent_date", "CREATE INDEX IF NOT EXISTS idx_reminder_sent_date ON payment_reminder_sent_date(student_id, sent_date)"),
        ("idx_parent_payment_reports_status", "CREATE INDEX IF NOT EXISTS idx_parent_payment_reports_status ON parent_payment_reports(status)"),
        ("idx_invitation_token", "CREATE INDEX IF NOT EXISTS idx_invitation_token ON invitation_links(token)"),
        ("idx_broadcast_tasks_status", "CREATE INDEX IF NOT EXISTS idx_broadcast_tasks_status ON broadcast_tasks(status)"),
        ("idx_broadcast_recipients_broadcast", "CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_broadcast ON broadcast_recipients(broadcast_id)"),
        ("idx_broadcast_channel_sends_broadcast", "CREATE INDEX IF NOT EXISTS idx_broadcast_channel_sends_broadcast ON broadcast_channel_sends(broadcast_id)"),
        ("idx_task_assignments_task", "CREATE INDEX IF NOT EXISTS idx_task_assignments_task ON task_assignments(task_id)"),
        ("idx_task_comments_task", "CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id)"),
        ("idx_feedback_status", "CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)"),
        ("idx_feedback_from", "CREATE INDEX IF NOT EXISTS idx_feedback_from ON feedback(from_user_id)"),
        ("idx_accounting_expense_date", "CREATE INDEX IF NOT EXISTS idx_accounting_expense_date ON accounting_expense(expense_date)"),
        ("idx_nutrition_deductions_student", "CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_student ON nutrition_deductions(student_id)"),
        ("idx_nutrition_deductions_user", "CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_user ON nutrition_deductions(user_id)"),
        ("idx_nutrition_deductions_date", "CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_date ON nutrition_deductions(deduction_date)"),
        ("idx_nutrition_deductions_charge_to", "CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_charge_to ON nutrition_deductions(charge_to_student_id)"),
        ("idx_student_charges_student", "CREATE INDEX IF NOT EXISTS idx_student_charges_student ON student_charges(student_id)"),
        ("idx_student_charges_date", "CREATE INDEX IF NOT EXISTS idx_student_charges_date ON student_charges(charge_date)"),
    ]:
        try:
            conn.execute(stmt)
        except Exception:
            pass
    # Дефолтные цены питания
    conn.execute("INSERT INTO meal_prices (meal_type, price) VALUES ('breakfast', 0), ('lunch', 0), ('dinner', 0) ON CONFLICT (meal_type) DO NOTHING")
    # Дефолтные назначения с ценой и типом начисления (обучение — ежемесячно, питание — ежедневно, остальные — по внесению)
    conn.execute("""
        INSERT INTO payment_purposes (code, name, sort_order, price, charge_frequency) VALUES
        ('education', 'Обучение', 1, 0, 'monthly'),
        ('food', 'Питание', 2, 0, 'daily'),
        ('consumables', 'Расходники', 3, 0, 'manual'),
        ('extra_classes', 'Доп занятия', 4, 0, 'manual'),
        ('after_school', 'Продленка', 5, 0, 'manual')
        ON CONFLICT (code) DO NOTHING
    """)
    # Колонка «в архиве» для учеников (существующие БД могли быть без неё)
    try:
        conn.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE")
        conn.commit()
        logger.info("PostgreSQL: колонка students.archived проверена/добавлена")
    except Exception as e:
        logger.warning("PostgreSQL: не удалось добавить students.archived: %s", e)
        conn.rollback()
    # Колонка запланированной даты рассылки (существующие БД могли быть без неё)
    try:
        conn.execute("ALTER TABLE broadcast_tasks ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ")
        conn.commit()
        logger.info("PostgreSQL: колонка broadcast_tasks.scheduled_at проверена/добавлена")
    except Exception as e:
        logger.warning("PostgreSQL: не удалось добавить broadcast_tasks.scheduled_at: %s", e)
        conn.rollback()
    # Планы питания: effective_from — с какой даты действует план (после 8:00 правки родителя только с завтра)
    for table, id_col in [("student_meal_plan", "student_id"), ("parent_meal_plan", "user_id")]:
        try:
            row = conn.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ? AND column_name = 'effective_from'
                """,
                (table,),
            ).fetchone()
            if row:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN effective_from DATE NOT NULL DEFAULT '2000-01-01'")
            conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
            conn.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({id_col}, effective_from)")
            conn.commit()
            logger.info("PostgreSQL: добавлена колонка %s.effective_from", table)
        except Exception as e:
            logger.warning("PostgreSQL: не удалось добавить %s.effective_from: %s", table, e)
            try:
                conn.rollback()
            except Exception:
                pass
    # Таблица настроек (дедлайн питания и т.д.) — могла отсутствовать в старых БД
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'app_settings'"
        ).fetchone()
        if not row:
            conn.execute("""
                CREATE TABLE app_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('nutrition_cutoff_hour', '8'), ('nutrition_cutoff_minute', '0'), ('nutrition_cutoff_timezone', 'Asia/Yekaterinburg')"
            )
            conn.commit()
            logger.info("PostgreSQL: создана таблица app_settings")
    except Exception as e:
        logger.warning("PostgreSQL: не удалось создать app_settings: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    # Календарь питания: в какие дни списывать (по умолчанию сб/вс — не списывать; переопределения здесь)
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'nutrition_calendar'"
        ).fetchone()
        if not row:
            conn.execute("""
                CREATE TABLE nutrition_calendar (
                    the_date     DATE PRIMARY KEY,
                    skip_charge  BOOLEAN NOT NULL
                )
            """)
            conn.commit()
            logger.info("PostgreSQL: создана таблица nutrition_calendar")
    except Exception as e:
        logger.warning("PostgreSQL: не удалось создать nutrition_calendar: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    conn.commit()
    logger.info("Таблицы PostgreSQL (Supabase) готовы")


def init_db() -> None:
    """Создаёт все таблицы школьной системы, если их ещё нет (SQLite или PostgreSQL/Supabase)."""
    if is_postgres():
        _init_db_postgres()
        return
    conn = get_connection()

    # Пользователи: администратор (первый), директор, бухгалтер, родители, учитель, столовая
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER UNIQUE,
            email           TEXT UNIQUE,
            password_hash   TEXT,
            role            TEXT NOT NULL CHECK(role IN ('administrator', 'director', 'deputy_director', 'accountant', 'parent', 'teacher', 'canteen')),
            full_name       TEXT NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Ученики: 0 = Детский сад, 1–11 класс; parent_id — основной родитель (кто первым добавлен)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name       TEXT NOT NULL,
            class_grade     INTEGER NOT NULL CHECK(class_grade >= 0 AND class_grade <= 11),
            class_letter    TEXT NOT NULL DEFAULT 'А',
            parent_id       INTEGER REFERENCES users(id),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Связь ученик — несколько родителей с ролями (мама, папа, опекун)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_parents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            parent_role     TEXT NOT NULL DEFAULT 'primary' CHECK(parent_role IN ('primary', 'mom', 'dad', 'guardian')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, user_id)
        )
    """)

    # Ежедневные данные по питанию (вносит учитель или столовая)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_nutrition (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            nutrition_date  TEXT NOT NULL,
            entered_by      INTEGER NOT NULL REFERENCES users(id),
            had_breakfast   INTEGER NOT NULL DEFAULT 0,
            had_lunch       INTEGER NOT NULL DEFAULT 0,
            had_snack       INTEGER NOT NULL DEFAULT 0,
            comment         TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, nutrition_date)
        )
    """)

    # Платежи: столовая, мероприятия, прочее. Подтверждает только бухгалтер
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            amount          REAL NOT NULL CHECK(amount > 0),
            purpose         TEXT NOT NULL CHECK(purpose IN ('education', 'food', 'consumables', 'extra_classes')),
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'confirmed', 'rejected')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_by    INTEGER REFERENCES users(id),
            confirmed_at    TEXT,
            comment         TEXT
        )
    """)

    # Мероприятия (экскурсии, праздники и т.д.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            description     TEXT,
            event_date      TEXT,
            amount_required REAL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Напоминания о платежах: раньше раз в месяц, теперь — по датам (3, 7, 10 и т.д.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            reminder_year   INTEGER NOT NULL,
            reminder_month  INTEGER NOT NULL,
            sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, reminder_year, reminder_month)
        )
    """)
    # Отправки по конкретной дате (дни 3, 7, 10; в янв 10,13,15; при отсутствии платежа — 11 и каждые 3 дня)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_reminder_sent_date (
            student_id      INTEGER NOT NULL REFERENCES students(id),
            sent_date       TEXT NOT NULL,
            sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (student_id, sent_date)
        )
    """)

    # Пригласительные ссылки для регистрации родителей (открывают страницу /register?token=...)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invitation_links (
            token           TEXT PRIMARY KEY,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            parent_role     TEXT NOT NULL DEFAULT 'primary' CHECK(parent_role IN ('primary', 'mom', 'dad', 'guardian')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at      TEXT,
            used_by_user_id INTEGER REFERENCES users(id)
        )
    """)

    # Сообщения родителей «Я совершил платёж» — попадают бухгалтеру и директору на проверку
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parent_payment_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id       INTEGER NOT NULL REFERENCES users(id),
            student_id      INTEGER NOT NULL REFERENCES students(id),
            reported_at     TEXT NOT NULL DEFAULT (datetime('now')),
            amount          REAL,
            comment         TEXT,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'entered', 'dismissed')),
            resolved_by     INTEGER REFERENCES users(id),
            resolved_at     TEXT
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_students_parent ON students(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_student_parents_student ON student_parents(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_student_parents_user ON student_parents(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_nutrition_date ON daily_nutrition(nutrition_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_nutrition_student ON daily_nutrition(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_student ON payments(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminder_sent_date ON payment_reminder_sent_date(student_id, sent_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_payment_reports_status ON parent_payment_reports(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invitation_token ON invitation_links(token)")

    # Отделы (директор создаёт и добавляет людей)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS department_members (
            department_id   INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            PRIMARY KEY (department_id, user_id)
        )
    """)
    # Очередь рассылок: веб создаёт задачу, бот отправляет в Telegram
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by      INTEGER NOT NULL REFERENCES users(id),
            message_text    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'sending', 'sent', 'partial')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at     TEXT,
            scheduled_at    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_recipients (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id    INTEGER NOT NULL REFERENCES broadcast_tasks(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            telegram_id     INTEGER NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'sent', 'failed')),
            sent_at         TEXT,
            error_message   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_tasks_status ON broadcast_tasks(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_broadcast ON broadcast_recipients(broadcast_id)")

    # Каналы для рассылки (общий чат ТГ, WhatsApp, группа МАХ и т.д.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_channels (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL,
            channel_type        TEXT NOT NULL CHECK(channel_type IN ('telegram', 'whatsapp', 'max')),
            channel_identifier  TEXT NOT NULL,
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_channel_sends (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id    INTEGER NOT NULL REFERENCES broadcast_tasks(id) ON DELETE CASCADE,
            channel_id      INTEGER NOT NULL REFERENCES broadcast_channels(id),
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'sent', 'failed')),
            sent_at         TEXT,
            error_message   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_channel_sends_broadcast ON broadcast_channel_sends(broadcast_id)")

    # Справочник назначений платежей (бухгалтер может добавлять варианты)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_purposes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            code            TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Цены на питание (завтрак, обед, ужин) — задаёт бухгалтер/директор
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meal_prices (
            meal_type       TEXT PRIMARY KEY CHECK(meal_type IN ('breakfast', 'lunch', 'dinner')),
            price           REAL NOT NULL DEFAULT 0,
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # План питания по умолчанию (родитель ставит ребёнка на завтрак/обед/ужин)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_meal_plan (
            student_id      INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
            has_breakfast   INTEGER NOT NULL DEFAULT 1,
            has_lunch       INTEGER NOT NULL DEFAULT 1,
            has_dinner      INTEGER NOT NULL DEFAULT 0,
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Родитель может стоять на питании сам (добавить себя в питание)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parent_meal_plan (
            user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            has_breakfast   INTEGER NOT NULL DEFAULT 0,
            has_lunch       INTEGER NOT NULL DEFAULT 0,
            has_dinner      INTEGER NOT NULL DEFAULT 0,
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Списания за питание (авто по плану или ручные от бухгалтера)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_deductions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER REFERENCES students(id) ON DELETE CASCADE,
            user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
            deduction_date  TEXT NOT NULL,
            amount          REAL NOT NULL DEFAULT 0,
            breakfast_amt   REAL DEFAULT 0,
            lunch_amt       REAL DEFAULT 0,
            dinner_amt      REAL DEFAULT 0,
            is_manual       INTEGER NOT NULL DEFAULT 0,
            reason          TEXT,
            created_by      INTEGER REFERENCES users(id),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK((student_id IS NOT NULL) OR (user_id IS NOT NULL))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_student ON nutrition_deductions(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_user ON nutrition_deductions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_date ON nutrition_deductions(deduction_date)")

    _migrate_users_canteen_role(conn)
    _migrate_users_administrator_role(conn)
    _migrate_users_deputy_director_role(conn)
    _migrate_deputy_permissions(conn)
    _migrate_accountant_permissions(conn)
    _migrate_student_parents(conn)
    _migrate_users_profile(conn)
    _migrate_payments_accounting(conn)
    _migrate_payments_purpose(conn)
    _migrate_payment_purposes(conn)
    _migrate_meal_plans(conn)
    _migrate_broadcast_channels(conn)
    _migrate_password_reset_tokens(conn)
    _migrate_class_grade_kindergarten(conn)
    _migrate_teacher_class(conn)
    _migrate_feedback_and_tasks(conn)
    _migrate_task_comments(conn)
    _migrate_payment_purposes_prices(conn)
    _migrate_student_charges(conn)
    _migrate_nutrition_charge_to_student(conn)
    _migrate_accounting_income_extra(conn)
    _migrate_students_archived(conn)
    _migrate_students_deleted_photo_comment(conn)
    _migrate_broadcast_scheduled_at(conn)
    _migrate_app_settings(conn)
    _migrate_meal_plan_effective_from(conn)
    _migrate_nutrition_calendar(conn)
    conn.commit()
    logger.info("Таблицы школьной БД готовы")


def _migrate_users_canteen_role(conn: sqlite3.Connection) -> None:
    """Добавить роль canteen в таблицу users (пересоздать, если нужно)."""
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
    row = cur.fetchone()
    if row and row[0] and "canteen" not in row[0]:
        conn.execute("ALTER TABLE users RENAME TO users_old")
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                email TEXT UNIQUE,
                password_hash TEXT,
                role TEXT NOT NULL CHECK(role IN ('director', 'accountant', 'parent', 'teacher', 'canteen')),
                full_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO users SELECT * FROM users_old")
        conn.execute("DROP TABLE users_old")
        logger.info("Миграция: добавлена роль canteen в users")


def _migrate_users_administrator_role(conn: sqlite3.Connection) -> None:
    """Добавить роль administrator в таблицу users (пересоздать, если нужно)."""
    import re
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
    row = cur.fetchone()
    if not row or not row[0] or "administrator" in row[0]:
        return
    sql = row[0]
    # Добавляем 'administrator' в список ролей (в начале — первый в системе)
    new_sql = re.sub(
        r"role IN \('director', 'accountant', 'parent', 'teacher', 'canteen'\)",
        "role IN ('administrator', 'director', 'accountant', 'parent', 'teacher', 'canteen')",
        sql,
        count=1,
    )
    if new_sql == sql:
        return
    conn.execute("ALTER TABLE users RENAME TO users_old")
    conn.execute(new_sql)
    conn.execute("INSERT INTO users SELECT * FROM users_old")
    conn.execute("DROP TABLE users_old")
    logger.info("Миграция: добавлена роль administrator в users")


def _migrate_users_deputy_director_role(conn: sqlite3.Connection) -> None:
    """Добавить роль deputy_director (заместитель директора) в таблицу users."""
    import re
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
    row = cur.fetchone()
    if not row or not row[0] or "deputy_director" in row[0]:
        return
    sql = row[0]
    # Вставить 'deputy_director' после 'director'
    new_sql = re.sub(
        r"('director', 'accountant')",
        "'director', 'deputy_director', 'accountant'",
        sql,
        count=1,
    )
    if new_sql == sql:
        new_sql = re.sub(
            r"('administrator', 'director', 'accountant')",
            "'administrator', 'director', 'deputy_director', 'accountant'",
            sql,
            count=1,
        )
    if new_sql == sql:
        return
    conn.execute("ALTER TABLE users RENAME TO users_old")
    conn.execute(new_sql)
    conn.execute("INSERT INTO users SELECT * FROM users_old")
    conn.execute("DROP TABLE users_old")
    logger.info("Миграция: добавлена роль deputy_director в users")


def _migrate_deputy_permissions(conn: sqlite3.Connection) -> None:
    """Таблица прав заместителя директора: какие разделы видит (назначает директор)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deputy_permissions (
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission_key  TEXT NOT NULL,
            PRIMARY KEY (user_id, permission_key)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deputy_permissions_user ON deputy_permissions(user_id)")
    logger.info("Миграция: таблица deputy_permissions")


def _migrate_accountant_permissions(conn: sqlite3.Connection) -> None:
    """Таблица прав бухгалтера: какие разделы видит (назначает директор/админ)."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accountant_permissions'")
    if cur.fetchone():
        return
    conn.execute("""
        CREATE TABLE accountant_permissions (
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission_key  TEXT NOT NULL,
            PRIMARY KEY (user_id, permission_key)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accountant_permissions_user ON accountant_permissions(user_id)")
    logger.info("Миграция: таблица accountant_permissions")


def _migrate_users_profile(conn: sqlite3.Connection) -> None:
    """Добавить колонки профиля: аватар, тема, привязки мессенджеров (WhatsApp, МАХ)."""
    cur = conn.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}
    if "avatar_path" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
        logger.info("Миграция: добавлена колонка users.avatar_path")
    if "theme_preference" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN theme_preference TEXT DEFAULT 'system'")
        logger.info("Миграция: добавлена колонка users.theme_preference")
    if "whatsapp_contact" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN whatsapp_contact TEXT")
        logger.info("Миграция: добавлена колонка users.whatsapp_contact")
    if "max_contact" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN max_contact TEXT")
        logger.info("Миграция: добавлена колонка users.max_contact")


def _migrate_payments_accounting(conn: sqlite3.Connection) -> None:
    """Колонки платежей для бухгалтерии: тип оплаты, комиссия, сумма поступившая; таблицы сальдо и списаний."""
    cur = conn.execute("PRAGMA table_info(payments)")
    cols = {row[1] for row in cur.fetchall()}
    if "payment_type" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN payment_type TEXT DEFAULT 'cash'")
        logger.info("Миграция: payments.payment_type")
    if "bank_commission" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN bank_commission REAL DEFAULT 0")
        logger.info("Миграция: payments.bank_commission")
    if "amount_received" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN amount_received REAL")
        logger.info("Миграция: payments.amount_received")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_opening_balance (
            period_key     TEXT PRIMARY KEY,
            balance        REAL NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_expense (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            amount         REAL NOT NULL CHECK(amount > 0),
            reason         TEXT NOT NULL,
            expense_date   TEXT NOT NULL,
            created_by     INTEGER REFERENCES users(id),
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounting_expense_date ON accounting_expense(expense_date)")


def _migrate_accounting_income_extra(conn: sqlite3.Connection) -> None:
    """Дополнительные средства в кассу (внесение администрацией с комментарием)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounting_income_extra (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            amount         REAL NOT NULL CHECK(amount > 0),
            comment        TEXT NOT NULL DEFAULT '',
            income_date    TEXT NOT NULL,
            created_by     INTEGER REFERENCES users(id),
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounting_income_extra_date ON accounting_income_extra(income_date)")
    logger.info("Миграция: accounting_income_extra")


def _migrate_students_archived(conn: sqlite3.Connection) -> None:
    """Колонка «в архиве»: ученик не участвует в списках и расчётах, данные сохраняются."""
    cur = conn.execute("PRAGMA table_info(students)")
    cols = {row[1] for row in cur.fetchall()}
    if "archived" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        logger.info("Миграция: students.archived")


def _migrate_students_deleted_photo_comment(conn: sqlite3.Connection) -> None:
    """Колонки: deleted_at (удалён из архива), photo_path (фото), comment (краткая сводка/комментарий)."""
    cur = conn.execute("PRAGMA table_info(students)")
    cols = {row[1] for row in cur.fetchall()}
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN deleted_at TEXT")
        logger.info("Миграция: students.deleted_at")
    if "photo_path" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN photo_path TEXT")
        logger.info("Миграция: students.photo_path")
    if "comment" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN comment TEXT")
        logger.info("Миграция: students.comment")


def _migrate_broadcast_scheduled_at(conn: sqlite3.Connection) -> None:
    """Колонка запланированной даты/времени отправки рассылки (NULL = отправить сразу)."""
    cur = conn.execute("PRAGMA table_info(broadcast_tasks)")
    cols = {row[1] for row in cur.fetchall()}
    if "scheduled_at" not in cols:
        conn.execute("ALTER TABLE broadcast_tasks ADD COLUMN scheduled_at TEXT")
        logger.info("Миграция: broadcast_tasks.scheduled_at")


def _migrate_app_settings(conn: sqlite3.Connection) -> None:
    """Таблица настроек приложения (в т.ч. дедлайн редактирования питания по времени Уфы)."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'")
    if cur.fetchone():
        return
    conn.execute("""
        CREATE TABLE app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('nutrition_cutoff_hour', '8'), ('nutrition_cutoff_minute', '0'), ('nutrition_cutoff_timezone', 'Asia/Yekaterinburg')")
    logger.info("Миграция: таблица app_settings")


def _migrate_meal_plan_effective_from(conn: sqlite3.Connection) -> None:
    """Добавить effective_from в планы питания: после дедлайна (8:00) правки родителя действуют только с завтрашнего дня."""
    # student_meal_plan
    cur = conn.execute("PRAGMA table_info(student_meal_plan)")
    cols = {row[1] for row in cur.fetchall()}
    if "effective_from" not in cols:
        conn.execute("ALTER TABLE student_meal_plan RENAME TO student_meal_plan_old")
        conn.execute("""
            CREATE TABLE student_meal_plan (
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                effective_from TEXT NOT NULL DEFAULT '2000-01-01',
                has_breakfast INTEGER NOT NULL DEFAULT 1,
                has_lunch INTEGER NOT NULL DEFAULT 1,
                has_dinner INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (student_id, effective_from)
            )
        """)
        conn.execute("""
            INSERT INTO student_meal_plan (student_id, effective_from, has_breakfast, has_lunch, has_dinner, updated_at)
            SELECT student_id, '2000-01-01', has_breakfast, has_lunch, has_dinner, updated_at FROM student_meal_plan_old
        """)
        conn.execute("DROP TABLE student_meal_plan_old")
        logger.info("Миграция: student_meal_plan.effective_from")
    # parent_meal_plan
    cur = conn.execute("PRAGMA table_info(parent_meal_plan)")
    cols = {row[1] for row in cur.fetchall()}
    if "effective_from" not in cols:
        conn.execute("ALTER TABLE parent_meal_plan RENAME TO parent_meal_plan_old")
        conn.execute("""
            CREATE TABLE parent_meal_plan (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                effective_from TEXT NOT NULL DEFAULT '2000-01-01',
                has_breakfast INTEGER NOT NULL DEFAULT 0,
                has_lunch INTEGER NOT NULL DEFAULT 0,
                has_dinner INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, effective_from)
            )
        """)
        conn.execute("""
            INSERT INTO parent_meal_plan (user_id, effective_from, has_breakfast, has_lunch, has_dinner, updated_at)
            SELECT user_id, '2000-01-01', has_breakfast, has_lunch, has_dinner, updated_at FROM parent_meal_plan_old
        """)
        conn.execute("DROP TABLE parent_meal_plan_old")
        logger.info("Миграция: parent_meal_plan.effective_from")


def _migrate_nutrition_calendar(conn: sqlite3.Connection) -> None:
    """Календарь питания: в какие дни списывать (по умолчанию сб/вс не списывать; переопределения в таблице)."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nutrition_calendar'")
    if cur.fetchone():
        return
    conn.execute("""
        CREATE TABLE nutrition_calendar (
            the_date     TEXT PRIMARY KEY,
            skip_charge  INTEGER NOT NULL
        )
    """)
    logger.info("Миграция: таблица nutrition_calendar")


def _migrate_payments_purpose(conn: sqlite3.Connection) -> None:
    """Расширить назначения платежей: Обучение, Питание, Расходники, Доп занятия (вместо столовая/мероприятие/прочее)."""
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='payments'")
    row = cur.fetchone()
    if not row or not row[0]:
        return
    sql = row[0]
    if "education" in sql and "food" in sql:
        return  # уже новая схема
    # Пересоздаём таблицу с новым CHECK и переносим данные (canteen->food, event->extra_classes, other->consumables)
    conn.execute("ALTER TABLE payments RENAME TO payments_old")
    conn.execute("""
        CREATE TABLE payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id),
            amount          REAL NOT NULL CHECK(amount > 0),
            purpose         TEXT NOT NULL CHECK(purpose IN ('education', 'food', 'consumables', 'extra_classes')),
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'confirmed', 'rejected')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_by    INTEGER REFERENCES users(id),
            confirmed_at    TEXT,
            comment         TEXT,
            payment_type    TEXT DEFAULT 'cash',
            bank_commission REAL DEFAULT 0,
            amount_received REAL
        )
    """)
    conn.execute("""
        INSERT INTO payments (
            id, student_id, amount, purpose, description, status, created_at,
            confirmed_by, confirmed_at, comment, payment_type, bank_commission, amount_received
        )
        SELECT
            id, student_id, amount,
            CASE purpose
                WHEN 'canteen' THEN 'food'
                WHEN 'event' THEN 'extra_classes'
                ELSE 'consumables'
            END,
            description, status, created_at, confirmed_by, confirmed_at, comment,
            COALESCE(payment_type, 'cash'), COALESCE(bank_commission, 0), amount_received
        FROM payments_old
    """)
    try:
        conn.execute("DROP TABLE payments_old")
    except sqlite3.OperationalError:
        pass
    logger.info("Миграция: назначения платежей — education, food, consumables, extra_classes")


def _migrate_payment_purposes(conn: sqlite3.Connection) -> None:
    """Справочник назначений: заполнить по умолчанию и убрать CHECK с payments (чтобы хранить любые коды из справочника)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_purposes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            code            TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    row = conn.execute("SELECT COUNT(*) FROM payment_purposes").fetchone()
    if row and row[0] == 0:
        conn.execute(
            """
            INSERT INTO payment_purposes (code, name, sort_order) VALUES
            ('education', 'Обучение', 1),
            ('food', 'Питание', 2),
            ('consumables', 'Расходники', 3),
            ('extra_classes', 'Доп занятия', 4)
            """
        )
        logger.info("Миграция: заполнен справочник назначений платежей")
    # Убрать CHECK(purpose IN (...)) с payments, чтобы можно было хранить коды из справочника
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='payments'")
    r = cur.fetchone()
    if not r or not r[0]:
        return
    sql = r[0]
    if "CHECK(purpose IN" in sql or "purpose IN (" in sql:
        conn.execute("ALTER TABLE payments RENAME TO payments_old")
        conn.execute("""
            CREATE TABLE payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id      INTEGER NOT NULL REFERENCES students(id),
                amount          REAL NOT NULL CHECK(amount > 0),
                purpose         TEXT NOT NULL,
                description     TEXT,
                status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'confirmed', 'rejected')),
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                confirmed_by    INTEGER REFERENCES users(id),
                confirmed_at    TEXT,
                comment         TEXT,
                payment_type    TEXT DEFAULT 'cash',
                bank_commission REAL DEFAULT 0,
                amount_received REAL
            )
        """)
        conn.execute("""
            INSERT INTO payments (id, student_id, amount, purpose, description, status, created_at,
                confirmed_by, confirmed_at, comment, payment_type, bank_commission, amount_received)
            SELECT id, student_id, amount, purpose, description, status, created_at,
                confirmed_by, confirmed_at, comment, payment_type, bank_commission, amount_received
            FROM payments_old
        """)
        conn.execute("DROP TABLE payments_old")
        logger.info("Миграция: с payments снят CHECK назначений (используется справочник)")


def _migrate_meal_plans(conn: sqlite3.Connection) -> None:
    """Таблицы питания: цены, планы, списания. Заполнить цены по умолчанию 0."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meal_prices (
            meal_type TEXT PRIMARY KEY CHECK(meal_type IN ('breakfast', 'lunch', 'dinner')),
            price REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_meal_plan (
            student_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
            has_breakfast INTEGER NOT NULL DEFAULT 1,
            has_lunch INTEGER NOT NULL DEFAULT 1,
            has_dinner INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parent_meal_plan (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            has_breakfast INTEGER NOT NULL DEFAULT 0,
            has_lunch INTEGER NOT NULL DEFAULT 0,
            has_dinner INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_deductions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            deduction_date TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            breakfast_amt REAL DEFAULT 0,
            lunch_amt REAL DEFAULT 0,
            dinner_amt REAL DEFAULT 0,
            is_manual INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK((student_id IS NOT NULL) OR (user_id IS NOT NULL))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_student ON nutrition_deductions(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_user ON nutrition_deductions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_date ON nutrition_deductions(deduction_date)")
    # Цены по умолчанию 0
    for mt in ("breakfast", "lunch", "dinner"):
        conn.execute(
            "INSERT OR IGNORE INTO meal_prices (meal_type, price) VALUES (?, 0)",
            (mt,),
        )
    logger.info("Миграция: таблицы питания (цены, планы, списания)")


def _migrate_broadcast_channels(conn: sqlite3.Connection) -> None:
    """Каналы рассылки: Telegram, WhatsApp, МАХ."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            channel_type TEXT NOT NULL CHECK(channel_type IN ('telegram', 'whatsapp', 'max')),
            channel_identifier TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_channel_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id INTEGER NOT NULL REFERENCES broadcast_tasks(id) ON DELETE CASCADE,
            channel_id INTEGER NOT NULL REFERENCES broadcast_channels(id),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
            sent_at TEXT,
            error_message TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_channel_sends_broadcast ON broadcast_channel_sends(broadcast_id)")
    logger.info("Миграция: каналы рассылки (broadcast_channels, broadcast_channel_sends)")


def _migrate_class_grade_kindergarten(conn: sqlite3.Connection) -> None:
    """Разрешить class_grade = 0 (Детский сад): было 1–11, стало 0–11."""
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='students'")
    row = cur.fetchone()
    sql = row[0] if row and row[0] else ""
    if "class_grade >= 1" in sql and "class_grade >= 0" not in sql:
        conn.execute("ALTER TABLE students RENAME TO students_old")
        conn.execute("""
            CREATE TABLE students (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name       TEXT NOT NULL,
                class_grade     INTEGER NOT NULL CHECK(class_grade >= 0 AND class_grade <= 11),
                class_letter    TEXT NOT NULL DEFAULT 'А',
                parent_id       INTEGER REFERENCES users(id),
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO students (id, full_name, class_grade, class_letter, parent_id, created_at, updated_at)
            SELECT id, full_name, class_grade, class_letter, parent_id, created_at, updated_at FROM students_old
        """)
        conn.execute("DROP TABLE students_old")
        logger.info("Миграция: добавлен класс 0 (Детский сад) в students")
    elif not sql:
        # Таблица ещё не создана — создаём сразу с 0–11 (в init_db выше создаётся с 1–11, но миграция идёт после)
        pass


def _migrate_feedback_and_tasks(conn: sqlite3.Connection) -> None:
    """Обратная связь от пользователей и текущие дела (задачи) с группами и назначениями."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id    INTEGER NOT NULL REFERENCES users(id),
            message_text    TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            status          TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new', 'read', 'replied', 'deleted', 'moved')),
            admin_reply     TEXT,
            replied_at      TEXT,
            replied_by_id   INTEGER REFERENCES users(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_from ON feedback(from_user_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            title               TEXT NOT NULL,
            description         TEXT,
            contact_info        TEXT,
            status              TEXT NOT NULL DEFAULT 'open'
                CHECK(status IN ('open', 'in_progress', 'done')),
            created_by_id       INTEGER NOT NULL REFERENCES users(id),
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
            source_feedback_id  INTEGER REFERENCES feedback(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_group_members (
            group_id     INTEGER NOT NULL REFERENCES task_groups(id) ON DELETE CASCADE,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_assignments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            group_id    INTEGER REFERENCES task_groups(id) ON DELETE CASCADE,
            user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
            can_view    INTEGER NOT NULL DEFAULT 1,
            is_executor INTEGER NOT NULL DEFAULT 0,
            CHECK((group_id IS NOT NULL AND user_id IS NULL) OR (group_id IS NULL AND user_id IS NOT NULL))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_assignments_task ON task_assignments(task_id)")
    logger.info("Миграция: feedback, tasks, task_groups, task_assignments")


def _migrate_task_comments(conn: sqlite3.Connection) -> None:
    """Комментарии к задачам (текущие дела) — для бота и веба."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            comment_text TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id)")
    logger.info("Миграция: task_comments")


def _migrate_payment_purposes_prices(conn: sqlite3.Connection) -> None:
    """Цены и тип начисления в назначениях: обучение (monthly), питание (daily), остальные (manual). Продленка."""
    cur = conn.execute("PRAGMA table_info(payment_purposes)")
    cols = {row[1] for row in cur.fetchall()}
    if "price" not in cols:
        conn.execute("ALTER TABLE payment_purposes ADD COLUMN price REAL NOT NULL DEFAULT 0")
        logger.info("Миграция: payment_purposes.price")
    if "charge_frequency" not in cols:
        conn.execute("ALTER TABLE payment_purposes ADD COLUMN charge_frequency TEXT NOT NULL DEFAULT 'manual'")
        conn.execute(
            "UPDATE payment_purposes SET charge_frequency = 'monthly' WHERE code = 'education'"
        )
        conn.execute(
            "UPDATE payment_purposes SET charge_frequency = 'daily' WHERE code = 'food'"
        )
        logger.info("Миграция: payment_purposes.charge_frequency")
    row = conn.execute("SELECT 1 FROM payment_purposes WHERE code = 'after_school'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO payment_purposes (code, name, sort_order, price, charge_frequency) VALUES ('after_school', 'Продленка', 5, 0, 'manual')"
        )
        logger.info("Миграция: добавлено назначение Продленка")


def _migrate_student_charges(conn: sqlite3.Connection) -> None:
    """Списания с баланса ученика: обучение (ежемесячно), расходники, доп занятия, продленка, прочее (вносит бухгалтер/учитель)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_charges (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            purpose_code    TEXT NOT NULL,
            amount          REAL NOT NULL CHECK(amount > 0),
            charge_date     TEXT NOT NULL,
            created_by      INTEGER REFERENCES users(id),
            description     TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_student_charges_student ON student_charges(student_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_student_charges_date ON student_charges(charge_date)")
    logger.info("Миграция: student_charges")


def _migrate_nutrition_charge_to_student(conn: sqlite3.Connection) -> None:
    """Питание родителя списывается с баланса ученика: charge_to_student_id — с какого ребёнка списать."""
    cur = conn.execute("PRAGMA table_info(nutrition_deductions)")
    cols = {row[1] for row in cur.fetchall()}
    if "charge_to_student_id" not in cols:
        conn.execute("ALTER TABLE nutrition_deductions ADD COLUMN charge_to_student_id INTEGER REFERENCES students(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_deductions_charge_to ON nutrition_deductions(charge_to_student_id)")
        logger.info("Миграция: nutrition_deductions.charge_to_student_id")


def _migrate_teacher_class(conn: sqlite3.Connection) -> None:
    """Привязка учителя к классу (классный руководитель) и временное замещение."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_class (
            class_grade     INTEGER NOT NULL PRIMARY KEY CHECK(class_grade >= 0 AND class_grade <= 11),
            teacher_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_substitute (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            class_grade         INTEGER NOT NULL CHECK(class_grade >= 0 AND class_grade <= 11),
            main_teacher_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            substitute_teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            valid_until         TEXT,
            UNIQUE(class_grade, main_teacher_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_substitute_sub ON teacher_substitute(substitute_teacher_id)")
    logger.info("Миграция: teacher_class, teacher_substitute")


def _migrate_password_reset_tokens(conn: sqlite3.Connection) -> None:
    """Таблица токенов для сброса пароля (ссылка «забыл пароль»)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token     TEXT PRIMARY KEY,
            user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_expires ON password_reset_tokens(expires_at)")
    logger.info("Миграция: таблица password_reset_tokens")


def _migrate_student_parents(conn: sqlite3.Connection) -> None:
    """Заполнить student_parents из существующих students.parent_id."""
    try:
        rows = conn.execute(
            "SELECT id, parent_id FROM students WHERE parent_id IS NOT NULL"
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO student_parents (student_id, user_id, parent_role) VALUES (?, ?, 'primary')",
                (r[0], r[1]),
            )
        if rows:
            logger.info("Миграция: заполнена таблица student_parents для %d учеников", len(rows))
    except sqlite3.OperationalError:
        pass


def close_db() -> None:
    """Закрыть соединение при остановке приложения (SQLite или PostgreSQL)."""
    _close_db()


# --- Пользователи ---

def user_create(
    *,
    role: str,
    full_name: str,
    telegram_id: int | None = None,
    email: str | None = None,
    password_hash: str | None = None,
) -> int:
    """Создать пользователя. Возвращает id."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO users (telegram_id, email, password_hash, role, full_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        (telegram_id, email, password_hash, role, full_name),
    )
    conn.commit()
    return cur.lastrowid


def user_by_telegram_id(telegram_id: int) -> dict[str, Any] | None:
    """Найти пользователя по Telegram ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
        (telegram_id,),
    ).fetchone()
    return dict(row) if row else None


def user_by_email(email: str) -> dict[str, Any] | None:
    """Найти пользователя по email."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1",
        (email.strip().lower(),),
    ).fetchone()
    return dict(row) if row else None


def user_by_id(user_id: int) -> dict[str, Any] | None:
    """Найти пользователя по id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def password_reset_create(user_id: int, expires_hours: int = 24) -> str:
    """Создать токен сброса пароля. Возвращает токен (передать в ссылке)."""
    conn = get_connection()
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(hours=expires_hours)).isoformat()
    conn.execute(
        "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    conn.commit()
    return token


def password_reset_get(token: str) -> dict[str, Any] | None:
    """Проверить токен: вернуть данные пользователя (id, email) или None если недействителен/истёк."""
    conn = get_connection()
    row = conn.execute(
        "SELECT prt.user_id, u.email FROM password_reset_tokens prt JOIN users u ON prt.user_id = u.id WHERE prt.token = ? AND prt.expires_at > datetime('now')",
        (token.strip(),),
    ).fetchone()
    return dict(row) if row else None


def password_reset_use(token: str) -> bool:
    """Удалить токен после успешной смены пароля (одноразовая ссылка)."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM password_reset_tokens WHERE token = ?", (token.strip(),))
    conn.commit()
    return cur.rowcount > 0


def user_link_telegram(user_id: int, telegram_id: int) -> None:
    """Привязать Telegram к существующему пользователю (например, после входа по email)."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET telegram_id = ?, updated_at = datetime('now') WHERE id = ?",
        (telegram_id, user_id),
    )
    conn.commit()


def user_update(
    user_id: int,
    full_name: str | None = None,
    email: str | None = None,
    password_hash: str | None = None,
    avatar_path: str | None = None,
    theme_preference: str | None = None,
    telegram_id: int | None = None,
    whatsapp_contact: str | None = None,
    max_contact: str | None = None,
    clear_telegram: bool = False,
) -> bool:
    """Обновить данные пользователя. None — не менять. clear_telegram=True — сбросить привязку Telegram (NULL)."""
    conn = get_connection()
    updates = []
    params = []
    if full_name is not None:
        updates.append("full_name = ?")
        params.append(full_name)
    if email is not None:
        updates.append("email = ?")
        params.append(email.strip().lower() if email else None)
    if password_hash is not None:
        updates.append("password_hash = ?")
        params.append(password_hash)
    if avatar_path is not None:
        updates.append("avatar_path = ?")
        params.append(avatar_path)
    if theme_preference is not None:
        if theme_preference not in ("light", "dark", "system"):
            theme_preference = "system"
        updates.append("theme_preference = ?")
        params.append(theme_preference)
    if clear_telegram:
        updates.append("telegram_id = NULL")
    elif telegram_id is not None:
        updates.append("telegram_id = ?")
        params.append(telegram_id)
    if whatsapp_contact is not None:
        updates.append("whatsapp_contact = ?")
        params.append(whatsapp_contact.strip() if isinstance(whatsapp_contact, str) else whatsapp_contact)
    if max_contact is not None:
        updates.append("max_contact = ?")
        params.append(max_contact.strip() if isinstance(max_contact, str) else max_contact)
    if not updates:
        return True
    updates.append("updated_at = datetime('now')")
    params.append(user_id)
    conn.execute(
        f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return True


def user_list(role: str | None = None) -> list[dict[str, Any]]:
    """Список пользователей, опционально по роли."""
    conn = get_connection()
    if role:
        rows = conn.execute(
            "SELECT * FROM users WHERE is_active = 1 AND role = ? ORDER BY full_name",
            (role,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM users WHERE is_active = 1 ORDER BY role, full_name"
        ).fetchall()
    return [dict(r) for r in rows]


def user_delete(user_id: int) -> tuple[bool, str]:
    """Деактивировать пользователя (is_active=0). Он исчезнет из списков и не сможет войти. Нельзя деактивировать последнего администратора. Возвращает (успех, сообщение об ошибке)."""
    conn = get_connection()
    row = conn.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False, "Пользователь не найден"
    role = row.get("role") if hasattr(row, "get") else (list(row.values())[1] if hasattr(row, "values") else row[1])
    if role == "administrator":
        cnt_row = conn.execute(
            "SELECT COUNT(*) AS s FROM users WHERE role = 'administrator' AND is_active = 1"
        ).fetchone()
        cnt = _scalar_float(cnt_row)
        if cnt <= 1:
            return False, "Нельзя удалить последнего администратора"
    cur = conn.execute("UPDATE users SET is_active = 0, updated_at = datetime('now') WHERE id = ?", (user_id,))
    conn.commit()
    return cur.rowcount > 0, ""


def deputy_permissions_list(user_id: int) -> list[str]:
    """Список ключей прав, выданных заместителю директора (user_id должен быть с ролью deputy_director)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT permission_key FROM deputy_permissions WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return [r[0] for r in rows]


def deputy_permissions_set(user_id: int, permission_keys: list[str]) -> None:
    """Задать права заместителю директора: заменяет текущий список на переданный."""
    conn = get_connection()
    conn.execute("DELETE FROM deputy_permissions WHERE user_id = ?", (user_id,))
    valid_keys = set(DEPUTY_PERMISSION_KEYS)
    for key in permission_keys:
        if key in valid_keys:
            conn.execute(
                "INSERT INTO deputy_permissions (user_id, permission_key) VALUES (?, ?)",
                (user_id, key),
            )
    conn.commit()


def deputy_has_permission(user_id: int, permission_key: str) -> bool:
    """Проверить, выдано ли заместителю директора право на раздел (для deputy_director; для директора/админа всегда True снаружи)."""
    conn = get_connection()
    if is_postgres():
        row = conn.execute(
            "SELECT 1 FROM deputy_permissions WHERE user_id = %s AND permission_key = %s",
            (user_id, permission_key),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM deputy_permissions WHERE user_id = ? AND permission_key = ?",
            (user_id, permission_key),
        ).fetchone()
    return row is not None


def accountant_permissions_list(user_id: int) -> list[str]:
    """Список ключей прав, выданных бухгалтеру (user_id должен быть с ролью accountant)."""
    conn = get_connection()
    if is_postgres():
        rows = conn.execute(
            "SELECT permission_key FROM accountant_permissions WHERE user_id = %s",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT permission_key FROM accountant_permissions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [r[0] for r in rows]


def accountant_permissions_set(user_id: int, permission_keys: list[str]) -> None:
    """Задать права бухгалтеру: заменяет текущий список на переданный."""
    conn = get_connection()
    valid_keys = set(ACCOUNTANT_PERMISSION_KEYS)
    if is_postgres():
        conn.execute("DELETE FROM accountant_permissions WHERE user_id = %s", (user_id,))
        for key in permission_keys:
            if key in valid_keys:
                conn.execute(
                    "INSERT INTO accountant_permissions (user_id, permission_key) VALUES (%s, %s)",
                    (user_id, key),
                )
    else:
        conn.execute("DELETE FROM accountant_permissions WHERE user_id = ?", (user_id,))
        for key in permission_keys:
            if key in valid_keys:
                conn.execute(
                    "INSERT INTO accountant_permissions (user_id, permission_key) VALUES (?, ?)",
                    (user_id, key),
                )
    conn.commit()


def accountant_has_permission(user_id: int, permission_key: str) -> bool:
    """Проверить, выдано ли бухгалтеру право на раздел."""
    conn = get_connection()
    if is_postgres():
        row = conn.execute(
            "SELECT 1 FROM accountant_permissions WHERE user_id = %s AND permission_key = %s",
            (user_id, permission_key),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM accountant_permissions WHERE user_id = ? AND permission_key = ?",
            (user_id, permission_key),
        ).fetchone()
    return row is not None


def users_with_telegram(user_ids: list[int] | None = None, role: str | None = None) -> list[dict[str, Any]]:
    """Пользователи с привязанным Telegram (для рассылок). Можно фильтровать по id или по роли."""
    conn = get_connection()
    q = "SELECT id, full_name, telegram_id FROM users WHERE is_active = 1 AND telegram_id IS NOT NULL"
    params: list[Any] = []
    if user_ids:
        placeholders = ",".join("?" * len(user_ids))
        q += f" AND id IN ({placeholders})"
        params.extend(user_ids)
    if role:
        q += " AND role = ?"
        params.append(role)
    q += " ORDER BY full_name"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


# --- Отделы (директор создаёт и добавляет людей) ---

def department_create(name: str) -> int:
    """Создать отдел."""
    conn = get_connection()
    cur = conn.execute("INSERT INTO departments (name) VALUES (?)", (name.strip(),))
    conn.commit()
    return cur.lastrowid


def department_list() -> list[dict[str, Any]]:
    """Список всех отделов."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM departments ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def department_by_id(department_id: int) -> dict[str, Any] | None:
    """Отдел по id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM departments WHERE id = ?", (department_id,)).fetchone()
    return dict(row) if row else None


def department_update(department_id: int, name: str) -> bool:
    """Переименовать отдел."""
    conn = get_connection()
    cur = conn.execute("UPDATE departments SET name = ? WHERE id = ?", (name.strip(), department_id))
    conn.commit()
    return cur.rowcount > 0


def department_delete(department_id: int) -> bool:
    """Удалить отдел (участники отвязываются)."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM departments WHERE id = ?", (department_id,))
    conn.commit()
    return cur.rowcount > 0


def department_members(department_id: int) -> list[dict[str, Any]]:
    """Участники отдела (с полными данными пользователя)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT u.id, u.full_name, u.role, u.telegram_id, u.email
        FROM department_members dm
        JOIN users u ON dm.user_id = u.id
        WHERE dm.department_id = ? AND u.is_active = 1
        ORDER BY u.full_name
        """,
        (department_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def department_member_ids(department_id: int) -> list[int]:
    """Список user_id участников отдела."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT user_id FROM department_members WHERE department_id = ?",
        (department_id,),
    ).fetchall()
    return [r[0] for r in rows]


def department_add_member(department_id: int, user_id: int) -> bool:
    """Добавить пользователя в отдел."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO department_members (department_id, user_id) VALUES (?, ?)",
            (department_id, user_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def department_remove_member(department_id: int, user_id: int) -> bool:
    """Убрать пользователя из отдела."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM department_members WHERE department_id = ? AND user_id = ?",
        (department_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


# --- Каналы рассылки (общий чат ТГ, WhatsApp, МАХ) ---

def broadcast_channel_list() -> list[dict[str, Any]]:
    """Список подключённых каналов для рассылки."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM broadcast_channels ORDER BY channel_type, name"
    ).fetchall()
    return [dict(r) for r in rows]


def broadcast_channel_create(name: str, channel_type: str, channel_identifier: str) -> int:
    """Добавить канал (telegram/whatsapp/max). Для Telegram — chat_id (число или строка)."""
    if channel_type not in ("telegram", "whatsapp", "max"):
        channel_type = "telegram"
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO broadcast_channels (name, channel_type, channel_identifier) VALUES (?, ?, ?)",
        (name.strip(), channel_type, channel_identifier.strip()),
    )
    conn.commit()
    return cur.lastrowid


def broadcast_channel_by_id(channel_id: int) -> dict[str, Any] | None:
    """Канал по id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM broadcast_channels WHERE id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def broadcast_channel_delete(channel_id: int) -> bool:
    """Удалить канал."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM broadcast_channels WHERE id = ?", (channel_id,))
    conn.commit()
    return cur.rowcount > 0


def broadcast_channel_sends_pending(broadcast_id: int) -> list[dict[str, Any]]:
    """Ожидающие отправки в каналы (с данными канала)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT bcs.id, bcs.channel_id, bc.channel_type, bc.channel_identifier, bc.name AS channel_name
        FROM broadcast_channel_sends bcs
        JOIN broadcast_channels bc ON bcs.channel_id = bc.id
        WHERE bcs.broadcast_id = ? AND bcs.status = 'pending'
        """,
        (broadcast_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def broadcast_mark_channel_sent(channel_send_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE broadcast_channel_sends SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
        (channel_send_id,),
    )
    conn.commit()


def broadcast_mark_channel_failed(channel_send_id: int, error_message: str = "") -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE broadcast_channel_sends SET status = 'failed', sent_at = datetime('now'), error_message = ? WHERE id = ?",
        (error_message[:500], channel_send_id),
    )
    conn.commit()


# --- Рассылки (очередь: веб создаёт, бот отправляет в Telegram) ---

def parent_user_ids_for_students(student_ids: list[int]) -> list[int]:
    """User_id всех родителей указанных учеников (для рассылки учителем)."""
    if not student_ids:
        return []
    conn = get_connection()
    placeholders = ",".join("?" * len(student_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT sp.user_id
        FROM student_parents sp
        WHERE sp.student_id IN ({placeholders})
        """,
        student_ids,
    ).fetchall()
    return [r[0] for r in rows]


def parent_user_ids_for_class(class_grade: int) -> list[int]:
    """User_id всех родителей учеников указанного класса (для рассылки по классу)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT DISTINCT sp.user_id
        FROM student_parents sp
        JOIN students s ON sp.student_id = s.id
        WHERE s.class_grade = ?
        """,
        (class_grade,),
    ).fetchall()
    return [r[0] for r in rows]


def parents_of_class_list(class_grade: int) -> list[dict[str, Any]]:
    """Список родителей учеников класса (id, full_name, telegram_id) для выбора в рассылке."""
    user_ids = parent_user_ids_for_class(class_grade)
    if not user_ids:
        return []
    return users_with_telegram(user_ids=user_ids)


def broadcast_create(
    created_by: int,
    message_text: str,
    recipient_user_ids: list[int] | None = None,
    scheduled_at: str | None = None,
) -> int:
    """Создать задачу рассылки. Если передан список user_id — добавить получателей (с telegram_id).
    scheduled_at — дата/время отправки в формате ISO или 'YYYY-MM-DD HH:MM' (NULL = отправить сразу). Возвращает broadcast_id."""
    conn = get_connection()
    if scheduled_at and scheduled_at.strip():
        cur = conn.execute(
            "INSERT INTO broadcast_tasks (created_by, message_text, status, scheduled_at) VALUES (?, ?, 'pending', ?)",
            (created_by, message_text.strip(), scheduled_at.strip()),
        )
    else:
        cur = conn.execute(
            "INSERT INTO broadcast_tasks (created_by, message_text, status) VALUES (?, ?, 'pending')",
            (created_by, message_text.strip()),
        )
    broadcast_id = cur.lastrowid
    if recipient_user_ids:
        users = users_with_telegram(user_ids=recipient_user_ids)
        for u in users:
            conn.execute(
                "INSERT INTO broadcast_recipients (broadcast_id, user_id, telegram_id) VALUES (?, ?, ?)",
                (broadcast_id, u["id"], u["telegram_id"]),
            )
    conn.commit()
    logger.info(
        "AUDIT | db broadcast_create | broadcast_id=%s | created_by=%s | recipients=%s",
        broadcast_id, created_by, len(recipient_user_ids) if recipient_user_ids else 0,
    )
    return broadcast_id


def broadcast_add_channel_sends(broadcast_id: int, channel_ids: list[int]) -> None:
    """Добавить отправки в каналы к задаче рассылки (общий чат школы)."""
    conn = get_connection()
    for ch_id in channel_ids:
        conn.execute(
            "INSERT INTO broadcast_channel_sends (broadcast_id, channel_id, status) VALUES (?, ?, 'pending')",
            (broadcast_id, ch_id),
        )
    conn.commit()


def broadcast_pending_task() -> dict[str, Any] | None:
    """Взять одну задачу рассылки со статусом pending, у которой время отправки наступило или не задано (для бота)."""
    conn = get_connection()
    # SQLite: datetime сравнение строк в формате ISO; PostgreSQL: NOW()
    if is_postgres():
        row = conn.execute(
            """SELECT * FROM broadcast_tasks
               WHERE status = 'pending'
                 AND (scheduled_at IS NULL OR scheduled_at <= NOW())
               ORDER BY id LIMIT 1"""
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM broadcast_tasks
               WHERE status = 'pending'
                 AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
               ORDER BY id LIMIT 1"""
        ).fetchone()
    return dict(row) if row else None


def broadcast_recipients_pending(broadcast_id: int) -> list[dict[str, Any]]:
    """Список получателей задачи с status=pending."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_id, telegram_id FROM broadcast_recipients WHERE broadcast_id = ? AND status = 'pending'",
        (broadcast_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def broadcast_mark_sending(broadcast_id: int) -> None:
    """Пометить задачу как «отправляется»."""
    conn = get_connection()
    conn.execute("UPDATE broadcast_tasks SET status = 'sending' WHERE id = ?", (broadcast_id,))
    conn.commit()


def broadcast_mark_recipient_sent(recipient_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE broadcast_recipients SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
        (recipient_id,),
    )
    conn.commit()


def broadcast_mark_recipient_failed(recipient_id: int, error_message: str = "") -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE broadcast_recipients SET status = 'failed', sent_at = datetime('now'), error_message = ? WHERE id = ?",
        (error_message[:500], recipient_id),
    )
    conn.commit()


def broadcast_mark_task_finished(broadcast_id: int) -> None:
    """Пометить задачу как sent или partial (если есть failed)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS failed FROM broadcast_recipients WHERE broadcast_id = ? AND status = 'failed'",
        (broadcast_id,),
    ).fetchone()
    failed = row[0] if row else 0
    status = "partial" if failed > 0 else "sent"
    conn.execute(
        "UPDATE broadcast_tasks SET status = ?, finished_at = datetime('now') WHERE id = ?",
        (status, broadcast_id),
    )
    conn.commit()


def broadcast_list(limit: int = 50) -> list[dict[str, Any]]:
    """История рассылок (для отображения в веб)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT bt.*, u.full_name AS created_by_name,
               (SELECT COUNT(*) FROM broadcast_recipients WHERE broadcast_id = bt.id AND status = 'sent') AS sent_count,
               (SELECT COUNT(*) FROM broadcast_recipients WHERE broadcast_id = bt.id AND status = 'failed') AS failed_count,
               (SELECT COUNT(*) FROM broadcast_recipients WHERE broadcast_id = bt.id) AS total_count
        FROM broadcast_tasks bt
        LEFT JOIN users u ON bt.created_by = u.id
        ORDER BY bt.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def broadcast_inbox_for_user(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Входящие рассылки для пользователя (для отображения в веб)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT bt.id, bt.message_text, bt.created_at, bt.status AS task_status,
               u.full_name AS created_by_name,
               br.status AS recipient_status
        FROM broadcast_recipients br
        JOIN broadcast_tasks bt ON br.broadcast_id = bt.id
        LEFT JOIN users u ON bt.created_by = u.id
        WHERE br.user_id = ?
        ORDER BY bt.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Пригласительные ссылки для родителей ---

def invitation_create(student_id: int, parent_role: str = "primary", expires_days: int = 30) -> str:
    """Создать приглашение для привязки родителя к ученику. Возвращает токен."""
    conn = get_connection()
    token = secrets.token_urlsafe(24)
    expires = (datetime.utcnow() + timedelta(days=expires_days)).isoformat() if expires_days else None
    conn.execute(
        "INSERT INTO invitation_links (token, student_id, parent_role, expires_at) VALUES (?, ?, ?, ?)",
        (token, student_id, parent_role, expires),
    )
    conn.commit()
    return token


def invitation_get_by_token(token: str) -> dict[str, Any] | None:
    """Найти приглашение по токену; вернуть None если использовано или просрочено."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT inv.*, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM invitation_links inv
        JOIN students s ON inv.student_id = s.id
        WHERE inv.token = ? AND inv.used_by_user_id IS NULL
          AND (inv.expires_at IS NULL OR inv.expires_at > datetime('now'))
        """,
        (token.strip(),),
    ).fetchone()
    return dict(row) if row else None


def invitation_mark_used(token: str, user_id: int) -> bool:
    """Отметить приглашение как использованное (привязать родителя к ученику)."""
    conn = get_connection()
    cur = conn.execute(
        "UPDATE invitation_links SET used_by_user_id = ? WHERE token = ? AND used_by_user_id IS NULL",
        (user_id, token.strip()),
    )
    conn.commit()
    return cur.rowcount > 0


# --- Ученики ---

def student_create(
    full_name: str,
    class_grade: int,
    parent_id: int | None,
    class_letter: str = "",
) -> int:
    """Добавить ученика. Если указан parent_id — привязать родителя с ролью primary."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO students (full_name, class_grade, class_letter, parent_id)
        VALUES (?, ?, ?, ?)
        """,
        (full_name, class_grade, class_letter or "А", parent_id),
    )
    student_id = cur.lastrowid
    if parent_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO student_parents (student_id, user_id, parent_role) VALUES (?, ?, 'primary')",
            (student_id, parent_id),
        )
    conn.commit()
    return student_id


def students_by_parent_id(parent_id: int) -> list[dict[str, Any]]:
    """Все активные (не в архиве, не удалённые) ученики, у которых этот пользователь числится родителем."""
    _ensure_students_archived_column_pg()
    conn = get_connection()
    where_archived = _archived_condition(False)
    where_deleted = _students_not_deleted_condition("s")
    rows = conn.execute(
        f"""
        SELECT s.* FROM students s
        JOIN student_parents sp ON s.id = sp.student_id
        WHERE sp.user_id = ? AND {where_archived} AND {where_deleted}
        ORDER BY s.class_grade, s.class_letter, s.full_name
        """,
        (parent_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def student_parents_by_student(student_id: int) -> list[dict[str, Any]]:
    """Список родителей ученика с ролями (мама, папа и т.д.)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT sp.id, sp.user_id, sp.parent_role, u.full_name, u.email, u.telegram_id
        FROM student_parents sp
        JOIN users u ON sp.user_id = u.id
        WHERE sp.student_id = ?
        ORDER BY sp.id
        """,
        (student_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def student_parents_add(student_id: int, user_id: int, parent_role: str = "guardian") -> bool:
    """Привязать родителя к ученику (роль: mom, dad, guardian)."""
    conn = get_connection()
    if parent_role not in ("mom", "dad", "guardian"):
        parent_role = "guardian"
    try:
        conn.execute(
            "INSERT INTO student_parents (student_id, user_id, parent_role) VALUES (?, ?, ?)",
            (student_id, user_id, parent_role),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def student_parent_can_manage(student_id: int, user_id: int) -> bool:
    """Проверка: может ли пользователь управлять этим учеником (родитель или сотрудник)."""
    user = user_by_id(user_id)
    if user and user.get("role") in ("director", "accountant"):
        return True
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM student_parents WHERE student_id = ? AND user_id = ?",
        (student_id, user_id),
    ).fetchone()
    return row is not None


def student_by_id(student_id: int, include_deleted: bool = False) -> dict[str, Any] | None:
    """Ученик по id. По умолчанию удалённые (deleted_at IS NOT NULL) не возвращаются."""
    _ensure_students_archived_column_pg()
    conn = get_connection()
    if include_deleted:
        if is_postgres():
            row = conn.execute("SELECT * FROM students WHERE id = %s", (student_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    else:
        if is_postgres():
            row = conn.execute(
                "SELECT * FROM students WHERE id = %s AND deleted_at IS NULL",
                (student_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM students WHERE id = ? AND deleted_at IS NULL",
                (student_id,),
            ).fetchone()
    return dict(row) if row else None


def _archived_condition(include_archived: bool, table_alias: str = "s") -> str:
    """SQL: активные (не в архиве) или все.
    В PostgreSQL колонка archived — BOOLEAN (сравниваем с FALSE). В SQLite — 0/1.
    Если в PG колонки archived ещё нет (ALTER не выполнился), возвращаем 1=1."""
    if include_archived:
        return "1=1"
    if is_postgres() and not _pg_students_has_archived_column():
        return "1=1"
    prefix = f"{table_alias}." if table_alias else ""
    if is_postgres():
        return f"({prefix}archived IS NOT TRUE)"
    return f"({prefix}archived = 0 OR {prefix}archived IS NULL)"


# Кэш: есть ли колонка students.archived в PostgreSQL (проверка information_schema).
_pg_students_archived_column_exists: bool | None = None


def _pg_students_has_archived_column() -> bool:
    """Для PostgreSQL: есть ли колонка archived в таблице students (кэш на процесс). Если ALTER не выполнился — False."""
    global _pg_students_archived_column_exists
    if not is_postgres():
        return True
    if _pg_students_archived_column_exists is not None:
        return _pg_students_archived_column_exists
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'students' AND column_name = 'archived'
            """
        ).fetchone()
        _pg_students_archived_column_exists = bool(row)
    except Exception:
        _pg_students_archived_column_exists = False
    return _pg_students_archived_column_exists


# Кэш: колонка students.archived уже проверена для PostgreSQL (избегаем лишних запросов).
_students_archived_column_ensured = False


def _ensure_students_archived_column_pg() -> None:
    """Для PostgreSQL: если в таблице students нет колонок archived, deleted_at, photo_path, comment — добавить."""
    global _students_archived_column_ensured, _pg_students_archived_column_exists
    if not is_postgres() or _students_archived_column_ensured:
        return
    conn = get_connection()
    try:
        for col, typ, default in [
            ("archived", "BOOLEAN NOT NULL DEFAULT FALSE", None),
            ("deleted_at", "TIMESTAMPTZ", None),
            ("photo_path", "TEXT", None),
            ("comment", "TEXT", None),
        ]:
            row = conn.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'students' AND column_name = %s
                """,
                (col,),
            ).fetchone()
            if not row:
                conn.execute(f"ALTER TABLE students ADD COLUMN {col} {typ}")
                conn.commit()
                logger.info("PostgreSQL: добавлена колонка students.%s", col)
                if col == "archived":
                    _pg_students_archived_column_exists = True  # чтобы фильтр и student_set_archived сразу видели колонку
    except Exception as e:
        logger.warning("PostgreSQL: не удалось добавить колонки students: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    _students_archived_column_ensured = True


def _students_not_deleted_condition(table_alias: str = "s") -> str:
    """SQL: ученик не удалён (deleted_at IS NULL). Колонка есть после миграции."""
    prefix = f"{table_alias}." if table_alias else ""
    return f"({prefix}deleted_at IS NULL)"


def students_all(include_archived: bool = False) -> list[dict[str, Any]]:
    """Все ученики (для бухгалтера/директора); по умолчанию только активные (не в архиве), не удалённые."""
    _ensure_students_archived_column_pg()
    conn = get_connection()
    where_archived = _archived_condition(include_archived)
    where_deleted = _students_not_deleted_condition("s")
    where = f"{where_archived} AND {where_deleted}"
    if is_postgres():
        rows = conn.execute(
            f"""
            SELECT s.*, u.full_name AS parent_name, u.email AS parent_email
            FROM students s
            LEFT JOIN users u ON s.parent_id = u.id
            WHERE {where}
            ORDER BY s.class_grade, s.class_letter, s.full_name
            """
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT s.*, u.full_name AS parent_name, u.email AS parent_email
            FROM students s
            LEFT JOIN users u ON s.parent_id = u.id
            WHERE {where}
            ORDER BY s.class_grade, s.class_letter, s.full_name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def student_set_archived(student_id: int, archived: bool) -> bool:
    """Перевести ученика в архив (True) или восстановить (False). В архиве не участвует в списках и расчётах."""
    if is_postgres() and not _pg_students_has_archived_column():
        return False  # колонка ещё не создана (ALTER не выполнился из-за таймаута)
    conn = get_connection()
    try:
        val = 1 if archived else 0
        cur = conn.execute("UPDATE students SET archived = ?, updated_at = datetime('now') WHERE id = ?", (val, student_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        if is_postgres():
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("student_set_archived: %s", e)
        return False


def student_delete_from_archive(student_id: int) -> bool:
    """Удалить ученика из архива: снять доступ родителей, пометить удалённым. Платежи и отчёты сохраняются (student_id остаётся в таблицах)."""
    _ensure_students_archived_column_pg()
    conn = get_connection()
    try:
        if is_postgres():
            conn.execute("DELETE FROM student_parents WHERE student_id = %s", (student_id,))
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "UPDATE students SET deleted_at = %s, updated_at = NOW() WHERE id = %s AND archived = TRUE",
                (now, student_id),
            )
        else:
            conn.execute("DELETE FROM student_parents WHERE student_id = ?", (student_id,))
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "UPDATE students SET deleted_at = ?, updated_at = datetime('now') WHERE id = ? AND archived = 1",
                (now, student_id),
            )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        if is_postgres():
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("student_delete_from_archive: %s", e)
        return False


def student_update(
    student_id: int,
    full_name: str | None = None,
    class_grade: int | None = None,
    class_letter: str | None = None,
    comment: str | None = None,
    photo_path: str | None = None,
) -> bool:
    """Обновить данные ученика (только переданные поля)."""
    conn = get_connection()
    updates = []
    params = []
    if full_name is not None:
        updates.append("full_name = ?")
        params.append(full_name.strip())
    if class_grade is not None:
        updates.append("class_grade = ?")
        params.append(class_grade)
    if class_letter is not None:
        updates.append("class_letter = ?")
        params.append((class_letter or "А").strip()[:10])
    if comment is not None:
        updates.append("comment = ?")
        params.append(comment.strip() if comment else None)
    if photo_path is not None:
        updates.append("photo_path = ?")
        params.append(photo_path.strip() if photo_path else None)
    if not updates:
        return True
    params.append(student_id)
    if is_postgres():
        q = "UPDATE students SET " + ", ".join(u.replace("?", "%s") for u in updates) + ", updated_at = NOW() WHERE id = %s"
    else:
        q = "UPDATE students SET " + ", ".join(updates) + ", updated_at = datetime('now') WHERE id = ?"
    try:
        cur = conn.execute(q, params)
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        if is_postgres():
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("student_update: %s", e)
        return False


def student_parents_remove(student_id: int, user_id: int) -> bool:
    """Отвязать родителя от ученика."""
    conn = get_connection()
    try:
        if is_postgres():
            cur = conn.execute("DELETE FROM student_parents WHERE student_id = %s AND user_id = %s", (student_id, user_id))
        else:
            cur = conn.execute("DELETE FROM student_parents WHERE student_id = ? AND user_id = ?", (student_id, user_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        if is_postgres():
            try:
                conn.rollback()
            except Exception:
                pass
        return False


def students_by_class_grade(class_grade: int) -> list[dict[str, Any]]:
    """Ученики одного класса (только активные, не в архиве, не удалённые)."""
    _ensure_students_archived_column_pg()
    conn = get_connection()
    where_archived = _archived_condition(False)
    where_deleted = _students_not_deleted_condition("s")
    rows = conn.execute(
        f"""
        SELECT s.*, u.full_name AS parent_name, u.email AS parent_email
        FROM students s
        LEFT JOIN users u ON s.parent_id = u.id
        WHERE s.class_grade = ? AND {where_archived} AND {where_deleted}
        ORDER BY s.class_letter, s.full_name
        """,
        (class_grade,),
    ).fetchall()
    return [dict(r) for r in rows]


def students_with_balance_by_class(class_grade: int) -> list[dict[str, Any]]:
    """Ученики класса с полем balance (сквозной баланс) и debt (задолженность, если balance < 0). Для учителя."""
    students = students_by_class_grade(class_grade)
    for s in students:
        bal = balance_total_for_student(s["id"])
        s["balance"] = round(bal, 2)
        s["debt"] = round(-bal, 2) if bal < 0 else 0
    return students


# --- Привязка учителя к классу и замещение ---

def teacher_class_set(class_grade: int, teacher_id: int) -> None:
    """Назначить классному руководителю класс. Один класс — один учитель."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO teacher_class (class_grade, teacher_id) VALUES (?, ?)
        ON CONFLICT(class_grade) DO UPDATE SET teacher_id = ?, created_at = datetime('now')
        """,
        (class_grade, teacher_id, teacher_id),
    )
    conn.commit()


def teacher_class_get_by_class(class_grade: int) -> int | None:
    """User_id классного руководителя для класса, или None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT teacher_id FROM teacher_class WHERE class_grade = ?",
        (class_grade,),
    ).fetchone()
    return row[0] if row else None


def teacher_class_get_by_teacher(teacher_id: int) -> int | None:
    """Класс (class_grade), который ведёт учитель, или None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT class_grade FROM teacher_class WHERE teacher_id = ?",
        (teacher_id,),
    ).fetchone()
    return row[0] if row else None


def teacher_class_remove(class_grade: int) -> bool:
    """Снять классного руководителя с класса."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM teacher_class WHERE class_grade = ?", (class_grade,))
    conn.commit()
    return cur.rowcount > 0


def teacher_class_list_all() -> list[dict[str, Any]]:
    """Список всех классов 0–11 с классным руководителем (если назначен)."""
    conn = get_connection()
    # Все классы 0..11, слева присоединяем teacher_class и users
    result = []
    for cg in range(0, 12):
        row = conn.execute(
            """
            SELECT tc.teacher_id, u.full_name AS teacher_name
            FROM teacher_class tc
            JOIN users u ON tc.teacher_id = u.id
            WHERE tc.class_grade = ?
            """,
            (cg,),
        ).fetchone()
        result.append({
            "class_grade": cg,
            "teacher_id": row[0] if row else None,
            "teacher_name": row[1] if row else None,
        })
    return result


def teacher_substitute_set(
    class_grade: int,
    main_teacher_id: int,
    substitute_teacher_id: int,
    valid_until: str | None = None,
) -> None:
    """Назначить замещение: другой учитель ведёт класс на время (valid_until — дата окончания, опционально)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO teacher_substitute (class_grade, main_teacher_id, substitute_teacher_id, valid_until)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(class_grade, main_teacher_id) DO UPDATE SET
            substitute_teacher_id = excluded.substitute_teacher_id,
            valid_until = excluded.valid_until
        """,
        (class_grade, main_teacher_id, substitute_teacher_id, valid_until or None),
    )
    conn.commit()


def teacher_substitute_remove(class_grade: int, main_teacher_id: int) -> bool:
    """Снять замещение по классу и классному руководителю."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM teacher_substitute WHERE class_grade = ? AND main_teacher_id = ?",
        (class_grade, main_teacher_id),
    )
    conn.commit()
    return cur.rowcount > 0


def teacher_substitute_list_for_teacher(teacher_id: int) -> list[dict[str, Any]]:
    """Классы, которые учитель ведёт по замещению (активные: valid_until пусто или >= сегодня)."""
    from datetime import date
    today = date.today().isoformat()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ts.class_grade, ts.valid_until, u.full_name AS main_teacher_name
        FROM teacher_substitute ts
        JOIN users u ON ts.main_teacher_id = u.id
        WHERE ts.substitute_teacher_id = ? AND (ts.valid_until IS NULL OR ts.valid_until >= ?)
        ORDER BY ts.class_grade
        """,
        (teacher_id, today),
    ).fetchall()
    return [dict(r) for r in rows]


def teacher_substitute_get(class_grade: int, main_teacher_id: int) -> dict[str, Any] | None:
    """Текущее замещение по классу и классному руководителю."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT ts.*, u.full_name AS substitute_teacher_name
        FROM teacher_substitute ts
        JOIN users u ON ts.substitute_teacher_id = u.id
        WHERE ts.class_grade = ? AND ts.main_teacher_id = ?
        """,
        (class_grade, main_teacher_id),
    ).fetchone()
    return dict(row) if row else None


def students_for_teacher(teacher_id: int) -> dict[str, Any]:
    """
    Ученики для учителя: свой класс (если назначен) + классы по замещению.
    Возвращает: my_class_grade, my_class_students, substitute_classes (список {class_grade, label, teacher_name, students}).
    """
    result: dict[str, Any] = {
        "my_class_grade": None,
        "my_class_students": [],
        "substitute_classes": [],
    }
    my_grade = teacher_class_get_by_teacher(teacher_id)
    if my_grade is not None:
        result["my_class_grade"] = my_grade
        result["my_class_students"] = students_by_class_grade(my_grade)
    for sub in teacher_substitute_list_for_teacher(teacher_id):
        cg = sub["class_grade"]
        label = f"{format_class_grade(cg)} (замещение)"
        result["substitute_classes"].append({
            "class_grade": cg,
            "label": label,
            "teacher_name": sub.get("main_teacher_name", ""),
            "valid_until": sub.get("valid_until"),
            "students": students_by_class_grade(cg),
        })
    return result


def teacher_can_charge_student(teacher_id: int, student_id: int) -> bool:
    """Может ли учитель вносить списание для данного ученика (свой класс или класс по замещению)."""
    student = student_by_id(student_id)
    if not student:
        return False
    cg = student.get("class_grade")
    if cg is None:
        return False
    my_grade = teacher_class_get_by_teacher(teacher_id)
    if my_grade == cg:
        return True
    for sub in teacher_substitute_list_for_teacher(teacher_id):
        if sub.get("class_grade") == cg:
            return True
    return False


# --- Обратная связь и текущие дела ---

def feedback_create(from_user_id: int, message_text: str) -> int:
    """Создать обратную связь от пользователя. Возвращает id."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO feedback (from_user_id, message_text) VALUES (?, ?)",
        (from_user_id, (message_text or "").strip()),
    )
    conn.commit()
    return cur.lastrowid


def feedback_list(status_filter: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Список обратной связи для админки. status_filter: new, read, replied, deleted, moved или None (все кроме deleted)."""
    conn = get_connection()
    q = """
        SELECT f.*, u.full_name AS from_name, u.email AS from_email
        FROM feedback f
        JOIN users u ON f.from_user_id = u.id
        WHERE 1=1
    """
    params: list[Any] = []
    if status_filter:
        q += " AND f.status = ?"
        params.append(status_filter)
    else:
        q += " AND f.status != 'deleted'"
    q += " ORDER BY f.created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def feedback_by_id(feedback_id: int) -> dict[str, Any] | None:
    """Одна запись обратной связи по id."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT f.*, u.full_name AS from_name, u.email AS from_email
        FROM feedback f
        JOIN users u ON f.from_user_id = u.id
        WHERE f.id = ?
        """,
        (feedback_id,),
    ).fetchone()
    return dict(row) if row else None


def feedback_mark_read(feedback_id: int) -> bool:
    """Пометить обратную связь как прочитанную."""
    conn = get_connection()
    cur = conn.execute(
        "UPDATE feedback SET status = 'read' WHERE id = ? AND status = 'new'",
        (feedback_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def feedback_reply(feedback_id: int, admin_reply: str, replied_by_id: int) -> bool:
    """Ответить на обратную связь."""
    from datetime import datetime
    conn = get_connection()
    cur = conn.execute(
        "UPDATE feedback SET status = 'replied', admin_reply = ?, replied_at = ?, replied_by_id = ? WHERE id = ?",
        (admin_reply.strip(), datetime.utcnow().isoformat(), replied_by_id, feedback_id),
    )
    conn.commit()
    return cur.rowcount > 0


def feedback_delete(feedback_id: int) -> bool:
    """Пометить обратную связь как удалённую (soft delete)."""
    conn = get_connection()
    cur = conn.execute("UPDATE feedback SET status = 'deleted' WHERE id = ?", (feedback_id,))
    conn.commit()
    return cur.rowcount > 0


def feedback_mark_moved(feedback_id: int) -> bool:
    """Пометить, что обратная связь перенесена в текущие дела."""
    conn = get_connection()
    cur = conn.execute("UPDATE feedback SET status = 'moved' WHERE id = ?", (feedback_id,))
    conn.commit()
    return cur.rowcount > 0


def feedback_list_by_user(from_user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Обратная связь, отправленная пользователем (для раздела «Мои обращения»)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT f.id, f.message_text, f.created_at, f.status, f.admin_reply, f.replied_at
        FROM feedback f
        WHERE f.from_user_id = ? AND f.status != 'deleted'
        ORDER BY f.created_at DESC
        LIMIT ?
        """,
        (from_user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Текущие дела (задачи) ---

def task_create(
    title: str,
    description: str,
    contact_info: str,
    created_by_id: int,
    source_feedback_id: int | None = None,
) -> int:
    """Создать задачу в текущих делах. Возвращает id."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO tasks (title, description, contact_info, created_by_id, source_feedback_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title.strip(), (description or "").strip(), (contact_info or "").strip(), created_by_id, source_feedback_id),
    )
    conn.commit()
    return cur.lastrowid


def task_update(task_id: int, title: str = None, description: str = None, contact_info: str = None, status: str = None) -> bool:
    """Обновить задачу. None — не менять."""
    conn = get_connection()
    updates = []
    params = []
    if title is not None:
        updates.append("title = ?")
        params.append(title.strip())
    if description is not None:
        updates.append("description = ?")
        params.append(description.strip())
    if contact_info is not None:
        updates.append("contact_info = ?")
        params.append(contact_info.strip())
    if status is not None and status in ("open", "in_progress", "done"):
        updates.append("status = ?")
        params.append(status)
    if not updates:
        return True
    updates.append("updated_at = datetime('now')")
    params.append(task_id)
    cur = conn.execute(
        f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def task_by_id(task_id: int) -> dict[str, Any] | None:
    """Задача по id с данными создателя."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT t.*, u.full_name AS created_by_name
        FROM tasks t
        JOIN users u ON t.created_by_id = u.id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


def task_list(limit: int = 100) -> list[dict[str, Any]]:
    """Список всех задач (текущие дела)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT t.*, u.full_name AS created_by_name
        FROM tasks t
        JOIN users u ON t.created_by_id = u.id
        ORDER BY t.updated_at DESC, t.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def task_delete(task_id: int) -> bool:
    """Удалить задачу."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    return cur.rowcount > 0


def task_comment_add(task_id: int, user_id: int, comment_text: str) -> int:
    """Добавить комментарий к задаче. Возвращает id комментария."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO task_comments (task_id, user_id, comment_text) VALUES (?, ?, ?)",
        (task_id, user_id, (comment_text or "").strip()),
    )
    conn.commit()
    return cur.lastrowid


def task_comments_list(task_id: int) -> list[dict[str, Any]]:
    """Список комментариев к задаче (с именем автора), по дате создания."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT c.id, c.task_id, c.user_id, c.comment_text, c.created_at, u.full_name AS author_name
        FROM task_comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.task_id = ?
        ORDER BY c.created_at ASC
        """,
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def user_can_access_tasks(user_id: int) -> bool:
    """Есть ли у пользователя доступ к разделу «Текущие дела» (веб и бот).
    True для администратора, директора, бухгалтера; для заместителя — только при наличии права «tasks».
    """
    user = user_by_id(user_id)
    if not user:
        return False
    role = user.get("role")
    if role in (ROLE_ADMINISTRATOR, ROLE_DIRECTOR, ROLE_ACCOUNTANT):
        return True
    if role == ROLE_DEPUTY_DIRECTOR:
        return deputy_has_permission(user_id, "tasks")
    return False


# --- Группы для назначения на задачи (родители, бухгалтерия, сторонние и т.д.) ---

def task_group_create(name: str) -> int:
    """Создать группу для задач."""
    conn = get_connection()
    cur = conn.execute("INSERT INTO task_groups (name) VALUES (?)", (name.strip(),))
    conn.commit()
    return cur.lastrowid


def task_group_list() -> list[dict[str, Any]]:
    """Список всех групп."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM task_groups ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def task_group_by_id(group_id: int) -> dict[str, Any] | None:
    """Группа по id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM task_groups WHERE id = ?", (group_id,)).fetchone()
    return dict(row) if row else None


def task_group_update(group_id: int, name: str) -> bool:
    """Переименовать группу."""
    conn = get_connection()
    cur = conn.execute("UPDATE task_groups SET name = ? WHERE id = ?", (name.strip(), group_id))
    conn.commit()
    return cur.rowcount > 0


def task_group_delete(group_id: int) -> bool:
    """Удалить группу (назначения с ней снимаются)."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM task_groups WHERE id = ?", (group_id,))
    conn.commit()
    return cur.rowcount > 0


def task_group_members_list(group_id: int) -> list[dict[str, Any]]:
    """Участники группы (user_id, full_name, email, role)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT u.id, u.full_name, u.email, u.role
        FROM task_group_members tgm
        JOIN users u ON tgm.user_id = u.id
        WHERE tgm.group_id = ?
        ORDER BY u.full_name
        """,
        (group_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def task_group_add_member(group_id: int, user_id: int) -> bool:
    """Добавить пользователя в группу."""
    conn = get_connection()
    try:
        conn.execute("INSERT INTO task_group_members (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def task_group_remove_member(group_id: int, user_id: int) -> bool:
    """Убрать пользователя из группы."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM task_group_members WHERE group_id = ? AND user_id = ?", (group_id, user_id))
    conn.commit()
    return cur.rowcount > 0


# --- Назначения на задачу (кто может смотреть, кто исполнитель) ---

def task_assignment_add(task_id: int, group_id: int = None, user_id: int = None, can_view: bool = True, is_executor: bool = False) -> bool:
    """Добавить назначение: группу или пользователя на задачу (просмотр и/или исполнитель)."""
    if (group_id is None) == (user_id is None):
        return False  # Ровно один должен быть задан
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO task_assignments (task_id, group_id, user_id, can_view, is_executor) VALUES (?, ?, ?, ?, ?)",
            (task_id, group_id, user_id, 1 if can_view else 0, 1 if is_executor else 0),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def task_assignment_list(task_id: int) -> list[dict[str, Any]]:
    """Назначения по задаче: группы и пользователи с флагами can_view, is_executor."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ta.*, tg.name AS group_name, u.full_name AS user_name
        FROM task_assignments ta
        LEFT JOIN task_groups tg ON ta.group_id = tg.id
        LEFT JOIN users u ON ta.user_id = u.id
        WHERE ta.task_id = ?
        ORDER BY ta.group_id IS NOT NULL DESC, tg.name, u.full_name
        """,
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def task_assignment_remove(assignment_id: int) -> bool:
    """Снять назначение по id."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM task_assignments WHERE id = ?", (assignment_id,))
    conn.commit()
    return cur.rowcount > 0


def tasks_for_user(user_id: int) -> list[dict[str, Any]]:
    """Задачи, которые пользователь может видеть: назначен на него лично или входит в назначенную группу."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT DISTINCT t.*, u.full_name AS created_by_name
        FROM tasks t
        JOIN users u ON t.created_by_id = u.id
        JOIN task_assignments ta ON ta.task_id = t.id
        LEFT JOIN task_group_members tgm ON ta.group_id = tgm.group_id
        WHERE (ta.user_id = ? OR tgm.user_id = ?) AND ta.can_view = 1
        ORDER BY t.updated_at DESC
        LIMIT 100
        """,
        (user_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Справочник назначений платежей (бухгалтер добавляет варианты) ---

def payment_purpose_list() -> list[dict[str, Any]]:
    """Список назначений для выбора в форме платежа и отчётах (с ценой и типом начисления)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, code, name, sort_order, COALESCE(price, 0) AS price, COALESCE(charge_frequency, 'manual') AS charge_frequency FROM payment_purposes ORDER BY sort_order, name"
    ).fetchall()
    return [dict(r) for r in rows]


def payment_purpose_codes() -> set[str]:
    """Множество допустимых кодов назначений (для проверки при создании платежа)."""
    conn = get_connection()
    rows = conn.execute("SELECT code FROM payment_purposes").fetchall()
    return {r[0] for r in rows}


def payment_purpose_name_by_code(code: str) -> str:
    """Название назначения по коду (для отображения в боте и т.д.)."""
    conn = get_connection()
    row = conn.execute("SELECT name FROM payment_purposes WHERE code = ?", (code,)).fetchone()
    return row[0] if row else code


def payment_purpose_create(code: str, name: str, sort_order: int = 0, price: float = 0, charge_frequency: str = "manual") -> int:
    """Добавить назначение. code — значение в БД (латиница), name — отображаемое название; price и charge_frequency (monthly/daily/manual)."""
    conn = get_connection()
    code = code.strip().lower().replace(" ", "_") or "other"
    code = "".join(c for c in code if c.isalnum() or c == "_") or "other"
    if charge_frequency not in ("monthly", "daily", "manual"):
        charge_frequency = "manual"
    cur = conn.execute(
        "INSERT INTO payment_purposes (code, name, sort_order, price, charge_frequency) VALUES (?, ?, ?, ?, ?)",
        (code, name.strip() or code, sort_order, float(price), charge_frequency),
    )
    conn.commit()
    return cur.lastrowid


def payment_purpose_by_id(purpose_id: int) -> dict[str, Any] | None:
    """Назначение по id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM payment_purposes WHERE id = ?", (purpose_id,)).fetchone()
    return dict(row) if row else None


def payment_purpose_update(
    purpose_id: int, code: str, name: str, sort_order: int | None = None, price: float | None = None, charge_frequency: str | None = None
) -> bool:
    """Изменить назначение (в т.ч. цену и тип начисления)."""
    conn = get_connection()
    code = code.strip().lower().replace(" ", "_") or "other"
    code = "".join(c for c in code if c.isalnum() or c == "_") or "other"
    updates = ["code = ?", "name = ?"]
    params = [code, name.strip() or code]
    if sort_order is not None:
        updates.append("sort_order = ?")
        params.append(sort_order)
    if price is not None:
        updates.append("price = ?")
        params.append(float(price))
    if charge_frequency is not None and charge_frequency in ("monthly", "daily", "manual"):
        updates.append("charge_frequency = ?")
        params.append(charge_frequency)
    params.append(purpose_id)
    cur = conn.execute(
        f"UPDATE payment_purposes SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def payment_purpose_delete(purpose_id: int) -> tuple[bool, str]:
    """Удалить назначение. Возвращает (успех, сообщение об ошибке). Нельзя удалить, если есть платежи с этим назначением."""
    conn = get_connection()
    row = conn.execute("SELECT code FROM payment_purposes WHERE id = ?", (purpose_id,)).fetchone()
    if not row:
        return False, "Назначение не найдено"
    code = row[0]
    count = conn.execute("SELECT COUNT(*) FROM payments WHERE purpose = ?", (code,)).fetchone()[0]
    if count > 0:
        return False, f"Нельзя удалить: есть {count} платежей с этим назначением"
    count_charges = conn.execute("SELECT COUNT(*) FROM student_charges WHERE purpose_code = ?", (code,)).fetchone()[0]
    if count_charges > 0:
        return False, f"Нельзя удалить: есть {count_charges} списаний с этим назначением"
    conn.execute("DELETE FROM payment_purposes WHERE id = ?", (purpose_id,))
    conn.commit()
    return True, ""


# --- Платежи ---

def payment_create(
    student_id: int,
    amount: float,
    purpose: str,
    description: str | None = None,
    payment_type: str = "cash",
    bank_commission: float = 0,
    amount_received: float | None = None,
) -> int:
    """Создать платёж (статус pending). payment_type: cash/безнал/telegram_stars/telegram_provider; amount_received — сумма, поступившая на счёт (для безнала)."""
    if payment_type not in ("cash", "cashless", "telegram_stars", "telegram_provider"):
        payment_type = "cash"
    valid_codes = payment_purpose_codes()
    if purpose not in valid_codes:
        purpose = "food" if "food" in valid_codes else (list(valid_codes)[0] if valid_codes else "other")
    if amount_received is not None:
        received = amount_received
    elif payment_type == "cashless":
        received = amount - bank_commission
    elif payment_type in ("telegram_stars", "telegram_provider"):
        received = amount  # вся сумма зачисляется
    else:
        received = amount
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO payments (student_id, amount, purpose, description, status, payment_type, bank_commission, amount_received)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (student_id, amount, purpose, description or "", payment_type, bank_commission, received),
    )
    conn.commit()
    payment_id = cur.lastrowid
    logger.info(
        "AUDIT | db payment_create | payment_id=%s | student_id=%s | amount=%s | purpose=%s",
        payment_id, student_id, amount, purpose,
    )
    return payment_id


def payment_confirm(
    payment_id: int,
    confirmed_by_user_id: int,
    comment: str | None = None,
) -> bool:
    """Подтвердить платёж (только бухгалтер/директор). Для старых записей проставляем amount_received = amount."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        UPDATE payments
        SET status = 'confirmed', confirmed_by = ?, confirmed_at = ?, comment = ?,
            amount_received = COALESCE(amount_received, amount)
        WHERE id = ? AND status = 'pending'
        """,
        (confirmed_by_user_id, now, comment or "", payment_id),
    )
    conn.commit()
    if cur.rowcount > 0:
        logger.info(
            "AUDIT | db payment_confirm | payment_id=%s | confirmed_by=%s",
            payment_id, confirmed_by_user_id,
        )
    return cur.rowcount > 0


def payment_reject(
    payment_id: int,
    confirmed_by_user_id: int,
    comment: str | None = None,
) -> bool:
    """Отклонить платёж."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        UPDATE payments
        SET status = 'rejected', confirmed_by = ?, confirmed_at = ?, comment = ?
        WHERE id = ? AND status = 'pending'
        """,
        (confirmed_by_user_id, now, comment or "", payment_id),
    )
    conn.commit()
    return cur.rowcount > 0


def payments_by_student(student_id: int) -> list[dict[str, Any]]:
    """История платежей по ученику."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.*, u.full_name AS confirmed_by_name
        FROM payments p
        LEFT JOIN users u ON p.confirmed_by = u.id
        WHERE p.student_id = ?
        ORDER BY p.created_at DESC
        """,
        (student_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def payments_pending_all() -> list[dict[str, Any]]:
    """Все неподтверждённые платежи (для бухгалтера)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.*, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM payments p
        JOIN students s ON p.student_id = s.id
        WHERE p.status = 'pending'
        ORDER BY p.created_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


def payment_by_id(payment_id: int) -> dict[str, Any] | None:
    """Платёж по id."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT p.*, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM payments p
        JOIN students s ON p.student_id = s.id
        WHERE p.id = ?
        """,
        (payment_id,),
    ).fetchone()
    return dict(row) if row else None


def balance_canteen_for_student(student_id: int) -> float:
    """Баланс питания по ученику: пополнения (подтверждённые платежи food) минус списания. Для обратной совместимости."""
    return balance_total_for_student(student_id)


def _scalar_float(row, key: str = "s") -> float:
    """Взять число из строки результата (SQLite Row или PostgreSQL dict). Не зависит от row[0] у dict."""
    if not row:
        return 0.0
    try:
        if hasattr(row, "keys") and key in row:
            v = row[key]
        elif hasattr(row, "values"):
            v = next(iter(row.values()), None)
        else:
            v = row[0]
    except (KeyError, TypeError, IndexError):
        v = None
    return float(v) if v is not None else 0.0


def balance_total_for_student(student_id: int) -> float:
    """Сквозной баланс ученика: все подтверждённые пополнения минус все списания (питание ученика и родителя с этого ребёнка, обучение, расходники, продленка и т.д.)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(amount_received, amount)), 0) AS s FROM payments WHERE student_id = ? AND status = 'confirmed'",
        (student_id,),
    ).fetchone()
    income = _scalar_float(row)
    row2 = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM nutrition_deductions WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    nutrition_student = _scalar_float(row2)
    row3 = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM nutrition_deductions WHERE charge_to_student_id = ?",
        (student_id,),
    ).fetchone()
    nutrition_charge_to = _scalar_float(row3)
    row4 = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM student_charges WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    charges = _scalar_float(row4)
    return income - nutrition_student - nutrition_charge_to - charges


# --- Списания по услугам (обучение, расходники, продленка и т.д.) ---

def student_charge_create(
    student_id: int,
    purpose_code: str,
    amount: float,
    charge_date: str,
    created_by: int | None = None,
    description: str | None = None,
) -> int:
    """Внести списание с баланса ученика (обучение, расходники, доп занятия, продленка, прочее). Возвращает id записи."""
    conn = get_connection()
    valid = payment_purpose_codes()
    if purpose_code not in valid:
        purpose_code = "consumables" if "consumables" in valid else list(valid)[0]
    cur = conn.execute(
        """
        INSERT INTO student_charges (student_id, purpose_code, amount, charge_date, created_by, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (student_id, purpose_code, amount, charge_date, created_by, description or ""),
    )
    conn.commit()
    return cur.lastrowid


def student_charges_for_student(
    student_id: int, date_from: str | None = None, date_to: str | None = None
) -> list[dict[str, Any]]:
    """Список списаний по ученику за период (с названием назначения)."""
    conn = get_connection()
    q = """
        SELECT sc.*, COALESCE(pp.name, sc.purpose_code) AS purpose_name
        FROM student_charges sc
        LEFT JOIN payment_purposes pp ON pp.code = sc.purpose_code
        WHERE sc.student_id = ?
        """
    params: list[Any] = [student_id]
    if date_from:
        q += " AND date(sc.charge_date) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(sc.charge_date) <= date(?)"
        params.append(date_to)
    q += " ORDER BY sc.charge_date DESC, sc.id DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def education_price_get() -> float:
    """Цена за обучение (ежемесячное начисление) из справочника назначений."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(price, 0) FROM payment_purposes WHERE code = 'education'"
    ).fetchone()
    return float(row[0]) if row else 0.0


def process_education_charges_for_month(month_date: str) -> int:
    """Начислить обучение за учебный месяц (1-е число): по всем ученикам одна сумма из справочника. month_date — дата 1-го числа месяца (YYYY-MM-DD). Возвращает число созданных списаний."""
    _ensure_students_archived_column_pg()
    price = education_price_get()
    if price <= 0:
        return 0
    conn = get_connection()
    where_archived = _archived_condition(False, "")
    rows = conn.execute(
        f"SELECT id FROM students WHERE {where_archived} ORDER BY id"
    ).fetchall()
    created = 0
    for row in rows:
        sid = row[0]
        # Проверяем, не начисляли ли уже за этот месяц
        exists = conn.execute(
            "SELECT 1 FROM student_charges WHERE student_id = ? AND purpose_code = 'education' AND date(charge_date) = date(?)",
            (sid, month_date),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO student_charges (student_id, purpose_code, amount, charge_date, description)
                VALUES (?, 'education', ?, ?, ?)
                """,
                (sid, price, month_date, "Начисление за месяц"),
            )
            created += 1
    conn.commit()
    return created


# --- Цены на питание (завтрак, обед, ужин) ---

def meal_prices_get_all() -> dict[str, float]:
    """Текущие цены на завтрак, обед, ужин."""
    conn = get_connection()
    rows = conn.execute("SELECT meal_type, price FROM meal_prices").fetchall()
    return {r[0]: float(r[1]) for r in rows} if rows else {"breakfast": 0, "lunch": 0, "dinner": 0}


def meal_prices_set(meal_type: str, price: float) -> None:
    """Установить цену на приём пищи (breakfast, lunch, dinner)."""
    if meal_type not in ("breakfast", "lunch", "dinner"):
        return
    conn = get_connection()
    conn.execute(
        "INSERT INTO meal_prices (meal_type, price, updated_at) VALUES (?, ?, datetime('now')) ON CONFLICT(meal_type) DO UPDATE SET price = ?, updated_at = datetime('now')",
        (meal_type, price, price),
    )
    conn.commit()


# --- План питания ученика (родитель настраивает по умолчанию) ---

def student_meal_plan_get(student_id: int) -> dict[str, Any] | None:
    """План питания ученика: завтрак/обед/ужин вкл/выкл (последний по дате вступления в силу)."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT student_id, has_breakfast, has_lunch, has_dinner FROM student_meal_plan
        WHERE student_id = ? ORDER BY effective_from DESC LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    if not row:
        return None
    return {"student_id": row[0], "has_breakfast": bool(row[1]), "has_lunch": bool(row[2]), "has_dinner": bool(row[3])}


def student_meal_plan_set(
    student_id: int,
    has_breakfast: bool = True,
    has_lunch: bool = True,
    has_dinner: bool = False,
    effective_from: str = "",
) -> None:
    """Сохранить план питания ученика. effective_from — с какой даты действует (YYYY-MM-DD); после дедлайна 8:00 передают завтра."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO student_meal_plan (student_id, effective_from, has_breakfast, has_lunch, has_dinner, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(student_id, effective_from) DO UPDATE SET
            has_breakfast = excluded.has_breakfast,
            has_lunch = excluded.has_lunch,
            has_dinner = excluded.has_dinner,
            updated_at = datetime('now')
        """,
        (student_id, effective_from, 1 if has_breakfast else 0, 1 if has_lunch else 0, 1 if has_dinner else 0),
    )
    conn.commit()


# --- Родитель на питании (сам себя ставит) ---

def parent_meal_plan_get(user_id: int) -> dict[str, Any] | None:
    """План питания родителя (последний по дате вступления в силу)."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT user_id, has_breakfast, has_lunch, has_dinner FROM parent_meal_plan
        WHERE user_id = ? ORDER BY effective_from DESC LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {"user_id": row[0], "has_breakfast": bool(row[1]), "has_lunch": bool(row[2]), "has_dinner": bool(row[3])}


def parent_meal_plan_set(
    user_id: int,
    has_breakfast: bool = False,
    has_lunch: bool = False,
    has_dinner: bool = False,
    effective_from: str = "",
) -> None:
    """Включить родителя в питании. effective_from — с какой даты действует (после дедлайна 8:00 передают завтра)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO parent_meal_plan (user_id, effective_from, has_breakfast, has_lunch, has_dinner, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, effective_from) DO UPDATE SET
            has_breakfast = excluded.has_breakfast,
            has_lunch = excluded.has_lunch,
            has_dinner = excluded.has_dinner,
            updated_at = datetime('now')
        """,
        (user_id, effective_from, 1 if has_breakfast else 0, 1 if has_lunch else 0, 1 if has_dinner else 0),
    )
    conn.commit()


def parent_meal_plan_remove(user_id: int, effective_from: str = "") -> None:
    """Убрать родителя из питания с даты effective_from (запись с нулями по приёмам пищи)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO parent_meal_plan (user_id, effective_from, has_breakfast, has_lunch, has_dinner, updated_at)
        VALUES (?, ?, 0, 0, 0, datetime('now'))
        ON CONFLICT(user_id, effective_from) DO UPDATE SET
            has_breakfast = 0, has_lunch = 0, has_dinner = 0, updated_at = datetime('now')
        """,
        (user_id, effective_from),
    )
    conn.commit()


# --- Списания за питание ---

def nutrition_deduction_create(
    student_id: int | None = None,
    user_id: int | None = None,
    charge_to_student_id: int | None = None,
    deduction_date: str = "",
    amount: float = 0,
    breakfast_amt: float = 0,
    lunch_amt: float = 0,
    dinner_amt: float = 0,
    is_manual: bool = False,
    reason: str | None = None,
    created_by: int | None = None,
) -> int:
    """Создать списание (авто по плану или ручное). Для питания родителя укажите charge_to_student_id — с баланса какого ученика списать."""
    assert (student_id is not None) or (user_id is not None)
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO nutrition_deductions (student_id, user_id, charge_to_student_id, deduction_date, amount, breakfast_amt, lunch_amt, dinner_amt, is_manual, reason, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (student_id, user_id, charge_to_student_id, deduction_date, amount, breakfast_amt, lunch_amt, dinner_amt, 1 if is_manual else 0, reason, created_by),
    )
    conn.commit()
    return cur.lastrowid


def nutrition_deductions_for_student(student_id: int, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """Списания по ученику за период (для отчёта родителю)."""
    conn = get_connection()
    q = "SELECT * FROM nutrition_deductions WHERE student_id = ?"
    params: list[Any] = [student_id]
    if date_from:
        q += " AND date(deduction_date) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(deduction_date) <= date(?)"
        params.append(date_to)
    q += " ORDER BY deduction_date DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def nutrition_deductions_for_parent(user_id: int, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """Списания по родителю (когда родитель сам на питании)."""
    conn = get_connection()
    q = "SELECT * FROM nutrition_deductions WHERE user_id = ?"
    params = [user_id]
    if date_from:
        q += " AND date(deduction_date) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(deduction_date) <= date(?)"
        params.append(date_to)
    q += " ORDER BY deduction_date DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def nutrition_deductions_for_student_charge_to(
    student_id: int, date_from: str | None = None, date_to: str | None = None
) -> list[dict[str, Any]]:
    """Списания за питание родителя, отнесённые на баланс данного ученика (charge_to_student_id)."""
    conn = get_connection()
    q = "SELECT * FROM nutrition_deductions WHERE charge_to_student_id = ?"
    params: list[Any] = [student_id]
    if date_from:
        q += " AND date(deduction_date) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(deduction_date) <= date(?)"
        params.append(date_to)
    q += " ORDER BY deduction_date DESC"
    try:
        rows = conn.execute(q, params).fetchall()
    except Exception:
        return []  # колонка charge_to_student_id может отсутствовать в старых БД
    return [dict(r) for r in rows]


# --- Вид для столовой: кто на питании на дату (по планам) ---

def canteen_view_for_date(target_date: str) -> dict[str, Any]:
    """По дате: список учеников по классам с планом (завтрак/обед/ужин) и список родителей на питании.
    Используется план, действующий на target_date (effective_from <= target_date, последний по дате)."""
    _ensure_students_archived_column_pg()
    conn = get_connection()
    where_archived = _archived_condition(False, "s")
    students_rows = conn.execute(
        f"""
        SELECT s.id, s.full_name, s.class_grade,
               COALESCE((SELECT smp.has_breakfast FROM student_meal_plan smp WHERE smp.student_id = s.id AND smp.effective_from <= ? ORDER BY smp.effective_from DESC LIMIT 1), 1) AS has_breakfast,
               COALESCE((SELECT smp.has_lunch FROM student_meal_plan smp WHERE smp.student_id = s.id AND smp.effective_from <= ? ORDER BY smp.effective_from DESC LIMIT 1), 1) AS has_lunch,
               COALESCE((SELECT smp.has_dinner FROM student_meal_plan smp WHERE smp.student_id = s.id AND smp.effective_from <= ? ORDER BY smp.effective_from DESC LIMIT 1), 0) AS has_dinner
        FROM students s
        WHERE {where_archived}
        ORDER BY s.class_grade, s.full_name
        """,
        (target_date, target_date, target_date),
    ).fetchall()
    students = [dict(r) for r in students_rows]
    by_class: dict[str, list[dict]] = {}
    counts_students = {"breakfast": 0, "lunch": 0, "dinner": 0}
    by_class_counts: dict[str, dict[str, int]] = {}
    for r in students:
        key = str(r["class_grade"])
        if key not in by_class:
            by_class[key] = []
            by_class_counts[key] = {"breakfast": 0, "lunch": 0, "dinner": 0}
        by_class[key].append({
            "id": r["id"],
            "full_name": r["full_name"],
            "class_grade": r["class_grade"],
            "has_breakfast": bool(r["has_breakfast"]),
            "has_lunch": bool(r["has_lunch"]),
            "has_dinner": bool(r["has_dinner"]),
        })
        if r.get("has_breakfast"):
            counts_students["breakfast"] += 1
            by_class_counts[key]["breakfast"] += 1
        if r.get("has_lunch"):
            counts_students["lunch"] += 1
            by_class_counts[key]["lunch"] += 1
        if r.get("has_dinner"):
            counts_students["dinner"] += 1
            by_class_counts[key]["dinner"] += 1
    counts = {"breakfast": counts_students["breakfast"], "lunch": counts_students["lunch"], "dinner": counts_students["dinner"]}
    # Родители на питании на target_date: план с effective_from <= target_date (последний по дате), хотя бы один приём
    parents = conn.execute(
        """
        SELECT u.id, u.full_name, p.has_breakfast, p.has_lunch, p.has_dinner
        FROM users u
        JOIN (
            SELECT pmp.user_id, pmp.has_breakfast, pmp.has_lunch, pmp.has_dinner
            FROM parent_meal_plan pmp
            WHERE pmp.effective_from <= ?
            AND pmp.effective_from = (SELECT max(p2.effective_from) FROM parent_meal_plan p2 WHERE p2.user_id = pmp.user_id AND p2.effective_from <= ?)
        ) p ON p.user_id = u.id
        WHERE u.is_active = 1 AND (p.has_breakfast = 1 OR p.has_lunch = 1 OR p.has_dinner = 1)
        ORDER BY u.full_name
        """,
        (target_date, target_date),
    ).fetchall()
    parent_list = [{"id": r["id"], "full_name": r["full_name"], "has_breakfast": bool(r["has_breakfast"]), "has_lunch": bool(r["has_lunch"]), "has_dinner": bool(r["has_dinner"])} for r in parents]
    counts_adults = {"breakfast": 0, "lunch": 0, "dinner": 0}
    for r in parent_list:
        if r["has_breakfast"]:
            counts["breakfast"] += 1
            counts_adults["breakfast"] += 1
        if r["has_lunch"]:
            counts["lunch"] += 1
            counts_adults["lunch"] += 1
        if r["has_dinner"]:
            counts["dinner"] += 1
            counts_adults["dinner"] += 1
    return {
        "date": target_date,
        "by_class": by_class,
        "students": students,
        "parents": parent_list,
        "counts": counts,
        "counts_students": counts_students,
        "counts_adults": counts_adults,
        "by_class_counts": by_class_counts,
        "class_grades_sorted": sorted(by_class.keys(), key=lambda k: int(k) if k.isdigit() else -1),
    }


# --- Начисление списаний за дату (по планам и ценам) — вызывать ежедневно ---

def _parse_date_weekday(date_str: str) -> int | None:
    """День недели для даты YYYY-MM-DD: 0=пн, 6=вс. None если дата невалидна."""
    try:
        y, m, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
        return datetime(y, m, d).weekday()  # 0=Mon, 6=Sun
    except (ValueError, IndexError):
        return None


def should_charge_nutrition_for_date(date_str: str) -> bool:
    """
    Нужно ли списывать питание за эту дату.
    По умолчанию: не списываем в субботу (5) и воскресенье (6).
    Если в nutrition_calendar есть запись — используем её (skip_charge = не списывать).
    """
    wd = _parse_date_weekday(date_str)
    if wd is None:
        return False
    conn = get_connection()
    if is_postgres():
        row = conn.execute(
            "SELECT skip_charge FROM nutrition_calendar WHERE the_date = %s",
            (date_str,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT skip_charge FROM nutrition_calendar WHERE the_date = ?",
            (date_str,),
        ).fetchone()
    if row is not None:
        return not (row[0] if is_postgres() else bool(row[0]))
    # По умолчанию: не списывать в сб и вс
    return wd not in (5, 6)


def nutrition_calendar_get(date_str: str) -> bool | None:
    """Получить переопределение для даты: True = не списывать, False = списывать, None = нет записи (по умолчанию сб/вс не списывать)."""
    conn = get_connection()
    if is_postgres():
        row = conn.execute(
            "SELECT skip_charge FROM nutrition_calendar WHERE the_date = %s",
            (date_str,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT skip_charge FROM nutrition_calendar WHERE the_date = ?",
            (date_str,),
        ).fetchone()
    if row is None:
        return None
    return bool(row[0]) if is_postgres() else bool(row[0])


def nutrition_calendar_set(date_str: str, skip_charge: bool) -> None:
    """Установить для даты: списывать (False) или не списывать (True)."""
    conn = get_connection()
    if is_postgres():
        conn.execute(
            """
            INSERT INTO nutrition_calendar (the_date, skip_charge) VALUES (%s, %s)
            ON CONFLICT (the_date) DO UPDATE SET skip_charge = EXCLUDED.skip_charge
            """,
            (date_str, skip_charge),
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO nutrition_calendar (the_date, skip_charge) VALUES (?, ?)
            """,
            (date_str, 1 if skip_charge else 0),
        )
    conn.commit()


def nutrition_calendar_get_range(date_from: str, date_to: str) -> list[dict]:
    """Список переопределений в диапазоне дат. Каждый элемент: {'date': 'YYYY-MM-DD', 'skip_charge': bool}."""
    conn = get_connection()
    if is_postgres():
        rows = conn.execute(
            "SELECT the_date::text, skip_charge FROM nutrition_calendar WHERE the_date >= %s AND the_date <= %s ORDER BY the_date",
            (date_from, date_to),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT the_date, skip_charge FROM nutrition_calendar WHERE the_date >= ? AND the_date <= ? ORDER BY the_date",
            (date_from, date_to),
        ).fetchall()
    return [{"date": str(r[0]), "skip_charge": bool(r[1])} for r in rows]


def process_nutrition_deductions_for_date(target_date: str) -> int:
    """Создать списания за дату по плану, действующему на эту дату (effective_from <= target_date), и текущим ценам."""
    if not should_charge_nutrition_for_date(target_date):
        return 0
    prices = meal_prices_get_all()
    conn = get_connection()
    created = 0
    students_plan_sql = """
        SELECT s.id,
               COALESCE((SELECT smp.has_breakfast FROM student_meal_plan smp WHERE smp.student_id = s.id AND smp.effective_from <= ? ORDER BY smp.effective_from DESC LIMIT 1), 1),
               COALESCE((SELECT smp.has_lunch FROM student_meal_plan smp WHERE smp.student_id = s.id AND smp.effective_from <= ? ORDER BY smp.effective_from DESC LIMIT 1), 1),
               COALESCE((SELECT smp.has_dinner FROM student_meal_plan smp WHERE smp.student_id = s.id AND smp.effective_from <= ? ORDER BY smp.effective_from DESC LIMIT 1), 0)
        FROM students s
    """
    for row in conn.execute(students_plan_sql, (target_date, target_date, target_date)).fetchall():
        student_id, b, l, d = row[0], bool(row[1]), bool(row[2]), bool(row[3])
        amt_b = prices.get("breakfast", 0) if b else 0
        amt_l = prices.get("lunch", 0) if l else 0
        amt_d = prices.get("dinner", 0) if d else 0
        total = amt_b + amt_l + amt_d
        if total <= 0:
            continue
        existing = conn.execute(
            "SELECT 1 FROM nutrition_deductions WHERE student_id = ? AND deduction_date = ?",
            (student_id, target_date),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO nutrition_deductions (student_id, deduction_date, amount, breakfast_amt, lunch_amt, dinner_amt, is_manual) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (student_id, target_date, total, amt_b, amt_l, amt_d),
        )
        created += 1
    parents_plan_sql = """
        SELECT p.user_id, p.has_breakfast, p.has_lunch, p.has_dinner
        FROM (
            SELECT pmp.user_id, pmp.has_breakfast, pmp.has_lunch, pmp.has_dinner
            FROM parent_meal_plan pmp
            WHERE pmp.effective_from <= ?
            AND pmp.effective_from = (SELECT max(p2.effective_from) FROM parent_meal_plan p2 WHERE p2.user_id = pmp.user_id AND p2.effective_from <= ?)
        ) p
        WHERE p.has_breakfast = 1 OR p.has_lunch = 1 OR p.has_dinner = 1
    """
    for row in conn.execute(parents_plan_sql, (target_date, target_date)).fetchall():
        uid, b, l, d = row[0], bool(row[1]), bool(row[2]), bool(row[3])
        amt_b = prices.get("breakfast", 0) if b else 0
        amt_l = prices.get("lunch", 0) if l else 0
        amt_d = prices.get("dinner", 0) if d else 0
        total = amt_b + amt_l + amt_d
        if total <= 0:
            continue
        existing = conn.execute(
            "SELECT 1 FROM nutrition_deductions WHERE user_id = ? AND deduction_date = ?",
            (uid, target_date),
        ).fetchone()
        if existing:
            continue
        # Питание родителя списывается с баланса первого привязанного ученика
        charge_to = conn.execute(
            "SELECT student_id FROM student_parents WHERE user_id = ? ORDER BY student_id LIMIT 1",
            (uid,),
        ).fetchone()
        charge_to_student_id = charge_to[0] if charge_to else None
        conn.execute(
            "INSERT INTO nutrition_deductions (user_id, charge_to_student_id, deduction_date, amount, breakfast_amt, lunch_amt, dinner_amt, is_manual) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (uid, charge_to_student_id, target_date, total, amt_b, amt_l, amt_d),
        )
        created += 1
    conn.commit()
    return created


# --- Бухгалтерия (учебный год сентябрь–июль) ---

def school_year_period() -> tuple[str, str]:
    """Начало и конец текущего учебного года (сентябрь – июль). Возвращает (date_from, date_to)."""
    now = datetime.utcnow()
    y, m = now.year, now.month
    if m >= 9:
        start = f"{y}-09-01"
        end = f"{y + 1}-07-31"
    else:
        start = f"{y - 1}-09-01"
        end = f"{y}-07-31"
    return start, end


def school_year_key() -> str:
    """Ключ периода для входящего сальдо: год начала учебного года (2024 = сентябрь 2024 – июль 2025)."""
    now = datetime.utcnow()
    y, m = now.year, now.month
    return str(y if m >= 9 else y - 1)


def accounting_get_opening_balance(period_key: str) -> float:
    """Входящее сальдо на начало периода (учебного года)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT balance FROM accounting_opening_balance WHERE period_key = ?",
        (period_key,),
    ).fetchone()
    return float(row["balance"]) if row else 0.0


def accounting_set_opening_balance(period_key: str, balance: float) -> None:
    """Установить входящее сальдо на начало учебного года."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO accounting_opening_balance (period_key, balance, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(period_key) DO UPDATE SET balance = ?, updated_at = datetime('now')
        """,
        (period_key, balance, balance),
    )
    conn.commit()


def accounting_expense_create(amount: float, reason: str, expense_date: str, created_by: int) -> int:
    """Внести списание (расход)."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO accounting_expense (amount, reason, expense_date, created_by) VALUES (?, ?, ?, ?)",
        (amount, reason, expense_date, created_by),
    )
    conn.commit()
    return cur.lastrowid


def accounting_expenses_list(date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """Список списаний за период."""
    conn = get_connection()
    q = "SELECT e.*, u.full_name AS created_by_name FROM accounting_expense e LEFT JOIN users u ON e.created_by = u.id WHERE 1=1"
    params: list[Any] = []
    if date_from:
        q += " AND e.expense_date >= ?"
        params.append(date_from)
    if date_to:
        q += " AND e.expense_date <= ?"
        params.append(date_to)
    q += " ORDER BY e.expense_date DESC, e.id DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def accounting_income_extra_create(amount: float, comment: str, income_date: str, created_by: int) -> int:
    """Внести дополнительные средства в кассу (с комментарием). Возвращает id записи."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO accounting_income_extra (amount, comment, income_date, created_by) VALUES (?, ?, ?, ?)",
        (amount, (comment or "").strip(), income_date, created_by),
    )
    conn.commit()
    return cur.lastrowid


def accounting_income_extra_list(date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """Список внесений дополнительных средств за период."""
    conn = get_connection()
    q = "SELECT e.*, u.full_name AS created_by_name FROM accounting_income_extra e LEFT JOIN users u ON e.created_by = u.id WHERE 1=1"
    params: list[Any] = []
    if date_from:
        q += " AND e.income_date >= ?"
        params.append(date_from)
    if date_to:
        q += " AND e.income_date <= ?"
        params.append(date_to)
    q += " ORDER BY e.income_date DESC, e.id DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def accounting_incomes_list(
    date_from: str | None = None,
    date_to: str | None = None,
    payment_type_filter: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> list[dict[str, Any]]:
    """Пополнения (подтверждённые платежи): сумма поступившая на счёт (amount_received или amount)."""
    conn = get_connection()
    q = """
        SELECT p.id, p.created_at AS movement_date, p.amount, p.amount_received, p.bank_commission,
               p.payment_type, p.purpose, p.status, s.full_name AS student_name
        FROM payments p
        JOIN students s ON p.student_id = s.id
        WHERE p.status = 'confirmed'
    """
    params: list[Any] = []
    if date_from:
        q += " AND date(p.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(p.created_at) <= date(?)"
        params.append(date_to)
    if payment_type_filter and payment_type_filter in ("cash", "cashless"):
        q += " AND p.payment_type = ?"
        params.append(payment_type_filter)
    if amount_min is not None:
        q += " AND COALESCE(p.amount_received, p.amount) >= ?"
        params.append(amount_min)
    if amount_max is not None:
        q += " AND COALESCE(p.amount_received, p.amount) <= ?"
        params.append(amount_max)
    q += " ORDER BY p.created_at DESC"
    rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["received"] = d.get("amount_received") if d.get("amount_received") is not None else d.get("amount", 0)
        out.append(d)
    return out


def accounting_balance_today() -> float:
    """Баланс на сегодня: входящее сальдо учебного года + пополнения + доп. средства − списания с 1 сентября по сегодня."""
    start, _ = school_year_period()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    opening = accounting_get_opening_balance(school_year_key())
    conn = get_connection()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN p.amount_received IS NOT NULL THEN p.amount_received ELSE p.amount END), 0) AS s
        FROM payments p
        WHERE p.status = 'confirmed'
          AND date(p.created_at) >= date(?)
          AND date(p.created_at) <= date(?)
        """,
        (start, today),
    ).fetchone()
    incomes = float(row["s"]) if row else 0.0
    row_extra = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM accounting_income_extra WHERE income_date >= ? AND income_date <= ?",
        (start, today),
    ).fetchone()
    incomes_extra = float(row_extra["s"]) if row_extra else 0.0
    row2 = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM accounting_expense WHERE expense_date >= ? AND expense_date <= ?",
        (start, today),
    ).fetchone()
    expenses = float(row2["s"]) if row2 else 0.0
    return opening + incomes + incomes_extra - expenses


def accounting_forecast_end_of_month() -> float:
    """Прогноз поступлений до конца месяца: сумма неподтверждённых платежей за текущий месяц (ожидаемые)."""
    now = datetime.utcnow()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")
    last_day = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    month_end = last_day.strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(p.amount), 0) AS s
        FROM payments p
        WHERE p.status = 'pending'
          AND date(p.created_at) >= date(?)
          AND date(p.created_at) <= date(?)
        """,
        (month_start, month_end),
    ).fetchone()
    return float(row["s"]) if row else 0.0


# --- Мероприятия ---

def event_create(
    name: str,
    description: str | None = None,
    event_date: str | None = None,
    amount_required: float | None = None,
) -> int:
    """Добавить мероприятие."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO events (name, description, event_date, amount_required)
        VALUES (?, ?, ?, ?)
        """,
        (name, description or "", event_date, amount_required),
    )
    conn.commit()
    return cur.lastrowid


def events_list() -> list[dict[str, Any]]:
    """Список мероприятий."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY event_date DESC, id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# --- Напоминания о платежах (по датам: 3, 7, 10; янв 10,13,15; июль/авг нет; при отсутствии платежа — 11 и каждые 3 дня) ---

def reminder_was_sent(student_id: int, year: int, month: int) -> bool:
    """Проверить, отправляли ли уже напоминание этому ученику в этом месяце (старая логика, для совместимости)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM payment_reminders WHERE student_id = ? AND reminder_year = ? AND reminder_month = ?",
        (student_id, year, month),
    ).fetchone()
    return row is not None


def reminder_mark_sent(student_id: int, year: int, month: int) -> None:
    """Отметить напоминание за месяц (старая логика)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO payment_reminders (student_id, reminder_year, reminder_month) VALUES (?, ?, ?)",
        (student_id, year, month),
    )
    conn.commit()


def has_food_payment_in_month(student_id: int, year: int, month: int) -> bool:
    """Есть ли у ученика хотя бы один подтверждённый платёж на питание в указанном месяце."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT 1 FROM payments
        WHERE student_id = ? AND purpose = 'food' AND status = 'confirmed'
          AND cast(strftime('%Y', created_at) AS INTEGER) = ?
          AND cast(strftime('%m', created_at) AS INTEGER) = ?
        LIMIT 1
        """,
        (student_id, year, month),
    ).fetchone()
    return row is not None


def reminder_was_sent_on_date(student_id: int, sent_date: str) -> bool:
    """Отправляли ли уже напоминание этому ученику в эту дату (sent_date = 'YYYY-MM-DD')."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM payment_reminder_sent_date WHERE student_id = ? AND sent_date = ?",
        (student_id, sent_date),
    ).fetchone()
    return row is not None


def reminder_mark_sent_on_date(student_id: int, sent_date: str) -> None:
    """Записать, что напоминание отправлено в эту дату."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO payment_reminder_sent_date (student_id, sent_date) VALUES (?, ?)",
        (student_id, sent_date),
    )
    conn.commit()


# --- Сообщения родителей «Я совершил платёж» (на проверку бухгалтеру/директору) ---

def parent_report_payment_create(parent_id: int, student_id: int, amount: float | None = None, comment: str | None = None) -> int:
    """Родитель сообщил о платеже — запись попадает бухгалтеру и директору на проверку."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO parent_payment_reports (parent_id, student_id, amount, comment, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (parent_id, student_id, amount, comment or ""),
    )
    conn.commit()
    return cur.lastrowid


def parent_report_payment_list_pending() -> list[dict[str, Any]]:
    """Список сообщений «Я совершил платёж», ожидающих проверки (для бухгалтера/директора)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT r.*, u.full_name AS parent_name, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM parent_payment_reports r
        JOIN users u ON r.parent_id = u.id
        JOIN students s ON r.student_id = s.id
        WHERE r.status = 'pending'
        ORDER BY r.reported_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def parent_report_by_id(report_id: int) -> dict[str, Any] | None:
    """Один отчёт по id."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT r.*, u.full_name AS parent_name, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM parent_payment_reports r
        JOIN users u ON r.parent_id = u.id
        JOIN students s ON r.student_id = s.id
        WHERE r.id = ?
        """,
        (report_id,),
    ).fetchone()
    return dict(row) if row else None


def parent_report_mark_entered(report_id: int, resolved_by_user_id: int) -> bool:
    """Отметить: платёж внесён (бухгалтер проверил и внёс в систему)."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        UPDATE parent_payment_reports
        SET status = 'entered', resolved_by = ?, resolved_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (resolved_by_user_id, now, report_id),
    )
    conn.commit()
    return cur.rowcount > 0


def parent_report_mark_dismissed(report_id: int, resolved_by_user_id: int) -> bool:
    """Отметить: проверено, платёж не найден / отказ."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        UPDATE parent_payment_reports
        SET status = 'dismissed', resolved_by = ?, resolved_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (resolved_by_user_id, now, report_id),
    )
    conn.commit()
    return cur.rowcount > 0


# --- Настройки приложения (дедлайн редактирования питания) ---

def app_settings_get(key: str) -> str:
    """Значение настройки по ключу (пустая строка если нет или таблица отсутствует)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    except Exception as e:
        logger.warning("app_settings_get: %s", e)
        return ""
    if not row:
        return ""
    return (row.get("value") if hasattr(row, "get") else row[0]) or ""


def app_settings_set(key: str, value: str) -> None:
    """Установить значение настройки."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def nutrition_cutoff_get() -> tuple[int, int, str]:
    """Время дедлайна редактирования питания (час 0–23, минута 0–59, таймзона). По умолчанию 8:00 Уфа (Asia/Yekaterinburg)."""
    hour = int(app_settings_get("nutrition_cutoff_hour") or "8")
    minute = int(app_settings_get("nutrition_cutoff_minute") or "0")
    tz_str = (app_settings_get("nutrition_cutoff_timezone") or "Asia/Yekaterinburg").strip()
    if not tz_str:
        tz_str = "Asia/Yekaterinburg"
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return hour, minute, tz_str


def nutrition_cutoff_set(hour: int, minute: int, timezone_str: str) -> None:
    """Сохранить время дедлайна редактирования питания."""
    app_settings_set("nutrition_cutoff_hour", str(max(0, min(23, hour))))
    app_settings_set("nutrition_cutoff_minute", str(max(0, min(59, minute))))
    app_settings_set("nutrition_cutoff_timezone", (timezone_str or "Asia/Yekaterinburg").strip())


def can_edit_nutrition_for_date(nutrition_date: str) -> tuple[bool, str]:
    """Можно ли редактировать питание на указанную дату. Проверка по серверу и настройке времени (обмануть сменой даты у пользователя нельзя).
    Возвращает (разрешено, сообщение об ошибке)."""
    try:
        hour, minute, tz_str = nutrition_cutoff_get()
    except Exception:
        hour, minute, tz_str = 8, 0, "Asia/Yekaterinburg"
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = ZoneInfo("Asia/Yekaterinburg")
    now_local = datetime.now(tz)
    today_str = now_local.strftime("%Y-%m-%d")
    if nutrition_date > today_str:
        return True, ""
    if nutrition_date < today_str:
        return True, ""
    cutoff_minutes = hour * 60 + minute
    current_minutes = now_local.hour * 60 + now_local.minute
    if current_minutes < cutoff_minutes:
        return True, ""
    return False, f"Редактирование питания на сегодня после {hour:02d}:{minute:02d} (по времени школы) запрещено."


# --- Ежедневное питание (учитель / столовая) ---

def daily_nutrition_upsert(
    student_id: int,
    nutrition_date: str,
    entered_by: int,
    had_breakfast: bool = False,
    had_lunch: bool = False,
    had_snack: bool = False,
    comment: str | None = None,
) -> None:
    """Внести или обновить данные по питанию на дату (одна запись на ученика в день)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO daily_nutrition (student_id, nutrition_date, entered_by, had_breakfast, had_lunch, had_snack, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, nutrition_date) DO UPDATE SET
            had_breakfast = excluded.had_breakfast,
            had_lunch = excluded.had_lunch,
            had_snack = excluded.had_snack,
            comment = excluded.comment,
            entered_by = excluded.entered_by
        """,
        (student_id, nutrition_date, entered_by, 1 if had_breakfast else 0, 1 if had_lunch else 0, 1 if had_snack else 0, comment or ""),
    )
    conn.commit()


def daily_nutrition_by_date(nutrition_date: str) -> list[dict[str, Any]]:
    """Данные по питанию на дату (все ученики)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT dn.*, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM daily_nutrition dn
        JOIN students s ON dn.student_id = s.id
        WHERE dn.nutrition_date = ?
        ORDER BY s.class_grade, s.class_letter, s.full_name
        """,
        (nutrition_date,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Отчёты с фильтрами (директор) ---

def payments_report(
    date_from: str | None = None,
    date_to: str | None = None,
    student_id: int | None = None,
    class_grade: int | None = None,
    class_letter: str | None = None,
    purpose: str | None = None,
    status: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> list[dict[str, Any]]:
    """Платежи с фильтрами: по датам, ученику, классу, назначению, статусу, сумме."""
    conn = get_connection()
    q = """
        SELECT p.*, s.full_name AS student_name, s.class_grade, s.class_letter
        FROM payments p
        JOIN students s ON p.student_id = s.id
        WHERE 1=1
    """
    params: list[Any] = []
    if date_from:
        q += " AND date(p.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(p.created_at) <= date(?)"
        params.append(date_to)
    if student_id:
        q += " AND p.student_id = ?"
        params.append(student_id)
    if class_grade is not None:
        q += " AND s.class_grade = ?"
        params.append(class_grade)
    if class_letter:
        q += " AND s.class_letter = ?"
        params.append(class_letter)
    if purpose:
        q += " AND p.purpose = ?"
        params.append(purpose)
    if status:
        q += " AND p.status = ?"
        params.append(status)
    if amount_min is not None:
        q += " AND p.amount >= ?"
        params.append(amount_min)
    if amount_max is not None:
        q += " AND p.amount <= ?"
        params.append(amount_max)
    q += " ORDER BY p.created_at DESC"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def report_totals(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, float]:
    """Сводка: суммы по статусам (подтверждённые, ожидающие) и по назначению за период."""
    conn = get_connection()
    q = "SELECT status, purpose, SUM(amount) AS total FROM payments WHERE 1=1"
    params: list[Any] = []
    if date_from:
        q += " AND date(created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        q += " AND date(created_at) <= date(?)"
        params.append(date_to)
    q += " GROUP BY status, purpose"
    rows = conn.execute(q, params).fetchall()
    result: dict[str, float] = {}
    for r in rows:
        key = f"{r['status']}_{r['purpose']}"
        result[key] = float(r["total"])
    return result


def parents_by_student_id(student_id: int) -> list[dict[str, Any]]:
    """Все родители ученика (для рассылки напоминаний)."""
    return student_parents_by_student(student_id)
