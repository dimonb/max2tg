"""Max Messenger CLI — multi-session Textual TUI."""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import pymax_patches

pymax_patches.apply()

from pymax import Message, SocketMaxClient
from pymax.payloads import UserAgentPayload
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message as TMsg
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

# ── config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path("config.yaml")


@dataclass
class SessionCfg:
    phone: str
    name: str
    token: str | None = None

    @property
    def work_dir(self) -> Path:
        digits = self.phone.lstrip("+")
        return Path(_cfg["work_dir"]) / digits

    @property
    def label(self) -> str:
        return self.name or self.phone


@dataclass
class _Config:
    work_dir: str
    sessions: list[SessionCfg]


def _load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            "work_dir: .cache/max   # base dir; each session in {work_dir}/{phone}/\n\n"
            "sessions: []\n"
        )
    return yaml.safe_load(CONFIG_FILE.read_text()) or {}


def _save_config() -> None:
    raw = {"work_dir": _cfg["work_dir"], "sessions": []}
    for s in _sessions.values():
        entry: dict[str, Any] = {"phone": s.phone, "name": s.name}
        if s.token:
            entry["token"] = s.token
        raw["sessions"].append(entry)
    CONFIG_FILE.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False)
    )


def _parse_sessions(raw: dict) -> dict[str, SessionCfg]:
    out: dict[str, SessionCfg] = {}
    for entry in raw.get("sessions", []):
        phone = entry["phone"]
        out[phone] = SessionCfg(
            phone=phone,
            name=entry.get("name") or phone,
            token=entry.get("token"),
        )
    return out


_cfg = _load_config()
_sessions: dict[str, SessionCfg] = _parse_sessions(_cfg)  # phone → SessionCfg

# ── pymax client factory ──────────────────────────────────────────────────────


def make_client(sc: SessionCfg) -> SocketMaxClient:
    sc.work_dir.mkdir(parents=True, exist_ok=True)
    client = SocketMaxClient(
        phone=sc.phone,
        token=sc.token,
        work_dir=str(sc.work_dir),
        headers=UserAgentPayload(device_type="DESKTOP"),
    )
    return client


# ── runtime session state ─────────────────────────────────────────────────────


@dataclass
class SessionState:
    cfg: SessionCfg
    client: SocketMaxClient | None = None
    status: str = "idle"       # idle | connecting | ok | error
    error: str = ""

    @property
    def status_icon(self) -> str:
        return {"ok": "●", "connecting": "◌", "error": "✗", "idle": "○"}.get(self.status, "○")

    @property
    def status_color(self) -> str:
        return {"ok": "green", "connecting": "yellow", "error": "red", "idle": "dim"}.get(
            self.status, "dim"
        )


# ── Textual messages ──────────────────────────────────────────────────────────


class EvMsg(TMsg):
    def __init__(self, phone: str, msg: Message) -> None:
        super().__init__()
        self.phone = phone
        self.msg = msg


class EvStatus(TMsg):
    def __init__(self, phone: str, status: str, error: str = "") -> None:
        super().__init__()
        self.phone = phone
        self.status = status
        self.error = error


class EvSynced(TMsg):
    def __init__(self, phone: str) -> None:
        super().__init__()
        self.phone = phone


# ── Login modal ───────────────────────────────────────────────────────────────

_LOGIN_CSS = """
LoginScreen {
    align: center middle;
    background: $background 70%;
}
#panel {
    width: 62;
    height: auto;
    background: $surface;
    border: round $primary;
    padding: 1 2;
}
#title {
    text-align: center;
    color: $primary;
    text-style: bold;
    margin-bottom: 1;
}
.lbl { color: $text-muted; margin-top: 1; }
#actions { layout: horizontal; height: 3; align: right middle; margin-top: 1; }
Button { margin-left: 1; }
#status { height: 1; margin-top: 1; }
"""


class LoginScreen(ModalScreen):
    CSS = _LOGIN_CSS
    BINDINGS = [Binding("escape", "dismiss", "Отмена")]

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Label("🔐  Добавить сессию", id="title")
            yield Label("Телефон:", classes="lbl")
            yield Input(placeholder="+79001234567", id="phone")
            yield Label("Имя (необязательно):", classes="lbl")
            yield Input(placeholder="Main", id="name")
            yield Label("SMS-код:", classes="lbl", id="code-lbl")
            yield Input(placeholder="123456", id="code")
            with Horizontal(id="actions"):
                yield Button("Отмена", id="cancel")
                yield Button("Получить код", id="submit", variant="primary")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self._temp_token: str | None = None
        self._client: SocketMaxClient | None = None
        self._stage = "phone"
        self.query_one("#code-lbl").display = False
        self.query_one("#code").display = False
        self.query_one("#phone").focus()

    def _status(self, text: str, error: bool = False) -> None:
        color = "red" if error else "green"
        self.query_one("#status", Label).update(f"[{color}]{text}[/]")

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#submit")
    async def submit(self) -> None:
        btn = self.query_one("#submit", Button)
        btn.disabled = True
        try:
            await (self._request_code() if self._stage == "phone" else self._verify_code())
        finally:
            btn.disabled = False

    @on(Input.Submitted, "#phone")
    async def phone_enter(self) -> None:
        if self._stage == "phone":
            await self.submit()

    @on(Input.Submitted, "#code")
    async def code_enter(self) -> None:
        if self._stage == "code":
            await self.submit()

    async def _request_code(self) -> None:
        phone = self.query_one("#phone", Input).value.strip()
        if not phone:
            self._status("Введите номер телефона", error=True)
            return
        self._status("Отправляем код…")
        try:
            name = self.query_one("#name", Input).value.strip() or phone
            sc = SessionCfg(phone=phone, name=name)
            self._client = make_client(sc)
            await self._client.connect(self._client.user_agent)
            self._temp_token = await self._client.request_code(phone)
            self._stage = "code"
            self.query_one("#phone", Input).disabled = True
            self.query_one("#name", Input).disabled = True
            self.query_one("#code-lbl").display = True
            self.query_one("#code").display = True
            self.query_one("#submit", Button).label = "Войти"
            self.query_one("#code", Input).focus()
            self._status(f"Код отправлен на {phone}")
        except Exception as e:
            self._status(str(e), error=True)

    async def _verify_code(self) -> None:
        code = self.query_one("#code", Input).value.strip()
        if not (len(code) == 6 and code.isdigit()):
            self._status("Нужно 6 цифр", error=True)
            return
        self._status("Входим…")
        try:
            await self._client.login_with_code(self._temp_token, code)
            await self._client.close()
            phone = self.query_one("#phone", Input).value.strip()
            name = self.query_one("#name", Input).value.strip() or phone
            self.dismiss(SessionCfg(phone=phone, name=name))
        except Exception as e:
            self._status(str(e), error=True)


# ── Main App ──────────────────────────────────────────────────────────────────


class MaxApp(App):
    CSS = """
    #layout { layout: horizontal; height: 1fr; }

    /* sidebar */
    #sidebar {
        width: 28;
        border-right: solid $primary-darken-2;
        background: $surface-darken-1;
    }
    #sidebar-title {
        background: $primary-darken-3;
        padding: 0 1;
        height: 1;
        text-style: bold;
        color: $text;
    }
    #sidebar-scroll { height: 1fr; }

    .sess-header {
        padding: 0 1;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    .sess-header:hover { background: $primary; }

    .dialog-row {
        padding: 0 2;
        height: 1;
        color: $text-muted;
    }
    .dialog-row:hover { color: $text; background: $primary-darken-3; }

    /* main area */
    #main { width: 1fr; height: 1fr; }
    #messages { height: 1fr; padding: 0 1; }

    #statusbar {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    #input-row {
        height: 3;
        border-top: solid $primary-darken-2;
        padding: 0 1;
        layout: horizontal;
        align: left middle;
    }
    #prompt { width: 2; color: $primary; padding-top: 1; }
    #cmd { width: 1fr; border: none; background: transparent; padding: 0; }
    """

    TITLE = "Max Messenger"
    BINDINGS = [
        Binding("ctrl+c", "quit", "Выход"),
        Binding("f1", "help", "Помощь"),
        Binding("f2", "add_session", "Добавить"),
        Binding("f5", "refresh", "Обновить"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Label(" 👥 Сессии", id="sidebar-title")
                yield ScrollableContainer(id="sidebar-scroll")
            with Vertical(id="main"):
                yield RichLog(id="messages", markup=True, wrap=True)
                yield Label("○  Инициализация…", id="statusbar")
                with Horizontal(id="input-row"):
                    yield Label(">", id="prompt")
                    yield Input(placeholder="  /help — справка", id="cmd")
        yield Footer()

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._log = self.query_one("#messages", RichLog)

        self._log.write(
            "[bold cyan]Max Messenger[/]  multi-session TUI\n"
            f"  config: [dim]{CONFIG_FILE}[/]\n"
            "  [dim]F1[/] помощь  [dim]F2[/] добавить сессию  [dim]F5[/] обновить\n"
        )

        if not _sessions:
            self._log.write("[yellow]Нет сессий в config.yaml → [bold]F2[/] или [bold]/add[/][/]")
            self.query_one("#statusbar", Label).update("○  Нет сессий")
            return

        for sc in _sessions.values():
            self._start_session(sc)

    # ── session management ──────────────────────────────────────────────────────

    def _start_session(self, sc: SessionCfg) -> None:
        state = SessionState(cfg=sc, client=make_client(sc), status="connecting")
        _sessions[sc.phone] = sc
        self._states[sc.phone] = state
        app = self

        @state.client.on_message()
        async def _on_msg(message: Message) -> None:
            app.post_message(EvMsg(sc.phone, message))

        @state.client.on_start
        def _on_start() -> None:
            app.post_message(EvSynced(sc.phone))

        self._run_session(sc.phone)
        self._rebuild_sidebar()

    @work(name="session")
    async def _run_session(self, phone: str) -> None:
        state = self._states[phone]
        self.post_message(EvStatus(phone, "connecting"))
        try:
            await state.client.start()
        except Exception as e:
            self.post_message(EvStatus(phone, "error", str(e)))

    # ── Textual event handlers ──────────────────────────────────────────────────

    def on_ev_msg(self, event: EvMsg) -> None:
        msg = event.msg
        sc = _sessions.get(event.phone)
        label = sc.label if sc else event.phone
        ts = datetime.datetime.fromtimestamp(msg.time / 1000).strftime("%H:%M:%S")
        sender = f"[green]{msg.sender}[/]" if msg.sender else "[dim]?[/]"
        text = msg.text or ""
        extra = ""
        if msg.attaches:
            kinds = [type(a).__name__.replace("Attach", "").lower() for a in msg.attaches]
            extra = f" [dim][{', '.join(kinds)}][/]"
        self._log.write(
            f"[dim]{ts}[/] [magenta]{label}[/] [cyan]{msg.chat_id}[/] "
            f"{sender}[dim]:[/] {text}{extra}"
        )

    def on_ev_status(self, event: EvStatus) -> None:
        state = self._states.get(event.phone)
        if not state:
            return
        state.status = event.status
        state.error = event.error
        self._rebuild_sidebar()
        active = sum(1 for s in self._states.values() if s.status == "ok")
        total = len(self._states)
        self.query_one("#statusbar", Label).update(
            f"[green]●[/] {active}/{total} сессий подключено"
            if active else f"[yellow]○[/] Подключение…"
        )

    def on_ev_synced(self, event: EvSynced) -> None:
        state = self._states.get(event.phone)
        if not state:
            return
        state.status = "ok"
        client = state.client
        me = str(client.me) if client and client.me else "???"
        ndlg = len(client.dialogs) if client else 0
        self._log.write(
            f"[green]✓[/] [magenta]{state.cfg.label}[/]  подключено как [bold]{me}[/]"
            f"  диалогов: [cyan]{ndlg}[/]"
        )
        self.on_ev_status(EvStatus(event.phone, "ok"))

    # ── sidebar ─────────────────────────────────────────────────────────────────

    def _rebuild_sidebar(self) -> None:
        scroll = self.query_one("#sidebar-scroll", ScrollableContainer)
        scroll.remove_children()
        for phone, state in self._states.items():
            ic = state.status_icon
            col = state.status_color
            label = state.cfg.label
            scroll.mount(
                Label(
                    f" [{col}]{ic}[/] [bold]{label}[/] [dim]{phone}[/]",
                    classes="sess-header",
                    markup=True,
                )
            )
            client = state.client
            if client and client.dialogs:
                for d in client.dialogs[:8]:
                    others = [v for k, v in d.participants.items() if v != d.owner]
                    partner = others[0] if others else "?"
                    preview = ""
                    if d.last_message and d.last_message.text:
                        preview = d.last_message.text[:18]
                    scroll.mount(
                        Label(
                            f"  💬 [green]{partner}[/] [dim]{preview}[/]",
                            classes="dialog-row",
                            markup=True,
                        )
                    )

    # ── input ───────────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#cmd")
    async def handle_cmd(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if text:
            await self._dispatch(text)

    async def _dispatch(self, raw: str) -> None:
        if not raw.startswith("/"):
            self._log.write("[dim]Отправка сообщений не реализована. /help[/]")
            return
        parts = raw.split(maxsplit=3)
        cmd = parts[0].lower()
        args = parts[1:]
        match cmd:
            case "/help" | "/?":         self.action_help()
            case "/add":                 await self.action_add_session()
            case "/rm":                  self._cmd_rm(args)
            case "/sessions" | "/ls":    self._cmd_sessions()
            case "/chats":               self._cmd_chats(args)
            case "/me":                  self._cmd_me(args)
            case "/config":              self._cmd_config()
            case "/clear":               self._log.clear()
            case "/quit" | "/q":         self.exit()
            case "/send" | "/s":         await self._cmd_send(args)
            case "/contact" | "/ac":     await self._cmd_contact(args)
            case _:
                self._log.write(f"[red]Неизвестная команда[/] {cmd}  —  /help")

    # ── commands ─────────────────────────────────────────────────────────────────

    def action_help(self) -> None:
        self._log.write(
            "\n[bold cyan]Команды:[/]\n"
            "  [bold]/add[/]                          добавить / залогинить новую сессию\n"
            "  [bold]/rm [/][dim]<phone>[/]                 удалить сессию\n"
            "  [bold]/sessions[/]  [dim]/ls[/]             список сессий\n"
            "  [bold]/chats[/]  [dim][session][/]           диалоги (всех или одной сессии)\n"
            "  [bold]/me[/]     [dim][session][/]           профиль аккаунта\n"
            "  [bold]/send[/]   [dim][session] <target> <text>[/]\n"
            "                            отправить сообщение; target — номер или chat_id\n"
            "  [bold]/contact[/] [dim][session] <phone>[/]  найти и добавить контакт по номеру\n"
            "  [bold]/config[/]                       показать конфиг\n"
            "  [bold]/clear[/]                        очистить лог\n"
            "  [bold]/quit[/]  [dim]/q[/]                   выход\n"
            "\n[bold cyan]Горячие клавиши:[/]\n"
            "  [bold]F1[/] помощь  [bold]F2[/] добавить  [bold]F5[/] обновить сайдбар\n"
        )

    async def action_add_session(self) -> None:
        result: SessionCfg | None = await self.push_screen_wait(LoginScreen())
        if result is None:
            return
        if result.phone in _sessions:
            self._log.write(f"[yellow]Сессия {result.phone} уже существует[/]")
            return
        _sessions[result.phone] = result
        _save_config()
        self._log.write(f"[green]Сессия [bold]{result.label}[/] добавлена. Подключаемся…[/]")
        self._start_session(result)

    def _cmd_rm(self, args: list[str]) -> None:
        if not args:
            self._log.write("[red]Использование:[/] /rm <phone>")
            return
        phone = args[0]
        state = self._states.pop(phone, None)
        if state and state.client:
            asyncio.create_task(state.client.close())
        _sessions.pop(phone, None)
        _save_config()
        self._rebuild_sidebar()
        self._log.write(f"[yellow]Сессия {phone} удалена[/]")

    def _cmd_sessions(self) -> None:
        if not self._states:
            self._log.write("[yellow]Нет активных сессий[/]")
            return
        self._log.write(f"\n[bold]Сессии ({len(self._states)}):[/]")
        for phone, state in self._states.items():
            ic, col = state.status_icon, state.status_color
            me = ""
            if state.client and state.client.me:
                me = f"  [dim]{state.client.me}[/]"
            ndlg = len(state.client.dialogs) if state.client else 0
            self._log.write(
                f"  [{col}]{ic}[/] [bold]{state.cfg.label}[/]  {phone}{me}"
                f"  [cyan]{ndlg}[/] диал."
            )

    def _cmd_chats(self, args: list[str]) -> None:
        targets = (
            [self._states[args[0]]] if args and args[0] in self._states
            else list(self._states.values())
        )
        for state in targets:
            client = state.client
            if not client or not client.dialogs:
                continue
            self._log.write(f"\n[bold magenta]{state.cfg.label}[/] — диалоги:")
            for d in client.dialogs:
                others = [v for k, v in d.participants.items() if v != d.owner]
                partner = others[0] if others else d.owner
                last = f"  [dim]{d.last_message.text[:50]}[/]" if d.last_message and d.last_message.text else ""
                self._log.write(f"  id=[cyan]{d.id}[/]  partner=[green]{partner}[/]{last}")

    def _cmd_me(self, args: list[str]) -> None:
        phones = [args[0]] if args and args[0] in self._states else list(self._states.keys())
        for phone in phones:
            state = self._states[phone]
            me = state.client.me if state.client else None
            line = f"[bold magenta]{state.cfg.label}[/]: {me}" if me else f"[yellow]{state.cfg.label}: нет профиля[/]"
            self._log.write(line)

    def _cmd_config(self) -> None:
        self._log.write(f"\n[bold]config.yaml[/] ({CONFIG_FILE.absolute()}):")
        self._log.write(f"  [cyan]work_dir[/]: {_cfg['work_dir']}")
        self._log.write(f"  [cyan]sessions[/]: {len(_sessions)}")
        for sc in _sessions.values():
            self._log.write(f"    [green]{sc.label}[/]  {sc.phone}  dir={sc.work_dir}")

    def _resolve_session(self, args: list[str]) -> tuple[SessionState | None, list[str]]:
        """Pick a session from args if specified, otherwise use the only active one."""
        active = {p: s for p, s in self._states.items() if s.status == "ok"}
        if not active:
            self._log.write("[red]Нет подключённых сессий[/]")
            return None, args
        # first arg is a session selector only if it's an exact match for a known session
        if args and args[0] in self._states:
            phone = args[0]
            state = active.get(phone)
            if not state:
                self._log.write(f"[red]Сессия {phone} не подключена[/]")
                return None, args[1:]
            return state, args[1:]
        if len(active) == 1:
            return next(iter(active.values())), args
        self._log.write(
            "[yellow]Несколько сессий активно. Укажи номер сессии первым аргументом:[/]\n"
            + "  " + "  ".join(active.keys())
        )
        return None, args

    async def _cmd_send(self, args: list[str]) -> None:
        """/send [session] <phone_or_chat_id> <text>"""
        state, args = self._resolve_session(args)
        if state is None:
            return
        if len(args) < 2:
            self._log.write("[red]Использование:[/] /send [session] <phone_или_chat_id> <текст>")
            return
        target, text = args[0], args[1]
        client = state.client
        try:
            if target.lstrip("+").isdigit() and target.startswith("+"):
                # phone number → search user
                self._log.write(f"[dim]Поиск пользователя {target}…[/]")
                user = await client.search_by_phone(target)
                chat_id = client.get_chat_id(client.me.id, user.id)
                self._log.write(
                    f"[dim]Найден: [green]{user}[/]  chat_id=[cyan]{chat_id}[/][/]"
                )
            else:
                # treat as chat_id
                chat_id = int(target)
            msg = await client.send_message(text, chat_id)
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self._log.write(
                f"[dim]{ts}[/] [magenta]{state.cfg.label}[/] → [cyan]{chat_id}[/]  "
                f"[bold green]Отправлено[/]: {text}"
                + (f"  [dim]id={msg.id}[/]" if msg else "")
            )
        except Exception as e:
            self._log.write(f"[red]Ошибка отправки:[/] {e}")

    async def _cmd_contact(self, args: list[str]) -> None:
        """/contact [session] <phone>  — найти пользователя по номеру и добавить в контакты"""
        state, args = self._resolve_session(args)
        if state is None:
            return
        if not args:
            self._log.write("[red]Использование:[/] /contact [session] <phone>")
            return
        phone = args[0]
        client = state.client
        try:
            self._log.write(f"[dim]Поиск {phone}…[/]")
            user = await client.search_by_phone(phone)
            self._log.write(f"[dim]Найден: [green]{user}[/]  id=[cyan]{user.id}[/][/]")
            contact = await client.add_contact(user.id)
            self._log.write(f"[green]✓ Контакт добавлен:[/] {contact}")
        except Exception as e:
            self._log.write(f"[red]Ошибка:[/] {e}")

    def action_refresh(self) -> None:
        self._rebuild_sidebar()


if __name__ == "__main__":
    MaxApp().run()
