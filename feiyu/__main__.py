import argparse
import contextlib
import json
import sys
from pathlib import Path

from . import hooks as hooks_mod
from . import keys, memory, permissions, pricing, schema as schema_mod, session, trace, ui
from . import __version__
from .agent import CONTEXT_FILES, MODES, STYLES, Feiyu, find_score


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
        "--json-schema",
        metavar="文件",
        default=None,
        help="跑完按这个 JSON schema 输出结果 只有 JSON 进 stdout，其余进 stderr ",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="跑完再开一个只看产出的模型给改动打分 ",
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
    spec = None
    if args.json_schema:
        try:
            spec = schema_mod.load(args.json_schema)
        except (OSError, ValueError) as exc:
            print(f"错误：读不了 schema —— {exc}", file=sys.stderr)
            return 2

    tracer = trace.Trace(workspace)
    try:
        agent = Feiyu(workspace, model=chosen, trace=tracer)
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    tracer.write("run_start", version=__version__, model=agent.model,
                 name=agent.name, mode=agent.mode, verify=agent.verify or None,
                 task=" ".join(args.prompt) if args.prompt else None)

    # 一次性任务也要能答应确认 装在早退分支后面的话 approve 一直是 None，
    # `feiyu "改一下 x"` 的每个写操作都会被当成"用户拒绝"（0.11.0 及以前就是这样）
    # 管道模式下 stdin 不是终端，ui.confirm 自己会返回 no——没人能回答
    agent.approve = lambda name, args: ui.confirm("要执行 %s 吗？" % ui.tool_label(name))
    agent.approve_plan = lambda plan: ui.confirm("按这个方案动手吗？") in ("yes", "always")
    # 项目钩子是仓库里带下来的命令，第一次见到（和内容变了之后）必须让用户点头
    agent.approve_hooks = lambda path, count: ui.ask_yes_no(
        "%s 里有 %d 条项目钩子，它们会在这台机器上执行 要启用吗？" % (hooks_mod.PROJECT, count))
    note = agent.load_hooks()

    if note:
        print(ui.notice(note), file=sys.stderr)
    if args.prompt:
        task = " ".join(args.prompt)
        if spec is None:
            _run_turn(agent, task)
            _print_review(agent, task, args.review)
            return _finish(agent, tracer, 0)
        # schema 模式下 stdout 只能有 JSON，人看的东西整体挪到 stderr
        with contextlib.redirect_stdout(sys.stderr):
            _run_turn(agent, task)
            _print_review(agent, task, args.review)
            value, problems = agent.finish_json(spec)
        if problems:
            print("错误：输出不满足 schema\n  " + "\n  ".join(problems), file=sys.stderr)
            return _finish(agent, tracer, 1)
        json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _finish(agent, tracer, 0)
    if not sys.stdin.isatty():
        return _finish(agent, tracer, _piped(agent))

    ui.STYLE_NAMES[:] = list(STYLES)
    ui.MODE_NAMES[:] = list(MODES)
    ui.MODEL_NAMES = lambda: list(keys.load())
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
        parts = [tag, agent.model]
        # 上下文显示占满多少，不显示绝对值：每次请求都有一千多 token 的固定底噪，
        # 绝对数字既吓人又没法据此做任何决定，占比才说明还剩多少余地
        if agent.context:
            share = agent.context / max(1, agent.context_limit)
            parts.append("上下文 %d%%" % max(1, round(share * 100)))
        # 没配价格就什么都不显示 拿 token 数冒充计费只会和左边那个数重复
        spent = agent.cost
        if spent:
            parts.append(pricing.yuan(spent))
        return "  ·  ".join(parts)

    inbox = ui.InputBox(status, on_cycle_mode=agent.cycle_mode)
    while True:
        try:
            text = inbox.ask().strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            return _finish(agent, tracer, 0)
        if not text:
            continue
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd == "/exit":
                return _finish(agent, tracer, 0)
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
            elif cmd == "/verify":
                print(_verify(agent, arg))
            elif cmd == "/review":
                print(_review(agent, arg))
            elif cmd == "/cost":
                print(_cost(agent, workspace))
            elif cmd == "/memory":
                print(_memory(agent, arg))
            elif cmd == "/hooks":
                print(_hooks(agent))
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
        agent.set_mode(arg)
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
        # 最长的命令 /compact / /context 正好 8 个字符，ljust(8) 会让名字和说明贴在一起
        rows.append("    " + ui.bold(cmd.ljust(10)) + ui.paint("grey", desc))
    rows.append("")
    rows.append(ui.notice("按键    ⏎ 发送 · \\⏎ 换行 · shift+tab 切模式 · esc 中断当前回答 · ctrl-d 退出"))
    rows.append(ui.notice("版本    feiyu " + __version__))
    return "\n".join(rows)


def _finish(agent: Feiyu, tracer, code: int) -> int:
    tracer.write("run_end", code=code, tokens=agent.tokens, cost=agent.cost,
                 touched=agent.touched or None, verified=agent.verified_ok)
    return code


def _print_review(agent: Feiyu, task: str, wanted: bool) -> None:
    if not wanted:
        return
    print(ui.notice("让一个没看过过程的模型给这次改动打分…"))
    verdict = agent.review(task)
    if not verdict:
        print(ui.warn("没有可评的改动"))
        return
    print()
    print(_review_body(verdict))


def _review_body(verdict: str) -> str:
    score = find_score(verdict)
    rows = ["\n".join("  " + ui.paint("grey", line) for line in verdict.split("\n"))]
    if score is not None:
        # 调色板里没有绿色，也别为这一处新加一个——blue 在这套配色里就是"没问题"
        color = "blue" if score >= 8 else ("amber" if score >= 6 else "red")
        rows += ["", "  " + ui.paint(color, "评分 %g / 10" % score)]
    return "\n".join(rows)


def _review(agent: Feiyu, arg: str) -> str:
    if not agent.ready:
        return ui.error("还没有模型")
    print(ui.notice("让一个没看过过程的模型给这次改动打分…"))
    verdict = agent.review(arg or "（沿用这次对话里的任务）")
    if not verdict:
        return ui.warn("没有可评的改动 有 git 的话看的是 git diff HEAD，"
                       "没有就看这次改过的文件")
    return _review_body(verdict)


def _verify(agent: Feiyu, arg: str) -> str:
    if arg in ("off", "关"):
        agent.verify = ""
        return ui.notice("已关掉自动校验")
    if arg:
        agent.verify = arg
        return ui.notice("改完代码会自动跑：" + arg)
    if not agent.verify:
        return ui.notice(
            "没有配校验命令 在 %s 里单起一行写 `verify: npm test`，"
            "或者 /verify <命令> 临时设一个 改完代码就会自动跑，不过就把报错交给它自己修 "
            % (agent.context_file or "、".join(CONTEXT_FILES)))
    state = {True: "上次通过", False: "上次没过", None: "还没跑过"}[agent.verified_ok]
    return ui.notice("当前校验命令：%s（%s） /verify off 关掉" % (agent.verify, state))


def _hooks(agent: Feiyu) -> str:
    found = agent.all_hooks
    rows = []
    if not found:
        rows.append(ui.notice("没有配任何钩子"))
    else:
        rows.append(ui.notice("会在这些时机跑（退出码非 0 就拦下，输出回给模型）"))
        for event in hooks_mod.EVENTS:
            mine = [h for h in found if h.event == event]
            if not mine:
                continue
            rows.append("   " + ui.bold(event))
            for h in mine:
                rows.append("      " + ui.paint("grey", "%-22s %s" % (
                    ui.clip(h.matcher, 22), ui.clip(h.command, ui.width() - 34))))
                rows.append("      " + ui.paint("grey", " " * 23 + "来自 " + h.source))
    if agent.hooks_note:
        rows += ["", ui.warn(agent.hooks_note)]
    path, _, state = hooks_mod.project_state(agent.toolbox.root)
    rows += ["", ui.notice("全局 %s" % hooks_mod.GLOBAL)]
    rows.append(ui.notice("项目 %s（%s）" % (hooks_mod.PROJECT, {
        "none": "没有这个文件", "trusted": "已信任", "unknown": "存在但未启用"}[state])))
    rows.append(ui.notice("改完配置重启生效 项目钩子内容一变就要重新确认"))
    return "\n".join(rows)


def _memory(agent: Feiyu, arg: str) -> str:
    root = agent.toolbox.root
    action, _, rest = arg.partition(" ")
    rest = rest.strip()

    if action in ("clear", "清空"):
        count = memory.clear(root)
        agent._refresh_system()
        return ui.notice("已经全忘了（%d 条）" % count)
    if action in ("forget", "忘"):
        if not rest.isdigit():
            return ui.error("用法：/memory forget <序号>，序号看 /memory")
        what = memory.forget(root, int(rest))
        if not what:
            return ui.error("没有第 %s 条" % rest)
        agent._refresh_system()
        return ui.notice("忘掉了：" + ui.clip(what, 60))
    if action in ("add", "记"):
        if not rest:
            return ui.error("用法：/memory add <要记住的一句话>")
        ok, note = memory.add(root, rest)
        agent._refresh_system()
        return (ui.notice if ok else ui.warn)(note)
    if action:
        return ui.error("未知用法 /memory %s，可用：/memory、add、forget <序号>、clear" % action)

    items = memory.load(root)
    if not items:
        return ui.notice(
            "这个目录还没有记住任何东西 它干活时觉得值得留到下次的事会自己记，"
            "也可以 /memory add <一句话> 手动加 ")
    rows = [ui.notice("这个目录记住的事（每次开工自动带上）")]
    for i, (when, what) in enumerate(items, 1):
        rows.append("   " + ui.paint("grey", "%2d. " % i) + ui.clip(what, ui.width() - 12))
        rows.append("       " + ui.paint("grey", when))
    used = sum(len(what) for _, what in items)
    rows += ["", ui.notice("%d / %d 条 · %d / %d 字 · 存在 %s" % (
        len(items), memory.MAX_ITEMS, used, memory.MAX_CHARS, memory.DIR))]
    rows.append(ui.notice("/memory forget <序号> 删一条 · /memory clear 全清"))
    return "\n".join(rows)


def _cost(agent: Feiyu, workspace) -> str:
    runs = trace.summarize(trace.read(days=7, workspace=str(workspace)))
    if not runs:
        return ui.notice("最近七天这个目录没有记录 日志在 " + trace.DIR)
    rows = [ui.notice("最近七天 · 这个目录 · 按任务")]
    total_tokens = total_cost = 0.0
    priced = False
    import time as _time
    for run in sorted(runs.values(), key=lambda r: r["start"])[-10:]:
        when = _time.strftime("%m-%d %H:%M", _time.localtime(run["start"]))
        money = pricing.yuan(run["cost"]) if run["priced"] else "—"
        task = ui.clip(run["task"] or "（交互）", 34)
        rows.append("   " + ui.paint("grey", "%s  %-36s %5s轮 %7s %9s" % (
            when, task, run["turns"], ui.tokens(run["tokens"]), money)))
        total_tokens += run["tokens"]
        total_cost += run["cost"]
        priced = priced or run["priced"]
    rows += ["", "   " + ui.bold("合计  %s  %s" % (
        ui.tokens(int(total_tokens)), pricing.yuan(total_cost) if priced else "未配价格"))]
    if agent.ready and agent.tokens:
        rows.append("   " + ui.paint("grey", "本次会话 %s" % ui.tokens(agent.tokens)))
    rows += ["", ui.notice("完整日志 %s · FEIYU_NO_LOG=1 可以关掉记录" % trace.DIR)]
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
