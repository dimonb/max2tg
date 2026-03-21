"""Async SQLite layer for the Max↔Telegram bridge."""

from __future__ import annotations

import aiosqlite

DB_PATH = ".cache/tg.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pins (
    tg_chat_id  INTEGER PRIMARY KEY,
    phone       TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_chat_id    INTEGER NOT NULL,
    tg_thread_id  INTEGER NOT NULL,
    max_chat_id   INTEGER NOT NULL,
    phone         TEXT NOT NULL,
    title         TEXT,
    UNIQUE(tg_chat_id, tg_thread_id),
    UNIQUE(tg_chat_id, max_chat_id, phone)
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_chat_id       INTEGER NOT NULL,
    tg_thread_id     INTEGER NOT NULL,
    tg_message_id    INTEGER NOT NULL,
    max_chat_id      INTEGER NOT NULL,
    max_message_id   INTEGER NOT NULL,
    phone            TEXT NOT NULL,
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(tg_chat_id, tg_message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_max
    ON messages(phone, max_chat_id, max_message_id);
"""


async def init_db() -> None:
    import os
    os.makedirs(".cache", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


# ── pins ──────────────────────────────────────────────────────────────────────

async def set_pin(tg_chat_id: int, phone: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pins(tg_chat_id, phone) VALUES (?, ?)",
            (tg_chat_id, phone),
        )
        await db.commit()


async def get_pin(tg_chat_id: int) -> str | None:
    """Return Max phone for a TG group, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT phone FROM pins WHERE tg_chat_id = ?", (tg_chat_id,))
        row = await cur.fetchone()
        return row[0] if row else None


async def get_pins_by_phone(phone: str) -> list[int]:
    """Return all TG group chat_ids pinned to a phone."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tg_chat_id FROM pins WHERE phone = ?", (phone,))
        return [r[0] for r in await cur.fetchall()]


# ── topics ────────────────────────────────────────────────────────────────────

async def get_topic_by_max(tg_chat_id: int, max_chat_id: int, phone: str) -> int | None:
    """Return tg_thread_id for a Max chat, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_thread_id FROM topics WHERE tg_chat_id=? AND max_chat_id=? AND phone=?",
            (tg_chat_id, max_chat_id, phone),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def get_topic_by_thread(tg_chat_id: int, tg_thread_id: int) -> tuple[int, str] | None:
    """Return (max_chat_id, phone) for a TG thread, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT max_chat_id, phone FROM topics WHERE tg_chat_id=? AND tg_thread_id=?",
            (tg_chat_id, tg_thread_id),
        )
        row = await cur.fetchone()
        return (row[0], row[1]) if row else None


async def upsert_topic(
    tg_chat_id: int,
    tg_thread_id: int,
    max_chat_id: int,
    phone: str,
    title: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO topics(tg_chat_id, tg_thread_id, max_chat_id, phone, title)
               VALUES (?, ?, ?, ?, ?)""",
            (tg_chat_id, tg_thread_id, max_chat_id, phone, title),
        )
        await db.commit()


# ── messages ──────────────────────────────────────────────────────────────────

async def save_message(
    tg_chat_id: int,
    tg_thread_id: int,
    tg_message_id: int,
    max_chat_id: int,
    max_message_id: int,
    phone: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO messages
               (tg_chat_id, tg_thread_id, tg_message_id, max_chat_id, max_message_id, phone)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tg_chat_id, tg_thread_id, tg_message_id, max_chat_id, max_message_id, phone),
        )
        await db.commit()


async def get_tg_message_id(phone: str, max_chat_id: int, max_message_id: int) -> tuple[int, int] | None:
    """Return (tg_chat_id, tg_message_id) by Max message, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT tg_chat_id, tg_message_id FROM messages
               WHERE phone=? AND max_chat_id=? AND max_message_id=?""",
            (phone, max_chat_id, max_message_id),
        )
        row = await cur.fetchone()
        return (row[0], row[1]) if row else None


async def get_max_message_id(tg_chat_id: int, tg_message_id: int) -> tuple[int, int, str] | None:
    """Return (max_chat_id, max_message_id, phone) by TG message, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT max_chat_id, max_message_id, phone FROM messages
               WHERE tg_chat_id=? AND tg_message_id=?""",
            (tg_chat_id, tg_message_id),
        )
        row = await cur.fetchone()
        return (row[0], row[1], row[2]) if row else None
