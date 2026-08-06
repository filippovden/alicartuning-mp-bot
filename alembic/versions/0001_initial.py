"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


product_status = sa.Enum(
    "draft", "ready", "publishing", "published", "partially_published", "error", name="productstatus"
)
marketplace = sa.Enum("wildberries", "ozon", name="marketplace")
image_type = sa.Enum("main", "lifestyle", "infographic", "video_cover", name="imagetype")
publish_status = sa.Enum("success", "error", name="publishstatus")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("wb_subject_id", sa.Integer(), nullable=True),
        sa.Column("ozon_category_id", sa.BigInteger(), nullable=True),
        sa.Column("ozon_type_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "category_attrs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace", marketplace, nullable=False),
        sa.Column("external_attr_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("attr_type", sa.String(64), server_default="string", nullable=False),
        sa.Column("dictionary", sa.JSON(), nullable=True),
        sa.UniqueConstraint("category_id", "marketplace", "external_attr_id", name="uq_category_attr"),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("keywords", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(255), server_default="ALICARTUNING", nullable=False),
        sa.Column("vendor_code", sa.String(128), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("cost_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("color", sa.String(255), nullable=True),
        sa.Column("material", sa.String(255), nullable=True),
        sa.Column("package_contents", sa.Text(), nullable=True),
        sa.Column("car_model", sa.String(255), nullable=True),
        sa.Column("country_of_origin", sa.String(128), server_default="Россия", nullable=False),
        sa.Column("length_mm", sa.Integer(), nullable=True),
        sa.Column("width_mm", sa.Integer(), nullable=True),
        sa.Column("height_mm", sa.Integer(), nullable=True),
        sa.Column("weight_g", sa.Integer(), nullable=True),
        sa.Column("status", product_status, server_default="draft", nullable=False),
        sa.Column("wb_nm_id", sa.String(64), nullable=True),
        sa.Column("ozon_product_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_products_vendor_code", "products", ["vendor_code"])

    op.create_table(
        "variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(128), nullable=False),
        sa.Column("size", sa.String(64), nullable=True),
        sa.Column("color", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "storage_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_file_id", sa.Integer(), sa.ForeignKey("storage_files.id"), nullable=False),
        sa.Column("image_type", image_type, server_default="main", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "attributes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("variants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("category_attr_id", sa.Integer(), sa.ForeignKey("category_attrs.id"), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
    )

    op.create_table(
        "publish_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace", marketplace, nullable=False),
        sa.Column("status", publish_status, nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "bot_dialogs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(128), nullable=True),
        sa.Column("data", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("bot_dialogs")
    op.drop_table("publish_logs")
    op.drop_table("attributes")
    op.drop_table("images")
    op.drop_table("storage_files")
    op.drop_table("variants")
    op.drop_index("ix_products_vendor_code", table_name="products")
    op.drop_table("products")
    op.drop_table("category_attrs")
    op.drop_table("categories")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")

    product_status.drop(op.get_bind(), checkfirst=True)
    marketplace.drop(op.get_bind(), checkfirst=True)
    image_type.drop(op.get_bind(), checkfirst=True)
    publish_status.drop(op.get_bind(), checkfirst=True)
