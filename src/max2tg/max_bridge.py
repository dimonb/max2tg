"""Max→TG forwarding: manages SocketMaxClient instances, routes messages to Telegram."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from . import pymax_patches

pymax_patches.apply()

from pymax import Message, SocketMaxClient
from pymax.files import File, Photo, Video
from pymax.payloads import UserAgentPayload
from pymax.types import (
    AudioAttach,
    ContactAttach,
    FileAttach,
    PhotoAttach,
    StickerAttach,
    VideoAttach,
)

from . import db

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message as TgMessage

log = logging.getLogger(__name__)


class MaxBridge:
    def __init__(self, sessions: list[dict], work_dir: str, bot: "Bot") -> None:
        self._sessions = sessions
        self._work_dir = work_dir
        self._bot = bot
        self._clients: dict[str, SocketMaxClient] = {}
        self._tasks: list[asyncio.Task] = []

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        for s in self._sessions:
            client = self._make_client(s)
            self._clients[s["phone"]] = client
            task = asyncio.create_task(
                self._run_client(s["phone"], client), name=f"max-{s['phone']}"
            )
            self._tasks.append(task)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                pass

    # ── client factory ────────────────────────────────────────────────────────

    def _make_client(self, s: dict) -> SocketMaxClient:
        import os
        digits = s["phone"].lstrip("+")
        work_dir = os.path.join(self._work_dir, digits)
        os.makedirs(work_dir, exist_ok=True)
        client = SocketMaxClient(
            phone=s["phone"],
            token=s.get("token"),
            work_dir=work_dir,
            headers=UserAgentPayload(device_type="DESKTOP"),
        )
        return client

    # ── run loop ──────────────────────────────────────────────────────────────

    async def _run_client(self, phone: str, client: SocketMaxClient) -> None:
        @client.on_message()
        async def on_msg(msg: Message) -> None:
            try:
                await self._forward_to_tg(phone, client, msg)
            except Exception:
                log.exception("Error forwarding message from %s", phone)

        try:
            await client.start()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Max client %s crashed", phone)

    # ── Max → TG ──────────────────────────────────────────────────────────────

    async def _forward_to_tg(
        self, phone: str, client: SocketMaxClient, msg: Message
    ) -> None:
        if not msg.chat_id:
            return

        tg_chat_ids = await db.get_pins_by_phone(phone)
        if not tg_chat_ids:
            return

        sender_name = await self._get_sender_name(client, msg)
        prefix = f"[{sender_name}] " if sender_name else ""
        text = f"{prefix}{msg.text or ''}"

        attach_types = [type(a).__name__ for a in (msg.attaches or [])]
        log.info(
            "Max→TG: from=%s (id=%s) session=%s max_chat=%d → tg_chats=%s type=%s text=%r",
            sender_name or "?", msg.sender, phone, msg.chat_id,
            tg_chat_ids, attach_types or "text", (msg.text or "")[:80],
        )

        # resolve reply: find tg_message_id for the linked Max message
        reply_to_tg_msg_id: int | None = None
        if msg.link and msg.link.message:
            row = await db.get_tg_message_id(phone, msg.chat_id, msg.link.message.id)
            if row:
                reply_to_tg_msg_id = row[1]

        for tg_chat_id in tg_chat_ids:
            thread_id = await self._ensure_topic(tg_chat_id, phone, client, msg.chat_id, msg.sender)
            sent = await self._send_to_tg(
                tg_chat_id, thread_id, text, msg, phone, client, reply_to_tg_msg_id
            )
            if sent and msg.id:
                await db.save_message(
                    tg_chat_id=tg_chat_id,
                    tg_thread_id=thread_id,
                    tg_message_id=sent.message_id,
                    max_chat_id=msg.chat_id,
                    max_message_id=msg.id,
                    phone=phone,
                )

    async def _ensure_topic(
        self,
        tg_chat_id: int,
        phone: str,
        client: SocketMaxClient,
        max_chat_id: int,
        sender_id: int | None = None,
    ) -> int:
        thread_id = await db.get_topic_by_max(tg_chat_id, max_chat_id, phone)
        if thread_id is not None:
            return thread_id

        title = await self._get_dialog_title(client, max_chat_id, sender_id)
        topic = await self._bot.create_forum_topic(tg_chat_id, title[:128])
        thread_id = topic.message_thread_id
        await db.upsert_topic(tg_chat_id, thread_id, max_chat_id, phone, title)
        log.info("Created topic %d '%s' for Max chat %d", thread_id, title, max_chat_id)
        return thread_id

    async def _send_to_tg(
        self,
        tg_chat_id: int,
        thread_id: int,
        text: str,
        msg: Message,
        phone: str,
        client: SocketMaxClient,
        reply_to: int | None,
    ) -> Any:
        from aiogram.types import ReplyParameters

        kwargs: dict[str, Any] = {
            "chat_id": tg_chat_id,
            "message_thread_id": thread_id,
        }
        if reply_to:
            kwargs["reply_parameters"] = ReplyParameters(message_id=reply_to)

        attaches = msg.attaches or []
        if attaches:
            # send first attach with caption=text, rest without
            first = await self._send_attach(attaches[0], text, msg, client, kwargs)
            for attach in attaches[1:]:
                await self._send_attach(attach, None, msg, client, kwargs)
            return first

        if text.strip():
            return await self._bot.send_message(**kwargs, text=text)
        return None

    async def _send_attach(
        self,
        attach: Any,
        caption: str | None,
        msg: Message,
        client: SocketMaxClient,
        kwargs: dict,
    ) -> Any:
        from aiogram.types import BufferedInputFile

        bot = self._bot
        cap = caption or ""

        if isinstance(attach, PhotoAttach):
            data = await self._download(attach.base_url)
            return await bot.send_photo(
                **kwargs, photo=BufferedInputFile(data, "photo.jpg"), caption=cap or None
            )

        if isinstance(attach, VideoAttach):
            vr = await client.get_video_by_id(msg.chat_id, msg.id, attach.video_id) if msg.id else None
            url = vr.url if vr else None
            if url:
                data = await self._download(url)
                return await bot.send_video(
                    **kwargs,
                    video=BufferedInputFile(data, f"video_{attach.video_id}.mp4"),
                    caption=cap or None,
                )
            return await bot.send_message(**kwargs, text=cap) if cap else None

        if isinstance(attach, FileAttach):
            fr = await client.get_file_by_id(msg.chat_id, msg.id, attach.file_id) if msg.id else None
            url = fr.url if fr else None
            if url:
                data = await self._download(url)
                name = attach.name or f"file_{attach.file_id}"
                return await bot.send_document(
                    **kwargs, document=BufferedInputFile(data, name), caption=cap or None
                )
            return await bot.send_message(**kwargs, text=cap) if cap else None

        if isinstance(attach, AudioAttach) and attach.url:
            data = await self._download(attach.url)
            return await bot.send_audio(
                **kwargs, audio=BufferedInputFile(data, "audio.ogg"), caption=cap or None
            )

        if isinstance(attach, StickerAttach) and attach.url:
            data = await self._download(attach.url)
            return await bot.send_sticker(**kwargs, sticker=BufferedInputFile(data, "sticker.webp"))

        if isinstance(attach, ContactAttach):
            parts = [attach.name or "", attach.first_name or "", attach.last_name or ""]
            contact_text = f"👤 {' '.join(p for p in parts if p)}".strip()
            full = f"{cap}\n{contact_text}".strip() if cap else contact_text
            return await bot.send_message(**kwargs, text=full)

        # fallback
        return await bot.send_message(**kwargs, text=cap) if cap else None

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _get_sender_name(self, client: SocketMaxClient, msg: Message) -> str:
        if not msg.sender:
            return ""
        if client.me and msg.sender == client.me.id:
            return "я"
        user = await client.get_user(msg.sender)
        if user and user.names:
            n = user.names[0]
            return (n.name or f"{n.first_name or ''} {n.last_name or ''}").strip()
        return str(msg.sender)

    async def _get_dialog_title(
        self, client: SocketMaxClient, max_chat_id: int, sender_id: int | None = None
    ) -> str:
        def _name(user: Any) -> str | None:
            if user and user.names:
                n = user.names[0]
                return (n.name or f"{n.first_name or ''} {n.last_name or ''}").strip() or None
            return None

        # 1. Try dialog participant list (populated at sync time)
        dialog = next((d for d in (client.dialogs or []) if d.id == max_chat_id), None)
        if dialog:
            others = [uid for uid in dialog.participants.values() if uid != dialog.owner]
            if others:
                title = _name(await client.get_user(others[0]))
                if title:
                    return title

        # 2. Fallback: sender of the incoming message (new chat not yet in dialogs)
        if sender_id and client.me and sender_id != client.me.id:
            title = _name(await client.get_user(sender_id))
            if title:
                return title

        return str(max_chat_id)

    async def _download(self, url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                r.raise_for_status()
                return await r.read()

    # ── TG → Max ──────────────────────────────────────────────────────────────

    async def send_to_max(
        self,
        phone: str,
        max_chat_id: int,
        text: str,
        reply_to_max_id: int | None = None,
        photo_bytes: bytes | None = None,
        photo_name: str = "photo.jpg",
        video_bytes: bytes | None = None,
        video_name: str = "video.mp4",
        file_bytes: bytes | None = None,
        file_name: str = "file",
    ) -> int | None:
        """Send a message/media to Max. Returns max_message_id or None."""
        client = self._clients.get(phone)
        if not client:
            log.warning("No active client for phone %s", phone)
            return None

        attach = None
        if photo_bytes:
            # Photo/Video/File require url or path for file_name; raw overrides download
            attach = Photo(raw=photo_bytes, url=f"https://x/{photo_name}")
        elif video_bytes:
            # Video requires url/path for file_name; raw is used by read()
            attach = Video(raw=video_bytes, url=f"https://x/{video_name}")
        elif file_bytes:
            attach = File(raw=file_bytes, url=f"https://x/{file_name}")

        sent = await client.send_message(
            text=text or "",
            chat_id=max_chat_id,
            attachment=attach,
            reply_to=reply_to_max_id,
        )
        return sent.id if sent else None

    async def send_by_phone(
        self,
        phone: str,
        contact_phone: str,
        text: str,
    ) -> tuple[int, int] | None:
        """Find contact by phone in Max, send message. Returns (max_chat_id, max_message_id)."""
        client = self._clients.get(phone)
        if not client:
            log.warning("No active client for phone %s", phone)
            return None
        user = await client.search_by_phone(contact_phone)
        chat_id = client.get_chat_id(client.me.id, user.id)
        sent = await client.send_message(text=text, chat_id=chat_id)
        if not sent:
            return None
        return chat_id, sent.id

    def active_phones(self) -> list[str]:
        """Return phones of currently connected clients."""
        return list(self._clients.keys())
