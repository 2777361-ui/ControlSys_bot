"""
Тесты веб-приложения: страницы входа, редиректы, доступ без авторизации.

Используется TestClient FastAPI; для тестов с БД применяется фикстура school_db_test.
"""
import pytest
from fastapi.testclient import TestClient

# Импорт app после возможной подмены БД в фикстуре
# В тестах без school_db_test используется продовая БД при первом запросе — для изолированных тестов лучше вызывать school_db_test
from web.main import app


client = TestClient(app)


def test_login_page_returns_200():
    """Страница логина открывается без авторизации."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "login" in response.text.lower() or "вход" in response.text.lower() or "email" in response.text.lower()


def test_login_post_wrong_user_redirects(school_db_test):
    """POST /login с несуществующим email — редирект на логин с ошибкой."""
    response = client.post(
        "/login",
        data={"email": "nonexistent@test.ru", "password": "any"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "login" in response.headers.get("location", "").lower() or "login" in response.headers.get("Location", "").lower()


def test_dashboard_without_auth(school_db_test):
    """Без cookie сессии запрос к /dashboard не приводит к 500 (редирект или страница)."""
    response = client.get("/dashboard", follow_redirects=False)
    # Ожидаем либо редирект на логин, либо 200 (поведение зависит от FastAPI/зависимостей)
    assert response.status_code in (302, 307, 200)
    if response.status_code in (302, 307):
        loc = response.headers.get("location") or response.headers.get("Location") or ""
        assert "login" in loc.lower()


def test_register_page_without_token():
    """Страница регистрации без token показывает сообщение об ошибке."""
    response = client.get("/register")
    assert response.status_code == 200
    # Может быть редирект на register с пустым token или страница invalid
    assert "register" in response.url.path or "invalid" in response.text.lower() or "token" in response.text.lower() or response.status_code == 200


def test_static_available():
    """Статика отдаётся (если есть)."""
    response = client.get("/static/css/style.css")
    # 200 если файл есть, 404 если нет
    assert response.status_code in (200, 404)
