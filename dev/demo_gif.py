"""录一段演示 GIF 给 README 用：pty + pyte 跑 ui_runner.py（假后端，不花钱），
按屏幕变化抓帧，渲染成 PNG，最后交给 ImageMagick 拼成 GIF

    python dev/demo_gif.py docs/demo.gif [--cols 84] [--rows 28] [--lang en]

跟 ui_drive.py 同一套要点：喂键和读屏在同一个 select 循环里、要回 CPR、框线和块字符画线段
需要 pyte、pillow 和 magick
"""
import argparse, fcntl, os, pty, select, shutil, struct, subprocess, sys, tempfile, termios, time
import pyte
from PIL import Image, ImageDraw, ImageFont

S = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("out", nargs="?", default=os.path.join(S, "demo.gif"))
ap.add_argument("--theme", default="default")
ap.add_argument("--cols", type=int, default=84)
ap.add_argument("--rows", type=int, default=28)
ap.add_argument("--lang", default="en")
ARGS = ap.parse_args()
COLS, ROWS = ARGS.cols, ARGS.rows
PROMPT = {"en": "add a mul function to calc.py and run the tests",
          "zh": "给 calc.py 加个 mul 函数，改完跑一遍测试"}[ARGS.lang]


def _font(pattern):
    try:
        return subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


MONO = _font("JetBrains Mono") or _font("monospace")
MONO_B = _font("JetBrains Mono:bold") or MONO
CJK = _font("Source Han Sans CN") or _font("Noto Sans CJK SC") or MONO
# ⏺ ⎿ ⏎ 这几个记号 JetBrains Mono 没有，渲染成豆腐块；Adwaita Mono 全有
SYM = _font("Adwaita Mono") or _font("Noto Sans Symbols 2") or MONO
CW, CH = 11, 22
PAD = 14
BG, FG = (0xFB, 0xF9, 0xF5), (0x14, 0x14, 0x14)
FM = ImageFont.truetype(MONO, 17); FB = ImageFont.truetype(MONO_B, 17); FC = ImageFont.truetype(CJK, 17)
FS = ImageFont.truetype(SYM, 17)


def _rgb(v, default):
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) if len(v) == 6 else default


def render(screen):
    img = Image.new("RGB", (COLS * CW + PAD * 2, ROWS * CH + PAD * 2), BG)
    d = ImageDraw.Draw(img)
    for y in range(ROWS):
        for x in range(COLS):
            c = screen.buffer[y][x]
            ch = c.data
            fg = FG if c.fg == "default" else _rgb(c.fg, FG)
            bg = None if c.bg == "default" else _rgb(c.bg, None)
            if c.reverse:
                fg, bg = (bg or BG), fg
            x0, y0 = PAD + x * CW, PAD + y * CH
            if bg:
                d.rectangle([x0, y0, x0 + CW - 1, y0 + CH - 1], fill=bg)
            if not ch or ch == " ":
                continue
            if ch in "─│╭╮╰╯├┤┬┴┼":
                mx, my = x0 + CW // 2, y0 + CH // 2
                if ch in "─├┤┬┴┼": d.line([x0, my, x0 + CW, my], fill=fg)
                if ch in "│├┤┼": d.line([mx, y0, mx, y0 + CH], fill=fg)
                if ch == "╭": d.line([mx, my, x0 + CW, my], fill=fg); d.line([mx, my, mx, y0 + CH], fill=fg)
                if ch == "╮": d.line([x0, my, mx, my], fill=fg); d.line([mx, my, mx, y0 + CH], fill=fg)
                if ch == "╰": d.line([mx, my, x0 + CW, my], fill=fg); d.line([mx, y0, mx, my], fill=fg)
                if ch == "╯": d.line([x0, my, mx, my], fill=fg); d.line([mx, y0, mx, my], fill=fg)
                if ch == "┬": d.line([mx, my, mx, y0 + CH], fill=fg)
                if ch == "┴": d.line([mx, y0, mx, my], fill=fg)
                continue
            if ch in "▀▄█":
                top = y0 if ch in "▀█" else y0 + CH // 2
                bot = y0 + CH if ch in "▄█" else y0 + CH // 2
                d.rectangle([x0, top, x0 + CW - 1, bot], fill=fg); continue
            wide = ord(ch) > 0x2E80
            font = FC if wide else FS if ord(ch) > 0x2000 else (FB if c.bold else FM)
            d.text((x0, y0 + 1), ch, font=font, fill=fg)
            if c.bold and wide:
                d.text((x0 + 1, y0 + 1), ch, font=font, fill=fg)
    return img


def _fresh_ws():
    ws = os.path.join(S, "ws")
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(ws)
    with open(os.path.join(ws, "calc.py"), "w") as f:
        f.write("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    with open(os.path.join(ws, "test_calc.py"), "w") as f:
        f.write("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n")
    with open(os.path.join(ws, "CLAUDE.md"), "w") as f:
        f.write("# demo\n\nverify: python -m unittest -q\n")
    return ws


def main():
    ws = _fresh_ws()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", COLORTERM="truecolor", LANG="zh_CN.UTF-8",
                          COLUMNS=str(COLS), LINES=str(ROWS), FEIYU_UI_THEME=ARGS.theme,
                          FEIYU_UI_WS=ws, FEIYU_UI_LANG=ARGS.lang)
        os.execv(sys.executable, [sys.executable, os.path.join(S, "ui_runner.py")])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    screen = pyte.Screen(COLS, ROWS); stream = pyte.ByteStream(screen)
    keys = list(PROMPT) + ["\r"]
    frames = []          # (时刻, 画面文本, 图)
    t0 = time.time(); last = t0; last_confirm = 0; last_shot = 0; last_text = None
    sent_prompt = False; confirmed = False; started_typing = 0
    tmp = tempfile.mkdtemp(prefix="feiyu-gif-")

    def shot(force=False):
        nonlocal last_shot, last_text
        now = time.time()
        scr = "\n".join(screen.display)
        if (scr != last_text and now - last_shot >= 0.06) or force:
            frames.append((now, render(screen)))
            last_shot, last_text = now, scr

    while True:
        r, _, _ = select.select([fd], [], [], 0.03)
        now = time.time()
        if r:
            try: data = os.read(fd, 65536)
            except OSError: break
            if not data: break
            stream.feed(data); last = now
            if b"\x1b[6n" in data:
                os.write(fd, ("\x1b[%d;%dR" % (screen.cursor.y + 1, screen.cursor.x + 1)).encode())
        idle = now - last
        scr = "\n".join(screen.display)
        shot()
        if not sent_prompt and idle > 1.0 and now - t0 > 1.5:
            sent_prompt = True; started_typing = now
        elif sent_prompt and keys and now - started_typing > 0.9 and idle > 0.04:
            os.write(fd, keys.pop(0).encode()); time.sleep(0.045)
        elif "[y]" in (scr.rstrip().splitlines() or [""])[-1] and idle > 1.2 and now - last_confirm > 1.5:
            os.write(fd, b"y"); confirmed = True; last_confirm = now; last = now
        elif confirmed and idle > 3.0 and "42" in scr:
            shot(force=True)
            os.write(fd, b"\x04"); break
        if now - t0 > 90:
            print("timeout", file=sys.stderr); break
    time.sleep(0.3)
    try: os.kill(pid, 9)
    except OSError: pass

    # 每帧的停留时间就是它到下一帧的间隔；最后一帧多停一会儿
    argv = ["magick", "-loop", "0"]
    for i, (t, img) in enumerate(frames):
        path = os.path.join(tmp, "f%04d.png" % i); img.save(path)
        nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 3.5
        delay = max(2, int(round((nxt - t) * 100)))
        argv += ["-delay", str(delay), path]
    argv += ["-layers", "optimize", ARGS.out]
    subprocess.run(argv, check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("%d frames, %.1fs, %s (%d KB)" % (len(frames), frames[-1][0] - frames[0][0], ARGS.out,
                                             os.path.getsize(ARGS.out) // 1024))


main()
