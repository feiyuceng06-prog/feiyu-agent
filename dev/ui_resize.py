"""终端缩放夹具：主程序挂假后端，跑在 **kitty 自己的屏幕引擎**里，跑完一轮之后反复改窗口宽度

    kitty +runpy "import runpy; runpy.run_path('dev/ui_resize.py', run_name='__main__')" [输出目录]

为什么不用 ui_drive.py 那套 pyte：pyte 改尺寸时只是截断 / 补空格，**不重排**；而 kitty、
gnome-terminal 这些会把超宽的行折到下一行（缩窄）或者把折过的接回去（放宽）——
「窗口一缩放界面就乱」恰恰出在这一步上，pyte 量不出来。kitty 的 Screen 是个 C 扩展，
`kitty +runpy` 能直接拿来用（跟用户终端是同一份代码），不用开窗口。

子进程用仓库的 .venv 跑（prompt_toolkit 在那儿），这个夹具自己只要 kitty 和 pillow。
输出：每一步一张 PNG（回滚区 + 当前屏）和 screens.txt；最后检查有没有边框碎片留在屏上。
"""
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import unicodedata

from kitty.config import defaults
from kitty.fast_data_types import Screen, set_options
from kitty.options.types import Options

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "shots-resize")
ROWS = 40
PY = os.path.join(ROOT, ".venv", "bin", "python")


class Callbacks:
    def __init__(self):
        self.out = bytearray()           # 终端回给程序的：CPR、设备属性之类

    def write(self, data):
        self.out += bytes(data)

    def __getattr__(self, name):         # 标题、铃声之类一律当没发生
        return lambda *a, **k: None


def make_screen(cols):
    set_options(Options(dict(defaults._asdict(), scrollback_pager_history_size=1024, click_interval=0.5)))
    cb = Callbacks()
    return Screen(cb, ROWS, cols, 5000, 10, 20, 0, cb), cb


def feed(screen, data):
    view = memoryview(data)
    while view:
        dest = screen.test_create_write_buffer()
        n = screen.test_commit_write_buffer(view, dest)
        view = view[n:]
        screen.test_parse_written_data()


def rows(screen):
    """回滚区 + 当前屏，按**显示**行（折过的行是两行），每行是 ANSI"""
    hist = screen.historybuf
    out = [hist.line(i).as_ansi() for i in reversed(range(hist.count))]
    out += [screen.line(y).as_ansi() for y in range(screen.lines)]
    return out


_SGR = re.compile(r"\x1b\[([0-9;:]*)m")


def cells(line):
    """一行 ANSI → [(字, 颜色, 粗, 宽)]，够画图用的那一小撮 SGR"""
    fg, bold, out, pos = None, False, [], 0
    for m in _SGR.finditer(line + "\x1b[m"):
        for ch in line[pos:m.start()]:
            wide = unicodedata.east_asian_width(ch) in "WF"
            out.append((ch, fg, bold, wide))
        pos = m.end()
        parts = [int(p) if p.isdigit() else 0 for p in re.split("[;:]", m.group(1) or "0")]
        i = 0
        while i < len(parts):
            p = parts[i]
            if p == 0:
                fg, bold = None, False
            elif p == 1:
                bold = True
            elif p == 22:
                bold = False
            elif p == 39:
                fg = None
            elif p == 38 and i + 4 < len(parts) and parts[i + 1] == 2:
                fg = tuple(parts[i + 2:i + 5])
                i += 4
            elif p == 38 and i + 2 < len(parts) and parts[i + 1] == 5:
                i += 2
            i += 1
    return out


def render(lines, cols, path):
    from PIL import Image, ImageDraw, ImageFont

    def font(pattern):
        try:
            return subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    cw, ch = 11, 22
    bg, ink = (0xFB, 0xF9, 0xF5), (0x14, 0x14, 0x14)
    mono = ImageFont.truetype(font("JetBrains Mono") or font("monospace"), 17)
    mono_b = ImageFont.truetype(font("JetBrains Mono:bold") or font("monospace"), 17)
    cjk = ImageFont.truetype(font("Source Han Sans CN") or font("Noto Sans CJK SC"), 17)
    img = Image.new("RGB", (cols * cw, max(1, len(lines)) * ch), bg)
    d = ImageDraw.Draw(img)
    for y, line in enumerate(lines):
        x = 0
        for c, fg, bold, wide in cells(line):
            color = fg or ink
            x0, y0 = x * cw, y * ch
            if c in "─│╭╮╰╯":
                mx, my = x0 + cw // 2, y0 + ch // 2
                if c == "─": d.line([x0, my, x0 + cw, my], fill=color)
                if c == "│": d.line([mx, y0, mx, y0 + ch], fill=color)
                if c == "╭": d.line([mx, my, x0 + cw, my], fill=color); d.line([mx, my, mx, y0 + ch], fill=color)
                if c == "╮": d.line([x0, my, mx, my], fill=color); d.line([mx, my, mx, y0 + ch], fill=color)
                if c == "╰": d.line([mx, my, x0 + cw, my], fill=color); d.line([mx, y0, mx, my], fill=color)
                if c == "╯": d.line([x0, my, mx, my], fill=color); d.line([mx, y0, mx, my], fill=color)
            elif c.strip():
                d.text((x0, y0 + 1), c, font=cjk if wide else (mono_b if bold else mono), fill=color)
            x += 2 if wide else 1
    img.save(path)


def plain(line):
    return _SGR.sub("", line)


def damage(lines):
    """屏幕底部（输入框那一片）有没有残影：输入提示行只能有一个，框线不能只剩半截
    （有头没尾、有尾没头 —— 那是终端把旧框线折成两截之后没擦干净的那一半）"""
    body = [plain(x).strip() for x in lines]
    while body and not body[-1]:          # 屏幕没满的时候底下全是空行
        body.pop()
    tail = body[-12:]
    bad = []
    prompts = [t for t in tail if t.startswith("│ >")]
    if len(prompts) != 1:
        bad.append("输入提示行有 %d 个" % len(prompts))
    for t in tail:
        if (t.endswith(("╮", "╯")) and not t.startswith(("╭", "╰"))) or \
           (t.startswith(("╭", "╰")) and not t.endswith(("╮", "╯"))):
            bad.append("半截框线「%s」" % t[:30])
    return bad


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    ws = os.path.join(OUT, "ws")
    os.makedirs(ws)
    with open(os.path.join(ws, "calc.py"), "w") as f:
        f.write("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    with open(os.path.join(ws, "test_calc.py"), "w") as f:
        f.write("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n")
    cols = 100
    screen, cb = make_screen(cols)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", COLORTERM="truecolor", LANG="zh_CN.UTF-8",
                          FEIYU_UI_THEME=os.environ.get("FEIYU_UI_THEME", "anthropic"), FEIYU_UI_WS=ws)
        for name in ("COLUMNS", "LINES"):
            os.environ.pop(name, None)
        os.execv(PY, [PY, os.path.join(HERE, "ui_runner.py")])

    def winsize(c):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, c, 0, 0))

    winsize(cols)
    keys = list("给 calc.py 加个 mul 函数，改完跑一遍测试") + ["\r"]
    plan = [("3_shrink60", 60), ("4_grow120", 120), ("5_shrink50", 50), ("6_back100", 100)]
    sent_at = 0.0
    t0 = last = time.time()
    stage, shots, report = "start", [], []

    def shot(name):
        lines = rows(screen)
        render(lines[-70:], screen.columns, os.path.join(OUT, name + ".png"))
        shots.append((name, [plain(x) for x in lines]))
        bad = damage(lines)
        report.append("%s（%d 列）：%s" % (name, screen.columns, "干净" if not bad else "；".join(bad)))

    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        now = time.time()
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            feed(screen, data)
            last = now
        if cb.out:
            os.write(fd, bytes(cb.out))
            del cb.out[:]
        idle = now - last
        text = "\n".join(plain(x) for x in rows(screen)[-ROWS:])
        tail = (text.rstrip().splitlines() or [""])[-1]
        if stage == "start" and idle > 1.2 and now - t0 > 1.5:
            shot("1_welcome")
            stage = "typing"
        elif stage == "typing" and keys and idle > 0.05:
            os.write(fd, keys.pop(0).encode())
            time.sleep(0.01)
        elif stage == "typing" and not keys:
            stage, sent_at = "running", now
        elif stage == "running" and screen.columns == cols and now - sent_at > 0.2:
            # 正在流式输出的时候缩窄一次：状态行、正文折行都得跟上
            screen.resize(ROWS, 84)
            winsize(84)
        elif stage == "running" and "[y]" in tail and idle > 0.6:
            os.write(fd, b"y")
            last = now
        elif stage == "running" and "42" in text and idle > 2.0:
            shot("2_done84")
            stage = "resize"
        elif stage == "resize" and idle > 1.2:
            if not plan:
                break
            name, c = plan.pop(0)
            screen.resize(ROWS, c)
            winsize(c)
            last = now
            stage = "settle:" + name
        elif stage.startswith("settle:") and idle > 1.2:
            shot(stage.split(":", 1)[1])
            stage = "resize"
        if now - t0 > 90:
            shot("timeout")
            break
    os.write(fd, b"\x04")
    time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    with open(os.path.join(OUT, "screens.txt"), "w") as f:
        for name, lines in shots:
            f.write("===== %s =====\n%s\n" % (name, "\n".join(lines[-ROWS:])))
    print("\n".join(report))


if __name__ == "__main__":
    main()
