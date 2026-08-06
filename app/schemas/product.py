from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NewProductRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str | None = None


class SaveDraftRequest(BaseModel):
    product_id: int
    title: str | None = None
    description: str | None = None
    brand: str | None = None
    vendor_code: str | None = None
    barcode: str | None = None
    cost_price: float | None = None
    price: float | None = None
    color: str | None = None
    material: str | None = None
    package_contents: str | None = None
    car_model: str | None = None
    length_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    weight_g: int | None = None


class PublishLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    marketplace: str
    status: str
    status_code: int | None
    message: str | None
    external_id: str | None
    created_at: datetime


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    description: str | None
    brand: str
    vendor_code: str | None
    price: float | None
    cost_price: float | None
    status: str
    wb_nm_id: str | None
    ozon_product_id: str | None
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[ProductOut]


class PublishResponse(BaseModel):
    product_id: int
    status: str
    wb: PublishLogOut | None = None
    ozon: PublishLogOut | None = None


class ValidationIssueOut(BaseModel):
    field: str
    message: str
    severity: str


class ValidationResponse(BaseModel):
    is_valid: bool
    issues: list[ValidationIssueOut]
