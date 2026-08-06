"""Работа с отзывами (раздел 4, V3 ТЗ): синхронизация отзывов с WB/Ozon, классификация
тональности и AI-автоответы в фирменном тоне.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Marketplace, Review, ReviewSentiment
from app.services.ai.client import AIContentGenerationError, AIContentService
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wb_feedbacks import WbFeedbacksClient

FALLBACK_TEMPLATES = {
    ReviewSentiment.POSITIVE: (
        "Спасибо за отзыв! Рады, что товар пришёлся по душе — будем рады видеть вас снова."
    ),
    ReviewSentiment.NEGATIVE: (
        "Спасибо за обратную связь. Если товар пришёл с браком или не подошёл — "
        "напишите, пожалуйста, в поддержку магазина, поможем с заменой или возвратом."
    ),
    ReviewSentiment.NEUTRAL: "Спасибо за отзыв! Если остались вопросы по товару — пишите, поможем.",
}


def classify_sentiment(rating: int | None) -> ReviewSentiment:
    if rating is None:
        return ReviewSentiment.NEUTRAL
    if rating >= 4:
        return ReviewSentiment.POSITIVE
    if rating <= 2:
        return ReviewSentiment.NEGATIVE
    return ReviewSentiment.NEUTRAL


async def sync_wb_reviews(session: AsyncSession, client: WbFeedbacksClient | None = None, take: int = 20) -> list[Review]:
    client = client or WbFeedbacksClient()
    feedbacks = await client.list_feedbacks(is_answered=False, take=take)

    reviews = []
    for fb in feedbacks:
        review = await _upsert_review(
            session,
            marketplace=Marketplace.WB,
            external_id=fb["id"],
            sku=fb.get("nmId"),
            rating=fb.get("productValuation"),
            author_name=fb.get("userName"),
            text=fb.get("text"),
        )
        reviews.append(review)
    return reviews


async def sync_ozon_reviews(session: AsyncSession, client: OzonClient | None = None, limit: int = 20) -> list[Review]:
    client = client or OzonClient()
    data = await client.list_reviews(status="UNPROCESSED", limit=limit)

    reviews = []
    for r in data.get("reviews", []):
        raw_text = r.get("text")
        text = raw_text.get("text") if isinstance(raw_text, dict) else raw_text
        review = await _upsert_review(
            session,
            marketplace=Marketplace.OZON,
            external_id=r["id"],
            sku=str(r.get("sku")) if r.get("sku") else None,
            rating=r.get("rating"),
            author_name=None,
            text=text,
        )
        reviews.append(review)
    return reviews


async def _upsert_review(
    session: AsyncSession,
    marketplace: Marketplace,
    external_id: str,
    sku: str | None,
    rating: int | None,
    author_name: str | None,
    text: str | None,
) -> Review:
    stmt = select(Review).where(Review.marketplace == marketplace, Review.external_review_id == str(external_id))
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing

    review = Review(
        marketplace=marketplace,
        external_review_id=str(external_id),
        sku=sku,
        rating=rating,
        author_name=author_name,
        text=text,
        sentiment=classify_sentiment(rating),
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return review


async def list_unanswered_reviews(session: AsyncSession, limit: int = 20) -> list[Review]:
    stmt = select(Review).where(Review.is_answered.is_(False)).order_by(Review.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def generate_reply(review: Review, product_title: str | None = None, ai_service: AIContentService | None = None) -> str:
    ai_service = ai_service or AIContentService()
    try:
        return await ai_service.generate_review_reply(product_title or "", review.rating or 5, review.text or "")
    except AIContentGenerationError:
        return FALLBACK_TEMPLATES.get(review.sentiment or ReviewSentiment.NEUTRAL, FALLBACK_TEMPLATES[ReviewSentiment.NEUTRAL])


async def answer_review(session: AsyncSession, review: Review, text: str) -> None:
    if review.marketplace == Marketplace.WB:
        client: WbFeedbacksClient | OzonClient = WbFeedbacksClient()
        await client.answer_feedback(review.external_review_id, text)
    else:
        client = OzonClient()
        await client.comment_review(review.external_review_id, text)

    review.is_answered = True
    review.reply_text = text
    review.answered_at = datetime.now(timezone.utc)
    await session.commit()
