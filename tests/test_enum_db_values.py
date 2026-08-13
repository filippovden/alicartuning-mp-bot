"""Регрессия на критический баг: SQLAlchemy Enum(...) без values_callable
отправляет в БД .name члена ("DRAFT"), а не .value ("draft") — Postgres-типы
же созданы alembic-миграциями со значениями (lowercase). На реальном Postgres
это роняет INSERT/UPDATE в любую enum-колонку с "invalid input value for
enum" (см. app/db/models.py: _enum_values). SQLite в тестах бага не ловит —
CHECK там строится по той же ошибочной логике, что и сам insert. Поэтому
здесь сверяем НЕ поведение insert (SQLite его не отличит), а собственно
список значений, который SQLAlchemy подставит в DDL/DML для колонки — он
должен буквально совпадать со значениями, зашитыми в alembic-миграциях.
"""

from __future__ import annotations

from app.db.models import (
    CategoryAttr,
    CompetitorPriceSnapshot,
    Image,
    Product,
    PublishLog,
    Review,
    ShopSnapshot,
)


def _enum_values_of(column) -> set[str]:
    return set(column.type.enums)


def test_product_status_column_uses_lowercase_values():
    assert _enum_values_of(Product.__table__.c.status) == {
        "draft",
        "ready",
        "publishing",
        "published",
        "partially_published",
        "error",
    }


def test_category_attr_marketplace_column_uses_lowercase_values():
    assert _enum_values_of(CategoryAttr.__table__.c.marketplace) == {"wildberries", "ozon"}


def test_image_type_column_uses_lowercase_values():
    assert _enum_values_of(Image.__table__.c.image_type) == {"main", "lifestyle", "infographic", "video_cover"}


def test_publish_log_columns_use_lowercase_values():
    assert _enum_values_of(PublishLog.__table__.c.marketplace) == {"wildberries", "ozon"}
    assert _enum_values_of(PublishLog.__table__.c.status) == {"success", "partial", "error"}


def test_review_columns_use_lowercase_values():
    assert _enum_values_of(Review.__table__.c.marketplace) == {"wildberries", "ozon"}
    assert _enum_values_of(Review.__table__.c.sentiment) == {"positive", "neutral", "negative"}


def test_competitor_price_snapshot_marketplace_column_uses_lowercase_values():
    assert _enum_values_of(CompetitorPriceSnapshot.__table__.c.marketplace) == {"wildberries", "ozon"}


def test_shop_snapshot_marketplace_column_uses_lowercase_values():
    assert _enum_values_of(ShopSnapshot.__table__.c.marketplace) == {"wildberries", "ozon"}
