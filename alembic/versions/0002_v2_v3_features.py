"""V2/V3: кэш категорий Ozon, отзывы

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

marketplace = sa.Enum("wildberries", "ozon", name="marketplace")
review_sentiment = sa.Enum("positive", "neutral", "negative", name="reviewsentiment")


def upgrade() -> None:
    op.create_table(
        "ozon_category_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("type_id", sa.BigInteger(), nullable=True),
        sa.Column("parent_category_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("is_leaf", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ozon_category_nodes_category_id", "ozon_category_nodes", ["category_id"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("marketplace", marketplace, nullable=False),
        sa.Column("external_review_id", sa.String(128), nullable=False),
        sa.Column("sku", sa.String(128), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("sentiment", review_sentiment, nullable=True),
        sa.Column("is_answered", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("marketplace", "external_review_id", name="uq_review_external"),
    )


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_index("ix_ozon_category_nodes_category_id", table_name="ozon_category_nodes")
    op.drop_table("ozon_category_nodes")
    review_sentiment.drop(op.get_bind(), checkfirst=True)
