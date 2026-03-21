# max2tg

Bridges **Max messenger** with **Telegram** — messages flow both ways through Telegram forum topics.

Each Max chat becomes a topic in a Telegram group. Reply, send photos/videos/files from either side and everything stays in sync.

---

## How it works

```
Max chat "Alice"  ←→  Topic "Alice" in TG group
Max chat "Bob"    ←→  Topic "Bob"   in TG group
```

- New message in Max → forwarded to the matching topic (with sender name in brackets).
- Message in a topic → forwarded to the matching Max chat.
- Replies, photos, videos, files, audio and stickers are supported in both directions.
- Multiple Max accounts can run simultaneously, each pinned to its own group (or the same one).

---

## Requirements

- Python 3.14+
- A Telegram bot token ([create one via @BotFather](https://t.me/BotFather))
- A Telegram group with **Topics enabled** (Group Settings → Topics)
- The bot added to the group as **administrator** with **Manage Topics** permission
- One or more Max messenger accounts (tokens obtained via the registration flow)

---

## Installation

```bash
git clone https://github.com/dimonb/max2tg
cd max2tg
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Configuration

Copy and edit `config.yaml`:

```yaml
work_dir: .cache   # session DBs live at {work_dir}/{phone_digits}/session.db
debug: false       # set true for verbose logging

telegram:
  bot_token: "7123456789:AAF..."
  whitelist:
    - 35243507     # your Telegram user_id — only these can use the bot

sessions:
  - phone: "+79001234567"
    name: "Main"
    telegram_id: 35243507   # which TG user owns this Max account
    token: "An_Sx6..."      # obtained during login (see below)
```

### Adding a Max account

Use the interactive TUI to log in and get a token:

```bash
max2tg-cli          # or: python -m max2tg.cli
# Press F2 or type /add, enter phone, get SMS, enter code
```

The token is saved to `config.yaml` automatically.

---

## Running the bridge

```bash
python -m max2tg
```

Or if installed as a script:

```bash
max2tg
```

### First-time setup

1. Start the bridge.
2. Open the Telegram group where you want Max chats to appear.
3. Make sure Topics are enabled and the bot is admin with Manage Topics.
4. Send `/pin +79001234567` in the group.
5. Write something in Max — a topic will appear automatically.

---

## Bot commands

| Command | Description |
|---------|-------------|
| `/start` | Show help |
| `/pin <phone>` | Link this group to a Max session |
| `/unpin` | Remove the link |
| `/send <phone> <text>` | Find a Max user by phone and send them a message |

**`/send` examples:**

```
/send +79001234567 Hey, what's up?
```

With multiple sessions, specify which one:

```
/send +79269900327 +79001234567 Hey!
```

In a group pinned to a session the session is picked automatically.

After `/send`, a topic is created for the new chat — future messages will bridge automatically.

---

## TUI (optional)

An interactive terminal UI for managing sessions and browsing chats directly:

```bash
max2tg-cli
```

Key bindings: `F1` help · `F2` add session · `F5` refresh sidebar · `Ctrl+C` quit

TUI commands: `/sessions` · `/chats` · `/me` · `/send <phone> <text>` · `/contact <phone>` · `/rm <phone>`

---

## Project layout

```
src/max2tg/
├── bridge.py         # entry point
├── db.py             # SQLite: pins / topics / message mappings
├── max_bridge.py     # Max client manager, Max↔TG forwarding
├── tg_bridge.py      # Telegram bot handlers
├── pymax_patches.py  # SSL and concurrency fixes for the pymax library
└── cli.py            # Textual TUI

tests/                # 53 tests, pytest-asyncio
config.yaml
```

---

## Development

```bash
source .venv/bin/activate
pytest              # run all tests
pytest -v           # verbose
```

Tests use an in-memory SQLite DB (tmp file per test) and mock all network calls. No real Max or Telegram connection needed.

---

## Logging

Set `debug: true` in `config.yaml` to enable `DEBUG`-level logs for `max2tg.*`.
Third-party libraries (aiogram, aiohttp) stay at `INFO` to avoid noise.
