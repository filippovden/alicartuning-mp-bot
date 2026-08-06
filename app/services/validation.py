"""Предпубликационная валидация карточки товара (раздел 10 ТЗ)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Attribute, Image, Product

TITLE_MAX_LEN = 200
TITLE_MAX_WORD_LEN = 27
DESCRIPTION_MAX_LEN = 6000

# Раздел 10: слова, запрещённые в названии/описании (Ozon)
FORBIDDEN_WORDS = {
    "акция",
    "скидка",
    "распродажа",
    "копия",
    "аналог",
    "реплика",
    "оригинал",
    "лучший",
    "лучшая цена",
    "самый лучший",
    "гарантия 100%",
}

ALLOWED_TITLE_CHARS = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 .,:;()\-/&\"№]+$")
ALLOWED_HTML_TAGS = {"b", "i", "ul", "ol", "li", "p", "br"}
HTML_TAG_RE = re.compile(r"</?([a-zA-Z0-9]+)[^>]*>")

WB_MIN_IMAGE_WIDTH, WB_MIN_IMAGE_HEIGHT = 900, 1200
WB_MIN_IMAGE_COUNT = 4
OZON_MIN_IMAGE_WIDTH, OZON_MIN_IMAGE_HEIGHT = 700, 933
OZON_MIN_IMAGE_COUNT = 1


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    field: str
    message: str
    severity: Severity = Severity.ERROR

    def __str__(self) -> str:
        prefix = "❌" if self.severity == Severity.ERROR else "⚠️"
        return f"{prefix} {self.message}"


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == Severity.ERROR for issue in self.issues)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    def as_text(self) -> str:
        return "\n".join(str(issue) for issue in self.issues)


async def validate_product(
    product: Product,
    attributes: list[Attribute],
    images: list[Image],
    session: AsyncSession,
) -> ValidationResult:
    issues: list[ValidationIssue] = []

    issues += _check_required_fields(product)
    issues += _check_required_attributes(product, attributes)
    issues += _check_title(product)
    issues += _check_description(product)
    issues += _check_numbers(product)
    issues += await _check_vendor_code_unique(product, session)
    issues += _check_images(images)

    return ValidationResult(issues=issues)


def _check_required_fields(product: Product) -> list[ValidationIssue]:
    issues = []
    required = {
        "title": "Не указано название товара",
        "brand": "Не указан бренд",
        "vendor_code": "Не указан артикул (SKU)",
        "price": "Не указана розничная цена",
        "category_id": "Не выбрана категория",
        "weight_g": "Не указан вес в упаковке",
        "length_mm": "Не указана длина упаковки",
        "width_mm": "Не указана ширина упаковки",
        "height_mm": "Не указана высота упаковки",
    }
    for field, message in required.items():
        if getattr(product, field) in (None, "", 0):
            issues.append(ValidationIssue(field=field, message=message))
    return issues


def _check_required_attributes(product: Product, attributes: list[Attribute]) -> list[ValidationIssue]:
    if not product.category:
        return []
    filled_ids = {attr.category_attr_id for attr in attributes if attr.value}
    issues = []
    for cat_attr in product.category.attrs:
        if cat_attr.required and cat_attr.id not in filled_ids:
            issues.append(
                ValidationIssue(
                    field=f"attr:{cat_attr.external_attr_id}",
                    message=f"Не указана обязательная характеристика «{cat_attr.name}» ({cat_attr.marketplace.value})",
                )
            )
    return issues


def _check_title(product: Product) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    title = product.title or ""
    if not title:
        return issues

    if len(title) > TITLE_MAX_LEN:
        issues.append(
            ValidationIssue(field="title", message=f"Название длиннее {TITLE_MAX_LEN} символов ({len(title)})")
        )

    for word in title.split():
        if len(word) > TITLE_MAX_WORD_LEN:
            issues.append(
                ValidationIssue(
                    field="title",
                    message=f"Слово «{word}» длиннее {TITLE_MAX_WORD_LEN} символов",
                )
            )
            break

    if not ALLOWED_TITLE_CHARS.match(title):
        issues.append(ValidationIssue(field="title", message="Название содержит недопустимые символы"))

    lowered = title.lower()
    for word in FORBIDDEN_WORDS:
        if word in lowered:
            issues.append(ValidationIssue(field="title", message=f"Название содержит запрещённое слово «{word}»"))

    return issues


def _check_description(product: Product) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    description = product.description or ""
    if not description:
        issues.append(ValidationIssue(field="description", message="Не указано описание товара"))
        return issues

    if len(description) > DESCRIPTION_MAX_LEN:
        issues.append(
            ValidationIssue(
                field="description",
                message=f"Описание длиннее {DESCRIPTION_MAX_LEN} символов ({len(description)})",
            )
        )
    elif len(description) < 50:
        issues.append(
            ValidationIssue(field="description", message="Описание слишком короткое (<50 символов)", severity=Severity.WARNING)
        )

    lowered = description.lower()
    for word in FORBIDDEN_WORDS:
        if word in lowered:
            issues.append(ValidationIssue(field="description", message=f"Описание содержит запрещённое слово «{word}»"))

    for tag in HTML_TAG_RE.findall(description):
        if tag.lower() not in ALLOWED_HTML_TAGS:
            issues.append(
                ValidationIssue(
                    field="description",
                    message=f"Недопустимый HTML-тег <{tag}> — будет удалён перед публикацией",
                    severity=Severity.WARNING,
                )
            )

    return issues


def _check_numbers(product: Product) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if product.price is not None and product.price <= 0:
        issues.append(ValidationIssue(field="price", message="Цена должна быть положительным числом"))
    if product.cost_price is not None and product.price is not None and product.price < product.cost_price:
        issues.append(ValidationIssue(field="price", message="Розничная цена ниже себестоимости"))
    for field, label in (("weight_g", "Вес"), ("length_mm", "Длина"), ("width_mm", "Ширина"), ("height_mm", "Высота")):
        value = getattr(product, field)
        if value is not None and value <= 0:
            issues.append(ValidationIssue(field=field, message=f"{label} должна быть положительным числом"))
    return issues


async def _check_vendor_code_unique(product: Product, session: AsyncSession) -> list[ValidationIssue]:
    if not product.vendor_code:
        return []
    stmt = select(Product.id).where(
        Product.vendor_code == product.vendor_code,
        Product.user_id == product.user_id,
        Product.id != product.id,
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        return [
            ValidationIssue(
                field="vendor_code",
                message=f"Артикул «{product.vendor_code}» уже используется другим товаром",
            )
        ]
    return []


def _check_images(images: list[Image]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if len(images) < WB_MIN_IMAGE_COUNT:
        issues.append(
            ValidationIssue(
                field="images",
                message=f"Для Wildberries рекомендуется загрузить не менее {WB_MIN_IMAGE_COUNT} фото (сейчас {len(images)})",
                severity=Severity.WARNING,
            )
        )
    if len(images) < OZON_MIN_IMAGE_COUNT:
        issues.append(ValidationIssue(field="images", message="Не загружено ни одного фото товара"))
    return issues


def check_image_dimensions(width: int, height: int) -> list[ValidationIssue]:
    """Отдельная проверка размеров конкретного файла при загрузке (см. раздел 10, 11)."""
    issues: list[ValidationIssue] = []
    if width < OZON_MIN_IMAGE_WIDTH or height < OZON_MIN_IMAGE_HEIGHT:
        issues.append(
            ValidationIssue(
                field="image",
                message=f"Фото {width}x{height}px меньше минимума Ozon ({OZON_MIN_IMAGE_WIDTH}x{OZON_MIN_IMAGE_HEIGHT}px)",
            )
        )
    if width < WB_MIN_IMAGE_WIDTH or height < WB_MIN_IMAGE_HEIGHT:
        issues.append(
            ValidationIssue(
                field="image",
                message=f"Фото {width}x{height}px меньше минимума Wildberries ({WB_MIN_IMAGE_WIDTH}x{WB_MIN_IMAGE_HEIGHT}px)",
                severity=Severity.WARNING,
            )
        )
    return issues
