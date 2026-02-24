"""
Создание первого администратора в системе.

Локально (интерактивно):
  python scripts/create_administrator.py

При деплое (без ввода с клавиатуры) — задать переменные окружения и запустить один раз:
  INIT_ADMIN_EMAIL=admin@example.com INIT_ADMIN_PASSWORD=секретный_пароль INIT_ADMIN_FULL_NAME="Иван Админов" python scripts/create_administrator.py

Администратор — главная роль: входит первым, создаёт пользователей и раздаёт им роли
и пароли (директор, бухгалтер, учитель, столовая, родитель). После создания войдите
на веб-интерфейс по email и паролю, откройте «Пользователи» → «Добавить пользователя».
"""
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))

from bot import school_db
from web.auth_utils import hash_password


def main():
    school_db.init_db()
    users = school_db.user_list()
    admins = [u for u in users if u.get("role") == "administrator"]
    if admins:
        print("Администратор уже есть в системе:", admins[0].get("full_name"))
        return

    # При деплое: задать INIT_ADMIN_EMAIL, INIT_ADMIN_PASSWORD, INIT_ADMIN_FULL_NAME и запустить скрипт один раз
    email = (os.environ.get("INIT_ADMIN_EMAIL") or "").strip().lower()
    password = (os.environ.get("INIT_ADMIN_PASSWORD") or "").strip()
    full_name = (os.environ.get("INIT_ADMIN_FULL_NAME") or "").strip() or "Администратор"

    if not email or not password:
        email = input("Email администратора (для входа на сайт): ").strip().lower()
        if not email:
            print("Email не может быть пустым.")
            return
        password = input("Пароль: ").strip()
        if len(password) < 4:
            print("Пароль должен быть не короче 4 символов.")
            return
        full_name = input("ФИО администратора: ").strip() or "Администратор"

    if school_db.user_by_email(email):
        print("Пользователь с таким email уже существует.")
        return
    if len(password) < 4:
        print("Пароль должен быть не короче 4 символов.")
        return

    password_hash = hash_password(password)
    school_db.user_create(
        role="administrator",
        full_name=full_name,
        email=email,
        password_hash=password_hash,
    )
    print("Администратор создан. Войдите на веб-интерфейс по адресу /login с указанным email и паролем.")
    print("Далее: «Пользователи» → «Добавить пользователя» — создавайте директора, бухгалтера, учителей и т.д.")


if __name__ == "__main__":
    main()
