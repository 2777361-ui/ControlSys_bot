"""
Сервис для запросов к OpenRouter API (LLM).
Поддерживает список бесплатных моделей с автоматическим fallback при ошибке.
"""
from __future__ import annotations

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
) -> str:
    """
    Отправляет запрос в OpenRouter, при ошибке пробует следующие модели из списка.
    history — список {"role": "user"|"assistant", "content": "..."}.
    Возвращает текст ответа ассистента или строку с ошибкой.
    """
    models = model_list or FREE_MODELS
    messages = _build_messages(user_message, history)

    for model in models:
        try:
            text = await _request_one(api_key, model, messages)
            if text:
                return text
            # Пустой ответ — пробуем следующую модель
        except Exception as e:  # noqa: BLE001
            logger.warning("Модель %s недоступна: %s", model, e)
            continue

    return "Не удалось получить ответ ни от одной модели. Попробуй позже или напиши /exit и снова /chat."


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


async def _request_one(api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    """Один запрос к одной модели. При ошибке — исключение или пустая строка."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }

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

    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    choice = data.get("choices")
    if not choice or not isinstance(choice, list):
        raise ValueError("Нет choices в ответе")

    first = choice[0]
    msg = first.get("message")
    if not msg or not isinstance(msg, dict):
        raise ValueError("Нет message в choice")

    content = msg.get("content")
    if content is None:
        return ""
    return str(content).strip()
