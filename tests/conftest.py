"""
Общие настройки pytest для проекта.
Добавляем src в sys.path, чтобы импорт bot.* работал при запуске из корня проекта.
Фикстуры для тестовой БД (school_db) и веб-приложения.
"""
import sys
import tempfile
from pathlib import Path

# Корень проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import pytest


@pytest.fixture
def school_db_test():
    """Подмена БД на временный файл для тестов: чистые таблицы, без влияния на прод."""
    import bot.school_db as db
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "school.db"
        db.set_db_path_for_tests(path)
        db.init_db()
        yield db
        db.close_db()
    # Восстанавливаем путь по умолчанию, чтобы остальные тесты (веб и т.д.) не падали
    db.reset_db_path_to_default()
