"""Веб-админка (SQLAdmin): по умолчанию выключена, монтируется только с паролем
(secure-by-default) — см. app/admin.py."""

import pytest
from fastapi import FastAPI

from app.admin import AdminAuth, register_admin
from app.config import Settings


def test_admin_panel_disabled_without_password(monkeypatch):
    import app.admin as admin_module

    monkeypatch.setattr(admin_module, "settings", Settings(admin_panel_password=""))
    app = FastAPI()
    result = register_admin(app)

    assert result is None
    assert not any("/admin" in getattr(r, "path", "") for r in app.routes)


def test_admin_panel_mounts_with_password(monkeypatch):
    import app.admin as admin_module

    monkeypatch.setattr(admin_module, "settings", Settings(admin_panel_password="secret123"))
    app = FastAPI()
    result = register_admin(app)

    assert result is not None
    assert any(getattr(r, "path", "") == "/admin" for r in app.routes)


# --- AdminAuth.login (см. code-review: сравнение через compare_digest,
# а не ==, чтобы не течь длину/содержимое пароля через тайминг) -------------


class _FakeRequest:
    def __init__(self, form_data: dict):
        self._form_data = form_data
        self.session: dict = {}

    async def form(self):
        return self._form_data


@pytest.mark.asyncio
async def test_admin_auth_login_succeeds_with_correct_credentials(monkeypatch):
    import app.admin as admin_module

    monkeypatch.setattr(
        admin_module, "settings", Settings(admin_panel_username="admin", admin_panel_password="secret123")
    )
    auth = AdminAuth(secret_key="test-secret")
    request = _FakeRequest({"username": "admin", "password": "secret123"})

    assert await auth.login(request) is True
    assert request.session["admin_authenticated"] is True


@pytest.mark.asyncio
async def test_admin_auth_login_fails_with_wrong_password(monkeypatch):
    import app.admin as admin_module

    monkeypatch.setattr(
        admin_module, "settings", Settings(admin_panel_username="admin", admin_panel_password="secret123")
    )
    auth = AdminAuth(secret_key="test-secret")
    request = _FakeRequest({"username": "admin", "password": "wrong"})

    assert await auth.login(request) is False
    assert "admin_authenticated" not in request.session


@pytest.mark.asyncio
async def test_admin_auth_login_fails_with_missing_fields(monkeypatch):
    import app.admin as admin_module

    monkeypatch.setattr(
        admin_module, "settings", Settings(admin_panel_username="admin", admin_panel_password="secret123")
    )
    auth = AdminAuth(secret_key="test-secret")
    request = _FakeRequest({})

    assert await auth.login(request) is False
