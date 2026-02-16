"""
Общие настройки pytest для проекта.
Добавляем src в sys.path, чтобы импорт bot.* работал при запуске из корня проекта.
"""
import sys
from pathlib import Path

# Корень проекта (папка Universus_bot)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
