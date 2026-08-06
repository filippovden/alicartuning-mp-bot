import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Category, Image, Product, User
from app.services.validation import (
    _check_description,
    _check_numbers,
    _check_required_fields,
    _check_title,
    validate_product,
)


def make_product(**overrides) -> Product:
    defaults = dict(
        title="ALICARTUNING / Накладки на зеркала для Lada Vesta",
        brand="ALICARTUNING",
        vendor_code="SKU-1",
        price=1200,
        cost_price=500,
        category_id=1,
        weight_g=300,
        length_mm=500,
        width_mm=200,
        height_mm=50,
        description="Прочные накладки из ABS-пластика. " * 3,
    )
    defaults.update(overrides)
    return Product(**defaults)


def test_required_fields_all_present():
    product = make_product()
    issues = _check_required_fields(product)
    assert issues == []


def test_required_fields_missing_title():
    product = make_product(title=None)
    issues = _check_required_fields(product)
    assert any(i.field == "title" for i in issues)


def test_title_too_long():
    product = make_product(title="A" * 201)
    issues = _check_title(product)
    assert any("длиннее" in i.message for i in issues)


def test_title_forbidden_word():
    product = make_product(title="ALICARTUNING аналог накладки на зеркала")
    issues = _check_title(product)
    assert any("запрещённое слово" in i.message for i in issues)


def test_description_forbidden_word():
    product = make_product(description="Это лучшая цена на рынке, покупайте скидка сейчас же прямо тут")
    issues = _check_description(product)
    assert any("запрещённое слово" in i.message for i in issues)


def test_price_below_cost_price():
    product = make_product(price=100, cost_price=500)
    issues = _check_numbers(product)
    assert any(i.field == "price" for i in issues)


def test_negative_weight():
    product = make_product(weight_g=-5)
    issues = _check_numbers(product)
    assert any(i.field == "weight_g" for i in issues)


@pytest.mark.asyncio
async def test_vendor_code_uniqueness(session):
    user = User(telegram_id=111, username="tester")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    existing = Product(user_id=user.id, vendor_code="DUP-1", title="Товар 1", brand="ALICARTUNING")
    session.add(existing)
    await session.commit()
    await session.refresh(existing)

    duplicate = Product(
        user_id=user.id,
        vendor_code="DUP-1",
        title="ALICARTUNING / Товар два для теста уникальности",
        brand="ALICARTUNING",
        price=1000,
        cost_price=500,
        weight_g=100,
        length_mm=100,
        width_mm=100,
        height_mm=100,
        description="Описание товара для проверки. " * 3,
    )
    session.add(duplicate)
    await session.commit()
    await session.refresh(duplicate)

    result = await validate_product(duplicate, [], [], session)
    assert not result.is_valid
    assert any("уже используется" in i.message for i in result.errors())


@pytest.mark.asyncio
async def test_valid_product_passes(session):
    user = User(telegram_id=222, username="tester2")
    category = Category(name="Тюнинг салона")
    session.add_all([user, category])
    await session.commit()
    await session.refresh(user)
    await session.refresh(category)

    product = Product(
        user_id=user.id,
        category_id=category.id,
        vendor_code="OK-1",
        title="ALICARTUNING / Накладки на зеркала для Lada Vesta",
        brand="ALICARTUNING",
        price=1200,
        cost_price=500,
        weight_g=300,
        length_mm=500,
        width_mm=200,
        height_mm=50,
        description="Прочные накладки из ABS-пластика, не трескаются при низких температурах. " * 2,
    )
    session.add(product)
    await session.commit()

    stmt = (
        select(Product)
        .where(Product.id == product.id)
        .options(selectinload(Product.category).selectinload(Category.attrs))
    )
    loaded = (await session.execute(stmt)).scalar_one()
    fake_images = [Image(product_id=loaded.id, storage_file_id=1, position=i) for i in range(4)]

    result = await validate_product(loaded, [], fake_images, session)
    assert result.errors() == []
