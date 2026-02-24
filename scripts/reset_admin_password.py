#!/usr/bin/env python3
"""
Сброс пароля администратора без почты (когда «Забыли пароль» недоступен).

Запуск с сервера (нужен доступ к окружению/БД):
  cd /path/to/Controlsysem_bot
  source .venv/bin/activate   # или активируйте своё окружение
  python scripts/reset_admin_password.py

Скрипт находит администратора в базе и задаёт ему новый пароль.
Используйте, если администратор забыл пароль и почта для сброса недоступна.
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
    if not admins:
        print("В системе нет пользователя с ролью «администратор». Создайте через scripts/create_administrator.py")
        return

    # Можно задать email или пароль через переменные окружения (для деплоя/автоматизации)
    email_arg = (os.environ.get("ADMIN_RECOVERY_EMAIL") or "").strip().lower()
    new_password_env = (os.environ.get("ADMIN_RECOVERY_NEW_PASSWORD") or "").strip()

    if email_arg:
        admin = next((u for u in admins if (u.get("email") or "").lower() == email_arg), None)
        if not admin:
            print(f"Администратор с email {email_arg!r} не найден. Доступные: {[u.get('email') for u in admins]}")
            return
    elif len(admins) == 1:
        admin = admins[0]
        print("Найден администратор:", admin.get("full_name"), "| email:", admin.get("email") or "(не задан)")
    else:
        print("Несколько администраторов:")
        for i, u in enumerate(admins, 1):
            print(f"  {i}. {u.get('full_name')} ({u.get('email') or 'без email'})")
        try:
            n = int(input("Номер (1-%d): " % len(admins)))
            if 1 <= n <= len(admins):
                admin = admins[n - 1]
            else:
                admin = admins[0]
        except (ValueError, EOFError):
            admin = admins[0]
        print("Выбран:", admin.get("full_name"))

    uid = admin["id"]

    if new_password_env:
        new_password = new_password_env
        if len(new_password) < 4:
            print("Пароль из ADMIN_RECOVERY_NEW_PASSWORD должен быть не короче 4 символов.")
            return
    else:
        new_password = input("Новый пароль для входа: ").strip()
        if len(new_password) < 4:
            print("Пароль должен быть не короче 4 символов.")
            return
        again = input("Повторите пароль: ").strip()
        if new_password != again:
            print("Пароли не совпадают.")
            return

    school_db.user_update(uid, password_hash=hash_password(new_password))
    print("Пароль администратора обновлён. Вход на сайт — по email и новому паролю.")


if __name__ == "__main__":
    main()
