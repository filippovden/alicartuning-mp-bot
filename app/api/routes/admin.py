"""Admin-роуты: сопоставление категорий WB/Ozon (см. app/services/category_search.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.services.category_search import search_ozon_categories, search_wb_categories, sync_ozon_category_tree

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/categories/search/wb")
async def search_wb(query: str, limit: int = 10) -> list[dict]:
    matches = await search_wb_categories(query, limit=limit)
    return [{"subject_id": m.subject_id, "name": m.name} for m in matches]


@router.get("/categories/search/ozon")
async def search_ozon(query: str, limit: int = 10, session: AsyncSession = Depends(get_session)) -> list[dict]:
    matches = await search_ozon_categories(session, query, limit=limit)
    return [
        {"category_id": m.category_id, "type_id": m.type_id, "full_name": m.full_name} for m in matches
    ]


@router.post("/categories/sync/ozon")
async def sync_ozon(session: AsyncSession = Depends(get_session)) -> dict:
    count = await sync_ozon_category_tree(session)
    return {"synced": count}
