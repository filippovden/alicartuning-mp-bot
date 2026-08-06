"""Admin-инструмент сопоставления внутренней категории с WB subjectID и Ozon
category_id/type_id — заменяет наивное сопоставление по имени из MVP.

WB отдаёт полнотекстовый поиск категорий по имени напрямую через API, поэтому для
него кэш не нужен. Ozon такого поиска не предоставляет — дерево категорий
(POST /v2/category/tree) кэшируется в таблице OzonCategoryNode и обновляется по
команде администратора /synccategories либо периодической Celery-задачей.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OzonCategoryNode
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wildberries import WildberriesClient


@dataclass
class WbCategoryMatch:
    subject_id: int
    name: str


@dataclass
class OzonCategoryMatch:
    category_id: int
    category_name: str
    type_id: int
    type_name: str

    @property
    def full_name(self) -> str:
        return f"{self.category_name} / {self.type_name}"


async def search_wb_categories(query: str, limit: int = 10, client: WildberriesClient | None = None) -> list[WbCategoryMatch]:
    client = client or WildberriesClient()
    nodes = await client.search_categories(query, limit=limit)
    return [WbCategoryMatch(subject_id=int(n.id), name=n.name) for n in nodes]


async def sync_ozon_category_tree(session: AsyncSession, client: OzonClient | None = None) -> int:
    """Полностью пересобирает кэш листьев дерева категорий Ozon. Возвращает число записей."""
    client = client or OzonClient()
    leaves = await client.get_category_leaves()

    await session.execute(delete(OzonCategoryNode))
    rows = [
        OzonCategoryNode(
            category_id=leaf["category_id"],
            type_id=leaf["type_id"],
            parent_category_id=None,
            name=f"{leaf['category_name']} / {leaf['type_name']}",
            is_leaf=True,
        )
        for leaf in leaves
        if leaf.get("category_id") and leaf.get("type_id")
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def search_ozon_categories(session: AsyncSession, query: str, limit: int = 10) -> list[OzonCategoryMatch]:
    stmt = (
        select(OzonCategoryNode)
        .where(OzonCategoryNode.is_leaf.is_(True), OzonCategoryNode.name.ilike(f"%{query}%"))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        OzonCategoryMatch(
            category_id=row.category_id,
            category_name=row.name.rsplit(" / ", 1)[0],
            type_id=row.type_id,
            type_name=row.name.rsplit(" / ", 1)[-1],
        )
        for row in rows
    ]


async def ozon_cache_is_empty(session: AsyncSession) -> bool:
    stmt = select(OzonCategoryNode.id).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none() is None
