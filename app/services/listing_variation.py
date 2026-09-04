"""Разные карточки (title/description/bullets/vendor_code) из одной основы
товара под разные магазины — раздел 3 ТЗ v5.

Факты о товаре не меняются (тот же материал, модель, цена — см. Product).
Меняется только ФОРМУЛИРОВКА и артикул на каждый магазин: если выложить одну
и ту же карточку в несколько кабинетов, площадка видит дубль. Это честное
разное описание одного и того же товара, а не попытка скрыть связь кабинетов
или обойти модерацию (раздел 0 ТЗ v5).

Без ANTHROPIC_API_KEY — детерминированные шаблоны-перестановки (порядок
буллетов/фактов меняется по variant_index), бот всё равно публикует. С ключом
— то же самое best-effort улучшается через уже существующий AIContentService;
любой сбой AI молча откатывается на шаблон (тот же принцип, что и везде в
проекте — см. ProductService._safe_generate_bullets)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Product, ShopListing
from app.services.ai.client import AIContentService, ProductDraft
from app.services.seo_coach import SUGGESTION_FORBIDDEN_WORDS
from app.services.shops import Shop
from app.services.validation import TITLE_MAX_LEN

logger = logging.getLogger(__name__)

VARIANT_COUNT = 8

TITLE_TEMPLATES = [
    "{brand} / {part} для {model} ({facts})",
    "{brand} / {part_cap} {model}, {facts}",
    "{brand} / {facts_cap} {part} для {model}",
    "{brand} / {part} {model} — {facts}",
]

DESC_INTRO_TEMPLATES = [
    "Комплект подходит для {model}.",
    "{part_cap} для {model} — проверенная посадка.",
    "Для {model}: {part}, всё готово к установке.",
    "{part_cap}, совместимо с {model}.",
]


@dataclass
class ListingVariation:
    title: str
    description: str
    bullets: list[str]
    vendor_code: str


def _strip_forbidden(text: str) -> str:
    for word in SUGGESTION_FORBIDDEN_WORDS:
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip(" ,.-")


def _part_type(product: Product) -> str:
    brand = product.brand or settings.brand_name
    title = (product.title or "").strip()
    prefix = f"{brand} /"
    if title.casefold().startswith(prefix.casefold()):
        title = title[len(prefix) :].strip()
    if title:
        return title
    return product.category.name if product.category else "деталь"


def _facts(product: Product) -> list[str]:
    return [f for f in (product.material, product.color) if f]


def _shop_code(shop: Shop) -> str:
    """2 буквы кода магазина из его id/названия для артикула — НЕ ключ
    (раздел 3.1 ТЗ v5). Берём первые буквы первых двух слов названия (а не
    первые 2 символа строки целиком) — иначе "WB Салон" и "WB Кузов" дают
    одинаковый код "WB", раз оба названия начинают с платформы."""
    words = re.findall(r"[A-Za-zА-Яа-я0-9]+", shop.name or shop.id)
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        word = words[0]
        return (word[:2] if len(word) >= 2 else word + "X").upper()
    return "XX"


def _clean_title(title: str) -> str:
    title = re.sub(r"\(\s*\)", "", title)  # пустые факты → «()» убираем
    title = re.sub(r"[,—]\s*$", "", title.strip())
    title = _strip_forbidden(title)
    title = re.sub(r"\s{2,}", " ", title).strip()
    if len(title) > TITLE_MAX_LEN:
        title = title[:TITLE_MAX_LEN].rsplit(" ", 1)[0].rstrip(" ,.-")
    return title


def _template_title(product: Product, variant_index: int) -> str:
    brand = product.brand or settings.brand_name
    part = _part_type(product)
    model = product.car_model or ""
    facts = ", ".join(_facts(product))
    template = TITLE_TEMPLATES[variant_index % len(TITLE_TEMPLATES)]
    title = template.format(
        brand=brand,
        part=part,
        part_cap=part.capitalize(),
        model=model,
        facts=facts,
        facts_cap=facts.capitalize() if facts else "",
    )
    return _clean_title(title)


def _template_bullets(product: Product, variant_index: int) -> list[str]:
    model = product.car_model
    candidates = [
        f"Подходит для {model}" if model else None,
        f"Материал: {product.material}" if product.material else None,
        f"Цвет: {product.color}" if product.color else None,
        product.package_contents,
    ]
    bullets = [c for c in candidates if c]
    while len(bullets) < 3:
        bullets.append(f"Качество {product.brand or settings.brand_name}")
    offset = variant_index % len(bullets)
    rotated = bullets[offset:] + bullets[:offset]
    return rotated[:3]


def _template_description(product: Product, variant_index: int, bullets: list[str]) -> str:
    model = product.car_model or "вашего автомобиля"
    part = _part_type(product)
    intro = DESC_INTRO_TEMPLATES[variant_index % len(DESC_INTRO_TEMPLATES)].format(
        model=model, part=part, part_cap=part.capitalize()
    )
    bullet_lines = "\n".join(f"✓ {b}" for b in bullets)
    description = f"{intro}\n\n{bullet_lines}"
    return _strip_forbidden(description)


async def _ai_variation(
    ai_service: AIContentService, product: Product
) -> tuple[str | None, list[str] | None, str | None]:
    """Best-effort улучшение текста через уже существующий AIContentService
    (та же генерация, что и для обычной карточки) — не заводит отдельный
    промпт/клиент Anthropic под вариации."""
    draft = ProductDraft(
        category=product.category.name if product.category else "",
        draft_title=_part_type(product),
        car_model=product.car_model or "",
        color=product.color or "",
        material=product.material or "",
        package_contents=product.package_contents or "",
    )
    content = await ai_service.generate_full_content(draft)
    return content.get("title"), content.get("bullets"), content.get("description")


def _build_vendor_code_base(product: Product, shop: Shop) -> str:
    part = _part_type(product)
    part_short = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", part).upper()[:6] or "ART"
    model_short = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", product.car_model or "MODEL").upper()[:10] or "MODEL"
    return f"{part_short}-{model_short}-{_shop_code(shop)}"


async def _unique_vendor_code(session: AsyncSession, shop_id: str, base: str) -> str:
    n = 1
    while True:
        candidate = f"{base}-{n:02d}"
        stmt = select(ShopListing.id).where(ShopListing.shop_id == shop_id, ShopListing.vendor_code == candidate)
        exists = (await session.execute(stmt)).scalar_one_or_none()
        if exists is None:
            return candidate
        n += 1


async def _dedupe_title(session: AsyncSession, product_id: int, shop_id: str, title: str, product: Product) -> str:
    """Два listing одного товара не должны получить побайтово одинаковый
    title (раздел 3.2 ТЗ v5) — если после генерации совпало, добавляем
    нейтральный отличитель из фактов, а не «вариант 2»."""
    stmt = select(ShopListing.title).where(ShopListing.product_id == product_id, ShopListing.shop_id != shop_id)
    existing_titles = {t for (t,) in (await session.execute(stmt)).all() if t}
    if title not in existing_titles:
        return title

    for fact in (product.color, product.material, product.car_model):
        if fact and fact.casefold() not in title.casefold():
            candidate = _clean_title(f"{title} ({fact})")
            if candidate not in existing_titles:
                return candidate
    return title


async def build_listing_variation(
    product: Product,
    shop: Shop,
    variant_index: int,
    session: AsyncSession,
    ai_service: AIContentService | None = None,
) -> ListingVariation:
    """Вход: факты товара (Product) + магазин + индекс варианта 0..7. Выход:
    title/description/bullets(3)/vendor_code для ОДНОГО listing этого магазина."""
    title = _template_title(product, variant_index)
    bullets = _template_bullets(product, variant_index)
    description = _template_description(product, variant_index, bullets)

    if settings.anthropic_api_key and settings.anthropic_api_key.strip():
        service = ai_service or AIContentService()
        try:
            ai_title, ai_bullets, ai_description = await _ai_variation(service, product)
            if ai_title:
                title = _clean_title(ai_title)
            if ai_bullets:
                bullets = [_strip_forbidden(b) for b in ai_bullets[:3]] or bullets
            if ai_description:
                description = _strip_forbidden(ai_description)
        except Exception as exc:
            logger.warning(
                "Не удалось получить AI-вариацию текста для товара %s/магазина %s (%s) — использую шаблон",
                product.id,
                shop.id,
                str(exc),
            )

    title = await _dedupe_title(session, product.id, shop.id, title, product)

    vendor_code_base = _build_vendor_code_base(product, shop)
    vendor_code = await _unique_vendor_code(session, shop.id, vendor_code_base)

    return ListingVariation(title=title, description=description, bullets=bullets, vendor_code=vendor_code)
