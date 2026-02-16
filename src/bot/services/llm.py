"""
Сервис для запросов к OpenRouter API (LLM).
Поддерживает список бесплатных моделей с автоматическим fallback при ошибке.

Суперлогирование:
  - Какую модель пробуем
  - Полная история сообщений, отправленных ИИ
  - Сырой ответ от ИИ (JSON до обработки)
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

# Бесплатные модели OpenRouter (без совсем мелких вроде 1.2B).
# Порядок: при ошибке пробуем следующую.
FREE_MODELS = [
    "openrouter/aurora-alpha",           # reasoning, быстрый
    "stepfun/step-3.5-flash:free",       # 196B MoE
    "arcee-ai/trinity-large-preview:free", # 400B sparse MoE
    "upstage/solar-pro-3:free",          # 102B MoE
    "openrouter/free",                   # роутер сам выбирает бесплатную модель
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MAX_TOKENS = 1024
TIMEOUT_SEC = 60

logger = logging.getLogger(__name__)


async def chat_completion(
    api_key: str,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    model_list: list[str] | None = None,
) -> tuple[str, str | None, str | None]:
    """
    Отправляет запрос в OpenRouter, при ошибке пробует следующие модели из списка.
    history — список {"role": "user"|"assistant", "content": "..."}.

    Возвращает кортеж:
      (текст_ответа, имя_модели, сырой_json_ответ)

    Если все модели не сработали — имя_модели и сырой_ответ будут None.
    """
    models = model_list or FREE_MODELS
    messages = _build_messages(user_message, history)

    # Логируем текущее сообщение пользователя
    logger.info("Сообщение пользователя для ИИ: %s", user_message[:500])

    # Логируем полную историю, которую отправляем в ИИ
    logger.debug(
        "Полная история сообщений для ИИ (%d шт.):\n%s",
        len(messages),
        json.dumps(messages, ensure_ascii=False, indent=2),
    )

    for model in models:
        logger.info("Пробуем модель: %s", model)
        try:
            text, raw_json = await _request_one(api_key, model, messages)
            if text:
                logger.info("Модель %s ответила успешно (длина ответа: %d)", model, len(text))
                return text, model, raw_json
            # Пустой ответ — пробуем следующую модель
            logger.warning("Модель %s вернула пустой ответ, пробуем следующую", model)
        except Exception as e:  # noqa: BLE001
            logger.warning("Модель %s недоступна: %s", model, e)
            continue

    error_msg = "Не удалось получить ответ ни от одной модели. Попробуй позже или напиши /exit и снова /chat."
    logger.error("Все модели не сработали!")
    return error_msg, None, None


def _build_messages(user_message: str, history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Собирает список сообщений для API: история + новый запрос."""
    out: list[dict[str, str]] = []
    if history:
        for h in history[-20:]:  # не более 20 пар
            role = h.get("role", "user")
            content = h.get("content", "")
            if role in ("user", "assistant", "system") and content:
                out.append({"role": role, "content": content})
    out.append({"role": "user", "content": user_message})
    return out


async def _request_one(
    api_key: str, model: str, messages: list[dict[str, str]]
) -> tuple[str, str]:
    """
    Один запрос к одной модели. При ошибке — исключение или пустая строка.
    Возвращает (текст_ответа, сырой_json_строка).
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }

    logger.debug("Отправляем запрос в OpenRouter: model=%s, messages_count=%d", model, len(messages))

    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        resp = await client.post(
            OPENROUTER_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/universus-echobot",
            },
        )

    # Сырой ответ — сохраняем как строку (JSON или текст)
    raw_text = resp.text

    if resp.status_code != 200:
        logger.error(
            "OpenRouter HTTP %d от модели %s. Сырой ответ:\n%s",
            resp.status_code, model, raw_text[:1000],
        )
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {raw_text[:200]}")

    data = resp.json()

    # Логируем полный сырой JSON-ответ от ИИ
    logger.debug(
        "Сырой ответ от модели %s:\n%s",
        model,
        json.dumps(data, ensure_ascii=False, indent=2)[:3000],
    )

    choice = data.get("choices")
    if not choice or not isinstance(choice, list):
        logger.error("Нет choices в ответе от модели %s: %s", model, raw_text[:500])
        raise ValueError("Нет choices в ответе")

    first = choice[0]
    msg = first.get("message")
    if not msg or not isinstance(msg, dict):
        logger.error("Нет message в choice от модели %s", model)
        raise ValueError("Нет message в choice")

    content = msg.get("content")
    if content is None:
        return "", raw_text
    return str(content).strip(), raw_text
