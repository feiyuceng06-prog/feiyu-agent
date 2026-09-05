"""终端外观：配色、消息渲染、输入区、状态栏

所有给人看的输出都从这里走 agent 和工具只负责给出结构化的事实，
怎么画由这个模块决定
"""

import json
import os
import re
import shutil
import sys
import threading
import time

from prompt_toolkit.utils import get_cwidth

from . import events, files

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
CORNERS = "╭╮╰╯"
DOT = "•"          # 列表记号
LOGO = True        # 欢迎框里画不画 logo

# 主题只换调色板和几个记号，布局一律不动 —— 布局是为可读性定的，不该随审美变
# swiss：国际现代主义（瑞士风格）：黑白打底、一个红色强调、直角、方块记号、不画图
THEMES = {
    "default": {
        "label": "默认",
        "rgb": dict(_RGB),
        "glyphs": {"BULLET": "⏺", "ELBOW": "⎿", "SPARK": "✻", "FRAMES": "✳✻✽✶",
                   "CORNERS": "╭╮╰╯", "DOT": "•", "LOGO": True},
    },
    "swiss": {
        "label": "国际现代主义",
        "rgb": {"grey": "122;122;122", "blue": "24;24;24", "red": "213;0;0",
                "amber": "213;0;0", "brown": "213;0;0"},
        "glyphs": {"BULLET": "■", "ELBOW": "└", "SPARK": "■", "FRAMES": "▘▝▗▖",
                   "CORNERS": "┌┐└┘", "DOT": "–", "LOGO": False},
    },
}
THEME = "default"


def set_theme(name: str) -> bool:
    """切主题：改的是模块级常量，所有画东西的函数都是调用时才读它们 """
    spec = THEMES.get(name)
    if spec is None:
        return False
    global THEME
    THEME = name
    _RGB.clear()
    _RGB.update(spec["rgb"])
    globals().update(spec["glyphs"])
    return True
MAX_WIDTH = 100
# 选项最多摆这么多个 再多就该拆成两个问题，也超出单键能舒服选的范围
MAX_OPTIONS = 4


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


def pad(text: str, limit: int) -> str:
    """按显示宽度右补空格 `ljust` 数的是字符数，CJK 一个字占两格，
    技能和自定义命令的名字可以是中文，用 ljust 会对不齐
    """
    return text + " " * max(0, limit - visible(text))


def clip_path(path: str, limit: int) -> str:
    """路径要从左边裁 裁右边的话看到的全是 /tmp/xxx/yyy/…，恰好不知道在哪个项目 """
    if visible(path) <= limit or limit <= 4:
        return path
    parts = path.split("/")
    kept: list[str] = []
    used = 1
    for part in reversed(parts):
        if used + get_cwidth(part) + 1 > limit or not part:
            break
        kept.insert(0, part)
        used += get_cwidth(part) + 1
    return "…/" + "/".join(kept) if kept else path[-limit:]


def _pad(text: str, inner: int) -> str:
    return text + " " * max(0, inner - visible(text))


def box(lines: list[str]) -> str:
    """圆角框 传进来的行可以带颜色码 """
    inner = width() - 4
    top = paint("grey", CORNERS[0] + "─" * (inner + 2) + CORNERS[1])
    bottom = paint("grey", CORNERS[2] + "─" * (inner + 2) + CORNERS[3])
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
    marker = {"auto": "⏵⏵", "default": "⏸", "plan": "◐", "readonly": "○"}.get(mode, "·")
    return paint("amber" if mode == "auto" else "grey", marker + " " + label)


def tool_label(name: str) -> str:
    if name.startswith("mcp_"):
        return "MCP " + name[4:]
    return _LABEL.get(name, name)


def welcome(workspace, version: str, project: str) -> str:
    """开工框里只放整个会话不会变的东西

    模型 / 风格 / 模式 / 沙箱都在这儿放过，但这个框是打进 scrollback 的，
    切完之后重画不了，它会一直挂着开工时那份值骗人 —— 会变的一律交给状态栏，
    那才是唯一一处始终为真的地方
    """
    home = os.path.expanduser("~")
    shown = str(workspace)
    if shown.startswith(home):
        shown = "~" + shown[len(home) :]
    room = width() - 4 - (LOGO_COLS + LOGO_GAP if width() >= LOGO_MIN_WIDTH else 0)
    info = [
        paint("brown", SPARK) + " " + bold("Feiyu") + paint("grey", f"  v{version}   终端编程助手"),
        "",
        paint("grey", "工作目录  ") + clip_path(shown, max(20, room - 10)),
        paint("grey", "项目约定  ") + project,
        "",
        paint("grey", "⏎ 发送   \\⏎ 换行   esc 中断   /help 更多"),
    ]
    art = logo_rows() if LOGO and width() >= LOGO_MIN_WIDTH else []
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
    "web_search": "WebSearch",
    "remember": "Remember",
    "task": "Task",
    "todo": "Todo",
    "exit_plan": "Plan",
    "job": "Job",
    "ask_user": "Ask",
    "git": "Git",
    "symbol": "Symbol",
    "skill": "Skill",
}
_HEADLINE_ARG = {
    "bash": "command",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "list_files": "pattern",
    "search": "regex",
    "web_fetch": "url",
    "web_search": "query",
    # 确认时人只看得到这一行，不把 fact 显出来就等于让人盲签
    "remember": "fact",
    "task": "prompt",
    "job": "action",
    "git": "action",
    "symbol": "name",
    "skill": "name",
    # 问句本身就是这次调用的全部内容，不显出来那行就只剩一个 Ask
    "ask_user": "question",
}


def _headline_key(name: str, args: dict) -> str | None:
    key = _HEADLINE_ARG.get(name)
    if key is None and name.startswith("mcp_"):
        # 外部工具的参数名事先不知道，拿第一个字符串参数当作用对象
        key = next((k for k, v in args.items() if isinstance(v, str) and v), None)
    return key


def tool_target(name: str, args: dict) -> str:
    """这次调用作用在什么上 并行时几条结果一起出来，光看 summary 分不清谁是谁 """
    key = _headline_key(name, args)
    return str(args.get(key, "")).replace("\n", " ") if key else ""


# 参数还在流式生成时，从没收完的 JSON 里抠出目标 这几个键就是 _HEADLINE_ARG 的键
_ARG_HINT = re.compile(
    r'"(path|command|url|regex|pattern|prompt|fact|plan|question|query)"\s*:\s*"((?:[^"\\]|\\.)*)"')
# 参数短的时候没什么可报的进度，别抖着一个「已生成 1 行」
_PROGRESS_MIN = 200
# 报给别的前端时按这个步长攒：上游一个 delta 一次回调，不攒的话一个长文件
# 就是几千条事件 终端那条路不受这个影响，它按 STATUS_TICK 重画
PROGRESS_STEP = 400


def tool_progress(name: str, partial: str) -> str:
    """工具参数流式生成时的一行状态 只出纯文本，上色是 Stream 的事 """
    head = ""
    found = _ARG_HINT.search(partial)
    if found:
        try:
            head = json.loads('"%s"' % found.group(2))
        except ValueError:
            head = found.group(2)
        head = head.replace("\n", " ").strip()
    label = tool_label(name)
    text = "%s %s" % (label, head) if head else label
    if len(partial) >= _PROGRESS_MIN:
        # 数的是 JSON 里的 \n 转义，也就是最终会写进文件的行数
        text += " · 已生成 %d 行" % (partial.count("\\n") + 1)
    return text


def tool_call(name: str, args: dict) -> str:
    events.emit("tool_start", name=name, label=tool_label(name), args=args,
                target=tool_target(name, args))
    label = tool_label(name)
    key = _headline_key(name, args)
    head = args.get(key, "") if key else ""
    head = clip(str(head).replace("\n", " "), max(8, width() - visible(label) - 6))
    tail = paint("grey", "(%s)" % head) if head else ""    # 没有参数就别留一对空括号
    return paint("brown", BULLET) + " " + bold(label) + tail


def tool_preview(rows: list[tuple[str, str]]) -> str:
    """确认之前先让人看见要改什么 跟结果那块同一套配色，但不带 ⎿ """
    events.emit("preview", rows=[{"kind": k, "text": t} for k, t in rows])
    out = []
    for kind, text in rows:
        color = {"-": "red", "+": "blue"}.get(kind, "grey")
        out.append("     " + paint(color, clip(text, width() - 5)))
    return "\n".join(out)


def tool_result(summary: str, detail: list[tuple[str, str]], *, is_error: bool) -> str:
    """detail 是 (kind, text)，kind 为 '-' / '+' / '' """
    events.emit("tool_result", summary=summary, is_error=is_error,
                detail=[{"kind": k, "text": t} for k, t in detail])
    tint = "red" if is_error else "grey"
    lines = ["  " + paint(tint, f"{ELBOW}  {clip(summary, width() - 6)}")]
    for kind, text in detail:
        color = {"-": "red", "+": "blue",
                 "done": "grey", "doing": "brown", "pending": "grey"}.get(kind, "grey")
        lines.append("     " + paint(color, clip(text, width() - 5)))
    return "\n".join(lines)


def hook_line(ok: bool, text: str) -> str:
    """钩子的结果挂在工具结果那一级（⎿），不然它在两块输出之间悬着没有归属 """
    events.emit("hook", ok=ok, text=text)
    return "  " + paint("grey" if ok else "red", f"{ELBOW}  {clip(text, width() - 6)}")


def plan(text: str) -> str:
    """方案要用户看着点头，所以给它一条竖边，跟普通的灰字区分开 """
    events.emit("plan", text=text or "")
    body = []
    for line in (text or "（没写方案）").split("\n"):
        for piece in wrap(line, width() - 6) or [""]:
            body.append("  " + paint("brown", "│ ") + piece)
    return "\n".join(body)


def wrap(line: str, limit: int) -> list[str]:
    """按显示宽度折行 CJK 算两格，所以不能用 textwrap

    西文往回找空格断词 —— 不找的话 `feiyu --resume` 会被劈成 `feiyu --res` 和 `ume`
    CJK 可以任意断，不用管
    """
    out, row, used = [], "", 0
    for ch in line:
        w = get_cwidth(ch)
        if used + w > limit and row:
            cut = len(row)
            if w == 1 and not ch.isspace():
                space = row.rfind(" ")
                # 太靠前就不回退了，否则一个长词能把半行留白
                if space > 0 and get_cwidth(row[:space]) * 2 >= limit:
                    cut = space
            out.append(row[:cut].rstrip())
            row = row[cut:].lstrip(" ")
            used = get_cwidth(row)
        row += ch
        used += w
    if row:
        out.append(row)
    return out


def _lines(text: str, color: str) -> str:
    """提示语按终端宽度折行 不折的话终端会自己软换行，断在词中间（feiyu → f / eiyu）"""
    rows = []
    for line in text.split("\n"):
        rows += wrap(line, width() - 4) or [""]
    return "\n".join("  " + paint(color, row) for row in rows)


def notice(text: str) -> str:
    events.emit("note", level="notice", text=text)
    return _lines(text, "grey")


def warn(text: str) -> str:
    events.emit("note", level="warn", text=text)
    return _lines(text, "amber")


def error(text: str) -> str:
    events.emit("note", level="error", text=text)
    return _lines(text, "red")


def tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


# ---- 流式 markdown ----


class MarkdownStream:
    """把流进来的 markdown 切成 (文本, 样式) 片段 样式由 Stream 上色

    只认终端里真正有用的那几样：**粗体**、`行内代码`、# 标题、- / 1. 列表、``` 代码块、
    | 表格 |、--- 分隔线 斜体不认——`*` 在命令行里多半是通配符，认了反而到处误伤

    表格是整张攒齐了才出（列宽要看完所有行才知道），其余都是流式的

    是流式的：一次只喂到几个字符，末尾那个还判不出来的（半个 `**`、半行 ```）
    先攒着，等下一批或者 flush
    """

    FENCE = "```"
    # 判定行首那几种记号最多要看这么多字符 攒够或者见到换行就能定
    LOOKAHEAD = 8

    def __init__(self) -> None:
        self.bold = False
        self.code = False
        self.block = False      # ``` 围起来的代码块
        self.head = False       # # 标题，到行尾为止
        self.quote = False      # > 引用，到行尾为止
        self._buf = ""
        self._sol = True        # 停在行首
        self._rows: list[str] = []      # 攒着还没画的表格行（原文）
        self.limit = MAX_WIDTH          # 表格最宽画到这里，Stream 会按终端宽度改

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buf += chunk
        return self._drain(False)

    def flush(self) -> list[tuple[str, str]]:
        out = self._drain(True)
        self.bold = self.code = self.head = self.quote = False
        return out

    def _style(self) -> str:
        if self.block:
            return "block"
        if self.code:
            return "code"
        if self.head or self.bold:
            return "bold"
        if self.quote:
            return "quote"
        return ""

    def _drain(self, final: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []

        def push(text: str, style: str | None = None) -> None:
            if not text:
                return
            if style is None:
                style = self._style()
            if out and out[-1][1] == style:
                out[-1] = (out[-1][0] + text, style)
            else:
                out.append((text, style))

        while self._buf:
            if self._sol:
                kept = self._open_line(final, push)
                if kept is None:
                    return out          # 还判不出来，攒着等下一批
                if kept:
                    continue
            take = self._inline(final, push)
            if not take:
                return out
        if final and self._rows:
            self._table(push)
        return out

    _SEP_CELL = re.compile(r":?-+:?")
    _LINK = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")
    # 还没收完但仍可能长成一个链接的样子
    _LINK_HEAD = re.compile(r"\[[^\]\n]*(\]\(?[^)\n]*)?")
    _ORDERED = re.compile(r"\d{1,3}[.)] ")

    @staticmethod
    def _cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        # 单元格里的记号不再细分样式，只把它们拿掉，宽度才算得准
        return [c.strip().replace("**", "").replace("`", "") for c in row.split("|")]

    def _table(self, push) -> None:
        """把攒下的表格行画成对齐的列 首行下面有 |---| 就当表头 """
        rows = [self._cells(r) for r in self._rows]
        self._rows = []
        aligns: list[str] | None = None
        body: list[list[str]] = []
        for cells in rows:
            if cells and all(self._SEP_CELL.fullmatch(c) for c in cells):
                if aligns is None and len(body) == 1:
                    aligns = ["right" if c.endswith(":") and not c.startswith(":") else "left"
                              for c in cells]
                continue
            body.append(cells)
        if not body:
            return
        ncol = max(len(r) for r in body)
        body = [r + [""] * (ncol - len(r)) for r in body]
        aligns = (aligns or []) + ["left"] * (ncol - len(aligns or []))
        widths = [max(get_cwidth(r[i]) for r in body) for i in range(ncol)]
        # 装不下就从最宽的列削，削到 4 格为止，再宽也只能让它折行
        while sum(widths) + 2 * (ncol - 1) > self.limit - 2 and max(widths) > 4:
            widths[widths.index(max(widths))] -= 1

        def line(cells: list[str]) -> str:
            out = []
            for cell, w, align in zip(cells, widths, aligns):
                cell = clip(cell, w)
                pad = " " * (w - get_cwidth(cell))
                out.append(pad + cell if align == "right" else cell + pad)
            return "  ".join(out).rstrip()

        for i, cells in enumerate(body):
            if i == 0 and aligns is not None and len(body) > 1:
                push(line(cells), "bold")
                push("\n", "")
                push("  ".join("─" * w for w in widths), "rule")
            else:
                push(line(cells), "")
            push("\n", "")

    def _open_line(self, final: bool, push) -> bool | None:
        """处理行首的记号 返回 True 表示吃掉了东西要重来，None 表示还判不出来 """
        buf = self._buf
        nl = buf.find("\n")
        head = buf if nl < 0 else buf[:nl]
        stripped = head.lstrip(" ")
        indent = len(head) - len(stripped)

        # 半个 ``` 落在 chunk 边界上时，光看这几个字符判不出是不是围栏，
        # 得攒着等下一批 —— 不攒的话它会当普通文字走掉，行首状态一丢，
        # 收尾那行 ``` 就再也认不出来，后面全被当成代码块（踩过）
        if not final and nl < 0 and len(stripped) < len(self.FENCE) \
                and self.FENCE.startswith(stripped):
            return None

        if self.block:
            # 代码块里只关心收尾的那行 ```
            if stripped.startswith(self.FENCE):
                if nl < 0 and not final:
                    return None
                self.block = False
                self._buf = buf[nl + 1:] if nl >= 0 else ""
                return True
            self._sol = False
            return False

        if stripped.startswith("|"):
            # 表格行要整行到齐才收，列宽得看完所有行才知道
            if nl < 0 and not final:
                return None
            self._rows.append(head)
            self._buf = buf[nl + 1:] if nl >= 0 else ""
            return True
        if self._rows:
            if not stripped and nl < 0 and not final:
                return None     # 可能只是下一行的缩进还没到
            self._table(push)

        if stripped and set(stripped.rstrip()) <= set("-*_"):
            # 可能是 --- 分隔线，也可能是 - 列表或 **粗体 的开头，得看到下一个字符
            if nl < 0 and not final:
                return None
            bare = stripped.rstrip()
            if len(bare) >= 3 and len(set(bare)) == 1:
                push("─", "hr")
                self._buf = buf[nl + 1:] if nl >= 0 else ""
                return True

        if stripped.startswith(self.FENCE):
            if nl < 0 and not final:
                return None
            self.block = True
            self._buf = buf[nl + 1:] if nl >= 0 else ""     # 连语言名一起吞掉
            return True

        marks = len(stripped) - len(stripped.lstrip("#"))
        if 1 <= marks <= 6:
            if len(stripped) <= marks and not final and nl < 0:
                return None
            if stripped[marks:marks + 1] == " ":
                self.head = True
                self._buf = buf[indent + marks + 1:]
                self._sol = False
                return True

        if stripped[:1] in ("-", "*", "+"):
            if len(stripped) < 2 and not final and nl < 0:
                return None
            if stripped[1:2] == " ":
                push(" " * indent + DOT + " ", "mark")
                self._buf = buf[indent + 2:]
                self._sol = False
                return True

        if stripped[:1].isdigit():
            if nl < 0 and not final and len(stripped) < 5 and \
                    stripped.rstrip(".) ").isdigit():
                return None
            found = self._ORDERED.match(stripped)
            if found:
                push(" " * indent + found.group(0), "mark")
                self._buf = buf[indent + found.end():]
                self._sol = False
                return True

        if stripped[:1] == ">":
            if len(stripped) < 2 and not final and nl < 0:
                return None
            if stripped[1:2] in (" ", ""):
                self.quote = True
                push(" " * indent + "│ ")
                self._buf = buf[indent + 2:]
                self._sol = False
                return True

        self._sol = False
        return False

    def _inline(self, final: bool, push) -> bool:
        """行内：往前扫到下一个记号或者换行 返回 False 表示要攒着等下一批 """
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch == "\n":
                push(buf[:i])
                self.head = self.quote = False       # 标题和引用只管到行尾
                push("\n")
                self._buf = buf[i + 1:]
                self._sol = True
                return True
            if self.block:
                i += 1
                continue
            if ch == "`":
                push(buf[:i])
                self.code = not self.code
                self._buf = buf[i + 1:]
                return True
            if ch == "[" and not self.code:
                found = self._LINK.match(buf, i)
                if found:
                    # [文字](地址) → 文字 (地址)：地址留着，终端里多半能直接点
                    push(buf[:i] + found.group(1))
                    push(" (%s)" % found.group(2), "rule")
                    self._buf = buf[found.end():]
                    return True
                if not final and self._LINK_HEAD.fullmatch(buf, i):
                    push(buf[:i])
                    self._buf = buf[i:]
                    return False    # 可能是半个链接，攒着
            if ch == "*" and not self.code:
                if i + 1 >= len(buf):
                    if not final:
                        push(buf[:i])
                        self._buf = buf[i:]
                        return False    # 单个 * 可能是半个 **，攒着
                    break
                if buf[i + 1] == "*":
                    push(buf[:i])
                    self.bold = not self.bold
                    self._buf = buf[i + 2:]
                    return True
            i += 1
        push(buf)
        self._buf = ""
        return False


# ---- 流式输出 ----


class Stream:
    """turn 期间的输出：先转 spinner，出正文后逐字吐，正文缩进两格 """

    # 攒到这么长还没结束的词，肯定是路径或者 URL 这类整块的东西：
    # 一整行装不下也只能硬断，那就不必再攒
    WORD_MAX = 60
    # 状态行的重画间隔；IDLE_TICK 是没东西可放时的轮询间隔 ——
    # 转得比重画快，是为了正文一来就能接上，又不必按吐字的节奏空转
    STATUS_TICK = 0.12
    IDLE_TICK = 0.03

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
        self._md = MarkdownStream()
        self._md.limit = self._limit
        self._style = ""        # 当前这段文字的 markdown 样式
        self._newline = True    # 刚换过行（跟 _fresh_line 不同：折行不算）
        self._hang = 0          # 列表项折行后续行多缩这么多，对齐到记号后面
        self._line_start = 0    # 这一行正文从哪一列开始，用来判断「行上已经有东西」
        self._status = None     # (工具名, 还没收完的参数)，有它就替掉「思考中…」
        self._status_drawn = False
        self._reported = -1     # 参数进度报到第几档了，见 progress()
        self._status_at = 0.0
        self._lead = True       # 正文第一行还没开始：开头带一个 ⏺，跟工具行区分开
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
        # 报的是**原始 markdown**，不是渲染完的 ANSI：怎么画是前端的事
        events.emit("text", delta=text)
        if not self._tty:
            sys.stdout.write(text)
            sys.stdout.flush()
            return
        with self._lock:
            self._buf.append(text)

    def progress(self, name: str, partial: str) -> None:
        """工具参数还在流式生成 存下就走：上游每个 delta 都会调一次，
        在这儿拼标签等于对越来越长的参数反复做正则，拼留到重画时（12 Hz）做 """
        if events.active():
            # **按步长报，不是每个 delta 都报**：上游一个 delta 一次调用，
            # 一个长文件就是几千条事件，全推到浏览器只会把它卡住
            # 同样只报原始值，不在这儿拼标签（拼是 O(n²)，见上）
            step = len(partial) // PROGRESS_STEP
            if step != self._reported:
                self._reported = step
                events.emit("tool_progress", name=name, size=len(partial))
        if not self._tty:
            return
        with self._lock:
            self._status = (name, partial)

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

    # markdown 样式怎么上色 code 用主色；代码块正文不压暗——那是用户最要逐字看的东西，
    # 左边那条灰竖线已经把它跟正文分开了（0.42.1 之前是灰的，浅底上对比度不够）
    _PAINT = {"bold": lambda t: bold(t),
              "code": lambda t: paint("brown", t),
              "block": lambda t: t,
              "quote": lambda t: paint("grey", t),
              "rule": lambda t: paint("grey", t)}

    def _emit(self, chunk: str) -> None:
        self._runs(self._md.feed(chunk))

    def _runs(self, runs: list[tuple[str, str]]) -> None:
        for text, style in runs:
            if style == "hr":
                self._flush_word()
                self._rule()
                continue
            if style != self._style:
                # 样式一变就得把攒着的词吐出去，否则整个词会被后来的样式染上
                self._flush_word()
                self._style = style
            self._chars(text)
            if style == "mark":
                self._flush_word()
                self._hang = self._col - self._gutter_width
        sys.stdout.flush()

    def _rule(self) -> None:
        if not self._fresh_line:
            sys.stdout.write("\n")
            self._fresh_line = True
        sys.stdout.write(self._gutter() + paint("grey", "─" * (self._limit - 4)) + "\n")
        self._fresh_line, self._col, self._newline, self._hang = True, 0, True, 0

    def _chars(self, chunk: str) -> None:
        """按框宽折行并保持两格缩进 英文断在词边界，CJK 可以任意断 """
        for ch in chunk:
            if ch == "\n":
                self._flush_word()
                sys.stdout.write("\n")
                self._fresh_line, self._col = True, 0
                self._newline = True    # 换行之后的空格是缩进，折行之后的不是
                self._hang = 0
            elif ch.isspace():
                self._flush_word()
                if self._newline:
                    self._put(ch)       # 原文自己的缩进，代码块和嵌套列表全靠它
                elif not self._fresh_line and self._col + 1 <= self._limit:
                    # 行尾那个空格不写出去，否则会溢出一列、逼终端自己软换行
                    sys.stdout.write(ch)
                    self._col += 1
            elif get_cwidth(ch) > 1:
                self._flush_word()
                self._put(ch)
            else:
                # 西文攒成整词再放，折行才能落在词边界上 —— 只在右边界附近攒的话，
                # 一条长 URL 会先放出去半截、剩下那截再折到下一行（踩过）
                self._word += ch
                if len(self._word) >= self.WORD_MAX:
                    self._flush_word()

    def _flush_word(self) -> None:
        if self._word:
            word, self._word = self._word, ""
            self._put(word)

    def _put(self, text: str) -> None:
        wrapped = not self._newline     # 不是原文换行来的新行，就是折行的续行
        self._newline = False
        while text:
            if self._fresh_line:
                indent = self._hang if wrapped else 0
                sys.stdout.write(self._gutter() + " " * indent)
                self._col = self._gutter_width + indent
                self._fresh_line, self._line_start = False, self._col
            room = self._limit - self._col
            width_left = get_cwidth(text)
            if width_left <= room:
                sys.stdout.write(self._painted(text))
                self._col += width_left
                return
            if self._col > self._line_start:
                sys.stdout.write("\n")
                self._fresh_line, self._col, wrapped = True, 0, True
                continue
            # 一整行都装不下：超长路径或标识符，只能硬断
            cut, used = 0, 0
            for ch in text:
                cw = get_cwidth(ch)
                if used + cw > room:
                    break
                cut, used = cut + 1, used + cw
            cut = max(cut, 1)
            sys.stdout.write(self._painted(text[:cut]) + "\n")
            text = text[cut:]
            self._fresh_line, self._col = True, 0

    _gutter_width = 2

    def _gutter(self) -> str:
        """每行开头那两格 正文第一行放 ⏺（跟工具调用行同一套记号，颜色分开），
        代码块用一条竖线把它跟正文隔开，其余是两个空格 """
        if self._lead:
            self._lead = False
            return paint("brown", BULLET) + " "
        if self._style == "block":
            return paint("grey", "│ ")
        return "  "

    def _painted(self, text: str) -> str:
        """宽度是按原文算好的，这里只负责套色 —— 先上色再量宽度就会把转义序列算进去 """
        style = self._PAINT.get(self._style)
        return style(text) if style else text

    def _run(self) -> None:
        frame = 0
        pending = ""
        while True:
            with self._lock:
                pending += "".join(self._buf)
                self._buf.clear()
                done, status = self._done, self._status
            if pending:
                self._hide_status()
                # 上游是一阵一阵来的 按积压量的比例匀速放出去，别整块蹦；
                # 收尾时放快些，免得话说完了字还在慢慢爬
                share = 3 if done else 6
                take = max(4 if done else 1, -(-len(pending) // share))
                self._emit(pending[:take])
                pending = pending[take:]
                time.sleep(self._tick)
                continue
            if done:
                self._runs(self._md.flush())
                self._hide_status()
                return
            if self._should_draw(status) and \
                    time.monotonic() - self._status_at >= self.STATUS_TICK:
                frame = self._draw_status(frame, status)
            time.sleep(self.IDLE_TICK)

    def _should_draw(self, status) -> bool:
        """正文开始之后只有工具参数在生成时才画状态行 ——
        不然正文两个 chunk 之间的每个空档都会插进来一行，字被切成一段一段的 """
        if time.monotonic() - self._begin < self._delay:
            return False
        return bool(status) or not self.wrote_text

    def _draw_status(self, frame: int, status: str) -> int:
        if not self._status_drawn and not self._fresh_line:
            # 正文停在半行上，状态行得另起一行画，不然会把说过的话擦掉
            self._flush_word()
            sys.stdout.write("\n")
            self._fresh_line, self._col = True, 0
        elapsed = int(time.monotonic() - self._begin)
        tail = " (%ds · esc 中断)" % elapsed
        text = tool_progress(*status) if status else "思考中…"
        body = clip(text, max(8, self._limit - visible(tail) - 4))
        sys.stdout.write("\r\033[K" + paint("brown", FRAMES[frame]) + paint("grey", " " + body + tail))
        sys.stdout.flush()
        self._status_drawn = True
        self._status_at = time.monotonic()
        return (frame + 1) % len(FRAMES)

    def _hide_status(self) -> None:
        if not self._status_drawn:
            return      # 没画过就别擦：光标这会儿多半停在正文中间
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        self._status_drawn = False


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


def ask_text(name: str, args: dict) -> str:
    """确认时问什么 —— 要认得出对象 光问「要执行 Update 吗」等于让人盲签 """
    what = str(args.get(_headline_key(name, args) or "", "")).replace("\n", " ")
    verb = {"write_file": "写", "edit_file": "改", "read_file": "读",
            "web_fetch": "抓", "web_search": "搜", "remember": "记住", "bash": "执行",
            "git": "git"}.get(name, "执行")
    if name.startswith("mcp_"):
        verb = "调 " + tool_label(name) + " "
    return "要%s吗？" % verb if not what else "要%s %s 吗？" % (verb, what)


def confirm(question: str, *, simple: bool = False) -> str:
    """turn 中途问一个单键 stdin 这时已被 EscInterrupt 切到 cbreak，直接读一个字节

    返回 "yes" / "no" / "always" 不是终端（管道里跑）时一律拒绝——没人能回答
    simple 只给是非两个选项：交方案那种「以后都不用问」没有意义，还会把计划模式废掉
    """
    guard = _active_guard
    if guard is None or not guard.enabled:
        return "no"
    guard.pause()
    try:
        keys = "  [y] 好  [n] 不用" if simple else "  [y] 执行  [n] 跳过  [a] 本轮全放  [s] 记住"
        # 这行靠 \r\033[K 擦掉，只能擦一行 —— 撑到第二行就会在屏幕上留半截残影
        question = clip(question, max(12, width() - visible(keys) - 3))
        prompt = "  " + paint("amber", question) + paint("grey", keys)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            try:
                ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", "ignore").lower()
            except OSError:
                ch = "n"
            allowed = ("y", "n", "\x1b", "\r", "\n", "") if simple else (
                "y", "n", "a", "s", "\x1b", "\r", "\n", "")
            if ch in allowed:
                break
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        return {"y": "yes", "a": "always", "s": "remember"}.get(ch, "no")
    finally:
        guard.resume()


def ask_choice(question: str, options: list[str]) -> str:
    """turn 中途问用户一句 返回他的答复，空串表示他没回答

    选项走单键（stdin 这时是 cbreak，跟 confirm 同一条路），另给一个键进自由输入
    —— 模型摆出来的选项经常不是用户真正想说的那句，只能选就等于逼他将就

    非终端（管道、一次性任务）一律返回空串：没人能回答，让模型自己定
    """
    guard = _active_guard
    if guard is None or not guard.enabled:
        return ""
    options = [o for o in (options or []) if o.strip()][:MAX_OPTIONS]

    lines = []
    for piece in wrap(question, width() - 6) or [""]:
        lines.append("  " + paint("brown", "│ ") + bold(piece))
    for i, option in enumerate(options, 1):
        head = "  %s %s " % (paint("brown", "│"), paint("brown", "[%d]" % i))
        for n, piece in enumerate(wrap(option, width() - 10) or [""]):
            lines.append(head + piece if n == 0 else "  " + paint("brown", "│") + "     " + piece)
    print("\n".join(lines))

    keys = "  [1-%d] 选  [t] 自己打字  [esc] 你定" % len(options) if options else \
           "  [t] 打字回答  [esc] 你定"
    guard.pause()
    try:
        sys.stdout.write("  " + paint("grey", keys))
        sys.stdout.flush()
        picked = _read_choice(len(options))
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        if picked == "t":
            return _read_line()
        if picked.isdigit():
            return options[int(picked) - 1]
        return ""
    finally:
        guard.resume()


def _read_choice(count: int) -> str:
    """单键：数字选项、t 打字、esc/回车放弃 """
    allowed = {"t", "\x1b", "\r", "\n", ""} | {str(i) for i in range(1, count + 1)}
    while True:
        try:
            ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", "ignore").lower()
        except OSError:
            return ""
        if ch in allowed:
            return ch if ch in ("t",) or ch.isdigit() else ""


def _read_line() -> str:
    """自由输入 prompt_toolkit 自己会存好再改 termios，cbreak 下起它是安全的 """
    from prompt_toolkit import prompt as ask_line
    from prompt_toolkit.formatted_text import ANSI

    try:
        return ask_line(ANSI("  " + paint("brown", "› "))).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""


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
        self._idle = threading.Event()   # 读线程确实停在暂停分支里了
        self._thread = None

    def pause(self) -> None:
        """等读线程真的停下来再返回

        只 set 一个标志是不够的：线程这时多半正阻塞在 select 上，醒来之后会把
        下一个字节直接读走 —— 那个字节是接下来要问的人（confirm 的按键、
        prompt_toolkit 的 CPR 回复）的，被吃掉的表现是按键没反应、
        或者屏幕上冒出一句 "terminal doesn't support cursor position requests"
        """
        self._paused.set()
        self._idle.wait(timeout=0.3)

    def resume(self) -> None:
        self._idle.clear()
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
                self._idle.set()
                time.sleep(0.03)
                continue
            self._idle.clear()
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            # select 等的这 0.1 秒里可能已经暂停了，醒来必须再看一眼：
            # 这个字节是别人的，读走就没了
            if self._paused.is_set():
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
    "/theme": "界面主题：default / swiss（国际现代主义）",
    "/mode": "切换权限模式：默认 / 自动 / 计划 / 只读",
    "/allow": "查看或添加权限规则",
    "/verify": "查看或设置改完自动跑的校验命令",
    "/review": "让一个没看过过程的模型给这次改动打分",
    "/cost": "按任务看最近的 token 和花费",
    "/memory": "查看 / 添加 / 删除这个目录的长期记忆",
    "/hooks": "看在哪些时机会跑哪些外部命令",
    "/sandbox": "bash 沙箱：off / workspace / strict",
    "/jobs": "看 / 停掉挂在后台跑的命令",
    "/mcp": "看接入了哪些 MCP 服务器和它们的工具",
    "/undo": "退掉它最近一次写文件的改动，可以连着退",
    "/skills": "看有哪些技能和自定义命令，各自是哪来的",
    "/helper": "旁活（摘要、子 agent、网页提炼）用哪条便宜的配置",
    "/compact": "把历史压缩成一段摘要，省上下文",
    "/context": "查看并重新加载项目约定文件",
    "/clear": "清空对话历史和存盘，重新开始",
    "/exit": "退出",
}
# 工作区里的自定义斜杠命令：名字 -> 用途 由 main() 在载入时填进来
# 单独放一份而不是并进 SLASH：SLASH 是内置命令的清单，被覆盖了不好查
EXTRA_SLASH: dict[str, str] = {}
# /style 和 /mode 的取值由 agent 注入，用于补全
STYLE_NAMES: list[str] = []
MODE_NAMES: list[str] = []
# 模型清单随时会变，用回调取而不是快照
MODEL_NAMES = lambda: []


def _history_file() -> str:
    """输入历史存哪儿

    **这个文件要 0600。** 它是用户在输入框里打过的每一行原文 —— 跟对话存盘同一
    性质的东西，只是没人会想到它。prompt_toolkit 的 FileHistory 是按默认 umask
    建的（多半 0644），所以这里先把它建出来并收紧，再交给它去追加。
    （0.50.1 之前它一直是 0644 的。）
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    folder = os.path.join(base, "feiyu")
    files.private_dir(folder)
    path = os.path.join(folder, "history")
    try:
        os.close(os.open(path, os.O_CREAT | os.O_APPEND, 0o600))
        os.chmod(path, 0o600)      # 已经存在的那份是老权限，也要收
    except OSError:
        pass
    return path


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
                    for name, desc in {**SLASH, **EXTRA_SLASH}.items():
                        if name.startswith(text):
                            yield Completion(name, start_position=-len(text), display_meta=desc)
                elif cmd in ("/style", "/mode", "/theme") and " " not in arg:
                    pool = {"/style": STYLE_NAMES, "/mode": MODE_NAMES,
                            "/theme": list(THEMES)}[cmd]
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
            return paint("grey", CORNERS[0] + "─" * (w - 2) + CORNERS[1])
        return paint("grey", CORNERS[0] + "─" * fill + " " + note + " ─" + CORNERS[1])

    def bottom_border(self) -> str:
        return paint("grey", CORNERS[2] + "─" * (width() - 2) + CORNERS[3])

    def ask(self) -> str:
        self.buffer.reset()
        return self.app.run()
