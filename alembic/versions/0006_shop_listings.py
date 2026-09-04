"""Мультимагазинность (срез v5): shop_listings + images.listing_id

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False обязательно через postgresql.ENUM, а не через sa.Enum:
# generic sa.Enum теряет флаг create_type при внутренней адаптации к
# dialect_impl (см. TypeEngine.dialect_impl в sqlalchemy/sql/sqltypes.py) —
# op.create_table ниже всё равно бы попытался сам выполнить CREATE TYPE.
# marketplace уже создан миграцией 0001_initial.py (category_attrs.marketplace/
# publish_logs.marketplace) — без create_type=False падает с "type marketplace
# already exists" на любой базе, где 0001 уже применена отдельным процессом от
# 0006 (например, рестарт контейнера на частично мигрированной базе — 0006 не
# бывает первой миграцией). listing_status создаётся явно ниже (checkfirst=True,
# он новый в этой миграции), create_type=False здесь не даёт op.create_table
# попытаться создать его ЕЩЁ раз при определении колонки status.
marketplace = postgresql.ENUM("wildberries", "ozon", name="marketplace", create_type=False)
listing_status = postgresql.ENUM("draft", "published", "partial", "error", name="listingstatus", create_type=False)


def upgrade() -> None:
    listing_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "shop_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shop_id", sa.String(64), nullable=False),
        sa.Column("platform", marketplace, nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("bullets", sa.JSON(), nullable=True),
        sa.Column("vendor_code", sa.String(128), nullable=False),
        sa.Column("wb_nm_id", sa.String(64), nullable=True),
        sa.Column("ozon_product_id", sa.String(64), nullable=True),
        sa.Column("status", listing_status, server_default="draft", nullable=False),
        sa.Column("publish_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("shop_id", "vendor_code", name="uq_shop_listing_vendor_code"),
    )
    op.create_index("ix_shop_listings_shop_id", "shop_listings", ["shop_id"])
    op.create_index("ix_shop_listings_product_id", "shop_listings", ["product_id"])

    op.add_column(
        "images",
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("shop_listings.id", ondelete="CASCADE"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("images", "listing_id")
    op.drop_index("ix_shop_listings_product_id", table_name="shop_listings")
    op.drop_index("ix_shop_listings_shop_id", table_name="shop_listings")
    op.drop_table("shop_listings")
    listing_status.drop(op.get_bind(), checkfirst=True)
