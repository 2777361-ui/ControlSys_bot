"""
Тесты загрузки конфигурации (get_token).
Проверяем, что токен читается из окружения и что без него — ошибка.
"""
import pytest

from bot.config import get_token


def test_get_token_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если BOT_TOKEN задан в окружении — возвращается его значение."""
    monkeypatch.setenv("BOT_TOKEN", "test_token_123")
    assert get_token() == "test_token_123"


def test_get_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если BOT_TOKEN не задан — ValueError с понятным текстом."""
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValueError) as exc_info:
        get_token()
    assert "BOT_TOKEN" in str(exc_info.value)


def test_get_token_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой BOT_TOKEN тоже считается отсутствующим."""
    monkeypatch.setenv("BOT_TOKEN", "")
    with pytest.raises(ValueError) as exc_info:
        get_token()
    assert "BOT_TOKEN" in str(exc_info.value)
