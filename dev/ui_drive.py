"""pty + pyte：把主程序接上伪终端，喂键、回 CPR、逐屏回放，最后渲染成 PNG

    python dev/ui_drive.py [输出目录] [--theme swiss] [--cols 80]

需要 pyte（pip install pyte）和 pillow 两个要点：
- 喂键和读屏必须在同一个 select 循环里（喂键期间也得回 CPR，不然 prompt_toolkit 认为终端高度未知）
- 框线和块字符直接画线段/矩形，别指望字体里有这些字形；字符格 1:2
工作区默认 dev/ws，每次从头铺
"""
import argparse, fcntl, os, pty, select, shutil, struct, subprocess, sys, termios, time
import pyte
from PIL import Image, ImageDraw, ImageFont

S = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("out", nargs="?", default=os.path.join(S, "shots"))
ap.add_argument("--theme", default="default")
ap.add_argument("--cols", type=int, default=100)
ARGS = ap.parse_args()
COLS, ROWS = ARGS.cols, 42


def _font(pattern):
    try:
        return subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


MONO = _font("JetBrains Mono") or _font("monospace")
MONO_B = _font("JetBrains Mono:bold") or MONO
CJK = _font("Source Han Sans CN") or _font("Noto Sans CJK SC") or MONO
CW, CH = 11, 22
BG, FG = (0xFB, 0xF9, 0xF5), (0x14, 0x14, 0x14)

def render(screen, path):
    img = Image.new("RGB", (COLS * CW, ROWS * CH), BG)
    d = ImageDraw.Draw(img)
    fm = ImageFont.truetype(MONO, 17); fb = ImageFont.truetype(MONO_B, 17); fc = ImageFont.truetype(CJK, 17)
    for y in range(ROWS):
        for x in range(COLS):
            c = screen.buffer[y][x]
            ch = c.data
            fg = FG if c.fg == "default" else tuple(int(c.fg[i:i+2], 16) for i in (0, 2, 4)) if len(c.fg) == 6 else FG
            bg = None if c.bg == "default" else tuple(int(c.bg[i:i+2], 16) for i in (0, 2, 4)) if len(c.bg) == 6 else None
            if c.reverse:
                fg, bg = (bg or BG), fg
            x0, y0 = x * CW, y * CH
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
            font = fc if wide else (fb if c.bold else fm)
            d.text((x0, y0 + 1), ch, font=font, fill=fg)
            if c.bold and wide:
                d.text((x0 + 1, y0 + 1), ch, font=font, fill=fg)
    img.save(path)

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
    os.makedirs(ARGS.out, exist_ok=True)
    ws = _fresh_ws()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", COLORTERM="truecolor", LANG="zh_CN.UTF-8",
                          COLUMNS=str(COLS), LINES=str(ROWS), FEIYU_UI_THEME=ARGS.theme, FEIYU_UI_WS=ws)
        os.execv(sys.executable, [sys.executable, os.path.join(S, "ui_runner.py")])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    screen = pyte.Screen(COLS, ROWS); stream = pyte.ByteStream(screen)
    keys = list("看看这个项目，然后给 calc.py 加个 mul 函数，改完跑一遍测试") + ["\r"]
    t0 = time.time(); last = t0; last_confirm = 0; sent_prompt = False; confirmed = False; shots = 0; done_final = False
    texts = []
    def text():
        return "\n".join(screen.display)
    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        now = time.time()
        if r:
            try: data = os.read(fd, 65536)
            except OSError: break
            if not data: break
            stream.feed(data); last = now
            if b"\x1b[6n" in data:
                os.write(fd, ("\x1b[%d;%dR" % (screen.cursor.y + 1, screen.cursor.x + 1)).encode())
        idle = now - last
        scr = text()
        if not sent_prompt and idle > 1.0 and now - t0 > 1.5:
            render(screen, os.path.join(ARGS.out, "1_welcome.png")); texts.append(("welcome", scr))
            sent_prompt = True
        elif sent_prompt and keys and idle > 0.05:
            os.write(fd, keys.pop(0).encode()); time.sleep(0.01)
        elif "[y]" in (scr.rstrip().splitlines() or [""])[-1] and idle > 0.8 and now - last_confirm > 1.5:
            if not confirmed:
                render(screen, os.path.join(ARGS.out, "2_confirm.png")); texts.append(("confirm", scr))
            os.write(fd, b"y"); confirmed = True; last_confirm = now; last = now
        elif confirmed and not done_final and idle > 2.5 and "42" in scr:
            render(screen, os.path.join(ARGS.out, "3_final.png")); texts.append(("final", scr))
            done_final = True
            os.write(fd, b"\x04"); break
        if now - t0 > 60:
            render(screen, os.path.join(ARGS.out, "timeout.png")); texts.append(("timeout", scr)); break
    time.sleep(0.3)
    try: os.kill(pid, 9)
    except OSError: pass
    with open(os.path.join(ARGS.out, "screens.txt"), "w") as f:
        for name, scr in texts:
            f.write("===== %s =====\n%s\n" % (name, scr))
    print("shots:", [n for n, _ in texts])

main()
