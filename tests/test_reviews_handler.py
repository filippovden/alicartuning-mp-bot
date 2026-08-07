"""Экранирование недоверенного текста отзывов перед отправкой в Telegram
(см. критические находки code-review — бот шлёт сообщения с parse_mode=HTML
по умолчанию, а review.text приходит из публичных отзывов WB/Ozon, то есть от
любого покупателя; см. app/bot/handlers/reviews.py)."""

from __future__ import annotations

import pytest

from app.bot.handlers import reviews as reviews_handler
from app.bot.handlers.reviews import _format_review, review_auto_reply
from app.db.models import Marketplace, Review

MALICIOUS_TEXT = '<a href="https://phishing.example.com">Служба поддержки</a>'


class _FakeMessage:
    def __init__(self):
        self.sent_texts: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.sent_texts.append(text)
        return self


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.alerts: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.alerts.append((text, show_alert))


async def _make_review(session, **overrides) -> Review:
    defaults = dict(marketplace=Marketplace.WB, external_review_id="ext-1", rating=1, text=MALICIOUS_TEXT)
    defaults.update(overrides)
    review = Review(**defaults)
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return review


# --- _format_review -----------------------------------------------------------


def test_format_review_escapes_html_in_text():
    review = Review(marketplace=Marketplace.WB, external_review_id="1", rating=1, text=MALICIOUS_TEXT, sku="ART-1")
    text = _format_review(review)
    assert "<a href" not in text
    assert "&lt;a href=" in text


def test_format_review_handles_no_text():
    review = Review(marketplace=Marketplace.WB, external_review_id="1", rating=5, text=None, sku="ART-1")
    text = _format_review(review)
    assert "(без текста)" in text


# --- review_auto_reply: AI-сгенерированный ответ тоже экранируется -----------


@pytest.mark.asyncio
async def test_review_auto_reply_escapes_generated_text(session, monkeypatch):
    async def fake_generate_reply(review):
        return MALICIOUS_TEXT

    async def fake_answer_review(session, review, text):
        review.is_answered = True
        review.reply_text = text

    monkeypatch.setattr(reviews_handler, "generate_reply", fake_generate_reply)
    monkeypatch.setattr(reviews_handler, "answer_review", fake_answer_review)

    review = await _make_review(session)
    callback = _FakeCallback(f"review_auto:{review.id}")

    await review_auto_reply(callback, session)

    assert len(callback.message.sent_texts) == 1
    sent = callback.message.sent_texts[0]
    assert "<a href" not in sent
    assert "&lt;a href=" in sent


@pytest.mark.asyncio
async def test_review_auto_reply_not_found_shows_alert(session, monkeypatch):
    callback = _FakeCallback("review_auto:999999")
    await review_auto_reply(callback, session)
    assert callback.alerts == [("Отзыв не найден", True)]
