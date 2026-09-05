import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import models  # noqa: F401  (регистрирует все модели в metadata)
from app.db.base import Base


@pytest.fixture(autouse=True)
def _no_progress_bar_sleep(monkeypatch):
    """Прогресс-полоска (app/bot/progress.py, срез v8) реально ждёт 0.8-1.0с
    между edit_text — нужно в проде против флуд-лимита Telegram, но не в
    тестах, где confirm_publish/shop_confirm_publish/quick_description
    вызываются десятками раз подряд (в т.ч. в N-итерационных симуляциях,
    см. test_full_ux_simulation.py).

    ВАЖНО: патчим именно app.bot.progress._sleep (приватный алиас asyncio.sleep
    внутри этого модуля), а НЕ asyncio.sleep напрямую — модуль asyncio один на
    весь процесс, и monkeypatch.setattr("....asyncio.sleep", ...) подменил бы
    его для всего кода сразу, включая не связанный с прогрессом debounce
    альбомных фото (app/bot/handlers/new_product.py: _answer_photos_received),
    который сам полагается на реальную задержку как на механизм синхронизации
    (см. регрессию, которая так и случилась при первой попытке этого фикса).
    Тесты, которым нужно проверить сам факт паузы (tests/test_progress.py),
    переопределяют этот же _sleep сами — monkeypatch откатывается после
    каждого теста, конфликта нет."""

    async def _fake_sleep(seconds):
        return None

    monkeypatch.setattr("app.bot.progress._sleep", _fake_sleep)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session

    await engine.dispose()
