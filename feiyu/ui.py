"""终端外观：配色、消息渲染、输入区、状态栏

所有给人看的输出都从这里走 agent 和工具只负责给出结构化的事实，
怎么画由这个模块决定
"""

import os
import re
import shutil
import sys
import threading
import time

from prompt_toolkit.utils import get_cwidth

# 黑灰打底，其余四色刻意压暗，浅底深底都读得清
_RGB = {
    "grey": "138;131;120",
    "blue": "91;127;166",
    "red": "176;87;78",
    "amber": "168;132;60",
    "brown": "150;103;74",
}

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

BULLET = "⏺"
ELBOW = "⎿"
SPARK = "✻"
FRAMES = "✳✻✽✶"
MAX_WIDTH = 100


def paint(color: str, text: str, *, bold: bool = False) -> str:
    if not _COLOR or not text:
        return text
    prefix = "\033[1m" if bold else ""
    if color:
        prefix += f"\033[38;2;{_RGB[color]}m"
    return f"{prefix}{text}\033[0m" if prefix else text


def bold(text: str) -> str:
    return paint("", text, bold=True)


def width() -> int:
    return min(shutil.get_terminal_size((80, 24)).columns, MAX_WIDTH)


_ANSI = re.compile(r"\033\[[0-9;]*m")


def visible(text: str) -> int:
    """显示宽度 必须先剥掉颜色码——get_cwidth 会把转义序列也算进去 """
    return get_cwidth(_ANSI.sub("", text))


def clip(text: str, limit: int) -> str:
    """按显示宽度截断，CJK 算两格 """
    if visible(text) <= limit or limit <= 1:
        return text
    kept, used = [], 0
    for ch in _ANSI.sub("", text):
        w = get_cwidth(ch)
        if used + w > limit - 1:
            break
        kept.append(ch)
        used += w
    return "".join(kept) + "…"


def _pad(text: str, inner: int) -> str:
    return text + " " * max(0, inner - visible(text))


def box(lines: list[str]) -> str:
    """圆角框 传进来的行可以带颜色码 """
    inner = width() - 4
    top = paint("grey", "╭" + "─" * (inner + 2) + "╮")
    bottom = paint("grey", "╰" + "─" * (inner + 2) + "╯")
    bar = paint("grey", "│")
    body = [f"{bar} {_pad(clip(line, inner), inner)} {bar}" for line in lines]
    return "\n".join([top, *body, bottom])


LOGO_COLS = 16
LOGO_GAP = 3
LOGO_MIN_WIDTH = 60


def logo_rows(cols: int = LOGO_COLS) -> list[str]:
    """把 logo.png 画成上下半块字符 一个字符格 = 上下两个像素，所以行数是列数的一半 """
    if not _COLOR:
        return []
    path = os.path.join(os.path.dirname(__file__), "logo.png")
    if not os.path.exists(path):
        return []
    try:
        from PIL import Image
    except ImportError:
        return []
    try:
        img = Image.open(path).convert("RGB")
    except OSError:
        return []
    img = _trim(img)
    w, h = img.size
    rows = max(1, round(h / w * cols / 2))
    img = img.resize((cols, rows * 2), Image.LANCZOS)
    px = img.load()
    out = []
    for r in range(rows):
        cells = []
        for c in range(cols):
            tr, tg, tb = px[c, r * 2]
            br, bg, bb = px[c, r * 2 + 1]
            cells.append("\033[38;2;%d;%d;%d;48;2;%d;%d;%dm▀" % (tr, tg, tb, br, bg, bb))
        out.append("".join(cells) + "\033[0m")
    return out


def _trim(img, threshold: int = 60, margin: int = 10):
    """裁掉 logo 四周的黑边并补成正方形 不裁的话缩到十几列后只剩一团白斑 """
    box = img.convert("L").point(lambda v: 255 if v > threshold else 0).getbbox()
    if not box:
        return img
    w, h = img.size
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = max(x1 - x0, y1 - y0) // 2 + margin
    return img.crop((max(0, cx - half), max(0, cy - half), min(w, cx + half), min(h, cy + half)))


def mode_tag(mode: str, label: str) -> str:
    """自动模式是唯一会不打招呼就动手的，用暖色点出来 """
    marker = {"auto": "⏵⏵", "default": "⏸", "readonly": "○"}.get(mode, "·")
    return paint("amber" if mode == "auto" else "grey", marker + " " + label)


def tool_label(name: str) -> str:
    return _LABEL.get(name, name)


def welcome(workspace, name: str, model: str, version: str, style: str, mode: str, project: str) -> str:
    home = os.path.expanduser("~")
    shown = str(workspace)
    if shown.startswith(home):
        shown = "~" + shown[len(home) :]
    info = [
        paint("brown", SPARK) + " " + bold("Feiyu") + paint("grey", f"  v{version}   终端编程助手"),
        "",
        paint("grey", "工作目录  ") + shown,
        paint("grey", "模型      ") + (f"{name} · {model}" if model else paint("amber", "未设置")),
        paint("grey", "风格      ") + style,
        paint("grey", "模式      ") + mode,
        paint("grey", "项目约定  ") + project,
        "",
        paint("grey", "⏎ 发送   \\⏎ 换行   esc 中断   /help 更多"),
    ]
    art = logo_rows() if width() >= LOGO_MIN_WIDTH else []
    if not art:
        return box(info)

    # 文字在 logo 右边、上下居中 先按剩余宽度裁好，拼出来就不会再被 box 裁到
    room = width() - 4 - LOGO_COLS - LOGO_GAP
    info = [clip(line, room) for line in info]
    pad = max(0, (len(art) - len(info)) // 2)
    blank = " " * LOGO_COLS
    rows = max(len(art), len(info) + pad)
    lines = []
    for i in range(rows):
        j = i - pad
        art_row = art[i] if i < len(art) else blank
        text = info[j] if 0 <= j < len(info) else ""
        lines.append(art_row + " " * LOGO_GAP + text)
    return box(lines)


def enter_screen() -> None:
    """清屏并把光标移到左上角，像 claude code 那样"切进来"，不留上一屏的东西 """
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


# ---- 工具调用 ----

_LABEL = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Update",
    "list_files": "List",
    "search": "Search",
    "web_fetch": "Fetch",
    "remember": "Remember",
    "task": "Task",
    "todo": "Todo",
    "exit_plan": "Plan",
}
_HEADLINE_ARG = {
    "bash": "command",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "list_files": "pattern",
    "search": "regex",
    "web_fetch": "url",
    # 确认时人只看得到这一行，不把 fact 显出来就等于让人盲签
    "remember": "fact",
    "exit_plan": "plan",
    "task": "prompt",
}


def tool_target(name: str, args: dict) -> str:
    """这次调用作用在什么上 并行时几条结果一起出来，光看 summary 分不清谁是谁 """
    key = _HEADLINE_ARG.get(name)
    return str(args.get(key, "")).replace("\n", " ") if key else ""


def tool_call(name: str, args: dict) -> str:
    label = _LABEL.get(name, name)
    key = _HEADLINE_ARG.get(name)
    head = args.get(key, "") if key else ""
    head = clip(str(head).replace("\n", " "), max(8, width() - visible(label) - 6))
    return paint("brown", BULLET) + " " + bold(label) + paint("grey", f"({head})")


def tool_result(summary: str, detail: list[tuple[str, str]], *, is_error: bool) -> str:
    """detail 是 (kind, text)，kind 为 '-' / '+' / '' """
    tint = "red" if is_error else "grey"
    lines = ["  " + paint(tint, f"{ELBOW}  {clip(summary, width() - 6)}")]
    for kind, text in detail:
        color = {"-": "red", "+": "blue",
                 "done": "grey", "doing": "brown", "pending": "grey"}.get(kind, "grey")
        lines.append("     " + paint(color, clip(text, width() - 5)))
    return "\n".join(lines)


def plan(text: str) -> str:
    """方案要用户看着点头，所以给它一条竖边，跟普通的灰字区分开 """
    body = []
    for line in (text or "（没写方案）").split("\n"):
        for piece in wrap(line, width() - 6) or [""]:
            body.append("  " + paint("brown", "│ ") + piece)
    return "\n".join(body)


def wrap(line: str, limit: int) -> list[str]:
    """按显示宽度折行 CJK 算两格，所以不能用 textwrap """
    out, row, used = [], "", 0
    for ch in line:
        w = get_cwidth(ch)
        if used + w > limit and row:
            out.append(row)
            row, used = "", 0
        row += ch
        used += w
    if row:
        out.append(row)
    return out


def notice(text: str) -> str:
    return "  " + paint("grey", text)


def warn(text: str) -> str:
    return "  " + paint("amber", text)


def error(text: str) -> str:
    return "  " + paint("red", text)


def tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


# ---- 流式输出 ----


class Stream:
    """turn 期间的输出：先转 spinner，出正文后逐字吐，正文缩进两格 """

    # 离右边界还有这么多列时才开始攒词 攒词是为了在词边界折行，
    # 但攒着就意味着整个词一起蹦出来，所以只在真的可能要折行的地方攒
    WRAP_MARGIN = 24

    def __init__(self, *, spinner_delay: float = 0.25, tick: float = 0.008):
        self._tty = sys.stdout.isatty()
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._done = False
        self._delay = spinner_delay
        self._tick = tick
        self._begin = time.monotonic()
        self._fresh_line = True
        self._col = 0
        self._word = ""
        self._limit = width()
        self.wrote_text = False
        self.text = ""      # 中断时要靠它把已经说出口的话补回历史
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if self._tty:
            self._thread.start()

    def write(self, text: str) -> None:
        if not text:
            return
        self.wrote_text = True
        self.text += text
        if not self._tty:
            sys.stdout.write(text)
            sys.stdout.flush()
            return
        with self._lock:
            self._buf.append(text)

    def finish(self) -> None:
        if not self._tty:
            return
        with self._lock:
            self._done = True
        self._thread.join()
        self._flush_word()
        if not self._fresh_line:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _emit(self, chunk: str) -> None:
        """按框宽折行并保持两格缩进 英文断在词边界，CJK 可以任意断 """
        for ch in chunk:
            if ch == "\n":
                self._flush_word()
                sys.stdout.write("\n")
                self._fresh_line, self._col = True, 0
            elif ch.isspace():
                self._flush_word()
                # 行尾那个空格不写出去，否则会溢出一列、逼终端自己软换行
                if not self._fresh_line and self._col + 1 <= self._limit:
                    sys.stdout.write(ch)
                    self._col += 1
            elif get_cwidth(ch) > 1:
                self._flush_word()
                self._put(ch)
            else:
                self._word += ch
                if self._col + len(self._word) < self._limit - self.WRAP_MARGIN:
                    self._flush_word()
        sys.stdout.flush()

    def _flush_word(self) -> None:
        if self._word:
            word, self._word = self._word, ""
            self._put(word)

    def _put(self, text: str) -> None:
        while text:
            if self._fresh_line:
                sys.stdout.write("  ")
                self._fresh_line, self._col = False, 2
            room = self._limit - self._col
            width_left = get_cwidth(text)
            if width_left <= room:
                sys.stdout.write(text)
                self._col += width_left
                return
            if self._col > 2:
                sys.stdout.write("\n")
                self._fresh_line, self._col = True, 0
                continue
            # 一整行都装不下：超长路径或标识符，只能硬断
            cut, used = 0, 0
            for ch in text:
                cw = get_cwidth(ch)
                if used + cw > room:
                    break
                cut, used = cut + 1, used + cw
            cut = max(cut, 1)
            sys.stdout.write(text[:cut] + "\n")
            text = text[cut:]
            self._fresh_line, self._col = True, 0

    def _run(self) -> None:
        frame = 0
        while True:
            with self._lock:
                pending = bool(self._buf)
                done = self._done
            if pending:
                break
            if done:
                self._clear_spinner()
                return
            if time.monotonic() - self._begin >= self._delay:
                elapsed = int(time.monotonic() - self._begin)
                label = paint("brown", FRAMES[frame]) + paint("grey", f" 思考中… ({elapsed}s · esc 中断)")
                sys.stdout.write("\r\033[K" + label)
                sys.stdout.flush()
                frame = (frame + 1) % len(FRAMES)
            time.sleep(0.12)

        self._clear_spinner()
        pending = ""
        while True:
            with self._lock:
                pending += "".join(self._buf)
                self._buf.clear()
                done = self._done
            if pending:
                # 上游是一阵一阵来的 按积压量的比例匀速放出去，别整块蹦；
                # 收尾时放快些，免得话说完了字还在慢慢爬
                share = 3 if done else 6
                take = max(4 if done else 1, -(-len(pending) // share))
                self._emit(pending[:take])
                pending = pending[take:]
            if done and not pending:
                return
            time.sleep(self._tick)

    def _clear_spinner(self) -> None:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


# ---- turn 期间按 esc 中断 ----


# 确认交互要读键盘，得先让 EscInterrupt 的 watcher 松手
_active_guard = None


def ask_yes_no(question: str) -> bool:
    """启动时问一句是非题 turn 中途用不了 confirm（那个要 EscInterrupt 的守卫）

    非终端时一律返回 False：没人能回答，而这个问句问的都是「要不要信任」，
    默认必须是不信任
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    from prompt_toolkit import prompt as ask_line
    from prompt_toolkit.formatted_text import ANSI

    try:
        answer = ask_line(ANSI("  " + paint("amber", question) +
                               paint("grey", " [y/N] "))).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return answer in ("y", "yes", "是")


def confirm(question: str) -> str:
    """turn 中途问一个单键 stdin 这时已被 EscInterrupt 切到 cbreak，直接读一个字节

    返回 "yes" / "no" / "always" 不是终端（管道里跑）时一律拒绝——没人能回答
    """
    guard = _active_guard
    if guard is None or not guard.enabled:
        return "no"
    guard.pause()
    try:
        prompt = ("  " + paint("amber", question)
                  + paint("grey", "  [y] 执行  [n] 跳过  [a] 本轮都允许  [s] 以后都不用问"))
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            try:
                ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", "ignore").lower()
            except OSError:
                ch = "n"
            if ch in ("y", "n", "a", "s", "\x1b", "\r", "\n", ""):
                break
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        return {"y": "yes", "a": "always", "s": "remember"}.get(ch, "no")
    finally:
        guard.resume()


class EscInterrupt:
    """把 stdin 切到 cbreak，读到 ESC 就给主线程丢 KeyboardInterrupt

    顺带把 turn 期间误敲的键吃掉，不会漏进下一轮输入
    """

    def __init__(self):
        self.enabled = os.name == "posix" and sys.stdin.isatty() and sys.stdout.isatty()
        self.triggered = False
        self._saved = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def __enter__(self):
        if not self.enabled:
            return self
        import termios
        import tty

        self._fd = sys.stdin.fileno()
        try:
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except (termios.error, OSError):
            self.enabled = False
            return self
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        global _active_guard
        _active_guard = self
        return self

    def _watch(self) -> None:
        import _thread
        import select

        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.03)
                continue
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                ch = os.read(self._fd, 1)
            except OSError:
                return
            if ch == b"\x1b":
                self.triggered = True
                _thread.interrupt_main()
                return

    def __exit__(self, *_exc) -> None:
        global _active_guard
        _active_guard = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        if self._saved is not None:
            import termios

            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except (termios.error, OSError):
                pass


# ---- 输入区 ----

SLASH = {
    "/help": "显示帮助",
    "/model": "查看或切换模型",
    "/key": "添加 / 查看 / 删除模型配置",
    "/style": "查看或切换回答风格",
    "/mode": "切换权限模式：默认 / 自动 / 只读",
    "/allow": "查看或添加权限规则",
    "/verify": "查看或设置改完自动跑的校验命令",
    "/review": "让一个没看过过程的模型给这次改动打分",
    "/cost": "按任务看最近的 token 和花费",
    "/memory": "查看 / 添加 / 删除这个目录的长期记忆",
    "/hooks": "看在哪些时机会跑哪些外部命令",
    "/compact": "把历史压缩成一段摘要，省上下文",
    "/context": "查看并重新加载项目约定文件",
    "/clear": "清空对话历史和存盘，重新开始",
    "/exit": "退出",
}
# /style 和 /mode 的取值由 agent 注入，用于补全
STYLE_NAMES: list[str] = []
MODE_NAMES: list[str] = []
# 模型清单随时会变，用回调取而不是快照
MODEL_NAMES = lambda: []


def _history_file() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    folder = os.path.join(base, "feiyu")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "history")


class InputBox:
    """带完整边框的多行输入

    没用 PromptSession：它只能在输入行上方画东西，底边框补不上；
    换 bottom_toolbar 的话又会被钉在屏幕底部，把框撑成十几行高的空壳
    所以自己搭一个三段布局：顶边框 / 输入 / 底边框
    """

    def __init__(self, status, on_cycle_mode=None):
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.filters import has_completions
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.layout.menus import CompletionsMenu
        from prompt_toolkit.application import Application
        from prompt_toolkit.formatted_text import ANSI

        self.status = status
        self.on_cycle_mode = on_cycle_mode

        class _Slash(Completer):
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if not text.startswith("/"):
                    return
                cmd, sep, arg = text.partition(" ")
                if not sep:
                    for name, desc in SLASH.items():
                        if name.startswith(text):
                            yield Completion(name, start_position=-len(text), display_meta=desc)
                elif cmd in ("/style", "/mode") and " " not in arg:
                    pool = STYLE_NAMES if cmd == "/style" else MODE_NAMES
                    for name in pool:
                        if name.startswith(arg):
                            yield Completion(name, start_position=-len(arg))
                elif cmd == "/model" and " " not in arg:
                    for name in MODEL_NAMES():
                        if name.startswith(arg):
                            yield Completion(name, start_position=-len(arg))
                elif cmd == "/key" and arg.startswith("rm "):
                    part = arg[3:]
                    for name in MODEL_NAMES():
                        if name.startswith(part):
                            yield Completion(name, start_position=-len(part))
                elif cmd == "/key" and " " not in arg:
                    for verb in ("add", "rm"):
                        if verb.startswith(arg):
                            yield Completion(verb, start_position=-len(arg))

        self.buffer = Buffer(
            multiline=True,
            history=FileHistory(_history_file()),
            auto_suggest=AutoSuggestFromHistory(),
            completer=_Slash(),
            complete_while_typing=True,
            accept_handler=lambda buf: self._accept(),
        )

        def line_prefix(lineno, wrap_count):
            if lineno == 0 and wrap_count == 0:
                return ANSI(paint("grey", "│") + " " + paint("brown", ">") + " ")
            return ANSI(paint("grey", "│") + "   ")

        self.input_window = Window(
            BufferControl(buffer=self.buffer),
            get_line_prefix=line_prefix,
            wrap_lines=True,
            height=Dimension(min=1),
            dont_extend_height=True,
        )
        body = HSplit(
            [
                Window(FormattedTextControl(lambda: ANSI(self.top_border())), height=1),
                self.input_window,
                Window(FormattedTextControl(lambda: ANSI(self.bottom_border())), height=1),
                # 补全菜单当成布局的一节放在框下面 做成 Float 的话会压在底边框上
                CompletionsMenu(max_height=6, scroll_offset=1),
            ]
        )
        layout = Layout(body, focused_element=self.input_window)

        keys = KeyBindings()

        @keys.add("enter")
        def _(event):
            buf = event.current_buffer
            # 行尾一个反斜杠表示还没说完，换行继续
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
            else:
                buf.validate_and_handle()

        @keys.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        # 补全菜单开着时上下键归菜单管，只有菜单关着才翻历史
        @keys.add("up", filter=~has_completions)
        def _(event):
            buf = event.current_buffer
            if buf.document.cursor_position_row == 0:
                buf.history_backward(count=event.arg)
            else:
                buf.cursor_up(count=event.arg)

        @keys.add("down", filter=~has_completions)
        def _(event):
            buf = event.current_buffer
            if buf.document.cursor_position_row == buf.document.line_count - 1:
                buf.history_forward(count=event.arg)
            else:
                buf.cursor_down(count=event.arg)

        @keys.add("s-tab")
        def _(event):
            if self.on_cycle_mode:
                self.on_cycle_mode()
                event.app.invalidate()      # 顶边框上的模式要立刻跟着变

        @keys.add("c-c")
        def _(event):
            event.app.exit(exception=KeyboardInterrupt, style="class:exiting")

        @keys.add("c-d")
        def _(event):
            if not event.current_buffer.text:
                event.app.exit(exception=EOFError, style="class:exiting")

        self.app = Application(
            layout=layout,
            key_bindings=keys,
            full_screen=False,
            erase_when_done=False,
        )

    def _accept(self) -> bool:
        self.app.exit(result=self.buffer.text)
        # 返回 True 让 buffer 不被清空，否则最后一次渲染时文字已经没了，
        # 留在 scrollback 里的会是个空框
        return True

    def top_border(self) -> str:
        """顶边框右侧嵌状态 塞不下就退回普通边框 """
        w = width()
        note = self.status()
        # 必须用 visible()：status 里可能带颜色码，get_cwidth 会把转义序列算成可见宽度
        fill = w - 5 - visible(note)
        if not note or fill < 8:
            return paint("grey", "╭" + "─" * (w - 2) + "╮")
        return paint("grey", "╭" + "─" * fill + " " + note + " ─╮")

    def bottom_border(self) -> str:
        return paint("grey", "╰" + "─" * (width() - 2) + "╯")

    def ask(self) -> str:
        self.buffer.reset()
        return self.app.run()
