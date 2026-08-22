"""FlowKit Launcher — một cửa sổ chạy hết các server của FlowKit.

Chạy trực tiếp:  python flowkit_launcher.py
Build ra .exe :  .\\build.ps1

Cấu hình nằm ở config.json cạnh file này (hoặc cạnh .exe khi đã build).
"""
from __future__ import annotations

import codecs
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "FlowKit Launcher"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
MAX_LOG_LINES = 5000


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = app_dir() / "config.json"

DEFAULT_CONFIG = {
    "servers": [
        {
            "id": "backend",
            "name": "Backend (agent)",
            "cwd": "D:\\youtube\\editor\\flowkit",
            "command": "python -m agent.main",
            "url": "http://127.0.0.1:8100/health",
            "autostart": True,
            "own_console": False,
        },
        {
            "id": "webui",
            "name": "Web UI (vite)",
            "cwd": "D:\\youtube\\editor\\flowkit\\webapp",
            "command": "npm run dev",
            "url": "http://127.0.0.1:5173",
            "autostart": True,
            "own_console": False,
        },
        {
            "id": "tts",
            "name": "TTS bridge (OmniVoice)",
            "cwd": "D:\\projects\\Python\\tts\\omni-colab\\omnivoice-rpc",
            "command": "powershell -NoProfile -ExecutionPolicy Bypass -File .\\run_bridge.ps1 {socket}",
            "socket_field": True,
            "socket": "",
            "url": "http://127.0.0.1:8000",
            "autostart": False,
            "own_console": False,
        },
    ],
    "start_all_delay_ms": 800,
    "autostart_on_launch": False,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and cfg.get("servers"):
                return cfg
        except Exception as exc:  # cấu hình hỏng thì lùi về mặc định, đừng chết app
            print("config.json loi (%s) - dung cau hinh mac dinh" % exc)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- ansi

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
# Mọi escape KHÔNG phải màu (di chuyển con trỏ, xoá màn hình, đặt tiêu đề…) — bỏ đi.
# [a-ln-zA-Z] cố tình chừa 'm' lại cho SGR_RE xử lý trước đó.
NOISE_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC (đặt tiêu đề cửa sổ)
    r"|\x1b\[[0-9;?]*[a-ln-zA-Z]"          # CSI khác SGR
    r"|\x1b[=>NOc]"                        # escape 1 ký tự
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f]"       # ký tự điều khiển (chừa \t \n \r)
)

_BASIC = [
    "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#cfd6e4",
    "#7f8899", "#ff7b86", "#b5e890", "#ffd68a", "#82c6ff", "#e0a3f5", "#6fdbe8", "#ffffff",
]


def _xterm_hex(n: int) -> str:
    if n < 16:
        return _BASIC[n]
    if n < 232:
        n -= 16
        lv = (0, 95, 135, 175, 215, 255)
        return "#%02x%02x%02x" % (lv[n // 36], lv[(n // 6) % 6], lv[n % 6])
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


class AnsiState:
    """Giữ màu/đậm đang có hiệu lực giữa các chunk stdout."""

    def __init__(self) -> None:
        self.fg: str | None = None
        self.bold = False

    def apply(self, params: str) -> None:
        codes = [int(p) for p in params.split(";") if p.isdigit()] or [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self.fg, self.bold = None, False
            elif c == 1:
                self.bold = True
            elif c in (21, 22):
                self.bold = False
            elif 30 <= c <= 37:
                self.fg = _BASIC[c - 30]
            elif 90 <= c <= 97:
                self.fg = _BASIC[c - 90 + 8]
            elif c == 39:
                self.fg = None
            elif c == 38 and i + 1 < len(codes):
                if codes[i + 1] == 5 and i + 2 < len(codes):
                    self.fg = _xterm_hex(codes[i + 2])
                    i += 2
                elif codes[i + 1] == 2 and i + 4 < len(codes):
                    self.fg = "#%02x%02x%02x" % (codes[i + 2], codes[i + 3], codes[i + 4])
                    i += 4
            i += 1

    @property
    def tag(self) -> str:
        return "ansi_%s_%d" % (self.fg or "def", int(self.bold))


# --------------------------------------------------------------------------- process

class ServerProc:
    """Một server = một cây tiến trình dưới cmd.exe, giết bằng taskkill /T."""

    def __init__(self, sid: str, events: "queue.Queue"):
        self.sid = sid
        self.events = events
        self.proc: subprocess.Popen | None = None
        self.started_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def _emit(self, kind: str, payload=None) -> None:
        self.events.put((kind, self.sid, payload))

    def start(self, cwd: str, command: str, own_console: bool = False) -> None:
        with self._lock:
            if self.running:
                return
            if not command.strip():
                self._emit("log", "!! Chưa có lệnh chạy.\n")
                return
            if cwd and not Path(cwd).is_dir():
                self._emit("log", "!! Thư mục không tồn tại: %s\n" % cwd)
                self._emit("state", "error")
                return

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env.pop("NO_COLOR", None)
            if not own_console:
                # Không có tty thì đa số CLI tự tắt màu — ép chúng giữ màu như console thật.
                env["FORCE_COLOR"] = "1"
                env["CLICOLOR_FORCE"] = "1"
                env["TERM"] = env.get("TERM") or "xterm-256color"

            kwargs: dict = dict(
                cwd=cwd or None,
                shell=True,
                env=env,
                creationflags=CREATE_NEW_PROCESS_GROUP
                | (CREATE_NEW_CONSOLE if own_console else CREATE_NO_WINDOW),
            )
            if not own_console:
                kwargs.update(
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    bufsize=0,
                )
            try:
                self.proc = subprocess.Popen(command, **kwargs)
            except Exception as exc:
                self._emit("log", "!! Không chạy được: %s\n" % exc)
                self._emit("state", "error")
                return

            self.started_at = time.time()
            self._emit("log", "\x1b[90m>> %s\n>> cwd: %s  (pid %s)\x1b[0m\n" % (command, cwd, self.proc.pid))
            if own_console:
                self._emit("log", "\x1b[90m>> log hiện ở cửa sổ console riêng\x1b[0m\n")
            self._emit("state", "running")
            target = self._pump if not own_console else self._watch
            threading.Thread(target=target, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        """Đọc byte thô (không theo dòng) để progress bar / prompt hiện ngay như console."""
        stream = proc.stdout
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = stream.read(4096)  # type: ignore[union-attr]
                if not chunk:
                    break
                text = decoder.decode(chunk)  # ghép ký tự UTF-8 bị cắt giữa hai chunk
                if text:
                    self._emit("log", text)
        except Exception:
            pass
        self._finish(proc)

    def _watch(self, proc: subprocess.Popen) -> None:
        proc.wait()
        self._finish(proc)

    def _finish(self, proc: subprocess.Popen) -> None:
        code = proc.wait()
        if proc is self.proc:
            self._emit("log", "\x1b[90m<< tiến trình kết thúc (exit %s)\x1b[0m\n" % code)
            self._emit("state", "stopped" if code in (0, 1, 3221225786) else "error")

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                self.proc = None
                self._emit("state", "stopped")
                return
            self._emit("log", "\x1b[90m-- dừng cây tiến trình pid %s\x1b[0m\n" % proc.pid)
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception as exc:
                self._emit("log", "!! taskkill lỗi: %s\n" % exc)
            try:
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.proc = None
            self._emit("state", "stopped")


# --------------------------------------------------------------------------- ui

COLORS = {
    "bg": "#1b1d23",
    "card": "#23262e",
    "fg": "#e6e6e6",
    "muted": "#8b93a1",
    "log_bg": "#14161a",
    "log_fg": "#cfd6e4",
    "running": "#3ddc84",
    "stopped": "#6b7280",
    "error": "#ff5c5c",
}
STATE_TEXT = {"running": "đang chạy", "stopped": "đã dừng", "error": "lỗi"}


class ConsoleView:
    """Text widget hiển thị stdout như console: giữ màu ANSI, \\r ghi đè dòng."""

    def __init__(self, parent: tk.Widget):
        self.frame = tk.Frame(parent, bg=COLORS["log_bg"])
        self.text = tk.Text(
            self.frame, bg=COLORS["log_bg"], fg=COLORS["log_fg"], insertbackground=COLORS["fg"],
            font=("Consolas", 9), wrap="char", bd=0, padx=8, pady=6, state="disabled",
            selectbackground="#33405c", spacing1=0, spacing3=0,
        )
        sb = ttk.Scrollbar(self.frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.state = AnsiState()
        self._tags: set[str] = set()
        self._at_line_start = True

    def _tag_for(self, st: AnsiState) -> str:
        name = st.tag
        if name not in self._tags:
            font = ("Consolas", 9, "bold") if st.bold else ("Consolas", 9)
            self.text.tag_configure(name, foreground=st.fg or COLORS["log_fg"], font=font)
            self._tags.add(name)
        return name

    def write(self, chunk: str) -> None:
        txt = self.text
        txt.config(state="normal")
        pos = 0
        for m in SGR_RE.finditer(chunk):
            self._emit(chunk[pos:m.start()])
            self.state.apply(m.group(1))
            pos = m.end()
        self._emit(chunk[pos:])
        # cắt bớt log cũ, giữ cửa sổ nhớ có giới hạn
        lines = int(txt.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            txt.delete("1.0", "%d.0" % (lines - MAX_LOG_LINES))
        txt.config(state="disabled")

    def _emit(self, raw: str) -> None:
        if not raw:
            return
        raw = NOISE_RE.sub("", raw).replace("\r\n", "\n")
        if not raw:
            return
        tag = self._tag_for(self.state)
        for part in re.split(r"([\r\n])", raw):
            if part == "\n":
                self.text.insert("end", "\n", tag)
            elif part == "\r":
                # con trỏ về đầu dòng: dòng kế tiếp sẽ ghi đè (progress bar)
                self.text.delete("end-1c linestart", "end-1c")
            elif part:
                self.text.insert("end", part, tag)

    def clear(self) -> None:
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def dump(self) -> str:
        return self.text.get("1.0", "end-1c")

    def see_end(self) -> None:
        self.text.see("end")


class ServerCard:
    def __init__(self, app: "LauncherApp", parent: tk.Widget, spec: dict):
        self.app = app
        self.spec = spec
        self.sid = spec["id"]
        self.proc = ServerProc(self.sid, app.events)
        self.state = "stopped"

        frame = tk.Frame(parent, bg=COLORS["card"], highlightbackground="#333844", highlightthickness=1)
        frame.pack(fill="x", padx=10, pady=(0, 8))
        self.frame = frame

        head = tk.Frame(frame, bg=COLORS["card"])
        head.pack(fill="x", padx=10, pady=(8, 4))

        self.dot = tk.Label(head, text="●", bg=COLORS["card"], fg=COLORS["stopped"], font=("Segoe UI", 13))
        self.dot.pack(side="left")
        tk.Label(head, text=spec.get("name", self.sid), bg=COLORS["card"], fg=COLORS["fg"],
                 font=("Segoe UI Semibold", 11)).pack(side="left", padx=(6, 10))
        self.status = tk.Label(head, text="đã dừng", bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 9))
        self.status.pack(side="left")

        self.btn_stop = ttk.Button(head, text="■ Stop", width=9, command=self.stop, state="disabled")
        self.btn_stop.pack(side="right", padx=(4, 0))
        self.btn_start = ttk.Button(head, text="▶ Start", width=9, command=self.start)
        self.btn_start.pack(side="right", padx=(4, 0))
        ttk.Button(head, text="↻", width=3, command=self.restart).pack(side="right", padx=(4, 0))
        if spec.get("url"):
            ttk.Button(head, text="🌐", width=3,
                       command=lambda: webbrowser.open(self.spec.get("url", ""))).pack(side="right", padx=(4, 0))

        body = tk.Frame(frame, bg=COLORS["card"])
        body.pack(fill="x", padx=10, pady=(0, 10))
        body.columnconfigure(1, weight=1)

        self.var_cwd = tk.StringVar(value=spec.get("cwd", ""))
        self.var_cmd = tk.StringVar(value=spec.get("command", ""))
        self.var_socket = tk.StringVar(value=spec.get("socket", ""))
        self.var_auto = tk.BooleanVar(value=bool(spec.get("autostart", False)))
        self.var_console = tk.BooleanVar(value=bool(spec.get("own_console", False)))

        row = 0
        self._label(body, "Thư mục", row)
        ttk.Entry(body, textvariable=self.var_cwd).grid(row=row, column=1, sticky="ew", pady=2)
        ttk.Button(body, text="…", width=3, command=self._pick_dir).grid(row=row, column=2, padx=(4, 0))

        row += 1
        self._label(body, "Lệnh", row)
        ttk.Entry(body, textvariable=self.var_cmd).grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)

        if spec.get("socket_field"):
            row += 1
            self._label(body, "Socket", row)
            ttk.Entry(body, textvariable=self.var_socket).grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
            row += 1
            tk.Label(body,
                     text="{socket} trong lệnh được thay bằng ô trên — bỏ trống = bridge tự đọc URL từ Google Drive.",
                     bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 8)).grid(
                row=row, column=1, columnspan=2, sticky="w")

        row += 1
        opts = tk.Frame(body, bg=COLORS["card"])
        opts.grid(row=row, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self._check(opts, "Nằm trong Start All", self.var_auto)
        self._check(opts, "Console riêng (cửa sổ ngoài)", self.var_console)

    def _label(self, parent: tk.Widget, text: str, row: int) -> None:
        tk.Label(parent, text=text, bg=COLORS["card"], fg=COLORS["muted"],
                 font=("Segoe UI", 9), width=9, anchor="w").grid(row=row, column=0, sticky="w", pady=2)

    def _check(self, parent: tk.Widget, text: str, var: tk.BooleanVar) -> None:
        tk.Checkbutton(parent, text=text, variable=var, bg=COLORS["card"], fg=COLORS["muted"],
                       selectcolor=COLORS["card"], activebackground=COLORS["card"],
                       activeforeground=COLORS["fg"], font=("Segoe UI", 9),
                       highlightthickness=0, bd=0).pack(side="left", padx=(0, 14))

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_cwd.get() or str(app_dir()), title="Chọn thư mục chạy")
        if d:
            self.var_cwd.set(os.path.normpath(d))

    # -- actions
    def resolved_command(self) -> str:
        cmd = self.var_cmd.get()
        if "{socket}" in cmd:
            cmd = cmd.replace("{socket}", self.var_socket.get().strip())
        return cmd.strip()

    def start(self) -> None:
        if self.proc.running:
            return
        self.app.collect_config()
        cwd = self.var_cwd.get().strip()
        self.proc.start(os.path.normpath(cwd) if cwd else "", self.resolved_command(),
                        own_console=bool(self.var_console.get()))

    def stop(self) -> None:
        self.proc.stop()

    def restart(self) -> None:
        if self.proc.running:
            self.stop()
            self.app.root.after(600, self.start)
        else:
            self.start()

    def set_state(self, state: str) -> None:
        self.state = state
        self.dot.config(fg=COLORS.get(state, COLORS["stopped"]))
        self.status.config(text=STATE_TEXT.get(state, state),
                           fg=COLORS["running"] if state == "running" else
                           COLORS["error"] if state == "error" else COLORS["muted"])
        self.btn_start.config(state="disabled" if state == "running" else "normal")
        self.btn_stop.config(state="normal" if state == "running" else "disabled")

    def tick(self) -> None:
        if self.proc.running:
            secs = int(time.time() - self.proc.started_at)
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            up = "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)
            self.status.config(text="đang chạy · pid %s · %s" % (self.proc.pid, up))

    def dump(self) -> dict:
        spec = dict(self.spec)
        spec["cwd"] = self.var_cwd.get().strip()
        spec["command"] = self.var_cmd.get().strip()
        spec["autostart"] = bool(self.var_auto.get())
        spec["own_console"] = bool(self.var_console.get())
        if spec.get("socket_field"):
            spec["socket"] = self.var_socket.get().strip()
        return spec


class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.events: queue.Queue = queue.Queue()
        self.cards: list[ServerCard] = []
        self.consoles: dict[str, ConsoleView] = {}

        root.title(APP_NAME)
        root.geometry("1060x860")
        root.minsize(900, 640)
        root.configure(bg=COLORS["bg"])

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=4)
        style.configure("TEntry", fieldbackground="#2c3039", foreground=COLORS["fg"],
                        insertcolor=COLORS["fg"], bordercolor="#3a3f4b")
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 5))

        self._build_toolbar()
        self._build_cards()
        self._build_logs()

        if not CONFIG_PATH.exists():   # lần chạy đầu: đẻ sẵn config.json để sửa tay được
            self.save()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(60, self._drain)
        self.root.after(1000, self._tick)
        if self.cfg.get("autostart_on_launch"):
            self.root.after(400, self.start_all)

    # -- layout
    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.root, bg=COLORS["bg"])
        bar.pack(fill="x", padx=10, pady=(10, 8))
        tk.Label(bar, text="FlowKit", bg=COLORS["bg"], fg=COLORS["fg"],
                 font=("Segoe UI Semibold", 14)).pack(side="left", padx=(2, 14))
        ttk.Button(bar, text="▶  Start All", command=self.start_all).pack(side="left", padx=3)
        ttk.Button(bar, text="■  Stop All", command=self.stop_all).pack(side="left", padx=3)
        ttk.Button(bar, text="↻  Restart All", command=self.restart_all).pack(side="left", padx=3)
        ttk.Button(bar, text="💾  Lưu cấu hình", command=self.save).pack(side="left", padx=(16, 3))
        ttk.Button(bar, text="📁", width=3, command=lambda: os.startfile(app_dir())).pack(side="left", padx=3)

        self.var_launch = tk.BooleanVar(value=bool(self.cfg.get("autostart_on_launch")))
        tk.Checkbutton(bar, text="Tự Start All khi mở app", variable=self.var_launch,
                       bg=COLORS["bg"], fg=COLORS["muted"], selectcolor=COLORS["bg"],
                       activebackground=COLORS["bg"], activeforeground=COLORS["fg"],
                       font=("Segoe UI", 9), highlightthickness=0, bd=0).pack(side="right")

    def _build_cards(self) -> None:
        wrap = tk.Frame(self.root, bg=COLORS["bg"])
        wrap.pack(fill="x")
        for spec in self.cfg["servers"]:
            self.cards.append(ServerCard(self, wrap, spec))

    def _build_logs(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        for card in self.cards:
            view = ConsoleView(nb)
            nb.add(view.frame, text=card.spec.get("name", card.sid))
            self.consoles[card.sid] = view

        foot = tk.Frame(self.root, bg=COLORS["bg"])
        foot.pack(fill="x", padx=10, pady=(0, 8))
        self.var_scroll = tk.BooleanVar(value=True)
        tk.Checkbutton(foot, text="Tự cuộn", variable=self.var_scroll, bg=COLORS["bg"], fg=COLORS["muted"],
                       selectcolor=COLORS["bg"], activebackground=COLORS["bg"], activeforeground=COLORS["fg"],
                       font=("Segoe UI", 9), highlightthickness=0, bd=0).pack(side="left")
        ttk.Button(foot, text="Xoá log", command=self._clear_current).pack(side="left", padx=8)
        ttk.Button(foot, text="Lưu log ra file", command=self._save_current).pack(side="left")
        self.footer = tk.Label(foot, text=str(CONFIG_PATH), bg=COLORS["bg"], fg=COLORS["muted"],
                               font=("Segoe UI", 8))
        self.footer.pack(side="right")
        self.nb = nb

    def _current(self) -> ServerCard:
        return self.cards[self.nb.index(self.nb.select())]

    def _clear_current(self) -> None:
        self.consoles[self._current().sid].clear()

    def _save_current(self) -> None:
        card = self._current()
        path = filedialog.asksaveasfilename(
            defaultextension=".log", initialfile="%s.log" % card.sid,
            filetypes=[("Log", "*.log"), ("Text", "*.txt"), ("All", "*.*")])
        if path:
            Path(path).write_text(self.consoles[card.sid].dump(), encoding="utf-8")
            self.footer.config(text="Đã lưu log · %s" % path)

    # -- events
    def _drain(self) -> None:
        touched: set[str] = set()
        try:
            for _ in range(400):  # trần mỗi nhịp để UI không đứng khi log đổ ào
                kind, sid, payload = self.events.get_nowait()
                if kind == "log":
                    view = self.consoles.get(sid)
                    if view is not None:
                        view.write(payload)
                        touched.add(sid)
                elif kind == "state":
                    for card in self.cards:
                        if card.sid == sid:
                            card.set_state(payload)
        except queue.Empty:
            pass
        if self.var_scroll.get():
            for sid in touched:
                self.consoles[sid].see_end()
        self.root.after(60, self._drain)

    def _tick(self) -> None:
        for card in self.cards:
            card.tick()
        self.root.after(1000, self._tick)

    # -- bulk actions
    def start_all(self) -> None:
        delay = int(self.cfg.get("start_all_delay_ms", 800))
        pending = [c for c in self.cards if c.var_auto.get() and not c.proc.running]
        for i, card in enumerate(pending):
            self.root.after(i * delay, card.start)

    def stop_all(self) -> None:
        for card in self.cards:
            if card.proc.running:
                threading.Thread(target=card.stop, daemon=True).start()

    def restart_all(self) -> None:
        self.stop_all()
        self.root.after(1500, self.start_all)

    # -- config
    def collect_config(self) -> None:
        self.cfg["servers"] = [c.dump() for c in self.cards]
        self.cfg["autostart_on_launch"] = bool(self.var_launch.get())

    def save(self) -> None:
        self.collect_config()
        try:
            save_config(self.cfg)
            self.footer.config(text="Đã lưu · %s" % CONFIG_PATH)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "Không lưu được cấu hình:\n%s" % exc)

    def on_close(self) -> None:
        running = [c for c in self.cards if c.proc.running]
        if running:
            names = ", ".join(c.spec.get("name", c.sid) for c in running)
            if not messagebox.askokcancel(APP_NAME, "Đang chạy: %s\n\nDừng hết rồi thoát?" % names):
                return
            for card in running:
                card.stop()
        self.collect_config()
        try:
            save_config(self.cfg)
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    icon = app_dir() / "icon.ico"
    if icon.exists():
        try:
            root.iconbitmap(str(icon))
        except Exception:
            pass
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
