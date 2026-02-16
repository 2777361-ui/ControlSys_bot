# Universus — эхобот (Telegram)

Telegram-бот на Python (aiogram), который повторяет всё, что пишет пользователь.

## Запуск

1. Создай бота в [@BotFather](https://t.me/BotFather), получи токен.
2. В корне проекта создай файл `.env` (по образцу `.env.example`):
   ```
   BOT_TOKEN=твой_токен_от_BotFather
   ```
3. Создай виртуальное окружение и установи зависимости:
   ```bash
   cd Bots/Universus_bot
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Запуск из корня проекта `Universus_bot`:
   ```bash
   python -m src.bot
   ```
   Либо из папки `src`:
   ```bash
   cd src && python -m bot
   ```

## Переменные окружения

| Переменная            | Описание                    | Пример        |
|-----------------------|-----------------------------|---------------|
| `BOT_TOKEN`           | Токен от BotFather          | (обязательно) |
| `OPENROUTER_API_KEY`  | Ключ OpenRouter для /chat   | (опционально) |

### Деплой на Amvera

Файл `.env` в репозиторий не попадает. Задай переменные в панели Amvera: **приложение → Настройки / Environment → Переменные окружения**. Добавь минимум `BOT_TOKEN`; при использовании `/chat` — также `OPENROUTER_API_KEY`. Запуск: `python app.py` (см. `amvera.yml`).

## Команды бота

- `/start` — приветствие и клавиатура.
- `/help` — справка.
- `/chat` — режим чата с ИИ (ответы через OpenRouter). Выход: `/exit`.
- `/plus1` — прибавить 1 к числу.
- Любой другой текст — бот повторяет его (эхо).

## Тесты

Покрыты сервисный слой (текст, числа, конфиг, логирование). Запуск из папки `Universus_bot` с активированным `.venv`:

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Путь `src` подставляется автоматически через `tests/conftest.py`.

## Структура

- `src/bot/main.py` — запуск и polling, FSM storage.
- `src/bot/routers/` — start, help, chat (ИИ), plus1, echo.
- `src/bot/services/` — text, numbers, llm (OpenRouter с fallback по моделям).
- `src/bot/keyboards/` — клавиатуры.
- `src/bot/config.py` — загрузка `.env`.

-тестовая строка-удалить