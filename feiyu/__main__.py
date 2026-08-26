import argparse
import sys
from pathlib import Path

from . import pricing, ui
from . import __version__
from .agent import STYLES, Feiyu
from .providers import PROVIDERS, detect_provider, missing_keys_hint


def main() -> int:
    parser = argparse.ArgumentParser(prog="feiyu", description="Feiyu 编程助手")
    parser.add_argument("-V", "--version", action="version", version=f"feiyu {__version__}")
    parser.add_argument(
        "-w",
        "--workspace",
        default=".",
        help="Feiyu 工作的目录（默认当前目录） ",
    )
    parser.add_argument(
        "-p",
        "--provider",
        default=None,
        choices=sorted(PROVIDERS),
        help="选择模型厂商（默认挑第一个设了 API key 的） ",
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

    provider = args.provider or detect_provider()
    if provider is None:
        print(f"错误：没有可用的 API key，请设置其中一个：{missing_keys_hint()}", file=sys.stderr)
        return 2

    try:
        agent = Feiyu(
            workspace,
            provider=provider,
            model=args.model,
            base_url=args.base_url,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if args.prompt:
        _run_turn(agent, " ".join(args.prompt))
        return 0
    if not sys.stdin.isatty():
        return _piped(agent)

    ui.STYLE_NAMES[:] = list(STYLES)
    ui.enter_screen()
    print(ui.welcome(workspace, provider, agent.model, __version__, STYLES[agent.style][0]))
    print()

    def status() -> str:
        if not agent.tokens:
            return provider
        spent = agent.cost
        # 价格表里没这个模型就别瞎报钱，退回显示 token 数
        money = pricing.yuan(spent) if spent is not None else "计费 " + ui.tokens(agent.tokens)
        return "%s  ·  上下文 %s  ·  %s" % (provider, ui.tokens(agent.context), money)

    inbox = ui.InputBox(status)
    while True:
        try:
            text = inbox.ask().strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            return 0
        if not text:
            continue
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd in ("/exit", "/quit"):
                return 0
            if cmd == "/help":
                print(_help())
            elif cmd == "/clear":
                agent.reset()
                print(ui.notice("对话历史已清空"))
            elif cmd == "/model":
                print(_model(agent, arg))
            elif cmd == "/style":
                print(_style(agent, arg))
            else:
                print(ui.error(f"未知命令 {cmd}，输入 /help 看可用命令"))
            print()
            continue
        print()
        _run_turn(agent, text)
        print()


def _model(agent: Feiyu, arg: str) -> str:
    if arg:
        if arg == agent.model:
            return ui.notice(f"已经在用 {arg} 了")
        agent.set_model(arg)
        return ui.notice(f"模型已切到 {arg}，对话历史保留")
    rows = [ui.notice("当前  ") + ui.bold(agent.model)]
    names = agent.list_models()
    if names:
        rows.append("")
        for n in names:
            tick = ui.paint("brown", " ●") if n == agent.model else "  "
            rows.append("   " + tick + " " + (ui.bold(n) if n == agent.model else ui.paint("grey", n)))
    else:
        rows.append(ui.notice("（拉不到模型列表）"))
    rows.append("")
    rows.append(ui.notice("/model <名字> 切换"))
    return "\n".join(rows)


def _style(agent: Feiyu, arg: str) -> str:
    if arg:
        if arg not in STYLES:
            return ui.error("没有 %s 这个风格，可选：%s" % (arg, "、".join(STYLES)))
        agent.set_style(arg)
        return ui.notice("回答风格已切到 " + STYLES[arg][0])
    rows = []
    for key, (label, _) in STYLES.items():
        tick = ui.paint("brown", " ●") if key == agent.style else "  "
        name = ui.bold(key.ljust(12)) if key == agent.style else ui.paint("grey", key.ljust(12))
        rows.append("   " + tick + " " + name + ui.paint("grey", label))
    rows.append("")
    rows.append(ui.notice("/style <名字> 切换 只影响 agent 怎么说话，不动模型 "))
    return "\n".join(rows)


def _help() -> str:
    rows = [ui.notice("命令")]
    for cmd, desc in ui.SLASH.items():
        rows.append("    " + ui.bold(cmd.ljust(8)) + ui.paint("grey", desc))
    rows.append("")
    rows.append(ui.notice("按键    ⏎ 发送 · \\⏎ 换行 · esc 中断当前回答 · ctrl-d 退出"))
    rows.append(ui.notice("版本    feiyu " + __version__))
    return "\n".join(rows)


def _piped(agent: Feiyu) -> int:
    """stdin 不是终端时（管道、脚本）：一行一个任务，不画框 """
    for line in sys.stdin:
        task = line.strip()
        if task and task not in ("/exit", "/quit"):
            _run_turn(agent, task)
    return 0


def _run_turn(agent: Feiyu, prompt: str) -> None:
    guard = ui.EscInterrupt()
    try:
        with guard:
            agent.send(prompt)
    except KeyboardInterrupt:
        print(ui.warn("已中断"))
    except Exception as exc:
        print(ui.error(f"错误：{exc}"))


if __name__ == "__main__":
    raise SystemExit(main())
