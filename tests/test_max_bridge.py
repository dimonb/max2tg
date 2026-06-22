"""Tests for max_bridge.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from max2tg.max_bridge import MaxBridge


def make_bridge(sessions=None, bot=None):
    return MaxBridge(
        sessions=sessions or [],
        work_dir="/tmp/test_max",
        bot=bot or AsyncMock(),
    )


# ── _get_sender_name ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sender_name_own_message():
    bridge = make_bridge()
    client = MagicMock()
    client.me = MagicMock(id=42)
    client.get_user = AsyncMock()

    msg = MagicMock(sender=42)
    result = await bridge._get_sender_name(client, msg)

    assert result == "я"
    client.get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_sender_name_known_user():
    bridge = make_bridge()
    client = MagicMock()
    client.me = MagicMock(id=1)

    # MagicMock(name=...) sets the mock's repr name, not an attribute
    n = MagicMock()
    n.name = "Alice"
    n.first_name = ""
    n.last_name = ""
    user = MagicMock()
    user.names = [n]
    client.get_user = AsyncMock(return_value=user)

    msg = MagicMock(sender=99)
    result = await bridge._get_sender_name(client, msg)
    assert result == "Alice"


@pytest.mark.asyncio
async def test_get_sender_name_first_last():
    bridge = make_bridge()
    client = MagicMock()
    client.me = MagicMock(id=1)

    n = MagicMock()
    n.name = None
    n.first_name = "Bob"
    n.last_name = "Smith"
    user = MagicMock()
    user.names = [n]
    client.get_user = AsyncMock(return_value=user)

    msg = MagicMock(sender=99)
    result = await bridge._get_sender_name(client, msg)
    assert result == "Bob Smith"


@pytest.mark.asyncio
async def test_get_sender_name_no_sender():
    bridge = make_bridge()
    client = MagicMock()
    msg = MagicMock(sender=None)
    result = await bridge._get_sender_name(client, msg)
    assert result == ""


@pytest.mark.asyncio
async def test_get_sender_name_unknown_user():
    bridge = make_bridge()
    client = MagicMock()
    client.me = MagicMock(id=1)
    client.get_user = AsyncMock(return_value=None)

    msg = MagicMock(sender=55)
    result = await bridge._get_sender_name(client, msg)
    assert result == "55"


# ── _get_dialog_title ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_dialog_title_from_dialog():
    bridge = make_bridge()
    client = MagicMock()

    dialog = MagicMock(id=42, owner=1, participants={"a": 1, "b": 2})
    client.dialogs = [dialog]

    n = MagicMock()
    n.name = "Contact Name"
    n.first_name = ""
    n.last_name = ""
    user = MagicMock()
    user.names = [n]
    client.get_user = AsyncMock(return_value=user)

    result = await bridge._get_dialog_title(client, 42)
    assert result == "Contact Name"


@pytest.mark.asyncio
async def test_get_dialog_title_fallback_to_id():
    bridge = make_bridge()
    client = MagicMock()
    client.dialogs = []

    result = await bridge._get_dialog_title(client, 42)
    assert result == "42"


@pytest.mark.asyncio
async def test_get_dialog_title_no_others():
    bridge = make_bridge()
    client = MagicMock()

    # only owner in participants
    dialog = MagicMock(id=42, owner=1, participants={"a": 1})
    client.dialogs = [dialog]

    result = await bridge._get_dialog_title(client, 42)
    assert result == "42"


# ── send_to_max ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_to_max_no_client():
    bridge = make_bridge()
    result = await bridge.send_to_max("+7900", 42, "hello")
    assert result is None


@pytest.mark.asyncio
async def test_send_to_max_text(tmp_path):
    bot = AsyncMock()
    bridge = make_bridge(bot=bot)

    client = AsyncMock()
    sent_msg = MagicMock(id=1001)
    client.send_message = AsyncMock(return_value=sent_msg)
    bridge._clients["+7900"] = client

    result = await bridge.send_to_max("+7900", 42, "hello")
    assert result == 1001
    client.send_message.assert_awaited_once_with(
        text="hello", chat_id=42, attachment=None, reply_to=None
    )


@pytest.mark.asyncio
async def test_send_to_max_with_photo():
    bridge = make_bridge()
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=MagicMock(id=5))
    bridge._clients["+7900"] = client

    photo_bytes = b"\xff\xd8\xff"  # fake JPEG
    result = await bridge.send_to_max("+7900", 42, "", photo_bytes=photo_bytes, photo_name="img.jpg")

    assert result == 5
    call_kwargs = client.send_message.call_args.kwargs
    attach = call_kwargs["attachment"]
    assert attach is not None
    # Photo stores file_name derived from url "https://x/img.jpg"
    assert attach.file_name == "img.jpg"


@pytest.mark.asyncio
async def test_send_to_max_returns_none_when_send_fails():
    bridge = make_bridge()
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=None)
    bridge._clients["+7900"] = client

    result = await bridge.send_to_max("+7900", 42, "hello")
    assert result is None


@pytest.mark.asyncio
async def test_send_to_max_with_reply():
    bridge = make_bridge()
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=MagicMock(id=99))
    bridge._clients["+7900"] = client

    await bridge.send_to_max("+7900", 42, "reply!", reply_to_max_id=500)

    call_kwargs = client.send_message.call_args.kwargs
    assert call_kwargs["reply_to"] == 500


# ── _forward_to_tg ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forward_to_tg_skips_when_no_pin(db):
    from max2tg import db as db_module

    bot = AsyncMock()
    bridge = make_bridge(bot=bot)

    client = AsyncMock()
    client.me = MagicMock(id=1)
    client.get_user = AsyncMock(return_value=None)
    client.dialogs = []

    msg = MagicMock(chat_id=42, id=100, text="hi", sender=2, attaches=None, link=None)

    await bridge._forward_to_tg("+7900", client, msg)

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_forward_to_tg_creates_topic_and_sends(db):
    from max2tg import db as db_module

    await db_module.set_pin(-100, "+7900")

    bot = AsyncMock()
    topic_mock = MagicMock(message_thread_id=7)
    bot.create_forum_topic = AsyncMock(return_value=topic_mock)
    sent_mock = MagicMock(message_id=888)
    bot.send_message = AsyncMock(return_value=sent_mock)

    bridge = make_bridge(bot=bot)
    client = AsyncMock()
    client.me = MagicMock(id=1)
    client.get_user = AsyncMock(return_value=None)
    client.dialogs = []

    msg = MagicMock(chat_id=42, id=100, text="hello", sender=2, attaches=None, link=None)

    await bridge._forward_to_tg("+7900", client, msg)

    bot.create_forum_topic.assert_awaited_once_with(-100, "42", icon_color=0xFFD67E)
    bot.send_message.assert_awaited_once()

    # verify mapping saved
    result = await db_module.get_tg_message_id("+7900", 42, 100)
    assert result == (-100, 888)


@pytest.mark.asyncio
async def test_forward_to_tg_reuses_existing_topic(db):
    from max2tg import db as db_module

    await db_module.set_pin(-100, "+7900")
    await db_module.upsert_topic(-100, 7, 42, "+7900", "Alice")

    bot = AsyncMock()
    sent_mock = MagicMock(message_id=888)
    bot.send_message = AsyncMock(return_value=sent_mock)

    bridge = make_bridge(bot=bot)
    client = AsyncMock()
    client.me = MagicMock(id=1)
    client.get_user = AsyncMock(return_value=None)
    client.dialogs = []

    msg = MagicMock(chat_id=42, id=100, text="hi", sender=2, attaches=None, link=None)

    await bridge._forward_to_tg("+7900", client, msg)

    bot.create_forum_topic.assert_not_awaited()


@pytest.mark.asyncio
async def test_forward_to_tg_with_reply(db):
    from max2tg import db as db_module

    await db_module.set_pin(-100, "+7900")
    await db_module.upsert_topic(-100, 7, 42, "+7900", "Alice")
    # tg 555 ↔ max 999
    await db_module.save_message(-100, 7, 555, 42, 999, "+7900")

    bot = AsyncMock()
    sent_mock = MagicMock(message_id=888)
    bot.send_message = AsyncMock(return_value=sent_mock)

    bridge = make_bridge(bot=bot)
    client = AsyncMock()
    client.me = MagicMock(id=1)
    client.get_user = AsyncMock(return_value=None)
    client.dialogs = []

    linked_msg = MagicMock(id=999)
    link = MagicMock(message=linked_msg)
    msg = MagicMock(chat_id=42, id=100, text="reply", sender=2, attaches=None, link=link)

    await bridge._forward_to_tg("+7900", client, msg)

    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["reply_parameters"].message_id == 555


# ── _send_attach: photo robustness ─────────────────────────────────────────────

def _photo_attach(base_url="https://cdn/photo", width=12000, height=12000):
    from pymax.types import PhotoAttach
    return PhotoAttach(
        base_url=base_url, height=height, width=width, photo_id=7,
        photo_token="t", preview_data=None, type=MagicMock(),
    )


@pytest.mark.asyncio
async def test_send_attach_photo_falls_back_to_document_on_bad_request():
    from aiogram.exceptions import TelegramBadRequest

    bot = AsyncMock()
    bot.send_photo = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="PHOTO_INVALID_DIMENSIONS")
    )
    bot.send_document = AsyncMock(return_value=MagicMock(message_id=321))

    bridge = make_bridge(bot=bot)
    bridge._download = AsyncMock(return_value=b"\xff\xd8\xff")
    msg = MagicMock(chat_id=42, id=100)

    result = await bridge._send_attach(_photo_attach(), "cap", msg, AsyncMock(), {"chat_id": -100})

    assert result.message_id == 321
    bot.send_photo.assert_awaited_once()
    bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_attach_photo_without_base_url_sends_caption_only():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
    bridge = make_bridge(bot=bot)
    bridge._download = AsyncMock()
    msg = MagicMock(chat_id=42, id=100)

    result = await bridge._send_attach(_photo_attach(base_url=""), "cap", msg, AsyncMock(), {"chat_id": -100})

    assert result.message_id == 9
    bridge._download.assert_not_awaited()
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_send_attach_swallows_failure_and_returns_none():
    bridge = make_bridge()
    bridge._send_attach = AsyncMock(side_effect=RuntimeError("boom"))
    msg = MagicMock(chat_id=42, id=100)

    result = await bridge._safe_send_attach(_photo_attach(), "cap", msg, AsyncMock(), {})

    assert result is None
