#!/bin/sh
# Одно приложение Amvera: веб (порт 80) + Telegram-бот, SQLite в /data (постоянное хранилище).
# Бот в фоне, главным процессом — uvicorn, чтобы платформа видела порт 80.
# Если заданы INIT_ADMIN_EMAIL и INIT_ADMIN_PASSWORD — один раз создаётся администратор при старте.
set -e
# SQLite в /data — не затирается при пересборке (persistenceMount в amvera.yml)
export SQLITE_DB_PATH="${SQLITE_DB_PATH:-/data/school.db}"
if [ -n "$INIT_ADMIN_EMAIL" ] && [ -n "$INIT_ADMIN_PASSWORD" ]; then
  python scripts/create_administrator.py || true
fi
python app.py &
exec uvicorn web.main:app --host 0.0.0.0 --port 80
