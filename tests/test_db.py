"""Tests for db.py."""

import pytest
import pytest_asyncio
from max2tg import db as db_module


# ── pins ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_and_get_pin(db):
    await db.set_pin(100, "+79001234567")
    assert await db.get_pin(100) == "+79001234567"


@pytest.mark.asyncio
async def test_get_pin_missing(db):
    assert await db.get_pin(999) is None


@pytest.mark.asyncio
async def test_set_pin_overwrites(db):
    await db.set_pin(100, "+79001234567")
    await db.set_pin(100, "+79007654321")
    assert await db.get_pin(100) == "+79007654321"


@pytest.mark.asyncio
async def test_get_pins_by_phone(db):
    await db.set_pin(100, "+7900")
    await db.set_pin(200, "+7900")
    await db.set_pin(300, "+7911")
    result = await db.get_pins_by_phone("+7900")
    assert set(result) == {100, 200}


@pytest.mark.asyncio
async def test_get_pins_by_phone_empty(db):
    assert await db.get_pins_by_phone("+7999") == []


# ── topics ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_and_get_topic_by_max(db):
    await db.upsert_topic(
        tg_chat_id=100, tg_thread_id=5, max_chat_id=42, phone="+7900", title="Alice"
    )
    result = await db.get_topic_by_max(100, 42, "+7900")
    assert result == 5


@pytest.mark.asyncio
async def test_get_topic_by_max_missing(db):
    assert await db.get_topic_by_max(100, 999, "+7900") is None


@pytest.mark.asyncio
async def test_upsert_topic_idempotent(db):
    await db.upsert_topic(100, 5, 42, "+7900", "Alice")
    await db.upsert_topic(100, 5, 42, "+7900", "Alice Updated")  # should not raise
    # first value wins (INSERT OR IGNORE)
    result = await db.get_topic_by_max(100, 42, "+7900")
    assert result == 5


@pytest.mark.asyncio
async def test_get_topic_by_thread(db):
    await db.upsert_topic(100, 5, 42, "+7900", "Alice")
    result = await db.get_topic_by_thread(100, 5)
    assert result == (42, "+7900")


@pytest.mark.asyncio
async def test_get_topic_by_thread_missing(db):
    assert await db.get_topic_by_thread(100, 999) is None


@pytest.mark.asyncio
async def test_multiple_topics_same_group(db):
    await db.upsert_topic(100, 1, 10, "+7900", "Alice")
    await db.upsert_topic(100, 2, 20, "+7900", "Bob")
    assert await db.get_topic_by_thread(100, 1) == (10, "+7900")
    assert await db.get_topic_by_thread(100, 2) == (20, "+7900")


# ── messages ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_tg_message_id(db):
    await db.save_message(
        tg_chat_id=100, tg_thread_id=5, tg_message_id=777,
        max_chat_id=42, max_message_id=1001, phone="+7900",
    )
    result = await db.get_tg_message_id("+7900", 42, 1001)
    assert result == (100, 777)


@pytest.mark.asyncio
async def test_get_tg_message_id_missing(db):
    assert await db.get_tg_message_id("+7900", 42, 9999) is None


@pytest.mark.asyncio
async def test_save_and_get_max_message_id(db):
    await db.save_message(
        tg_chat_id=100, tg_thread_id=5, tg_message_id=777,
        max_chat_id=42, max_message_id=1001, phone="+7900",
    )
    result = await db.get_max_message_id(100, 777)
    assert result == (42, 1001, "+7900")


@pytest.mark.asyncio
async def test_get_max_message_id_missing(db):
    assert await db.get_max_message_id(100, 9999) is None


@pytest.mark.asyncio
async def test_save_message_idempotent(db):
    await db.save_message(100, 5, 777, 42, 1001, "+7900")
    await db.save_message(100, 5, 777, 42, 1001, "+7900")  # INSERT OR IGNORE, no error
    result = await db.get_max_message_id(100, 777)
    assert result == (42, 1001, "+7900")


@pytest.mark.asyncio
async def test_save_multiple_messages(db):
    await db.save_message(100, 5, 10, 42, 100, "+7900")
    await db.save_message(100, 5, 11, 42, 101, "+7900")
    assert await db.get_tg_message_id("+7900", 42, 100) == (100, 10)
    assert await db.get_tg_message_id("+7900", 42, 101) == (100, 11)
