import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .agent import Feiyu
from .providers import PROVIDERS

BANNER = "\033[1mFeiyu\033[0m — 终端编程助手 输入任务开始，输入 /exit 退出 \n"


def main() -> int:
    parser = argparse.ArgumentParser(prog="feiyu", description="Feiyu 编程助手")
    parser.add_argument(
        "-w",
        "--workspace",
        default=".",
        help="Feiyu 工作的目录（默认当前目录） ",
    )
    parser.add_argument(
        "-p",
        "--provider",
        default="anthropic",
        choices=sorted(PROVIDERS),
        help="选择模型厂商（默认 anthropic） ",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="指定具体模型（默认用该厂商的默认模型） ",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="覆盖接口地址（比如 Kimi 的国内站） ",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="直接跑一个任务，跑完就退出 ",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"错误：{workspace} 不是一个目录", file=sys.stderr)
        return 2

    try:
        agent = Feiyu(
            workspace,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if args.prompt:
        _run_turn(agent, " ".join(args.prompt))
        return 0

    _show_logo()
    print(BANNER, end="")
    print(f"\033[2m工作目录：{workspace}  ·  模型：{args.provider}\033[0m\n")
    while True:
        try:
            user_input = input("\033[1m›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            return 0
        _run_turn(agent, user_input)
        print()
    return 0


def _show_logo() -> None:
    logo = Path(__file__).parent / "logo.png"
    if not logo.exists():
        return
    try:
        if os.environ.get("KITTY_WINDOW_ID") and shutil.which("kitty"):
            subprocess.run(["kitty", "+kitten", "icat", "--align", "left", str(logo)], check=False)
        elif os.environ.get("TERM_PROGRAM") == "iTerm.app":
            _show_iterm2(logo)
        elif shutil.which("img2sixel"):
            subprocess.run(["img2sixel", str(logo)], check=False)
        else:
            _show_blocks(logo)
    except Exception:
        pass


def _show_iterm2(logo: Path) -> None:
    import base64

    data = base64.b64encode(logo.read_bytes()).decode()
    sys.stdout.write(f"\033]1337;File=inline=1;width=18;preserveAspectRatio=1:{data}\a\n")
    sys.stdout.flush()


def _show_blocks(logo: Path, cols: int = 36) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    if os.name == "nt":
        os.system("")
    img = Image.open(logo).convert("RGBA")
    w, h = img.size
    rows = max(1, round(h / w * cols / 2))
    img = img.resize((cols, rows * 2), Image.LANCZOS)
    px = img.load()
    lines = []
    for r in range(rows):
        cells = []
        for c in range(cols):
            tr, tg, tb, ta = px[c, r * 2]
            br, bg, bb, ba = px[c, r * 2 + 1]
            top, bot = ta >= 128, ba >= 128
            if not top and not bot:
                cells.append("\033[0m ")
            elif top and bot:
                cells.append(f"\033[38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}m▀")
            elif top:
                cells.append(f"\033[0;38;2;{tr};{tg};{tb}m▀")
            else:
                cells.append(f"\033[0;38;2;{br};{bg};{bb}m▄")
        lines.append("".join(cells) + "\033[0m")
    print("\n".join(lines))


def _run_turn(agent: Feiyu, prompt: str) -> None:
    try:
        agent.send(prompt)
    except KeyboardInterrupt:
        print("\n\033[2m[已中断]\033[0m")
    except Exception as exc:
        print(f"\n\033[31m错误：{exc}\033[0m", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
