"""
Перезапись доступа директора: новый email и пароль для входа на сайт.

Запуск из корня проекта:
  cd /path/to/Controlsysem_bot
  source .venv/bin/activate
  python3 scripts/reset_director.py

Скрипт находит директора в базе и перезаписывает его email и пароль.
"""
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
    directors = [u for u in users if u.get("role") == "director"]
    if not directors:
        print("В системе нет пользователя с ролью «директор». Создайте его через scripts/create_director.py")
        return

    d = directors[0]
    if len(directors) > 1:
        print("Найдено директоров:", len(directors))
        for i, x in enumerate(directors, 1):
            print(f"  {i}. {x.get('full_name')} ({x.get('email') or 'без email'})")
        try:
            n = int(input("Номер директора для сброса (1-%d): " % len(directors)))
            if 1 <= n <= len(directors):
                d = directors[n - 1]
        except (ValueError, EOFError):
            d = directors[0]

    print("Текущий директор:", d.get("full_name"), "| email:", d.get("email") or "(не задан)")
    print("Введите новые данные (Enter — оставить как есть).")
    new_email = input("Новый email: ").strip().lower()
    new_password = input("Новый пароль: ").strip()
    new_name = input("Новое ФИО: ").strip()

    uid = d["id"]
    updated = False
    if new_email:
        school_db.user_update(uid, email=new_email)
        print("  Email обновлён.")
        updated = True
    if new_password:
        if len(new_password) < 4:
            print("  Пароль не изменён: должен быть не короче 4 символов.")
        else:
            school_db.user_update(uid, password_hash=hash_password(new_password))
            print("  Пароль обновлён.")
            updated = True
    if new_name:
        school_db.user_update(uid, full_name=new_name)
        print("  ФИО обновлено.")
        updated = True

    if not updated:
        print("Ничего не введено — изменения не применены.")
    else:
        print("Доступ директора перезаписан. Вход на сайт — по новому email и паролю.")


if __name__ == "__main__":
    main()
