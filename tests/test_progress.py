"""Прогресс-полоска (срез v8): одно сообщение, которое бот редактирует
(edit_text) по мере реальных шагов кода, а не поток отдельных сообщений и не
поддельная покадровая анимация (см. app/bot/progress.py).
"""

from __future__ import annotations

import pytest

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from app.bot.progress import (
    ProgressHandle,
    fail_progress,
    finish_progress,
    render_progress,
    set_progress,
    start_progress,
)


class _FakeMessage:
    def __init__(self):
        self.answered: list[str] = []
        self.edited: list[tuple[str, object]] = []
        self._edit_side_effect = None

    async def answer(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        self.answered.append(text)
        return self

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> "_FakeMessage":
        if self._edit_side_effect is not None:
            effect = self._edit_side_effect
            self._edit_side_effect = None
            raise effect
        self.edited.append((text, reply_markup))
        return self


def _no_wait(monkeypatch) -> list[float]:
    """Убирает реальные паузы MIN_EDIT_INTERVAL в тестах — тест проверяет факт
    и порядок вызовов, а не секундомер (раздел 1 ТЗ v8: интервал — деталь
    реализации, не то, что нужно ждать в юнит-тестах). Патчим app.bot.progress
    _sleep — приватный алиас asyncio.sleep внутри этого модуля, не сам
    asyncio.sleep (тот один на процесс и нужен нетронутым другому коду,
    например album-debounce в app/bot/handlers/new_product.py)."""
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("app.bot.progress._sleep", fake_sleep)
    return sleeps


# --- render_progress -----------------------------------------------------------


def test_render_progress_basic_bar_and_percent():
    text = render_progress(80, "Пишу название")
    assert "80%" in text
    assert text.count("█") == 8
    assert text.count("░") == 2


def test_render_progress_zero_percent_is_all_empty():
    text = render_progress(0, "Начинаю")
    assert "0%" in text
    assert text.count("█") == 0
    assert text.count("░") == 10


def test_render_progress_hundred_percent_is_all_filled():
    text = render_progress(100, "Готово")
    assert "100%" in text
    assert text.count("█") == 10
    assert text.count("░") == 0


def test_render_progress_clamps_out_of_range_values():
    assert "0%" in render_progress(-5, "x")
    assert "100%" in render_progress(150, "x")


def test_render_progress_includes_title_and_escapes_html():
    text = render_progress(50, "<script>alert(1)</script>", title="Публикую <b>карточку</b>")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "<b>карточку</b>" not in text
    assert "&lt;b&gt;карточку&lt;/b&gt;" in text


def test_render_progress_default_title():
    assert "Собираю карточку" in render_progress(10, "шаг")


# --- start_progress / set_progress ----------------------------------------------


@pytest.mark.asyncio
async def test_start_progress_sends_single_message_at_zero_percent(monkeypatch):
    _no_wait(monkeypatch)
    message = _FakeMessage()

    handle = await start_progress(message.answer, "Собираю карточку")

    assert isinstance(handle, ProgressHandle)
    assert len(message.answered) == 1
    assert "0%" in message.answered[0]
    assert "Собираю карточку" in message.answered[0]


@pytest.mark.asyncio
async def test_set_progress_edits_same_message_not_sends_new_one(monkeypatch):
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    await set_progress(handle, 35, "Категория")
    await set_progress(handle, 80, "Текст готов")

    # Одно исходное сообщение — дальше только editText, ни одного нового answer.
    assert len(message.answered) == 1
    assert len(message.edited) == 2
    assert "35%" in message.edited[0][0] and "Категория" in message.edited[0][0]
    assert "80%" in message.edited[1][0] and "Текст готов" in message.edited[1][0]


@pytest.mark.asyncio
async def test_set_progress_waits_between_fast_consecutive_edits(monkeypatch):
    """Раздел 1 ТЗ v8: не чаще раза в 0.8-1.0с — если два шага идут быстрее,
    второй edit должен подождать, а не выстрелить сразу же."""
    sleeps = _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    await set_progress(handle, 10, "Шаг 1")
    await set_progress(handle, 20, "Шаг 2")

    # Хотя бы одна пауза перед вторым edit — start_progress и оба set_progress
    # выполнились "мгновенно" в тесте, без монки задержка была бы 0.
    assert len(sleeps) >= 1
    assert all(s > 0 for s in sleeps)


# --- сбои: edit не проходит — полоска не должна ронять диалог ------------------


@pytest.mark.asyncio
async def test_set_progress_disables_silently_on_bad_request(monkeypatch):
    """Раздел 0 ТЗ v8: «если edit не прошёл — не падать»."""
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    message._edit_side_effect = TelegramBadRequest(method=None, message="message to edit not found")
    await set_progress(handle, 50, "Шаг")  # не должно бросить исключение
    assert message.edited == []

    # Дальнейшие вызовы молча ничего не делают, тоже не падают.
    await set_progress(handle, 100, "Готово")
    assert message.edited == []


@pytest.mark.asyncio
async def test_set_progress_ignores_message_not_modified_without_disabling(monkeypatch):
    """"message is not modified" — не настоящая ошибка (тот же текст), полоска
    остаётся рабочей для следующих, действительно новых шагов."""
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    message._edit_side_effect = TelegramBadRequest(method=None, message="message is not modified")
    await set_progress(handle, 10, "Шаг 1")
    assert message.edited == []

    await set_progress(handle, 20, "Шаг 2")
    assert len(message.edited) == 1
    assert "20%" in message.edited[0][0]


@pytest.mark.asyncio
async def test_set_progress_retries_once_after_retry_after(monkeypatch):
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    message._edit_side_effect = TelegramRetryAfter(method=None, message="flood", retry_after=2)
    await set_progress(handle, 40, "Шаг")

    assert len(message.edited) == 1
    assert "40%" in message.edited[0][0]


# --- fail_progress / finish_progress --------------------------------------------


@pytest.mark.asyncio
async def test_fail_progress_edits_message_with_warning_and_keyboard(monkeypatch):
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    marker_kb = object()
    await fail_progress(handle, "Не получилось собрать текст.", reply_markup=marker_kb)

    assert len(message.edited) == 1
    text, kb = message.edited[0]
    assert text == "⚠️ Не получилось собрать текст."
    assert kb is marker_kb


@pytest.mark.asyncio
async def test_fail_progress_disables_handle_for_further_updates(monkeypatch):
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")

    await fail_progress(handle, "Ошибка.")
    await set_progress(handle, 100, "Готово")

    assert len(message.edited) == 1  # второй edit (от set_progress) не прошёл


@pytest.mark.asyncio
async def test_finish_progress_disables_further_updates(monkeypatch):
    _no_wait(monkeypatch)
    message = _FakeMessage()
    handle = await start_progress(message.answer, "Собираю карточку")
    await set_progress(handle, 100, "Готово")

    await finish_progress(handle)
    await set_progress(handle, 100, "Готово ещё раз")

    assert len(message.edited) == 1  # второй set_progress после finish — no-op
