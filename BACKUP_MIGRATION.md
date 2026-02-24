# Резервное копирование и перенос базы данных

Как сохранить данные, восстановить их и переехать на другой сервер (например, Amvera или другой хостинг с PostgreSQL).

---

## Какая база используется

- **Продакшен (Render + Supabase):** PostgreSQL. Данные хранятся в Supabase, строка подключения в переменной `DATABASE_URL`.
- **Локально:** SQLite, файл `data/school.db`.

Ниже — бэкап и перенос для **PostgreSQL** (основной сценарий) и кратко для SQLite.

---

## 1. Бэкап PostgreSQL (Supabase)

### Способ А — через `pg_dump` (рекомендуется)

У себя на компьютере (нужен установленный PostgreSQL-клиент: `pg_dump` и `psql`).

1. Возьми строку подключения из Supabase: **Project Settings → Database → Connection string**. Для бэкапа подойдёт **Direct connection** (не Session mode). Формат:
   ```
   postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
   ```
   Или из **Connection pooling** — URI с портом 6543 (Transaction mode). Пароль — тот же, что в дашборде.

2. Создай дамп (все таблицы и данные в один файл):
   ```bash
   pg_dump "postgresql://postgres.XXX:ПАРОЛЬ@db.XXX.supabase.co:5432/postgres" --no-owner --no-acl -F c -f backup.dump
   ```
   - `-F c` — формат «custom» (удобен для `pg_restore`).
   - Файл `backup.dump` — бинарный дамп. Храни его в безопасном месте.

3. **Только SQL-текст** (удобно открыть в редакторе или выполнить в любой PostgreSQL):
   ```bash
   pg_dump "postgresql://postgres.XXX:ПАРОЛЬ@db.XXX.supabase.co:5432/postgres" --no-owner --no-acl -f backup.sql
   ```
   Файл `backup.sql` можно открыть блокнотом/IDE и выполнять через `psql` или любой клиент.

### Способ Б — через Supabase Dashboard

1. В Supabase: **Project Settings → Database**.
2. Раздел **Backups** (если есть на твоём плане) — там можно скачать автоматические бэкапы.
3. Либо используй **Table Editor** и экспорт по таблицам (менее удобно для полного переноса).

### Чем открыть бэкап

| Формат | Чем открыть / восстановить |
|--------|----------------------------|
| `backup.dump` (custom) | `pg_restore` (см. ниже), или DBeaver/DataGrip — через «Restore». |
| `backup.sql` (текст) | Любой текстовый редактор; выполнить: `psql "NEW_DATABASE_URL" -f backup.sql`. |

---

## 2. Восстановление / перенос на новый сервер (например, Amvera)

Идея: развернуть на новом месте PostgreSQL, восстановить в него дамп, в приложении указать новый `DATABASE_URL`.

### Шаг 1 — PostgreSQL на новом хосте

- **Amvera (Yandex Cloud):** создать Managed Service for PostgreSQL или использовать БД в рамках сервиса.
- **Другой хостинг:** создать базу PostgreSQL и получить строку подключения в формате:
  ```
  postgresql://USER:PASSWORD@HOST:PORT/DATABASE
  ```

### Шаг 2 — Восстановление дампа

Если бэкап в формате **custom** (`.dump`):

```bash
pg_restore -d "postgresql://USER:PASSWORD@NEW_HOST:5432/postgres" --no-owner --no-acl --clean --if-exists backup.dump
```

- `--clean --if-exists` — удалить существующие объекты перед восстановлением (чтобы не было конфликтов).
- При ошибках «role does not exist» их можно игнорировать, если таблицы и данные подтянулись.

Если бэкап в формате **SQL** (`.sql`):

```bash
psql "postgresql://USER:PASSWORD@NEW_HOST:5432/postgres" -f backup.sql
```

### Шаг 3 — Подключение приложения

В окружении бота и веб-приложения на Amvera (или другом сервере) задай переменную:

```
DATABASE_URL=postgresql://USER:PASSWORD@NEW_HOST:5432/postgres
```

Используй **Session mode** (порт 6543 и хост pooler), если провайдер это поддерживает (как у Supabase), чтобы избежать лимитов соединений. После перезапуска приложение будет работать уже с новой БД.

---

## 3. Бэкап локальной SQLite

Файл базы: `data/school.db`.

- **Простой бэкап:** скопировать файл:
  ```bash
  cp data/school.db data/school.db.backup-$(date +%Y%m%d)
  ```
- **Текстовый дамп** (удобно открыть и при необходимости перенести в другую SQLite/систему):
  ```bash
  sqlite3 data/school.db .dump > backup_sqlite.sql
  ```

### Чем открыть SQLite-бэкап

| Файл | Чем открыть |
|------|-------------|
| `school.db` | [DB Browser for SQLite](https://sqlitebrowser.org/), или в терминале: `sqlite3 data/school.db`. |
| `backup_sqlite.sql` | Текстовый редактор; восстановить: `sqlite3 new.db < backup_sqlite.sql`. |

---

## 4. Перенос с SQLite на PostgreSQL

Если раньше работали локально на SQLite и нужно переехать на Supabase/Amvera:

1. Сделай дамп SQLite в виде SQL (см. выше). Учти, что синтаксис SQLite и PostgreSQL отличается (типы, автоинкремент и т.д.).
2. Надёжный вариант: развернуть PostgreSQL, запустить приложение с новым `DATABASE_URL` — таблицы создадутся автоматически при первом запуске (`init_db`). Затем перенести данные вручную (экспорт из SQLite в CSV и импорт в PostgreSQL) или адаптировать дамп под PostgreSQL и выполнить только `INSERT`-ы.

В текущем проекте схема создаётся автоматически при наличии `DATABASE_URL`, поэтому на новом сервере достаточно указать пустую PostgreSQL-базу и дать приложению создать таблицы; данные нужно подгружать из бэкапа (например, восстановив дамп с Supabase в эту новую БД).

---

## Краткая шпаргалка

| Действие | Команда / инструмент |
|----------|----------------------|
| Бэкап Supabase (бинарный) | `pg_dump "DATABASE_URL" --no-owner --no-acl -F c -f backup.dump` |
| Бэкап Supabase (SQL) | `pg_dump "DATABASE_URL" --no-owner --no-acl -f backup.sql` |
| Восстановить в новую БД (dump) | `pg_restore -d "NEW_URL" --no-owner --no-acl --clean --if-exists backup.dump` |
| Восстановить в новую БД (sql) | `psql "NEW_URL" -f backup.sql` |
| Открыть/просмотреть | Текст: редактор; dump: DBeaver, DataGrip, `pg_restore -l backup.dump` (список объектов) |
| Бэкап SQLite | `cp data/school.db data/school.db.backup` или `sqlite3 data/school.db .dump > backup.sql` |
| Открыть SQLite | DB Browser for SQLite, `sqlite3 data/school.db` |

После восстановления БД на Amvera (или любом другом сервере) задай в приложении новый `DATABASE_URL` — переезд завершён.
