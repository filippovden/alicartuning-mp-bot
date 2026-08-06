import httpx
import pytest
import respx

from app.db.models import Marketplace, Review, ReviewSentiment
from app.services.marketplaces.ozon import OzonClient
from app.services.marketplaces.wb_feedbacks import WbFeedbacksClient
from app.services.review_service import (
    answer_review,
    classify_sentiment,
    list_unanswered_reviews,
    sync_ozon_reviews,
    sync_wb_reviews,
)

WB_FEEDBACKS_URL = "https://feedbacks-api.wildberries.ru"
OZON_BASE_URL = "https://api-seller.ozon.ru"


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        (5, ReviewSentiment.POSITIVE),
        (4, ReviewSentiment.POSITIVE),
        (3, ReviewSentiment.NEUTRAL),
        (2, ReviewSentiment.NEGATIVE),
        (1, ReviewSentiment.NEGATIVE),
        (None, ReviewSentiment.NEUTRAL),
    ],
)
def test_classify_sentiment(rating, expected):
    assert classify_sentiment(rating) == expected


@pytest.mark.asyncio
@respx.mock
async def test_sync_wb_reviews_upserts(session):
    respx.get(f"{WB_FEEDBACKS_URL}/api/v1/feedbacks").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "feedbacks": [
                        {"id": "fb-1", "nmId": 111, "productValuation": 2, "userName": "Иван", "text": "Плохо подошло"},
                    ]
                }
            },
        )
    )
    client = WbFeedbacksClient(api_key="test", base_url=WB_FEEDBACKS_URL)
    reviews = await sync_wb_reviews(session, client=client)
    assert len(reviews) == 1
    assert reviews[0].sentiment == ReviewSentiment.NEGATIVE
    assert reviews[0].marketplace == Marketplace.WB

    # повторный вызов не должен создавать дубликат
    reviews_again = await sync_wb_reviews(session, client=client)
    assert reviews_again[0].id == reviews[0].id

    unanswered = await list_unanswered_reviews(session)
    assert len(unanswered) == 1


@pytest.mark.asyncio
@respx.mock
async def test_sync_ozon_reviews_parses_nested_text(session):
    respx.post(f"{OZON_BASE_URL}/v1/review/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "reviews": [
                    {"id": "rv-1", "sku": 222, "rating": 5, "text": {"text": "Отлично, всё подошло"}},
                ]
            },
        )
    )
    client = OzonClient(client_id="cid", api_key="key", base_url=OZON_BASE_URL)
    reviews = await sync_ozon_reviews(session, client=client)
    assert len(reviews) == 1
    assert reviews[0].text == "Отлично, всё подошло"
    assert reviews[0].sentiment == ReviewSentiment.POSITIVE


@pytest.mark.asyncio
@respx.mock
async def test_answer_review_marks_answered_wb(session):
    respx.patch(f"{WB_FEEDBACKS_URL}/api/v1/feedbacks").mock(return_value=httpx.Response(204))

    review = Review(
        marketplace=Marketplace.WB,
        external_review_id="fb-42",
        text="Норм",
        rating=4,
        sentiment=ReviewSentiment.POSITIVE,
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)

    # answer_review создаёт WbFeedbacksClient() с настройками по умолчанию — они уже
    # указывают на WB_FEEDBACKS_URL (см. app/config.py), поэтому respx-мок сработает
    # без подмены клиента.
    await answer_review(session, review, "Спасибо за отзыв!")

    assert review.is_answered is True
    assert review.reply_text == "Спасибо за отзыв!"
    assert review.answered_at is not None
