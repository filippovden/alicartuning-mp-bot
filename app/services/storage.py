"""Хранилище файлов (раздел 5, 11 ТЗ).

MVP: локальный диск. `StorageFile.path` — путь на диске контейнера, читается
внутренними обработчиками (image_pipeline: удаление фона, инфографика). Этот
путь ВСЕГДА локальный, независимо от бэкенда — чтобы не переписывать чтение
файлов в image_pipeline.py под S3.

`StorageFile.url` — то, что отдаётся наружу (например, для загрузки фото в
WB — см. ProductService._publish_to_wb). При STORAGE_BACKEND=local это тот же
локальный путь, НЕ публичный HTTP(S)-адрес — WB не сможет по нему скачать
фото. Если заданы STORAGE_BACKEND=s3 и S3_ENDPOINT_URL/S3_BUCKET/
S3_ACCESS_KEY/S3_SECRET_KEY, файл ДОПОЛНИТЕЛЬНО заливается в S3-совместимое
хранилище (подходит для AWS S3, MinIO, Selectel/Timeweb/Yandex Object
Storage — везде, где есть S3 API), и url указывает на реальный публичный
HTTPS-адрес.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import StorageFile

logger = logging.getLogger(__name__)


def s3_configured() -> bool:
    return bool(
        settings.storage_backend == "s3"
        and settings.s3_endpoint_url
        and settings.s3_bucket
        and settings.s3_access_key
        and settings.s3_secret_key
    )


async def save_bytes(session: AsyncSession, content: bytes, filename: str, content_type: str | None = None) -> StorageFile:
    root = Path(settings.storage_local_path)
    root.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix or ".jpg"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = root / unique_name
    file_path.write_bytes(content)

    url = str(file_path)
    if s3_configured():
        try:
            url = await _upload_to_s3(unique_name, content, content_type)
        except Exception:
            logger.exception(
                "Не удалось загрузить файл в S3 (%s) — фото сохранено только "
                "локально, публикация в WB не сможет скачать его по URL",
                unique_name,
            )

    storage_file = StorageFile(
        path=str(file_path),
        url=url,
        content_type=content_type,
        size_bytes=len(content),
    )
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    return storage_file


async def _upload_to_s3(key: str, content: bytes, content_type: str | None) -> str:
    """Заливает файл в S3-совместимое хранилище и возвращает публичный URL.

    boto3 синхронный — выполняем в отдельном потоке, чтобы не блокировать event
    loop. ACL="public-read" поддерживается большинством S3-совместимых
    провайдеров (AWS, MinIO, Selectel, Timeweb S3); если ваш провайдер её не
    принимает — настройте публичный доступ на уровне бакета в его консоли и
    уберите ACL из вызова ниже. URL собирается в path-style
    (endpoint/bucket/key) — для virtual-hosted-style провайдеров поправьте
    формат под свой S3.
    """

    def _put() -> str:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=content,
            ContentType=content_type or "application/octet-stream",
            ACL="public-read",
        )
        return f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{key}"

    return await asyncio.to_thread(_put)
