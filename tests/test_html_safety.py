"""Регрессия: бот отправляет сообщения с parse_mode=HTML по умолчанию
(app/bot/main.py), поэтому текстовые плейсхолдеры вида `<ID>`/`<запрос>` ломают
отправку — Telegram отвечает `can't parse entities: Unsupported start tag "id"`,
и хендлер падает необработанным исключением (бот молчит, будто не работает).

Это уже случалось: WELCOME содержал `/clone <ID>`, `/edit <ID>` и т.п. — бот падал
на каждом /start. Проверяем весь модуль текстов на такие плейсхолдеры, а не
только константы, которые ловили руками.
"""

from __future__ import annotations

from pathlib import Path

from app.bot import texts
from app.services.validation import ALLOWED_HTML_TAGS, HTML_TAG_RE


def test_texts_module_has_no_disallowed_html_tags():
    content = Path(texts.__file__).read_text(encoding="utf-8")
    tags = HTML_TAG_RE.findall(content)
    bad_tags = sorted({t for t in tags if t.lower() not in ALLOWED_HTML_TAGS})
    assert not bad_tags, (
        f"В app/bot/texts.py найдены HTML-подобные конструкции, недопустимые для "
        f"parse_mode=HTML: {bad_tags}. Используйте квадратные скобки для "
        f"плейсхолдеров, например /clone [ID], а не /clone <ID>."
    )


def test_welcome_and_more_menu_use_square_bracket_placeholders():
    assert "<" not in texts.WELCOME
    assert "<" not in texts.MORE_MENU
    assert "/edit [ID]" in texts.MORE_MENU
    assert "/status [ID]" in texts.MORE_MENU
