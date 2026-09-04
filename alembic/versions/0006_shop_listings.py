"""Мультимагазинность (срез v5): shop_listings + images.listing_id

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

marketplace = sa.Enum("wildberries", "ozon", name="marketplace")
listing_status = sa.Enum("draft", "published", "partial", "error", name="listingstatus")


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
