"""Хранилище файлов (раздел 5, 11 ТЗ). MVP: локальный диск, интерфейс совместим с S3."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import StorageFile


async def save_bytes(session: AsyncSession, content: bytes, filename: str, content_type: str | None = None) -> StorageFile:
    root = Path(settings.storage_local_path)
    root.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix or ".jpg"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = root / unique_name
    file_path.write_bytes(content)

    storage_file = StorageFile(
        path=str(file_path),
        url=str(file_path),
        content_type=content_type,
        size_bytes=len(content),
    )
    session.add(storage_file)
    await session.commit()
    await session.refresh(storage_file)
    return storage_file
