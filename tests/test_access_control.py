"""Whitelist доступа (централизованный outer middleware, см. app/bot/middlewares.py).

Критичный security-фикс: неавторизованный пользователь не должен иметь возможности
вызвать НИ ОДИН хендлер бота, и пустой whitelist должен запрещать всем (fail closed).
"""

import pytest

from app.bot.middlewares import AccessControlMiddleware, DENIED_TEXT
from app.config import settings


class _FakeUser:
    def __init__(self, uid: int):
        self.id = uid


class _FakeMessage:
    def __init__(self, user: _FakeUser):
        self.from_user = user
        self.answered: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answered.append(text)


class _FakeCallbackQuery:
    def __init__(self, user: _FakeUser):
        self.from_user = user
        self.answered: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answered.append((text, show_alert))


class _FakeUserEventWithUserField:
    """aiogram.types.PollAnswer / MessageReactionUpdated отдают отправителя через
    .user, а не .from_user, как остальные типы Update — см. app/bot/middlewares.py."""

    def __init__(self, user: _FakeUser):
        self.user = user


class _FakeUpdate:
    """Минимальный дублёр aiogram.types.Update — только поля, которые читает middleware."""

    def __init__(self, message=None, callback_query=None, poll_answer=None, message_reaction=None):
        self.message = message
        self.edited_message = None
        self.channel_post = None
        self.edited_channel_post = None
        self.callback_query = callback_query
        self.inline_query = None
        self.chosen_inline_result = None
        self.shipping_query = None
        self.pre_checkout_query = None
        self.poll_answer = poll_answer
        self.my_chat_member = None
        self.chat_member = None
        self.chat_join_request = None
        self.message_reaction = message_reaction


@pytest.mark.asyncio
async def test_denies_when_whitelist_empty(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "")
    middleware = AccessControlMiddleware()

    user = _FakeUser(111)
    message = _FakeMessage(user)
    update = _FakeUpdate(message=message)

    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True

    await middleware(handler, update, {})

    assert handler_called is False
    assert message.answered == [DENIED_TEXT]


@pytest.mark.asyncio
async def test_denies_user_not_in_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "999")
    middleware = AccessControlMiddleware()

    user = _FakeUser(111)
    message = _FakeMessage(user)
    update = _FakeUpdate(message=message)

    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True

    await middleware(handler, update, {})

    assert handler_called is False
    assert message.answered == [DENIED_TEXT]


@pytest.mark.asyncio
async def test_allows_user_in_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "111,222")
    middleware = AccessControlMiddleware()

    user = _FakeUser(111)
    message = _FakeMessage(user)
    update = _FakeUpdate(message=message)

    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "ok"

    result = await middleware(handler, update, {})

    assert handler_called is True
    assert result == "ok"
    assert message.answered == []


@pytest.mark.asyncio
async def test_denies_callback_query_with_alert(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "999")
    middleware = AccessControlMiddleware()

    user = _FakeUser(111)
    callback = _FakeCallbackQuery(user)
    update = _FakeUpdate(callback_query=callback)

    async def handler(event, data):
        raise AssertionError("handler не должен вызываться для неавторизованного пользователя")

    await middleware(handler, update, {})

    assert callback.answered == [(DENIED_TEXT, True)]


@pytest.mark.asyncio
async def test_denies_when_no_user_extractable(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "111")
    middleware = AccessControlMiddleware()

    update = _FakeUpdate()  # апдейт без message/callback_query — например, my_chat_member=None

    async def handler(event, data):
        raise AssertionError("handler не должен вызываться, если пользователя не удалось определить")

    result = await middleware(handler, update, {})
    assert result is None


@pytest.mark.asyncio
async def test_allows_poll_answer_from_whitelisted_user(monkeypatch):
    """PollAnswer отдаёт отправителя через .user, а не .from_user — регрессионный
    тест на находку code-review (иначе whitelisted-админ получал бы «Доступ
    запрещён» на голосование в собственном опросе)."""
    monkeypatch.setattr(settings, "telegram_admin_ids", "111")
    middleware = AccessControlMiddleware()

    user = _FakeUser(111)
    update = _FakeUpdate(poll_answer=_FakeUserEventWithUserField(user))

    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "ok"

    result = await middleware(handler, update, {})

    assert handler_called is True
    assert result == "ok"


@pytest.mark.asyncio
async def test_allows_message_reaction_from_whitelisted_user(monkeypatch):
    monkeypatch.setattr(settings, "telegram_admin_ids", "111")
    middleware = AccessControlMiddleware()

    user = _FakeUser(111)
    update = _FakeUpdate(message_reaction=_FakeUserEventWithUserField(user))

    handler_called = False

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "ok"

    result = await middleware(handler, update, {})

    assert handler_called is True
    assert result == "ok"


# --- app.bot.handlers.admin._is_admin (fail-closed, симметрично middleware) -----


@pytest.mark.asyncio
async def test_is_admin_fails_closed_when_whitelist_empty(monkeypatch):
    from app.bot.handlers.admin import _is_admin

    monkeypatch.setattr(settings, "telegram_admin_ids", "")
    message = _FakeMessage(_FakeUser(111))

    assert _is_admin(message) is False


@pytest.mark.asyncio
async def test_is_admin_true_for_whitelisted_user(monkeypatch):
    from app.bot.handlers.admin import _is_admin

    monkeypatch.setattr(settings, "telegram_admin_ids", "111")
    message = _FakeMessage(_FakeUser(111))

    assert _is_admin(message) is True


@pytest.mark.asyncio
async def test_is_admin_false_for_non_whitelisted_user(monkeypatch):
    from app.bot.handlers.admin import _is_admin

    monkeypatch.setattr(settings, "telegram_admin_ids", "111")
    message = _FakeMessage(_FakeUser(999))

    assert _is_admin(message) is False
