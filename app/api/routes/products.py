"""Роуты нашего backend (см. таблицу «Наш Backend» в разделе 7 ТЗ)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_product_service, require_api_token
from app.schemas.product import (
    NewProductRequest,
    ProductListResponse,
    ProductOut,
    PublishLogOut,
    PublishResponse,
    SaveDraftRequest,
    ValidationIssueOut,
    ValidationResponse,
)
from app.services.product_service import ProductService
from app.services.storage import save_bytes

router = APIRouter(tags=["products"], dependencies=[Depends(require_api_token)])


@router.post("/newProduct", response_model=ProductOut)
async def new_product(payload: NewProductRequest, service: ProductService = Depends(get_product_service)) -> ProductOut:
    user = await service.get_or_create_user(payload.telegram_id, payload.username, payload.full_name)
    product = await service.create_draft(user.id)
    return ProductOut.model_validate(product)


@router.post("/saveDraft", response_model=ProductOut)
async def save_draft(payload: SaveDraftRequest, service: ProductService = Depends(get_product_service)) -> ProductOut:
    fields = payload.model_dump(exclude={"product_id"}, exclude_unset=True)
    try:
        product = await service.update_fields(payload.product_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProductOut.model_validate(product)


@router.get("/listProducts", response_model=ProductListResponse)
async def list_products(telegram_id: int, service: ProductService = Depends(get_product_service)) -> ProductListResponse:
    user = await service.get_or_create_user(telegram_id, None, None)
    products = await service.list_products(user.id)
    return ProductListResponse(items=[ProductOut.model_validate(p) for p in products])


@router.get("/products/{product_id}/validate", response_model=ValidationResponse)
async def validate_product(product_id: int, service: ProductService = Depends(get_product_service)) -> ValidationResponse:
    try:
        result = await service.validate(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValidationResponse(
        is_valid=result.is_valid,
        issues=[ValidationIssueOut(field=i.field, message=i.message, severity=i.severity.value) for i in result.issues],
    )


@router.post("/publish/{product_id}", response_model=PublishResponse)
async def publish(product_id: int, service: ProductService = Depends(get_product_service)) -> PublishResponse:
    try:
        summary = await service.publish(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    product = await service.get_product(product_id)
    return PublishResponse(
        product_id=product_id,
        status=product.status.value,
        wb=PublishLogOut.model_validate(summary.wb) if summary.wb else None,
        ozon=PublishLogOut.model_validate(summary.ozon) if summary.ozon else None,
    )


@router.post("/uploadImage/{product_id}")
async def upload_image(product_id: int, file: UploadFile, service: ProductService = Depends(get_product_service)) -> dict:
    content = await file.read()
    storage_file = await save_bytes(service.session, content, filename=file.filename or "image.jpg", content_type=file.content_type)
    image = await service.add_image(product_id, storage_file.id)
    return {"image_id": image.id, "storage_file_id": storage_file.id}
