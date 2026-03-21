"""Shared fixtures."""

import pytest
import pytest_asyncio
import aiosqlite
from max2tg import db as db_module


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """Fresh in-memory-like DB for each test (uses tmp file)."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    await db_module.init_db()
    return db_module
