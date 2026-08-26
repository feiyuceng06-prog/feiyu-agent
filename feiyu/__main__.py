import argparse
import sys
from pathlib import Path

from . import keys, permissions, pricing, session, ui
from . import __version__
from .agent import CONTEXT_FILES, MODES, STYLES, Feiyu


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
        "-r",
        "--resume",
        action="store_true",
        help="接着上次在这个目录里的对话 ",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="启动时用 /key 清单里的哪一条（默认上次用的） ",
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

    chosen = args.model or keys.pick_default()
    if args.model and args.model not in keys.load():
        print(f"错误：/key 清单里没有 {args.model}", file=sys.stderr)
        return 2
    try:
        agent = Feiyu(workspace, model=chosen)
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if args.prompt:
        _run_turn(agent, " ".join(args.prompt))
        return 0
    if not sys.stdin.isatty():
        return _piped(agent)

    ui.STYLE_NAMES[:] = list(STYLES)
    ui.MODE_NAMES[:] = list(MODES)
    ui.MODEL_NAMES = lambda: list(keys.load())
    agent.approve = lambda name, args: ui.confirm("要执行 %s 吗？" % ui.tool_label(name))
    ui.enter_screen()
    saved = session.load(workspace)
    resumed = bool(args.resume and saved and agent.restore(saved))

    project = f"{agent.context_file}（{len(agent.context_text)} 字）" if agent.context_text else "无"
    print(ui.welcome(workspace, agent.name, agent.model, __version__,
                     STYLES[agent.style][0], MODES[agent.mode][0], project))
    print()
    if not agent.ready:
        print(ui.warn("还没有模型 用 /key add 添加一个（接口地址 + 密钥 + 模型 id）"))
        print()
    elif resumed:
        print(ui.notice("已接上上次的对话（%s）/clear 可以重新开始" % session.describe(saved)))
        print()
    elif saved:
        print(ui.notice("这个目录有上次的对话（%s）想接着聊就 feiyu --resume" % session.describe(saved)))
        print()

    def status() -> str:
        # 显示具体模型名而不是厂商——厂商名说明不了在用哪个模型
        tag = ui.mode_tag(agent.mode, MODES[agent.mode][0])
        if not agent.ready:
            return "%s  ·  %s" % (tag, "未设置模型")
        if not agent.tokens:
            return "%s  ·  %s" % (tag, agent.model)
        spent = agent.cost
        # 价格表里没这个模型就别瞎报钱，退回显示 token 数
        money = pricing.yuan(spent) if spent is not None else "计费 " + ui.tokens(agent.tokens)
        return "%s  ·  %s  ·  上下文 %s  ·  %s" % (
            ui.mode_tag(agent.mode, MODES[agent.mode][0]), agent.model, ui.tokens(agent.context), money)

    inbox = ui.InputBox(status, on_cycle_mode=agent.cycle_mode)
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
            if cmd == "/exit":
                return 0
            if cmd == "/help":
                print(_help())
            elif cmd == "/clear":
                agent.reset()
                session.drop(workspace)
                print(ui.notice("对话历史已清空，存盘也删了"))
            elif cmd == "/model":
                print(_model(agent, arg))
            elif cmd == "/key":
                print(_key(agent, arg))
            elif cmd == "/mode":
                print(_mode(agent, arg))
            elif cmd == "/compact":
                print(_compact(agent))
            elif cmd == "/context":
                print(_context(agent))
            elif cmd == "/allow":
                print(_allow(arg))
            elif cmd == "/style":
                print(_style(agent, arg))
            else:
                print(ui.error(f"未知命令 {cmd}，输入 /help 看可用命令"))
            print()
            continue
        if not agent.ready:
            print(ui.error("还没有模型，先 /key add 添加，再 /model 选中"))
            print()
            continue
        print()
        _run_turn(agent, text)
        session.save(workspace, agent)
        print()


def _model(agent: Feiyu, arg: str) -> str:
    entries = keys.load()
    if arg:
        if arg not in entries:
            return ui.error(f"清单里没有 {arg}，/model 看有哪些，/key add 添加")
        if arg == agent.name:
            return ui.notice(f"已经在用 {arg} 了")
        agent.use_model(arg)
        return ui.notice(f"已切到 {arg}（{agent.model}）对话历史保留")
    if not entries:
        return ui.notice("清单是空的 用 /key add 添加第一个模型")
    rows = []
    pad = max(len(n) for n in entries) + 3
    for name, e in entries.items():
        tick = ui.paint("brown", " ●") if name == agent.name else "  "
        shown = name.ljust(pad - (ui.visible(name) - len(name)))
        label = ui.bold(shown) if name == agent.name else ui.paint("grey", shown)
        note = e["model"] + ("" if e.get("price") else "   (未填价格)")
        rows.append("   " + tick + " " + label + ui.paint("grey", note))
    rows += ["", ui.notice("/model <名字> 切换 · /key add 添加 · /key rm <名字> 删除")]
    return "\n".join(rows)


def _mode(agent: Feiyu, arg: str) -> str:
    if arg:
        if arg not in MODES:
            return ui.error("没有 %s 这个模式，可选：%s" % (arg, "、".join(MODES)))
        agent.mode = arg
        return ui.notice("已切到「%s」 %s" % (MODES[arg][0], MODES[arg][1]))
    rows = []
    for key, (label, desc) in MODES.items():
        tick = ui.paint("brown", " ●") if key == agent.mode else "  "
        name = ui.bold(key.ljust(10)) if key == agent.mode else ui.paint("grey", key.ljust(10))
        rows.append("   " + tick + " " + name + ui.paint("grey", "%-6s %s" % (label, desc)))
    rows += ["", ui.notice("shift+tab 循环切换，或 /mode <名字>")]
    return "\n".join(rows)


def _allow(arg: str) -> str:
    rules = permissions.load()
    if arg:
        if not permissions._RULE.match(arg):
            return ui.error("规则要写成 工具(模式) 的样子，比如 Bash(npm test:*) 或 Read(*)")
        if permissions.add_allow(arg):
            return ui.notice("已加进允许列表：" + arg)
        return ui.notice(arg + " 已经在列表里了")
    rows = [ui.notice("允许（直接跑，不问）")]
    rows += ["     " + ui.paint("grey", r) for r in rules["allow"]]
    if rules["deny"]:
        rows += ["", ui.notice("拒绝（一律不放行）")]
        rows += ["     " + ui.paint("red", r) for r in rules["deny"]]
    rows += ["", ui.notice("内置拦截：碰 .ssh、id_rsa、.aws/credentials、"
                           "~/.config/feiyu/、.env 这类路径的命令一律拒")]
    rows += ["", ui.notice("/allow <工具(模式)> 添加 · 确认时按 [s] 也会记下来 · "
                           "规则存在 " + permissions.STORE)]
    return "\n".join(rows)


def _compact(agent: Feiyu) -> str:
    if not agent.ready:
        return ui.error("还没有模型")
    before = agent.context
    print(ui.notice("正在把历史压缩成摘要…"))
    summary = agent.compact()
    if not summary:
        return ui.warn("压缩没成功，历史照旧")
    head = summary if len(summary) <= 300 else summary[:300] + "…"
    body = "\n".join("  " + ui.paint("grey", line) for line in head.split("\n"))
    return "\n".join([ui.notice("压缩完成 压缩前上下文 " + ui.tokens(before)), "", body])


def _context(agent: Feiyu) -> str:
    agent.reload_context()
    if not agent.context_text:
        return ui.notice("工作区里没有项目约定文件 放一个 %s 就会自动读进来" % "、".join(CONTEXT_FILES))
    head = agent.context_text
    if len(head) > 400:
        head = head[:400] + "…"
    body = "\n".join("  " + ui.paint("grey", line) for line in head.split("\n"))
    return "\n".join([
        ui.notice("已加载 %s（%d 字，刚重新读过）" % (agent.context_file, len(agent.context_text))),
        "", body])


def _key(agent: Feiyu, arg: str) -> str:
    action, _, rest = arg.partition(" ")
    rest = rest.strip()
    entries = keys.load()
    if action == "add":
        return _key_add()
    if action == "rm":
        if not rest:
            return ui.error("用法：/key rm <名字>")
        if not keys.remove(rest):
            return ui.error(f"清单里没有 {rest}")
        if rest == agent.name:
            return ui.notice(f"已删除 {rest} 当前会话还在用它，重启后就没了")
        return ui.notice(f"已删除 {rest}")
    if action:
        return ui.error(f"未知用法 /key {action}，可用：/key、/key add、/key rm <名字>")
    if not entries:
        return ui.notice("清单是空的 用 /key add 加一个")
    rows = []
    for name, e in entries.items():
        rows.append("   " + ui.bold(name))
        rows.append(
            "      "
            + ui.paint("grey", "%s   %s   %s" % (e["model"], e.get("base_url") or "(厂商默认地址)", keys.mask(e["key"])))
        )
    rows += ["", ui.notice("/key add 添加 · /key rm <名字> 删除 · /model <名字> 切过去")]
    return "\n".join(rows)


def _key_add() -> str:
    """逐项问 key 那一项走隐藏输入，也不会进 feiyu 自己的历史文件 """
    from prompt_toolkit import prompt as ask_line
    from prompt_toolkit.formatted_text import ANSI

    def ask(label: str, secret: bool = False) -> str:
        return ask_line(ANSI("   " + ui.paint("grey", label)), is_password=secret).strip()

    try:
        name = ask("名称（/model 里显示成这个）: ")
        if not name:
            return ui.notice("已取消")
        base_url = ask("接口地址 base_url: ")
        model = ask("模型 id: ")
        secret = ask("API key: ", secret=True)
        kind = ask("接口类型 openai / anthropic [openai]: ").lower() or "openai"
        print(ui.notice("下面三项是价格，人民币每百万 token，留空就只显示 token 数不算钱"))
        price = _ask_price(ask)
    except (KeyboardInterrupt, EOFError):
        print()
        return ui.notice("已取消")
    if not model or not secret:
        return ui.error("模型 id 和 key 都不能为空，已取消")
    if kind not in ("openai", "anthropic"):
        return ui.error(f"接口类型只能是 openai 或 anthropic，收到 {kind}，已取消")
    keys.add(name, base_url=base_url, model=model, key=secret, kind=kind, price=price)
    tail = "，已带价格" if price else "，未填价格（界面上显示 token 数）"
    return ui.notice(f"已保存{tail} 用 /model {name} 切过去")


def _ask_price(ask) -> dict | None:
    fields = [("hit", "缓存命中价（没有缓存就填 0）: "), ("input", "输入价: "), ("output", "输出价: ")]
    out = {}
    for key, label in fields:
        raw = ask("  " + label)
        if not raw:
            return None
        try:
            out[key] = float(raw)
        except ValueError:
            print(ui.error(f"「{raw}」不是数字，价格这块跳过"))
            return None
    return out


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
    rows.append(ui.notice("按键    ⏎ 发送 · \\⏎ 换行 · shift+tab 切模式 · esc 中断当前回答 · ctrl-d 退出"))
    rows.append(ui.notice("版本    feiyu " + __version__))
    return "\n".join(rows)


def _piped(agent: Feiyu) -> int:
    """stdin 不是终端时（管道、脚本）：一行一个任务，不画框 """
    for line in sys.stdin:
        task = line.strip()
        if task and task != "/exit":
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
