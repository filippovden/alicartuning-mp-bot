"""SEO-коуч карточки (раздел 2, срез v4): один экран с выводами по живой выдаче
Wildberries вместо сырого топ-5 (см. app/services/competitor_analysis.py) — где
мы по цене относительно рынка, каких слов не хватает в названии, сколько фото
у топа. Только Wildberries: у Ozon нет публичного поиска по каталогу конкурентов
(см. search_ozon_competitors), поэтому раздел «Выдача» на Ozon не претендует —
предложенные правки названия/цены применяются как текст карточки, без обещания
позиции в поиске Ozon.

Тонкий сервис поверх уже существующего search_wb_competitors — без своего
HTTP-клиента и без копирования логики поиска.
"""

from __future__ import annotations

import html
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from app.config import settings
from app.db.models import ImageType, Product, ProductStatus
from app.services.competitor_analysis import (
    STOPWORDS,
    CompetitorAnalysisError,
    search_wb_competitors,
)
from app.services.validation import FORBIDDEN_WORDS, TITLE_MAX_LEN

logger = logging.getLogger(__name__)

# «Хит» запрещён в промпте инфографики (app/services/ai/prompts.py:
# INFOGRAPHIC_PROMPT) и в примерах ТЗ, но исторически не входит в
# validation.FORBIDDEN_WORDS (тот список жёстко блокирует публикацию — трогать
# его ради одного слова из другого контекста не нужно). Здесь это только
# фильтр предложений SEO-коуча, поэтому расширяем локально.
SUGGESTION_FORBIDDEN_WORDS = FORBIDDEN_WORDS | {"хит"}

TOP_N_FOR_FEEDBACKS = 10
MIN_WORD_MENTIONS = 3
MIN_TOP_PHOTOS = 5
TYPICAL_FEEDBACKS_GAP_THRESHOLD = 20
MAX_ACTIONS = 5

ActionKind = Literal["price", "title", "photos", "reviews", "honest_limit"]


@dataclass
class Action:
    priority: int
    kind: ActionKind
    text: str


@dataclass
class SEOReport:
    query: str
    items_count: int
    our_price: float | None = None
    market_min: float | None = None
    market_p25: float | None = None
    market_median: float | None = None
    market_p75: float | None = None
    market_max: float | None = None
    price_rank: int | None = None
    price_vs_median_pct: float | None = None
    top_title_keywords: list[str] = field(default_factory=list)
    missing_in_our_title: list[str] = field(default_factory=list)
    forbidden_in_our_title: list[str] = field(default_factory=list)
    our_photo_count: int = 0
    typical_top_feedbacks: float | None = None
    our_rating_gap_note: str = ""
    actions: list[Action] = field(default_factory=list)
    suggested_title: str | None = None
    suggested_price: float | None = None


def build_query(product: Product) -> str:
    """Собирает поисковый запрос из полей товара, не из сырого title целиком
    (там бренд + маркетинговый мусор) — раздел 2.1 ТЗ."""
    brand = product.brand or settings.brand_name
    part_type = _strip_brand_prefix(product.title, brand) if product.title else ""

    if not part_type:
        category_name = product.category.name if product.category else ""
        pieces = [p for p in (product.car_model, category_name) if p]
        return " ".join(pieces)

    pieces = [part_type]
    if product.car_model:
        pieces.append(product.car_model)
    return " ".join(pieces)


def _strip_brand_prefix(title: str, brand: str) -> str:
    stripped = title.strip()
    prefix = f"{brand} /"
    if stripped.casefold().startswith(prefix.casefold()):
        stripped = stripped[len(prefix) :].strip()
    return stripped


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    frac = k - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


_WORD_RE = re.compile(r"[.,()/\"'«»]")


def _title_words(text: str) -> set[str]:
    cleaned = _WORD_RE.sub(" ", text.lower())
    return {w for w in cleaned.split() if len(w) >= 3}


def _competitor_word_counts(items) -> Counter[str]:
    counter: Counter[str] = Counter()
    for item in items:
        words = {w for w in _title_words(item.name) if w not in STOPWORDS}
        counter.update(words)
    return counter


def _forbidden_words_in(text: str) -> list[str]:
    lowered = text.casefold()
    return [w for w in FORBIDDEN_WORDS if w in lowered]


def _strip_forbidden_words(text: str) -> str:
    for word in SUGGESTION_FORBIDDEN_WORDS:
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip(" ,.-")


def suggested_title_for_product(product: Product) -> str | None:
    """Предложенное название в формате магазина — только из уже известных полей
    товара (title/car_model/material/color), без обращения к выдаче WB. Вынесено
    отдельной публичной функцией, чтобы хендлер «✍️ Подставить название»
    (seotitle:{id}) не гонял лишний сетевой поиск ради того, что не зависит
    от рынка — раздел 3.3 ТЗ v4."""
    brand = product.brand or settings.brand_name
    part_type = _strip_brand_prefix(product.title, brand) if product.title else ""
    if not part_type and product.category:
        part_type = product.category.name
    if not part_type:
        return None

    # part_type может уже содержать «для {модель}» и «(материал, цвет)», если
    # title уже когда-то приводили к этому формату — добавляем только то,
    # чего там ещё нет, иначе повторный вызов на уже готовом title удвоил бы
    # хвост вместо того, чтобы честно вернуть None («предлагать нечего»).
    body = part_type
    if product.car_model and product.car_model.casefold() not in body.casefold():
        body += f" для {product.car_model}"

    extras = [e for e in (product.material, product.color) if e]
    missing_extras = [e for e in extras if e.casefold() not in body.casefold()]
    if missing_extras:
        body += f" ({', '.join(missing_extras)})"

    title = f"{brand} / {body}"
    title = _strip_forbidden_words(title)
    if len(title) > TITLE_MAX_LEN:
        title = title[:TITLE_MAX_LEN].rsplit(" ", 1)[0].rstrip(" ,.-")
    if not title:
        return None

    if title.casefold() == (product.title or "").strip().casefold():
        return None  # уже в нужном формате — предлагать нечего
    return title


def _suggest_price(cost_price: float | None, p25: float | None, median: float | None) -> float | None:
    if p25 is None or median is None:
        return None
    candidate = round((p25 + median) / 2, 2)
    if cost_price is not None and candidate <= cost_price:
        # Рынок ниже себестоимости — демпинговать до этой цены нельзя,
        # честная реакция — отдельный action, а не тихая заниженная цена.
        return None
    return candidate


def _honest_limit_report(query: str, text: str) -> SEOReport:
    return SEOReport(
        query=query,
        items_count=0,
        actions=[Action(priority=1, kind="honest_limit", text=text)],
    )


async def build_seo_report(product: Product, *, limit: int = 20) -> SEOReport:
    """Строит отчёт по выдаче WB для карточки — раздел 2.2 ТЗ. Любой сбой
    поиска (сеть, пустая выдача, нечего собрать в query) возвращает честный
    отчёт с одним action вместо traceback или выдуманных данных."""
    brand = product.brand or settings.brand_name
    query = build_query(product)

    if not query:
        return _honest_limit_report(
            "", "Недостаточно данных о товаре для поиска — заполните название или модель авто."
        )

    try:
        report = await search_wb_competitors(query, limit=limit, exclude_brand=brand)
    except CompetitorAnalysisError:
        return _honest_limit_report(query, "Поиск Wildberries сейчас недоступен — попробуй повторить чуть позже.")

    if not report.items:
        return _honest_limit_report(query, f"По запросу «{query}» конкурентов на Wildberries не нашлось.")

    our_price = float(product.price) if product.price else None
    cost_price = float(product.cost_price) if product.cost_price else None

    competitor_prices = sorted(i.price for i in report.items if i.price)
    market_min = competitor_prices[0] if competitor_prices else None
    market_max = competitor_prices[-1] if competitor_prices else None
    market_p25 = _percentile(competitor_prices, 0.25)
    market_median = _percentile(competitor_prices, 0.5)
    market_p75 = _percentile(competitor_prices, 0.75)

    price_rank = None
    price_vs_median_pct = None
    if our_price is not None:
        combined = sorted(competitor_prices + [our_price])
        price_rank = combined.index(our_price) + 1
        if market_median:
            price_vs_median_pct = round((our_price - market_median) / market_median * 100, 1)

    our_title = product.title or ""
    our_title_words = _title_words(our_title)
    word_counts = _competitor_word_counts(report.items)
    missing_in_our_title = [
        word for word, count in word_counts.most_common(20) if count >= MIN_WORD_MENTIONS and word not in our_title_words
    ]
    missing_in_our_title = [w for w in missing_in_our_title if w not in SUGGESTION_FORBIDDEN_WORDS][:5]
    forbidden_in_our_title = _forbidden_words_in(our_title)

    our_photo_count = len([img for img in product.images if img.image_type == ImageType.MAIN])

    top_items = report.items[:TOP_N_FOR_FEEDBACKS]
    top_feedbacks = [i.feedbacks for i in top_items if i.feedbacks is not None]
    typical_top_feedbacks = round(sum(top_feedbacks) / len(top_feedbacks), 1) if top_feedbacks else None

    our_rating_gap_note = ""
    if typical_top_feedbacks is not None and typical_top_feedbacks >= TYPICAL_FEEDBACKS_GAP_THRESHOLD:
        our_rating_gap_note = (
            f"У топа выдачи в среднем {typical_top_feedbacks:.0f} отзывов — без своих отзывов "
            "органике тяжело; отзывы придут с продажами, ускорить может реклама в кабинете WB."
        )

    suggested_title = suggested_title_for_product(product)

    suggested_price = _suggest_price(cost_price, market_p25, market_median)
    if suggested_price is not None and our_price is not None and abs(suggested_price - our_price) < 1:
        suggested_price = None  # уже практически там же — не дублируем совет

    actions: list[Action] = []

    if missing_in_our_title:
        words = ", ".join(f"«{w}»" for w in missing_in_our_title[:3])
        actions.append(Action(priority=0, kind="title", text=f"Добавить в название: {words}"))

    if suggested_price is not None:
        actions.append(
            Action(
                priority=0,
                kind="price",
                text=f"Цена: поставить {suggested_price:.0f}₽ (между 25-м процентилем и медианой рынка, выше себестоимости)",
            )
        )
    elif cost_price is not None and market_median is not None and market_median < cost_price:
        actions.append(
            Action(
                priority=0,
                kind="price",
                text="Себестоимость выше рынка — демпинг до цены конкурентов убьёт маржу. "
                "Либо оставь текущую цену, либо снижай себестоимость.",
            )
        )

    if our_photo_count < MIN_TOP_PHOTOS:
        actions.append(
            Action(
                priority=0,
                kind="photos",
                text=f"Догрузить {MIN_TOP_PHOTOS - our_photo_count} фото/инфографику — в топе обычно {MIN_TOP_PHOTOS}+",
            )
        )

    if our_rating_gap_note:
        actions.append(Action(priority=0, kind="reviews", text=our_rating_gap_note))

    actions = actions[:MAX_ACTIONS]
    for idx, action in enumerate(actions, start=1):
        action.priority = idx

    return SEOReport(
        query=query,
        items_count=len(report.items),
        our_price=our_price,
        market_min=market_min,
        market_p25=market_p25,
        market_median=market_median,
        market_p75=market_p75,
        market_max=market_max,
        price_rank=price_rank,
        price_vs_median_pct=price_vs_median_pct,
        top_title_keywords=report.top_keywords(8),
        missing_in_our_title=missing_in_our_title,
        forbidden_in_our_title=forbidden_in_our_title,
        our_photo_count=our_photo_count,
        typical_top_feedbacks=typical_top_feedbacks,
        our_rating_gap_note=our_rating_gap_note,
        actions=actions,
        suggested_title=suggested_title,
        suggested_price=suggested_price,
    )


DIGEST_RELEVANT_KINDS = ("price", "title")


async def build_daily_seo_digest(products: list[Product]) -> list[str]:
    """Строки дайджеста «📈 Выдача за сутки» — раздел 4 ТЗ v4, вызывается из
    существующей ежедневной Celery-задачи snapshot_competitor_prices_task, не
    отдельным beat. Берём только опубликованные/частично опубликованные товары
    с чем считать запрос (title или car_model), и только если у отчёта нашлась
    реально actionable находка (цена/название) — иначе не спамим админов
    пустышками вроде «маловато фото» на каждый снимок."""
    lines: list[str] = []
    for product in products:
        if product.status not in (ProductStatus.PUBLISHED, ProductStatus.PARTIALLY_PUBLISHED):
            continue
        if not (product.title or product.car_model):
            continue
        try:
            report = await build_seo_report(product)
        except Exception as exc:
            logger.warning("SEO-дайджест: не удалось построить отчёт для товара %s (%s)", product.id, str(exc))
            continue

        relevant = [a for a in report.actions if a.kind in DIGEST_RELEVANT_KINDS]
        if not relevant:
            continue

        label = html.escape(product.vendor_code or product.title or f"#{product.id}")
        lines.append(f"• #{product.id} {label} — {html.escape(relevant[0].text)}")

    return lines


def format_seo_digest(lines: list[str]) -> str:
    return "📈 <b>Выдача за сутки</b>\n" + "\n".join(lines)
