"""Entry point: starts Max clients and Telegram bot in one asyncio loop."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import db
from .max_bridge import MaxBridge
from .tg_bridge import build_dispatcher, register_commands

log = logging.getLogger(__name__)

CONFIG_FILE = Path("config.yaml")


def load_config() -> dict:
    return yaml.safe_load(CONFIG_FILE.read_text())


async def main() -> None:
    cfg = load_config()
    tg_cfg = cfg.get("telegram", {})

    debug: bool = cfg.get("debug", False)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    if debug:
        # third-party libs are very noisy at DEBUG; keep them at INFO unless wanted
        for noisy in ("aiogram", "aiosqlite", "aiohttp", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.INFO)
        # enable pymax debug to diagnose sync/login issues
        logging.getLogger("pymax").setLevel(logging.DEBUG)
    bot_token: str = tg_cfg["bot_token"]
    whitelist: list[int] = tg_cfg.get("whitelist", [])
    sessions: list[dict] = cfg.get("sessions", [])
    work_dir: str = cfg.get("work_dir", ".cache/max")

    await db.init_db()

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    max_bridge = MaxBridge(sessions=sessions, work_dir=work_dir, bot=bot)
    dp = build_dispatcher(max_bridge, sessions, whitelist)

    await register_commands(bot)
    log.info("Starting bridge: %d Max sessions, whitelist=%s", len(sessions), whitelist)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(max_bridge.start(), name="max-bridge")
        tg.create_task(dp.start_polling(bot, handle_signals=False), name="tg-polling")
