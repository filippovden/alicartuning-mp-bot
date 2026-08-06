from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PARTIALLY_PUBLISHED = "partially_published"
    ERROR = "error"


class Marketplace(str, enum.Enum):
    WB = "wildberries"
    OZON = "ozon"


class ImageType(str, enum.Enum):
    MAIN = "main"
    LIFESTYLE = "lifestyle"
    INFOGRAPHIC = "infographic"
    VIDEO_COVER = "video_cover"


class PublishStatus(str, enum.Enum):
    SUCCESS = "success"
    ERROR = "error"


# --- USERS ---------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    products: Mapped[list["Product"]] = relationship(back_populates="user")
    dialogs: Mapped[list["BotDialog"]] = relationship(back_populates="user")


# --- CATEGORIES ------------------------------------------------------------


class Category(Base):
    """Соответствие внутренней категории категориям WB и Ozon."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    wb_subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ozon_category_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ozon_type_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attrs: Mapped[list["CategoryAttr"]] = relationship(back_populates="category")
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class CategoryAttr(Base):
    """Обязательные/опциональные характеристики категории (кэш ответа API WB/Ozon)."""

    __tablename__ = "category_attrs"
    __table_args__ = (UniqueConstraint("category_id", "marketplace", "external_attr_id", name="uq_category_attr"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    marketplace: Mapped[Marketplace] = mapped_column(Enum(Marketplace), nullable=False)
    external_attr_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    attr_type: Mapped[str] = mapped_column(String(64), default="string")
    dictionary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    category: Mapped["Category"] = relationship(back_populates="attrs")


# --- PRODUCTS / VARIANTS ----------------------------------------------------


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)

    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str] = mapped_column(String(255), default="ALICARTUNING")

    vendor_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    cost_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    color: Mapped[str | None] = mapped_column(String(255), nullable=True)
    material: Mapped[str | None] = mapped_column(String(255), nullable=True)
    package_contents: Mapped[str | None] = mapped_column(Text, nullable=True)
    car_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country_of_origin: Mapped[str] = mapped_column(String(128), default="Россия")

    length_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_g: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[ProductStatus] = mapped_column(Enum(ProductStatus), default=ProductStatus.DRAFT)

    wb_nm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ozon_product_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="products")
    category: Mapped["Category | None"] = relationship(back_populates="products")
    variants: Mapped[list["Variant"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    images: Mapped[list["Image"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    attributes: Mapped[list["Attribute"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    publish_logs: Mapped[list["PublishLog"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Variant(Base):
    """Вариация товара (цвет/размер). Для простых товаров создаётся один вариант."""

    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="variants")
    attributes: Mapped[list["Attribute"]] = relationship(back_populates="variant")


class Attribute(Base):
    """Заполненное значение характеристики товара (привязано к category_attrs)."""

    __tablename__ = "attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), nullable=True)
    category_attr_id: Mapped[int] = mapped_column(ForeignKey("category_attrs.id"))
    value: Mapped[str] = mapped_column(Text, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="attributes")
    variant: Mapped["Variant | None"] = relationship(back_populates="attributes")
    category_attr: Mapped["CategoryAttr"] = relationship()


# --- IMAGES / STORAGE --------------------------------------------------------


class StorageFile(Base):
    __tablename__ = "storage_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    images: Mapped[list["Image"]] = relationship(back_populates="storage_file")


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    storage_file_id: Mapped[int] = mapped_column(ForeignKey("storage_files.id"))
    image_type: Mapped[ImageType] = mapped_column(Enum(ImageType), default=ImageType.MAIN)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="images")
    storage_file: Mapped["StorageFile"] = relationship(back_populates="images")


# --- PUBLISH LOGS -------------------------------------------------------------


class PublishLog(Base):
    __tablename__ = "publish_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    marketplace: Mapped[Marketplace] = mapped_column(Enum(Marketplace), nullable=False)
    status: Mapped[PublishStatus] = mapped_column(Enum(PublishStatus), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="publish_logs")


# --- BOT DIALOGS ---------------------------------------------------------------


class BotDialog(Base):
    """Временное состояние FSM-диалога пользователя (черновик ввода)."""

    __tablename__ = "bot_dialogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="dialogs")
