import secrets
from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.services.product_service import ProductService


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_product_service(session: AsyncSession = Depends(get_session)) -> ProductService:
    return ProductService(session)


async def require_api_token(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Аутентификация backend API общим секретом (см. API_AUTH_TOKEN в .env).

    Без этого любой, кто достучится до порта API (docker-compose публикует его
    на хост как 8000:8000), мог без единого ключа дёргать /publish/{id} и
    публиковать что угодно на реальный WB/Ozon чужими сохранёнными ключами
    магазина, либо читать/менять чужие товары по перебору product_id (IDOR).
    Fail-closed, симметрично AccessControlMiddleware бота: пустой
    API_AUTH_TOKEN означает «ключ не выпущен» и отклоняет ВСЕХ, а не отключает
    проверку.
    """
    if not settings.api_auth_token or not x_api_key or not secrets.compare_digest(x_api_key, settings.api_auth_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный или отсутствующий X-API-Key")
