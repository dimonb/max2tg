# Max ↔ Telegram Bridge — план реализации

## Обзор

Телеграм-бот, который зеркалирует переписку из Max messenger в топики TG-группы и обратно.
Работает в одном asyncio event loop рядом с Max клиентами.

---

## Изменения в config.yaml

```yaml
work_dir: .cache/max

telegram:
  token: "7123456789:AAF..."          # токен бота
  whitelist: [123456789, 987654321]   # Telegram user_id — только они могут управлять ботом

sessions:
  - phone: "+79269900327"
    name: Main
    token: "An_Sx6HQ9HDi..."
    telegram_id: 123456789            # TG user_id владельца этой Max-сессии
```

Поле `telegram_id` нужно чтобы понимать, к чьему аккаунту относится сессия
(несколько Max-сессий могут принадлежать одному TG-пользователю).

---

## Архитектура

```
bridge.py          — точка входа, склеивает всё
├── db.py          — async SQLite обёртка (aiosqlite)
├── max_bridge.py  — запускает SocketMaxClient-ы, обрабатывает входящие сообщения
└── tg_bridge.py   — aiogram бот, обрабатывает команды и входящие сообщения TG
```

Один процесс, один asyncio event loop:
- aiogram работает в режиме polling (`dp.start_polling`)
- каждый Max клиент — отдельный `asyncio.Task`
- они общаются через `asyncio.Queue` или напрямую вызывают методы друг друга

**Библиотеки:**
```
aiogram==3.26.0    # TG бот, async-native
aiosqlite          # async SQLite
aiohttp            # скачивать медиа (уже используется aiogram как транзитивная зависимость)
```

---

## База данных `.cache/tg.db`

```sql
-- Какая TG-группа привязана к какой Max-сессии
CREATE TABLE IF NOT EXISTS pins (
    tg_chat_id  INTEGER PRIMARY KEY,
    phone       TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Топик TG <-> чат Max
CREATE TABLE IF NOT EXISTS topics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_chat_id    INTEGER NOT NULL,
    tg_thread_id  INTEGER NOT NULL,   -- forum_topic.message_thread_id
    max_chat_id   INTEGER NOT NULL,
    phone         TEXT NOT NULL,
    title         TEXT,               -- название топика = имя контакта
    UNIQUE(tg_chat_id, tg_thread_id),
    UNIQUE(tg_chat_id, max_chat_id, phone)
);

-- Маппинг сообщений для реплаев
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
```

---

## Потоки данных

### 1. `/pin <phone>` в TG-группе

```
TG group message → бот
  ├── проверить user_id в whitelist
  ├── проверить phone в config.sessions
  ├── INSERT OR REPLACE INTO pins(tg_chat_id, phone)
  └── ответить "✅ Группа привязана к сессии {name} ({phone})"
```

### 2. Max → Telegram (входящее из Max)

```
on_message(msg, phone)
  ├── найти pin для phone → tg_chat_id
  ├── если нет пина — пропустить
  ├── найти topic для (tg_chat_id, msg.chat_id, phone)
  │     ├── если нет → create_forum_topic(tg_chat_id, title=dialog_name)
  │     │             INSERT INTO topics(...)
  │     │             tg_thread_id = новый thread_id
  │     └── иначе → tg_thread_id из БД
  ├── собрать текст: "[ИмяОтправителя] msg.text"
  │     (имя через client.get_user(msg.sender) или из кэша диалога)
  ├── обработать reply (msg.link):
  │     если msg.link → найти tg_message_id в messages(max_message_id=msg.link.message.id)
  │     reply_to = найденный tg_message_id (или None)
  ├── отправить медиа или текст в тред (см. §Медиа)
  │     → tg_message_id
  └── INSERT INTO messages(tg_chat_id, tg_thread_id, tg_message_id,
                           max_chat_id, max_message_id, phone)
```

### 3. Telegram → Max (входящее из TG)

```
message_handler(msg)
  ├── проверить user_id в whitelist
  ├── убедиться, что msg.message_thread_id задан (это топик)
  ├── найти (max_chat_id, phone) по (tg_chat_id, tg_thread_id) из topics
  ├── если нет — игнорировать
  ├── обработать reply (msg.reply_to_message):
  │     если есть → найти max_message_id в messages(tg_message_id=reply_to.message_id)
  │     reply_to_max = найденный max_message_id (или None)
  ├── отправить в Max:
  │     client.send_message(text, max_chat_id, reply_to=reply_to_max)
  │     + медиа (§Медиа TG→Max)
  │     → max_message_id
  └── INSERT INTO messages(...)
```

---

## Медиа

### Max → Telegram

| Тип              | pymax-поле           | Как достать URL                           | Метод TG       |
|------------------|----------------------|-------------------------------------------|----------------|
| PhotoAttach      | `base_url`           | напрямую (`{base_url}`)                   | `send_photo`   |
| VideoAttach      | `video_id`, `token`  | `await client.get_video_by_id(...)` → `.url` | `send_video`|
| FileAttach       | `file_id`, `token`   | `await client.get_file_by_id(...)` → `.url` | `send_document`|
| AudioAttach      | `url`                | напрямую                                  | `send_audio`   |
| StickerAttach    | `url`                | напрямую                                  | `send_sticker` |
| ContactAttach    | поля контакта        | форматировать текстом                     | текст          |

Загрузка: скачать через `aiohttp.ClientSession.get(url)` → передать `BufferedInputFile` в aiogram.

### Telegram → Max

Загрузить файл из TG (`await bot.download(file_id)`) → BytesIO → передать в `send_message`.

Pymax `send_message` принимает `attachment: Photo | File | Video`. Нужно разобраться с
upload API (возможно, pymax автоматически загружает при передаче объекта, или нужно вызвать
отдельный метод). **Требует изучения исходников pymax перед реализацией.**

---

## Детали реализации

### Определение имени диалога (title для топика)

```python
async def get_dialog_title(client, msg) -> str:
    dialog = next((d for d in client.dialogs if d.id == msg.chat_id), None)
    if dialog:
        others = [uid for uid in dialog.participants.values() if uid != dialog.owner]
        if others:
            user = await client.get_user(others[0])
            return str(user)  # User.__str__ = имя
    if msg.sender:
        user = await client.get_user(msg.sender)
        return str(user) if user else str(msg.chat_id)
    return str(msg.chat_id)
```

### Reply из Max в TG

`msg.link` — объект `MessageLink`:
- `msg.link.message.id` — ID сообщения в Max, на которое отвечают
- Ищем в `messages` запись с `max_message_id = msg.link.message.id` → берём `tg_message_id`

### Создание топика

```python
topic = await bot.create_forum_topic(chat_id=tg_chat_id, name=title[:128])
tg_thread_id = topic.message_thread_id
```

Требуется, чтобы в TG-группе включены «Темы» (Topics). Бот должен быть **администратором** с правом управлять темами.

### Проверка whitelist

```python
def is_allowed(user_id: int) -> bool:
    return user_id in cfg["telegram"]["whitelist"]
```

---

## Файловая структура после реализации

```
max2tg/
├── bridge.py          # asyncio.run(main()) — запуск всего
├── db.py              # DB class: init_db, get_pin, set_pin, get_topic, upsert_topic, save_message, ...
├── max_bridge.py      # MaxBridge: запускает клиентов, on_message → пишет в TG
├── tg_bridge.py       # TgBridge: aiogram dp, handlers (/pin, message)
├── pymax_patches.py   # (уже есть)
├── cli.py             # (уже есть, отдельный TUI)
├── config.yaml
└── .cache/
    ├── tg.db
    └── max/
        └── 79269900327/
            └── session.db
```

---

## Порядок реализации

1. **`db.py`** — схема + все CRUD-методы
2. **Команда `/pin`** в `tg_bridge.py` + проверка whitelist
3. **Max → TG**: текстовые сообщения (без медиа), создание топиков
4. **TG → Max**: текстовые сообщения
5. **Реплаи** в обе стороны
6. **Медиа Max → TG** (фото, файлы, видео, аудио)
7. **Медиа TG → Max** (исследовать pymax upload API)
8. **Запуск нескольких Max-сессий** одновременно

---

## Открытые вопросы

- Как pymax принимает медиа для отправки (нужно изучить `AttachPhotoPayload` и upload flow)?
- Нужно ли создавать топик при `/pin` или только при первом сообщении?
- Что делать если у Max-чата нет имени (групповой чат без названия)?
- Обработка ошибок при недоступности TG/Max (retry, DLQ)?
