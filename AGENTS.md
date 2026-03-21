# AGENTS.md — context for LLM assistants

This file describes the architecture, key decisions, and gotchas for the `max2tg` project.
Read this before making changes.

---

## Working rules

- **Do NOT commit or push unless the user explicitly says so** (e.g. "commit", "push", "commit and push").

---

## What this project does

Bridges **Max messenger** (Russian messenger, formerly ICQ / Mail.ru Agent) with **Telegram**.
- Every Max chat becomes a **Telegram forum topic** in a pinned group.
- Messages flow in both directions with reply and media support.
- Multiple Max sessions can run simultaneously.
- A Textual TUI (`max2tg-cli`) is available for direct interaction with Max.

---

## Repository layout

```
max2tg/
├── src/max2tg/           # installable package (src-layout)
│   ├── bridge.py         # asyncio entry point — wires everything together
│   ├── db.py             # aiosqlite layer: pins / topics / messages tables
│   ├── max_bridge.py     # MaxBridge: manages SocketMaxClient instances
│   ├── tg_bridge.py      # aiogram router: /pin /unpin /send /start handlers
│   ├── pymax_patches.py  # MUST be imported first — patches pymax SSL/lock bugs
│   ├── cli.py            # Textual TUI (separate tool, not part of the bridge)
│   ├── client.py         # minimal console client (dev/debug use)
│   └── register.py       # one-shot phone registration helper
├── tests/
│   ├── conftest.py       # `db` fixture: patches DB_PATH to a tmp file
│   ├── test_db.py
│   ├── test_max_bridge.py
│   └── test_tg_bridge.py
├── config.yaml           # runtime config (gitignore the token values)
├── plan.md               # original design plan
└── pyproject.toml        # src-layout, Python ≥ 3.14, pytest asyncio_mode=auto
```

---

## Config shape (`config.yaml`)

```yaml
work_dir: .cache          # Max session DBs live at {work_dir}/{phone_digits}/session.db
debug: false              # true → DEBUG level for max2tg.* loggers

telegram:
  bot_token: "..."
  whitelist: [35243507]   # Telegram user_ids allowed to interact with the bot

sessions:
  - phone: "+79269900327"
    name: "Dmitrii"
    telegram_id: 35243507  # which TG user owns this Max account
    token: "..."           # Max auth token (saved after first login)
```

---

## Database (`.cache/tg.db`)

Three tables, all accessed via async helpers in `db.py`:

| Table | Key | Purpose |
|-------|-----|---------|
| `pins` | `tg_chat_id` | TG group → Max session phone |
| `topics` | `(tg_chat_id, tg_thread_id)` | TG topic ↔ Max chat |
| `messages` | `(tg_chat_id, tg_message_id)` | TG msg ↔ Max msg (for replies) |

All DB functions open+close a connection per call (no persistent connection).
`DB_PATH` is a module-level string — tests monkeypatch it via the `db` fixture.

---

## pymax_patches

`pymax_patches.py` exists as an extension point for future patches. Currently `apply()`
is a no-op — all previously needed fixes have been committed directly to the
[dimonb/PyMax](https://github.com/dimonb/PyMax) fork:

| Commit | Fix |
|--------|-----|
| `07b5d94` | `ReplyLink.message_id: str → int`; remove `str()` in `send_message` |
| `f63fcbc` | SSL context: TLS 1.2 only (min+max), remove duplicate `set_ciphers("DEFAULT")` and `session_stats()` |
| `40bdc5a` | `_sync`: parse `self.me` from `profile` directly when server omits `profile.contact` |
| `a55ee49` | `_setup_logger`: set `propagate = False` to prevent double output when host app uses `basicConfig` |
| `ead3ddd` | `_setup_logger`: skip own handler entirely when root logger already has handlers (uniform format) |
| `794b4a4` | Persist sync cursor (`time` field from LOGIN response) to `Auth.chat_marker`; pass as `chats_sync` on reconnect so server delivers missed messages |

The pymax library (`mSsaWin/PyMax`) originally had a concurrent send bug
(`_sock_lock` absent) and an orphan recv-loop bug — these were already fixed in
the fork's earlier commits (`43e9ca0`, `b31f3d4`) before this project started.

---

## MaxBridge (`max_bridge.py`)

- `start()` creates one `asyncio.Task` per session, each running `client.start()` which
  blocks until disconnected (pymax handles reconnect internally).
- `on_message()` callback posts to `_forward_to_tg()` which:
  1. Looks up pinned TG groups for the session phone.
  2. Creates a forum topic if this Max chat has none yet (`_ensure_topic`).
  3. Sends text/media to TG and saves the message mapping.
- `send_to_max(phone, max_chat_id, text, ...)` — used by the TG→Max path.
- `send_by_phone(phone, contact_phone, text)` — does `search_by_phone` + `get_chat_id`
  + `send_message`; used by the `/send` bot command.
- Media from Max: photos have `base_url` directly; videos/files need
  `get_video_by_id` / `get_file_by_id` to get a download URL.
- Media to Max: use `Photo(raw=bytes, url="https://x/{name}")`, same trick for
  `Video` and `File` — the fake URL sets `file_name`; `raw` is returned by `read()`
  without actually hitting the URL.

---

## TgBridge (`tg_bridge.py`)

aiogram 3 router with four handlers:

| Handler | Trigger | Whitelist check |
|---------|---------|-----------------|
| `cmd_start` | `/start` | yes |
| `cmd_pin` | `/pin <phone>` | yes |
| `cmd_unpin` | `/unpin` | yes |
| `cmd_send` | `/send [session] <phone> <text>` | yes |
| `handle_topic_message` | any message with `message_thread_id` | yes |

Session resolution in `/send` (priority order):
1. Explicit session phone as first arg (must be a known session key).
2. Phone pinned to the current TG group via `db.get_pin(chat_id)`.
3. Only one active session → use it.
4. Multiple sessions, no pin → ask user to specify.

`build_dispatcher()` injects `max_bridge`, `sessions` (dict), and `whitelist` (set)
into the dispatcher's data dict — aiogram passes these as kwargs to handlers.

---

## Testing

```bash
source .venv/bin/activate
pytest                   # runs all 53 tests
pytest tests/test_db.py  # just DB tests
```

- `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio` decorators (they're
  there for clarity but aren't required).
- The `db` fixture monkeypatches `db.DB_PATH` to a temp file — tests never touch
  `.cache/tg.db`.
- Network calls (Max API, Telegram API) are mocked with `AsyncMock`.
- **Gotcha**: `MagicMock(name="Alice")` sets the mock's internal repr name, NOT a
  `.name` attribute. Use `m = MagicMock(); m.name = "Alice"` instead.

---

## Key pymax API facts

```python
# Message fields used:
msg.id          # int
msg.chat_id     # int
msg.sender      # int | None (user_id)
msg.text        # str
msg.attaches    # list[PhotoAttach | VideoAttach | FileAttach | AudioAttach | StickerAttach | ContactAttach] | None
msg.link        # MessageLink | None  ← this is the reply reference (NOT reply_to)
msg.link.message.id  # int — Max message_id of the message being replied to

# User fields:
user.id
user.names      # list[Names]; Names has .name, .first_name, .last_name

# Dialog fields:
dialog.id           # == max_chat_id
dialog.owner        # int (user_id)
dialog.participants # dict[str, int]  ← values are user_ids

# Useful client methods:
await client.search_by_phone(phone)              # → User
client.get_chat_id(my_id, other_id)              # → int (local computation)
await client.get_video_by_id(chat_id, msg_id, video_id)  # → VideoRequest with .url
await client.get_file_by_id(chat_id, msg_id, file_id)    # → FileRequest with .url
await client.send_message(text, chat_id, attachment=None, reply_to=None)
await client.add_contact(user_id)
```

---

## Running

```bash
# bridge (Max ↔ Telegram)
python -m max2tg

# TUI for direct Max interaction
python -m max2tg.cli   # or: max2tg-cli
```

The bridge requires a TG group with **Topics enabled** and the bot as **admin with
Manage Topics permission**. Then `/pin +79001234567` in the group to activate.
