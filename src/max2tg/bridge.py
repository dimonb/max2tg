"""Entry point: starts Max clients and Telegram bot in one asyncio loop."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

import yaml
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import db
from .max_bridge import MaxBridge
from .tg_bridge import build_dispatcher, register_commands

log = logging.getLogger(__name__)

CONFIG_FILE = Path("config.yaml")


def load_config() -> dict:
    def _expand(m: re.Match) -> str:
        var, _, default = m.group(1).partition(":-")
        value = os.environ.get(var)
        if value is None:
            if default:
                return default
            raise RuntimeError(f"Environment variable ${{{var}}} is not set")
        return value

    text = re.sub(r"\$\{([^}]+)\}", _expand, CONFIG_FILE.read_text())
    return yaml.safe_load(text)


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

    # Merge sessions from config.yaml and tg.db (db takes precedence for duplicates)
    db_sessions = await db.get_sessions()
    sessions_map: dict[str, dict] = {s["phone"]: s for s in sessions}
    for s in db_sessions:
        if s["phone"] not in sessions_map:
            sessions_map[s["phone"]] = s
    sessions = list(sessions_map.values())

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    max_bridge = MaxBridge(sessions=sessions, work_dir=work_dir, bot=bot)
    dp = build_dispatcher(max_bridge, sessions, whitelist, work_dir)

    await register_commands(bot)
    log.info("Starting bridge: %d Max sessions, whitelist=%s", len(sessions), whitelist)

    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL") or tg_cfg.get("webhook_url")

    # message_reaction must be explicitly requested (not sent by default)
    allowed_updates = set(dp.resolve_used_update_types()) | {"message_reaction"}

    async with asyncio.TaskGroup() as tg:
        tg.create_task(max_bridge.start(), name="max-bridge")
        if webhook_url:
            tg.create_task(
                _run_webhook(bot, dp, webhook_url, tg_cfg, allowed_updates), name="tg-webhook"
            )
        else:
            tg.create_task(
                dp.start_polling(bot, handle_signals=False, allowed_updates=allowed_updates),
                name="tg-polling",
            )


async def _run_webhook(
    bot: Bot, dp: Dispatcher, webhook_url: str, tg_cfg: dict, allowed_updates: set[str]
) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    path = tg_cfg.get("webhook_path", "/webhook")
    host = tg_cfg.get("webhook_host", "0.0.0.0")
    port = int(tg_cfg.get("webhook_port", 8080))

    # Secret token: Telegram sends it in X-Telegram-Bot-Api-Secret-Token header,
    # aiogram's SimpleRequestHandler verifies it automatically.
    # Derived from bot token via HMAC-SHA256 truncated to 32 hex chars (valid: A-Z a-z 0-9 _ -).
    import hashlib, hmac
    _derived = hmac.new(bot.token.encode(), b"webhook-secret", hashlib.sha256).hexdigest()[:32]
    secret = (
        os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        or tg_cfg.get("webhook_secret")
        or _derived
    )

    full_url = webhook_url.rstrip("/") + path
    await bot.set_webhook(
        url=full_url,
        secret_token=secret,
        drop_pending_updates=False,
        allowed_updates=list(allowed_updates),
    )
    log.info("Webhook set to %s (listening on %s:%d)", full_url, host, port)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        await runner.cleanup()
        await bot.delete_webhook()
