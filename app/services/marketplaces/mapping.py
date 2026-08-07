"""Маппинг внутренней модели товара в запросы WB/Ozon API (раздел 9 ТЗ)."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.db.models import Attribute, CategoryAttr, Marketplace, Product


def build_wb_variant(product: Product, attributes: list[Attribute]) -> dict[str, Any]:
    """Формирует объект variants[] для POST /content/v2/cards/upload."""
    characteristics = [
        {"id": int(attr.category_attr.external_attr_id), "value": _wb_value(attr.value)}
        for attr in attributes
        if attr.category_attr.marketplace == Marketplace.WB
    ]

    variant: dict[str, Any] = {
        "vendorCode": product.vendor_code,
        "title": product.title,
        "description": product.description or "",
        "brand": product.brand,
        "characteristics": characteristics,
        "sizes": [
            {
                "techSize": _wb_tech_size(attributes),
                "price": int(product.price) if product.price else 0,
                "skus": [product.barcode] if product.barcode else [],
            }
        ],
    }
    return variant


def _wb_tech_size(attributes: list[Attribute]) -> str:
    """WB требует sizes[].techSize даже для категорий без реальной размерной
    сетки (запчасти/аксессуары автотюнинга) — без этого поля карточка чаще
    отклоняется на таких категориях. "0" — стандартное значение WB для
    «безразмерных» товаров; если среди характеристик категории всё же есть
    явный размер (название содержит «размер»), используем его значение вместо
    заглушки."""
    for attr in attributes:
        if attr.category_attr.marketplace == Marketplace.WB and "размер" in attr.category_attr.name.lower():
            return attr.value
    return "0"


def build_ozon_item(product: Product, attributes: list[Attribute], vat: str | None = None) -> dict[str, Any]:
    """Формирует объект items[] для POST /v2/product/import.

    category_id и type_id обязательны вместе — Ozon с 2022+ использует двухуровневую
    категоризацию (см. OzonClient.get_category_leaves): category_id указывает раздел
    дерева, type_id — конкретный тип товара внутри него; без type_id запрос будет
    отклонён API. vat обязателен для каждого товара (раздел 10 ТЗ — «Ошибки и лимиты»);
    по умолчанию берётся из настроек (OZON_DEFAULT_VAT), можно переопределить точечно.
    """
    attrs = [
        {"attribute_id": int(attr.category_attr.external_attr_id), "values": [{"value": attr.value}]}
        for attr in attributes
        if attr.category_attr.marketplace == Marketplace.OZON
    ]

    item: dict[str, Any] = {
        "offer_id": product.vendor_code,
        "name": product.title,
        "description": product.description or "",
        "price": str(int(product.price)) if product.price else "0",
        "currency_code": "RUB",
        "vat": vat if vat is not None else settings.ozon_default_vat,
        "category_id": product.category.ozon_category_id if product.category else None,
        "type_id": product.category.ozon_type_id if product.category else None,
        "attributes": attrs,
        # Единицы измерения объявлены явно ниже и должны соответствовать хранимым
        # значениям: weight_g — граммы, length/width/height_mm — миллиметры.
        "weight": product.weight_g or 0,
        "depth": product.length_mm or 0,
        "width": product.width_mm or 0,
        "height": product.height_mm or 0,
        "dimension_unit": "mm",
        "weight_unit": "g",
    }
    if product.barcode:
        item["barcode"] = product.barcode
    return item


def _wb_value(value: str) -> Any:
    """WB принимает значения либо строкой, либо списком строк (для мульти-справочников)."""
    if "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def category_attr_question_text(attr: CategoryAttr) -> str:
    """Текст вопроса боту для запроса значения обязательной характеристики категории."""
    suffix = " (обязательно)" if attr.required else ""
    return f"Укажите «{attr.name}»{suffix}:"
