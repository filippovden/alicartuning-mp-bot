"""Снимки магазинов-конкурентов по ссылке (/shop)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

marketplace = sa.Enum("wildberries", "ozon", name="marketplace")


def upgrade() -> None:
    op.create_table(
        "shop_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("marketplace", marketplace, server_default="wildberries", nullable=False),
        sa.Column("seller_id", sa.String(64), nullable=False),
        sa.Column("item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("max_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("avg_rating", sa.Numeric(3, 2), nullable=True),
        sa.Column("total_feedbacks", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_shop_snapshots_seller_id", "shop_snapshots", ["seller_id"])
    op.create_index("ix_shop_snapshots_captured_at", "shop_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_shop_snapshots_captured_at", table_name="shop_snapshots")
    op.drop_index("ix_shop_snapshots_seller_id", table_name="shop_snapshots")
    op.drop_table("shop_snapshots")
