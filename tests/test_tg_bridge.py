"""Tests for tg_bridge.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from max2tg.tg_bridge import is_allowed, build_dispatcher


# ── is_allowed ────────────────────────────────────────────────────────────────

def test_is_allowed_when_in_whitelist():
    assert is_allowed(12345, {12345, 99999}) is True


def test_is_allowed_when_not_in_whitelist():
    assert is_allowed(77777, {12345, 99999}) is False


def test_is_allowed_empty_whitelist():
    assert is_allowed(12345, set()) is False


# ── /pin handler ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pin_command_success(db):
    from max2tg.tg_bridge import cmd_pin
    from aiogram.types import Message, User, Chat

    message = MagicMock(spec=Message)
    message.text = "/pin +79001234567"
    message.chat = MagicMock(id=-100123)
    message.from_user = MagicMock(id=35243507)
    message.reply = AsyncMock()

    sessions = {"+79001234567": {"phone": "+79001234567", "name": "Dmitrii"}}
    whitelist = {35243507}

    await cmd_pin(message, whitelist=whitelist, sessions=sessions)

    message.reply.assert_awaited_once()
    reply_text = message.reply.call_args[0][0]
    assert "✅" in reply_text

    pin = await db.get_pin(-100123)
    assert pin == "+79001234567"


@pytest.mark.asyncio
async def test_pin_command_unknown_phone(db):
    from max2tg.tg_bridge import cmd_pin

    message = MagicMock()
    message.text = "/pin +79999999999"
    message.chat = MagicMock(id=-100123)
    message.from_user = MagicMock(id=35243507)
    message.reply = AsyncMock()

    await cmd_pin(message, whitelist={35243507}, sessions={})

    message.reply.assert_awaited_once()
    assert "не найдена" in message.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_pin_command_blocked_for_non_whitelist(db):
    from max2tg.tg_bridge import cmd_pin

    message = MagicMock()
    message.text = "/pin +79001234567"
    message.chat = MagicMock(id=-100123)
    message.from_user = MagicMock(id=99999)  # not in whitelist
    message.reply = AsyncMock()

    await cmd_pin(message, whitelist={35243507}, sessions={"+79001234567": {}})

    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_command_no_args(db):
    from max2tg.tg_bridge import cmd_pin

    message = MagicMock()
    message.text = "/pin"
    message.chat = MagicMock(id=-100123)
    message.from_user = MagicMock(id=35243507)
    message.reply = AsyncMock()

    await cmd_pin(message, whitelist={35243507}, sessions={})

    message.reply.assert_awaited_once()
    assert "Использование" in message.reply.call_args[0][0]


# ── handle_topic_message ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_topic_message_routes_to_max(db):
    from max2tg.tg_bridge import handle_topic_message

    await db.upsert_topic(
        tg_chat_id=-100, tg_thread_id=5,
        max_chat_id=42, phone="+7900", title="Alice",
    )

    max_bridge = MagicMock()
    max_bridge.send_to_max = AsyncMock(return_value=1001)

    message = MagicMock()
    message.from_user = MagicMock(id=35243507)
    message.chat = MagicMock(id=-100)
    message.message_thread_id = 5
    message.message_id = 777
    message.reply_to_message = None
    message.photo = None
    message.video = None
    message.document = None
    message.audio = None
    message.voice = None
    message.text = "Hello Max!"
    message.caption = None
    message.bot = AsyncMock()

    await handle_topic_message(message, whitelist={35243507}, max_bridge=max_bridge)

    max_bridge.send_to_max.assert_awaited_once()
    call_kwargs = max_bridge.send_to_max.call_args.kwargs
    assert call_kwargs["phone"] == "+7900"
    assert call_kwargs["max_chat_id"] == 42
    assert call_kwargs["text"] == "Hello Max!"

    # message mapping saved
    result = await db.get_max_message_id(-100, 777)
    assert result == (42, 1001, "+7900")


@pytest.mark.asyncio
async def test_handle_topic_message_unknown_topic(db):
    from max2tg.tg_bridge import handle_topic_message

    max_bridge = MagicMock()
    max_bridge.send_to_max = AsyncMock()

    message = MagicMock()
    message.from_user = MagicMock(id=35243507)
    message.chat = MagicMock(id=-100)
    message.message_thread_id = 999  # not mapped
    message.bot = AsyncMock()

    await handle_topic_message(message, whitelist={35243507}, max_bridge=max_bridge)

    max_bridge.send_to_max.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_topic_message_blocked_non_whitelist(db):
    from max2tg.tg_bridge import handle_topic_message

    await db.upsert_topic(-100, 5, 42, "+7900", "Alice")

    max_bridge = MagicMock()
    max_bridge.send_to_max = AsyncMock()

    message = MagicMock()
    message.from_user = MagicMock(id=99999)  # not in whitelist
    message.chat = MagicMock(id=-100)
    message.message_thread_id = 5
    message.bot = AsyncMock()

    await handle_topic_message(message, whitelist={35243507}, max_bridge=max_bridge)

    max_bridge.send_to_max.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_topic_message_with_reply(db):
    from max2tg.tg_bridge import handle_topic_message

    await db.upsert_topic(-100, 5, 42, "+7900", "Alice")
    # original message was tg_id=50 ↔ max_id=500
    await db.save_message(-100, 5, 50, 42, 500, "+7900")

    max_bridge = MagicMock()
    max_bridge.send_to_max = AsyncMock(return_value=501)

    message = MagicMock()
    message.from_user = MagicMock(id=35243507)
    message.chat = MagicMock(id=-100)
    message.message_thread_id = 5
    message.message_id = 51
    message.reply_to_message = MagicMock(message_id=50)
    message.photo = None
    message.video = None
    message.document = None
    message.audio = None
    message.voice = None
    message.text = "reply text"
    message.caption = None
    message.bot = AsyncMock()

    await handle_topic_message(message, whitelist={35243507}, max_bridge=max_bridge)

    call_kwargs = max_bridge.send_to_max.call_args.kwargs
    assert call_kwargs["reply_to_max_id"] == 500


# ── /send ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_send_single_session(db):
    from max2tg.tg_bridge import cmd_send

    max_bridge = MagicMock()
    max_bridge.active_phones.return_value = ["+7900"]
    max_bridge.send_by_phone = AsyncMock(return_value=(42, 1001))

    message = MagicMock()
    message.text = "/send +79001234567 Hello!"
    message.chat = MagicMock(id=-999)   # not pinned
    message.from_user = MagicMock(id=35243507)
    message.message_id = 10
    message.reply = AsyncMock()
    message.bot = AsyncMock()
    message.bot.create_forum_topic = AsyncMock(return_value=MagicMock(message_thread_id=7))

    sessions = {"+7900": {"phone": "+7900", "name": "Dmitrii"}}

    await cmd_send(message, whitelist={35243507}, sessions=sessions, max_bridge=max_bridge)

    max_bridge.send_by_phone.assert_awaited_once_with("+7900", "+79001234567", "Hello!")
    # last reply contains ✅
    last_reply = message.reply.call_args_list[-1][0][0]
    assert "✅" in last_reply


@pytest.mark.asyncio
async def test_cmd_send_uses_pinned_session(db):
    from max2tg.tg_bridge import cmd_send

    await db.set_pin(-100, "+7900")

    max_bridge = MagicMock()
    max_bridge.send_by_phone = AsyncMock(return_value=(42, 1001))

    message = MagicMock()
    message.text = "/send +79001234567 Hi"
    message.chat = MagicMock(id=-100)
    message.from_user = MagicMock(id=35243507)
    message.message_id = 10
    message.reply = AsyncMock()
    message.bot = AsyncMock()
    message.bot.create_forum_topic = AsyncMock(return_value=MagicMock(message_thread_id=7))

    sessions = {"+7900": {"name": "Dmitrii"}, "+7911": {"name": "Other"}}

    await cmd_send(message, whitelist={35243507}, sessions=sessions, max_bridge=max_bridge)

    # must use the pinned session, not ask
    max_bridge.send_by_phone.assert_awaited_once_with("+7900", "+79001234567", "Hi")


@pytest.mark.asyncio
async def test_cmd_send_explicit_session(db):
    from max2tg.tg_bridge import cmd_send

    max_bridge = MagicMock()
    max_bridge.send_by_phone = AsyncMock(return_value=(42, 1001))

    message = MagicMock()
    message.text = "/send +7900 +79001234567 Hello explicit"
    message.chat = MagicMock(id=-999)
    message.from_user = MagicMock(id=35243507)
    message.message_id = 10
    message.reply = AsyncMock()
    message.bot = AsyncMock()
    message.bot.create_forum_topic = AsyncMock(return_value=MagicMock(message_thread_id=7))

    sessions = {"+7900": {"name": "Dmitrii"}, "+7911": {"name": "Other"}}

    await cmd_send(message, whitelist={35243507}, sessions=sessions, max_bridge=max_bridge)

    max_bridge.send_by_phone.assert_awaited_once_with("+7900", "+79001234567", "Hello explicit")


@pytest.mark.asyncio
async def test_cmd_send_multiple_sessions_no_pin_asks(db):
    from max2tg.tg_bridge import cmd_send

    max_bridge = MagicMock()
    max_bridge.active_phones.return_value = ["+7900", "+7911"]
    max_bridge.send_by_phone = AsyncMock()

    message = MagicMock()
    message.text = "/send +79001234567 Hello"
    message.chat = MagicMock(id=-999)  # not pinned
    message.from_user = MagicMock(id=35243507)
    message.reply = AsyncMock()
    message.bot = AsyncMock()

    sessions = {"+7900": {}, "+7911": {}}

    await cmd_send(message, whitelist={35243507}, sessions=sessions, max_bridge=max_bridge)

    max_bridge.send_by_phone.assert_not_awaited()
    reply_text = message.reply.call_args_list[-1][0][0]
    assert "+7900" in reply_text or "Multiple" in reply_text


@pytest.mark.asyncio
async def test_cmd_send_error_propagated(db):
    from max2tg.tg_bridge import cmd_send

    max_bridge = MagicMock()
    max_bridge.active_phones.return_value = ["+7900"]
    max_bridge.send_by_phone = AsyncMock(side_effect=Exception("user not found"))

    message = MagicMock()
    message.text = "/send +79001234567 Hi"
    message.chat = MagicMock(id=-999)
    message.from_user = MagicMock(id=35243507)
    message.reply = AsyncMock()
    message.bot = AsyncMock()

    sessions = {"+7900": {"name": "Dmitrii"}}

    await cmd_send(message, whitelist={35243507}, sessions=sessions, max_bridge=max_bridge)

    last_reply = message.reply.call_args_list[-1][0][0]
    assert "Error" in last_reply
    assert "user not found" in last_reply


@pytest.mark.asyncio
async def test_cmd_send_blocked_non_whitelist(db):
    from max2tg.tg_bridge import cmd_send

    max_bridge = MagicMock()
    max_bridge.send_by_phone = AsyncMock()

    message = MagicMock()
    message.text = "/send +79001234567 Hi"
    message.from_user = MagicMock(id=99999)
    message.reply = AsyncMock()
    message.bot = AsyncMock()

    await cmd_send(message, whitelist={35243507}, sessions={}, max_bridge=max_bridge)

    max_bridge.send_by_phone.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_send_missing_args(db):
    from max2tg.tg_bridge import cmd_send

    max_bridge = MagicMock()
    max_bridge.send_by_phone = AsyncMock()

    message = MagicMock()
    message.text = "/send +79001234567"   # missing text
    message.chat = MagicMock(id=-999)
    message.from_user = MagicMock(id=35243507)
    message.reply = AsyncMock()
    message.bot = AsyncMock()

    await cmd_send(message, whitelist={35243507}, sessions={}, max_bridge=max_bridge)

    max_bridge.send_by_phone.assert_not_awaited()
    assert "Usage" in message.reply.call_args[0][0]


# ── build_dispatcher ──────────────────────────────────────────────────────────

def test_build_dispatcher_sets_data():
    from max2tg.tg_bridge import build_dispatcher

    sessions = [{"phone": "+7900", "name": "Test"}]
    whitelist = [35243507]
    max_bridge = MagicMock()

    dp = build_dispatcher(max_bridge, sessions, whitelist)

    assert dp["whitelist"] == {35243507}
    assert "+7900" in dp["sessions"]
    assert dp["max_bridge"] is max_bridge
