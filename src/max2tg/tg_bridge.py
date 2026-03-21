"""Telegram bot: handles /pin command and routes TG messages to Max."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from . import db

if TYPE_CHECKING:
    from .max_bridge import MaxBridge

log = logging.getLogger(__name__)

router = Router()


BOT_COMMANDS = [
    BotCommand(command="start",  description="Show help and status"),
    BotCommand(command="pin",    description="Pin this group to a Max session: /pin <phone>"),
    BotCommand(command="unpin",  description="Remove pin from this group"),
    BotCommand(command="send",   description="Send a message by phone: /send <phone> <text>"),
    BotCommand(command="login",  description="Add Max session: /login <phone> [name]"),
    BotCommand(command="logout", description="Remove Max session: /logout <phone>"),
]


async def register_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)


def build_dispatcher(
    max_bridge: "MaxBridge",
    sessions: list[dict],
    whitelist: list[int],
    work_dir: str = ".cache",
) -> Dispatcher:
    dp = Dispatcher()

    dp["max_bridge"] = max_bridge
    dp["sessions"] = {s["phone"]: s for s in sessions}
    dp["whitelist"] = set(whitelist)
    dp["work_dir"] = work_dir
    dp["pending_logins"] = {}   # user_id → {phone, name, temp_token, client}

    dp.include_router(router)
    return dp


def is_allowed(user_id: int, whitelist: set[int]) -> bool:
    return user_id in whitelist


# ── /start ───────────────────────────────────────────────────────────────────

_HELP = """
<b>Max ↔ Telegram Bridge</b>

This bot mirrors conversations between <b>Max messenger</b> and <b>Telegram forum groups</b>.

<b>How it works:</b>
• Each Max chat becomes a <b>topic</b> in the Telegram group.
• Messages sent to a topic are forwarded to the corresponding Max chat, and vice versa.
• Replies, photos, videos, files and audio are supported in both directions.

<b>Setup:</b>
1. Create a Telegram group and enable <i>Topics</i> (Group Settings → Topics).
2. Add this bot as an <b>administrator</b> with <i>Manage Topics</i> permission.
3. Send <code>/pin +79001234567</code> in the group to link it to a Max session.
   Topics will be created automatically as new Max messages arrive.

<b>Commands:</b>
/pin &lt;phone&gt; — link this group to a Max session (e.g. <code>/pin +79001234567</code>)
/unpin — remove the link from this group
/send &lt;phone&gt; &lt;text&gt; — find a Max user by phone and send them a message
  With multiple sessions: <code>/send &lt;session&gt; &lt;phone&gt; &lt;text&gt;</code>
/login &lt;phone&gt; [name] — add a new Max session (sends OTP to phone)
/logout &lt;phone&gt; — remove a Max session
/start — show this message

<b>Sending messages:</b>
Just write in a topic — the message will be delivered to the Max chat.
Reply to a message in the topic to send a reply in Max.

<i>Only users in the whitelist can interact with the bot.</i>
""".strip()


def _tg_user_str(message: Message) -> str:
    u = message.from_user
    if not u:
        return "unknown"
    parts = [p for p in [u.first_name, u.last_name] if isinstance(p, str) and p]
    name = " ".join(parts) or str(u.id)
    return f"{name} (id={u.id}, @{u.username})" if isinstance(u.username, str) else f"{name} (id={u.id})"


def _check_allowed(message: Message, whitelist: set[int]) -> bool:
    if not message.from_user or not is_allowed(message.from_user.id, whitelist):
        log.warning(
            "Unauthorized access denied: user=%s command=%s chat_id=%s",
            _tg_user_str(message),
            (message.text or "").split()[0] if message.text else "?",
            message.chat.id,
        )
        return False
    return True


@router.message(Command("start"))
async def cmd_start(message: Message, whitelist: set[int]) -> None:
    if not _check_allowed(message, whitelist):
        return
    await message.answer(_HELP)


# ── /pin ─────────────────────────────────────────────────────────────────────

@router.message(Command("pin"))
async def cmd_pin(
    message: Message,
    whitelist: set[int],
    sessions: dict[str, dict],
) -> None:
    if not _check_allowed(message, whitelist):
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Использование: /pin <phone>\nПример: /pin +79001234567")
        return

    phone = args[1].strip()
    if phone not in sessions:
        await message.reply(f"Сессия {phone} не найдена в config.yaml")
        return

    tg_chat_id = message.chat.id

    # Verify the group has Topics enabled and the bot is admin with manage_topics permission.
    bot: Bot = message.bot  # type: ignore[assignment]
    try:
        chat = await bot.get_chat(tg_chat_id)
        if not getattr(chat, "is_forum", False):
            await message.reply("❌ This group does not have Topics enabled.\nEnable Topics in Group Settings → Topics, then retry.")
            return
        me = await bot.get_me()
        member = await bot.get_chat_member(tg_chat_id, me.id)
        if not getattr(member, "can_manage_topics", False):
            await message.reply("❌ The bot is not an admin with Manage Topics permission.\nGrant that permission and retry.")
            return
    except Exception as e:
        await message.reply(f"❌ Could not verify bot permissions: {e}")
        return

    await db.set_pin(tg_chat_id, phone)
    name = sessions[phone].get("name", phone)
    await message.reply(
        f"✅ Группа привязана к сессии [{name}] {phone}\n"
        "Новые чаты из Max будут появляться здесь как топики."
    )
    log.info("Pinned chat %d to phone %s", tg_chat_id, phone)


# ── /unpin ────────────────────────────────────────────────────────────────────

@router.message(Command("unpin"))
async def cmd_unpin(message: Message, whitelist: set[int]) -> None:
    if not _check_allowed(message, whitelist):
        return
    tg_chat_id = message.chat.id
    phone = await db.get_pin(tg_chat_id)
    if not phone:
        await message.reply("Эта группа не привязана ни к одной сессии.")
        return
    # remove pin (set to sentinel — simplest without adding DELETE to db.py)
    async with __import__("aiosqlite").connect(db.DB_PATH) as conn:
        await conn.execute("DELETE FROM pins WHERE tg_chat_id = ?", (tg_chat_id,))
        await conn.commit()
    await message.reply(f"✅ Привязка к {phone} снята.")


# ── /send ─────────────────────────────────────────────────────────────────────

@router.message(Command("send"))
async def cmd_send(
    message: Message,
    whitelist: set[int],
    sessions: dict[str, dict],
    max_bridge: "MaxBridge",
) -> None:
    if not _check_allowed(message, whitelist):
        return

    # parse: /send [session_phone] <contact_phone> <text>
    parts = (message.text or "").split(maxsplit=3)
    # parts[0] = "/send", then up to 3 more tokens
    args = parts[1:]

    # detect optional session prefix: known session phone
    phone: str | None = None
    if args and args[0] in sessions:
        phone = args.pop(0)

    if len(args) < 2:
        await message.reply(
            "Usage: <code>/send &lt;contact_phone&gt; &lt;text&gt;</code>\n"
            "With multiple sessions: <code>/send &lt;session_phone&gt; &lt;contact_phone&gt; &lt;text&gt;</code>\n"
            "In a pinned group the session is picked automatically."
        )
        return

    contact_phone, text = args[0], args[1]

    # pick session: prefer the one pinned to this group, then single active, then ask
    if phone is None:
        pinned_phone = await db.get_pin(message.chat.id)
        if pinned_phone and pinned_phone in sessions:
            phone = pinned_phone
        else:
            active = max_bridge.active_phones()
            if not active:
                await message.reply("No active Max sessions.")
                return
            if len(active) > 1:
                phones_list = "\n".join(f"  <code>{p}</code>" for p in active)
                await message.reply(
                    f"Multiple sessions active. Specify one:\n{phones_list}\n\n"
                    f"<code>/send &lt;session_phone&gt; {contact_phone} {text}</code>"
                )
                return
            phone = active[0]

    await message.reply(f"Searching for <code>{contact_phone}</code> in Max…")
    try:
        result = await max_bridge.send_by_phone(phone, contact_phone, text)
    except Exception as e:
        await message.reply(f"Error: {e}")
        return

    if not result:
        await message.reply("Failed to send message.")
        return

    max_chat_id, max_msg_id = result

    # ensure topic exists in pinned groups so future messages are bridged
    tg_chat_ids = await db.get_pins_by_phone(phone)
    topic_links = []
    for tg_chat_id in tg_chat_ids:
        thread_id = await db.get_topic_by_max(tg_chat_id, max_chat_id, phone)
        if thread_id is None:
            client = max_bridge._clients.get(phone)
            title = (
                await max_bridge._get_dialog_title(client, max_chat_id)
                if client else contact_phone
            )
            topic = await message.bot.create_forum_topic(tg_chat_id, title[:128])
            thread_id = topic.message_thread_id
            await db.upsert_topic(tg_chat_id, thread_id, max_chat_id, phone, title)
        await db.save_message(tg_chat_id, thread_id, message.message_id, max_chat_id, max_msg_id, phone)
        topic_links.append(f"https://t.me/c/{str(tg_chat_id).lstrip('-100')}/{thread_id}")

    reply = f"✅ Sent to <code>{contact_phone}</code> via [{sessions[phone].get('name', phone)}]"
    if topic_links:
        reply += f"\nTopic: {topic_links[0]}"
    await message.reply(reply)
    log.info("Sent to %s via %s, max_chat_id=%d", contact_phone, phone, max_chat_id)


# ── /login ────────────────────────────────────────────────────────────────────

@router.message(Command("login"))
async def cmd_login(
    message: Message,
    whitelist: set[int],
    sessions: dict[str, dict],
    max_bridge: "MaxBridge",
    pending_logins: dict,
    work_dir: str,
) -> None:
    if not _check_allowed(message, whitelist):
        return

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 2:
        await message.reply("Usage: <code>/login &lt;phone&gt; [name]</code>\nExample: <code>/login +79001234567 Vasya</code>")
        return

    phone = args[1].strip()
    name = args[2].strip() if len(args) > 2 else phone

    if phone in sessions:
        await message.reply(f"Session <code>{phone}</code> is already active.")
        return

    from .max_bridge import MaxBridge as _MB
    from pymax import SocketMaxClient
    from pymax.payloads import UserAgentPayload
    import os

    digits = phone.lstrip("+")
    client_work_dir = os.path.join(work_dir, digits)
    os.makedirs(client_work_dir, exist_ok=True)
    client = SocketMaxClient(
        phone=phone,
        work_dir=client_work_dir,
        headers=UserAgentPayload(device_type="DESKTOP"),
    )

    try:
        await client.connect()
        temp_token = await client.request_code(phone)
    except Exception as e:
        await client._cleanup_client()
        await message.reply(f"❌ Failed to request code: {e}")
        log.exception("Login request_code failed for %s", phone)
        return

    pending_logins[message.from_user.id] = {
        "phone": phone,
        "name": name,
        "temp_token": temp_token,
        "client": client,
    }
    log.info("Login initiated for %s by tg_user=%d", phone, message.from_user.id)
    await message.reply(
        f"📱 Code sent to <code>{phone}</code>.\n"
        "Reply with the <b>6-digit code</b> from SMS.\n"
        "Use /cancel to abort."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, whitelist: set[int], pending_logins: dict) -> None:
    if not _check_allowed(message, whitelist):
        return
    state = pending_logins.pop(message.from_user.id, None)
    if not state:
        await message.reply("Nothing to cancel.")
        return
    await state["client"]._cleanup_client()
    await message.reply("✅ Login cancelled.")


# ── /logout ────────────────────────────────────────────────────────────────────

@router.message(Command("logout"))
async def cmd_logout(
    message: Message,
    whitelist: set[int],
    sessions: dict[str, dict],
    max_bridge: "MaxBridge",
) -> None:
    if not _check_allowed(message, whitelist):
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        active = max_bridge.active_phones()
        phones = "\n".join(f"  <code>{p}</code>" for p in active) or "  (none)"
        await message.reply(f"Usage: <code>/logout &lt;phone&gt;</code>\n\nActive sessions:\n{phones}")
        return

    phone = args[1].strip()
    if phone not in sessions:
        await message.reply(f"Session <code>{phone}</code> not found.")
        return

    await max_bridge.remove_session(phone)
    sessions.pop(phone, None)
    await db.delete_session(phone)
    log.info("Session %s removed by tg_user=%d", phone, message.from_user.id)
    await message.reply(f"✅ Session <code>{phone}</code> removed.")


# ── OTP code handler ──────────────────────────────────────────────────────────

@router.message(F.text.regexp(r"^\d{6}$"))
async def handle_otp_code(
    message: Message,
    whitelist: set[int],
    sessions: dict[str, dict],
    max_bridge: "MaxBridge",
    pending_logins: dict,
) -> None:
    if not message.from_user:
        return
    state = pending_logins.get(message.from_user.id)
    if not state:
        return  # not a pending login — let other handlers deal with it

    pending_logins.pop(message.from_user.id)
    client = state["client"]
    phone = state["phone"]
    name = state["name"]
    code = message.text.strip()

    try:
        await client.login_with_code(state["temp_token"], code, start=False)
    except Exception as e:
        await client._cleanup_client()
        await message.reply(f"❌ Login failed: {e}")
        log.exception("login_with_code failed for %s", phone)
        return

    await client._cleanup_client()

    session = {"phone": phone, "name": name, "telegram_id": message.from_user.id}
    await db.upsert_session(phone=phone, name=name, telegram_id=message.from_user.id)
    sessions[phone] = session
    await max_bridge.add_session(session)

    log.info("Session %s added via bot by tg_user=%d", phone, message.from_user.id)
    await message.reply(f"✅ Logged in as <code>{phone}</code> ({name}).\nMax messages will now be bridged.")


# ── incoming TG messages → Max ────────────────────────────────────────────────

@router.message(F.message_thread_id)
async def handle_topic_message(
    message: Message,
    whitelist: set[int],
    max_bridge: "MaxBridge",
) -> None:
    if not _check_allowed(message, whitelist):
        return

    tg_chat_id = message.chat.id
    thread_id = message.message_thread_id

    row = await db.get_topic_by_thread(tg_chat_id, thread_id)
    if not row:
        return  # unknown topic, not mapped to Max

    max_chat_id, phone = row

    # resolve reply
    reply_to_max_id: int | None = None
    if message.reply_to_message:
        r = await db.get_max_message_id(tg_chat_id, message.reply_to_message.message_id)
        if r:
            reply_to_max_id = r[1]

    # extract media
    photo_bytes: bytes | None = None
    photo_name = "photo.jpg"
    video_bytes: bytes | None = None
    video_name = "video.mp4"
    file_bytes: bytes | None = None
    file_name = "file"
    bot: Bot = message.bot  # type: ignore[assignment]

    if message.photo:
        largest = max(message.photo, key=lambda p: p.file_size or 0)
        photo_bytes = await _download_tg(bot, largest.file_id)

    elif message.video:
        video_bytes = await _download_tg(bot, message.video.file_id)
        video_name = message.video.file_name or "video.mp4"

    elif message.document:
        file_bytes = await _download_tg(bot, message.document.file_id)
        file_name = message.document.file_name or "file"

    elif message.audio:
        file_bytes = await _download_tg(bot, message.audio.file_id)
        file_name = message.audio.file_name or "audio.ogg"

    elif message.voice:
        file_bytes = await _download_tg(bot, message.voice.file_id)
        file_name = "voice.ogg"

    text = message.caption or message.text or ""
    media_type = (
        "photo" if photo_bytes else
        "video" if video_bytes else
        "file" if file_bytes else
        "text"
    )
    log.info(
        "TG→Max: from=%s chat=%d thread=%d → max_chat=%d session=%s type=%s text=%r",
        _tg_user_str(message), tg_chat_id, thread_id, max_chat_id, phone, media_type,
        text[:80],
    )

    max_msg_id = await max_bridge.send_to_max(
        phone=phone,
        max_chat_id=max_chat_id,
        text=text,
        reply_to_max_id=reply_to_max_id,
        photo_bytes=photo_bytes,
        photo_name=photo_name,
        video_bytes=video_bytes,
        video_name=video_name,
        file_bytes=file_bytes,
        file_name=file_name,
    )

    if max_msg_id:
        await db.save_message(
            tg_chat_id=tg_chat_id,
            tg_thread_id=thread_id,
            tg_message_id=message.message_id,
            max_chat_id=max_chat_id,
            max_message_id=max_msg_id,
            phone=phone,
        )


async def _download_tg(bot: Bot, file_id: str) -> bytes:
    from io import BytesIO
    bio = BytesIO()
    await bot.download(file_id, destination=bio)
    return bio.getvalue()
