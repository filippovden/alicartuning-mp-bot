"""Веб-админка (SQLAdmin): по умолчанию выключена, монтируется только с паролем
(secure-by-default) — см. app/admin.py."""

from fastapi import FastAPI

from app.admin import register_admin
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
