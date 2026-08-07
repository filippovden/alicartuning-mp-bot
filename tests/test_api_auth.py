"""Аутентификация backend API (см. security-review критических исправлений —
раньше FastAPI-роуты /newProduct, /saveDraft, /publish/{id} и т.д. были
полностью открыты без единого ключа, а docker-compose публикует порт API на
хост; см. app/api/deps.py:require_api_token).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_session, require_api_token
from app.api.routes import admin as admin_routes
from app.api.routes import products
from app.config import settings
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


# --- Роутер подключён к require_api_token (проверка через интроспекцию, без
# HTTP-запросов — гарантирует, что защита не потеряется при рефакторинге) ----


def test_products_router_requires_api_token():
    assert require_api_token in [d.dependency for d in products.router.dependencies]


def test_admin_router_requires_api_token():
    assert require_api_token in [d.dependency for d in admin_routes.router.dependencies]


# --- require_api_token как чистая функция (fail-closed) ----------------------


@pytest.mark.asyncio
async def test_require_api_token_denies_when_not_configured(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "api_auth_token", "")
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(x_api_key="anything")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_token_denies_missing_header(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(x_api_key=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_token_denies_wrong_key(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(x_api_key="wrong-token")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_token_allows_correct_key(monkeypatch):
    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    await require_api_token(x_api_key="correct-token")  # не должно бросить исключение


# --- Сквозная проверка через реальный ASGI-стек (TestClient) -----------------


def test_health_does_not_require_auth(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_list_products_denies_without_token(client, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    response = client.get("/listProducts", params={"telegram_id": 1})
    assert response.status_code == 401


def test_list_products_denies_wrong_token(client, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    response = client.get("/listProducts", params={"telegram_id": 1}, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_admin_categories_search_denies_without_token(client, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    response = client.get("/admin/categories/search/ozon", params={"query": "x"})
    assert response.status_code == 401


def test_list_products_succeeds_with_correct_token(client, monkeypatch, session):
    monkeypatch.setattr(settings, "api_auth_token", "correct-token")

    async def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        response = client.get("/listProducts", params={"telegram_id": 777}, headers={"X-API-Key": "correct-token"})
        assert response.status_code == 200
        assert response.json() == {"items": []}
    finally:
        app.dependency_overrides.clear()


def test_publish_route_also_protected(client, monkeypatch):
    """/publish/{id} — самый чувствительный роут (реальная публикация на WB/Ozon
    чужими сохранёнными ключами магазина) — обязан требовать токен как и все."""
    monkeypatch.setattr(settings, "api_auth_token", "correct-token")
    response = client.post("/publish/1")
    assert response.status_code == 401
