from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.services.product_service import ProductService


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_product_service(session: AsyncSession = Depends(get_session)) -> ProductService:
    return ProductService(session)
