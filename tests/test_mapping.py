"""Маппинг товара в запросы WB/Ozon (раздел 9 ТЗ) — см. app/services/marketplaces/mapping.py."""

from app.db.models import Attribute, Category, CategoryAttr, Marketplace, Product
from app.services.marketplaces.mapping import build_ozon_item, build_wb_variant


def _make_category(**overrides) -> Category:
    defaults = dict(id=1, name="Накладки на зеркала", wb_subject_id=212, ozon_category_id=100, ozon_type_id=200)
    defaults.update(overrides)
    return Category(**defaults)


def _make_product(category: Category | None, **overrides) -> Product:
    defaults = dict(
        id=1,
        user_id=1,
        title="ALICARTUNING / Накладки на зеркала",
        brand="ALICARTUNING",
        vendor_code="ART-1",
        price=1200,
        barcode="4600000000001",
        weight_g=300,
        length_mm=500,
        width_mm=200,
        height_mm=50,
        description="Описание",
        category=category,
    )
    defaults.update(overrides)
    return Product(**defaults)


def _make_attribute(category: Category, marketplace: Marketplace, external_id: str, name: str, value: str) -> Attribute:
    cat_attr = CategoryAttr(
        id=external_id and int(external_id) + 1000,
        category=category,
        marketplace=marketplace,
        external_attr_id=external_id,
        name=name,
        required=True,
    )
    return Attribute(category_attr=cat_attr, value=value)


# --- Ozon ----------------------------------------------------------------


def test_build_ozon_item_includes_category_and_type_id():
    category = _make_category(ozon_category_id=100, ozon_type_id=200)
    product = _make_product(category)

    item = build_ozon_item(product, [])

    assert item["category_id"] == 100
    assert item["type_id"] == 200


def test_build_ozon_item_without_category_has_none_ids():
    product = _make_product(None)
    item = build_ozon_item(product, [])
    assert item["category_id"] is None
    assert item["type_id"] is None


def test_build_ozon_item_uses_default_vat_from_settings(monkeypatch):
    import app.services.marketplaces.mapping as mapping_module

    monkeypatch.setattr(mapping_module.settings, "ozon_default_vat", "0.20")
    category = _make_category()
    product = _make_product(category)

    item = build_ozon_item(product, [])
    assert item["vat"] == "0.20"


def test_build_ozon_item_vat_override_takes_precedence(monkeypatch):
    import app.services.marketplaces.mapping as mapping_module

    monkeypatch.setattr(mapping_module.settings, "ozon_default_vat", "0.20")
    category = _make_category()
    product = _make_product(category)

    item = build_ozon_item(product, [], vat="0")
    assert item["vat"] == "0"


def test_build_ozon_item_units_are_grams_and_millimeters():
    category = _make_category()
    product = _make_product(category, weight_g=555, length_mm=10, width_mm=20, height_mm=30)

    item = build_ozon_item(product, [])

    assert item["weight"] == 555
    assert item["weight_unit"] == "g"
    assert item["depth"] == 10
    assert item["width"] == 20
    assert item["height"] == 30
    assert item["dimension_unit"] == "mm"


def test_build_ozon_item_only_includes_ozon_attributes():
    category = _make_category()
    product = _make_product(category)
    wb_attr = _make_attribute(category, Marketplace.WB, "1", "Материал", "ABS-пластик")
    ozon_attr = _make_attribute(category, Marketplace.OZON, "2", "Цвет", "Чёрный")

    item = build_ozon_item(product, [wb_attr, ozon_attr])

    assert len(item["attributes"]) == 1
    assert item["attributes"][0]["attribute_id"] == 2
    assert item["attributes"][0]["values"] == [{"value": "Чёрный"}]


# --- Wildberries -------------------------------------------------------------


def test_build_wb_variant_only_includes_wb_attributes():
    category = _make_category()
    product = _make_product(category)
    wb_attr = _make_attribute(category, Marketplace.WB, "10", "Материал", "ABS-пластик")
    ozon_attr = _make_attribute(category, Marketplace.OZON, "20", "Цвет", "Чёрный")

    variant = build_wb_variant(product, [wb_attr, ozon_attr])

    assert len(variant["characteristics"]) == 1
    assert variant["characteristics"][0]["id"] == 10
    assert variant["characteristics"][0]["value"] == "ABS-пластик"


def test_build_wb_variant_basic_fields():
    category = _make_category()
    product = _make_product(category, vendor_code="SKU-42", price=1999, barcode="4600000000099")

    variant = build_wb_variant(product, [])

    assert variant["vendorCode"] == "SKU-42"
    assert variant["title"] == product.title
    assert variant["brand"] == "ALICARTUNING"
    assert variant["sizes"][0]["price"] == 1999
    assert variant["sizes"][0]["skus"] == ["4600000000099"]
