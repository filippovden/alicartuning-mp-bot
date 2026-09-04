"""Снимки цен конкурентов для тайминг-аналитики

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False обязательно через postgresql.ENUM (не sa.Enum — теряет флаг
# при адаптации к dialect_impl). Тип marketplace уже создан миграцией
# 0001_initial.py — см. подробное объяснение в 0006_shop_listings.py.
marketplace = postgresql.ENUM("wildberries", "ozon", name="marketplace", create_type=False)


def upgrade() -> None:
    op.create_table(
        "competitor_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("marketplace", marketplace, server_default="wildberries", nullable=False),
        sa.Column("avg_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("max_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_competitor_price_snapshots_product_id", "competitor_price_snapshots", ["product_id"])
    op.create_index("ix_competitor_price_snapshots_captured_at", "competitor_price_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_competitor_price_snapshots_captured_at", table_name="competitor_price_snapshots")
    op.drop_index("ix_competitor_price_snapshots_product_id", table_name="competitor_price_snapshots")
    op.drop_table("competitor_price_snapshots")
