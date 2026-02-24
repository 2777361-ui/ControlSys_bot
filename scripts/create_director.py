"""
Скрипт создания первого директора в системе.

Запуск из корня проекта (с активированным .venv):
  python scripts/create_director.py

Запросит email и пароль, создаст пользователя с ролью director в school.db.
После этого можно войти на веб-интерфейс и добавлять бухгалтеров, родителей, учеников.
"""
import sys
from pathlib import Path

# Корень проекта и src — для импорта bot и web
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))

from bot import school_db
from web.auth_utils import hash_password


def main():
    school_db.init_db()
    # Проверяем, есть ли уже директор
    users = school_db.user_list()
    directors = [u for u in users if u.get("role") == "director"]
    if directors:
        print("Директор уже есть в системе:", directors[0].get("full_name"))
        return
    email = input("Email директора (для входа на сайт): ").strip().lower()
    if not email:
        print("Email не может быть пустым.")
        return
    if school_db.user_by_email(email):
        print("Пользователь с таким email уже существует.")
        return
    password = input("Пароль: ").strip()
    if len(password) < 4:
        print("Пароль должен быть не короче 4 символов.")
        return
    full_name = input("ФИО директора: ").strip() or "Директор"
    password_hash = hash_password(password)
    school_db.user_create(
        role="director",
        full_name=full_name,
        email=email,
        password_hash=password_hash,
    )
    print("Директор создан. Войдите на веб-интерфейс по адресу /login с указанным email и паролем.")


if __name__ == "__main__":
    main()
