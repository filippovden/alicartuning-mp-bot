"""Хранилище файлов: локально — всегда, плюс S3-аплоад при настроенном
STORAGE_BACKEND=s3 (см. критические правки — WB не может скачать фото по
локальному "URL"/пути на диске, нужен реальный публичный http(s) адрес).
"""

from __future__ import annotations

import boto3
import pytest

from app.config import settings
from app.services.storage import s3_configured, save_bytes


def _set_s3_settings(monkeypatch, **overrides):
    defaults = dict(
        storage_backend="s3",
        s3_endpoint_url="https://s3.example.com",
        s3_bucket="my-bucket",
        s3_access_key="AKIAEXAMPLE",
        s3_secret_key="secret",
    )
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(settings, key, value)


def test_s3_configured_false_by_default(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "local")
    assert s3_configured() is False


def test_s3_configured_false_when_credential_missing(monkeypatch):
    _set_s3_settings(monkeypatch, s3_access_key="")
    assert s3_configured() is False


def test_s3_configured_false_when_backend_still_local(monkeypatch):
    _set_s3_settings(monkeypatch, storage_backend="local")
    assert s3_configured() is False


def test_s3_configured_true_when_fully_set(monkeypatch):
    _set_s3_settings(monkeypatch)
    assert s3_configured() is True


@pytest.mark.asyncio
async def test_save_bytes_local_only_by_default(session, monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "local")
    storage_file = await save_bytes(session, b"hello", filename="photo.jpg", content_type="image/jpeg")

    assert storage_file.url == storage_file.path
    assert not storage_file.url.startswith(("http://", "https://"))


@pytest.mark.asyncio
async def test_save_bytes_uploads_to_s3_and_sets_public_url(session, monkeypatch):
    _set_s3_settings(monkeypatch)
    put_calls = []

    class _FakeS3Client:
        def put_object(self, **kwargs):
            put_calls.append(kwargs)

    monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FakeS3Client())

    storage_file = await save_bytes(session, b"hello", filename="photo.jpg", content_type="image/jpeg")

    assert storage_file.url.startswith("https://s3.example.com/my-bucket/")
    assert storage_file.path != storage_file.url  # path остаётся локальным путём
    assert len(put_calls) == 1
    assert put_calls[0]["Bucket"] == "my-bucket"
    assert put_calls[0]["Body"] == b"hello"
    assert put_calls[0]["ContentType"] == "image/jpeg"
    assert put_calls[0]["ACL"] == "public-read"


@pytest.mark.asyncio
async def test_save_bytes_falls_back_to_local_url_when_s3_upload_fails(session, monkeypatch):
    _set_s3_settings(monkeypatch)

    class _FailingS3Client:
        def put_object(self, **kwargs):
            raise RuntimeError("S3 недоступен")

    monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FailingS3Client())

    storage_file = await save_bytes(session, b"hello", filename="photo.jpg")

    # Сбой S3 не должен ронять сохранение — файл остаётся доступен локально.
    assert storage_file.url == storage_file.path
