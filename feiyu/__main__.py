import argparse
import contextlib
import json
import sys
from pathlib import Path

from . import hooks as hooks_mod
from . import keys, memory, permissions, pricing, progress, sandbox as sandbox_mod
from . import schema as schema_mod, session, trace, ui
from . import __version__
from .agent import CONTEXT_FILES, MODE_ORDER, MODES, STYLES, Feiyu, find_score
from .providers import OpenAICompatBackend


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
        "--mode",
        choices=MODE_ORDER,
        default=None,
        help="权限模式 一次性任务和管道模式下没人能回答确认，要它自己动手就得写 auto ",
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
    agent.approve = lambda name, args: ui.confirm(ui.ask_text(name, args))
    agent.approve_plan = lambda plan: ui.confirm("按这个方案动手吗？", simple=True) == "yes"
    # 同理装在这里：ask_choice 自己会认出非终端并返回空串，工具收到空串就让模型自己定
    agent.ask_user = ui.ask_choice
    # 项目钩子是仓库里带下来的命令，第一次见到（和内容变了之后）必须让用户点头
    agent.approve_hooks = lambda path, count: ui.ask_yes_no(
        "%s 里有 %d 条项目钩子，它们会在这台机器上执行 要启用吗？" % (hooks_mod.PROJECT, count))
    if args.mode:
        agent.set_mode(args.mode)      # 一律走 set_mode：它要重拼系统提示
    note = agent.load_hooks()
    # 带了上次的半截进度就说一声 不说的话下次开工它突然提起一件旧事，
    # 用户不知道这话是哪来的
    if progress.as_prompt(agent.resumed):
        print(ui.notice("上次在这个目录的任务还没做完（%s），清单已经带给它了 "
                        "/clear 可以清掉 " % agent.resumed.get("when", "上次")),
              file=sys.stderr)

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
    print(ui.welcome(workspace, __version__, project))
    print()
    boxed, why = sandbox_mod.effective()
    if boxed == "off":
        # 沙箱关着是唯一需要主动说一声的情况 开着是常态，状态栏不必天天挂着
        print(ui.warn("bash 没有沙箱，它执行的命令能写你机器上任何地方"
                      + ("：%s" % why if why else "（/sandbox workspace 打开）")))
        print()
    if workspace == Path.home():
        # 在家目录里跑，工作区就是整个家：列一次目录三十多万个文件，
        # 沙箱那道"工作区外只读"也等于没有了
        print(ui.warn("工作目录是你的家目录，它能读写这里的一切，列目录也会很慢"))
        print(ui.notice("先 cd 进具体项目再跑，或者 feiyu -w <目录>"))
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
        # 风格只在不是默认时才显示 欢迎框是 scrollback，切完不会重画，
        # 不放一份到这条常驻的状态栏上，用户切了风格就再也看不到它现在是什么
        if agent.style != "default":
            parts.append(STYLES[agent.style][0])
        # 沙箱关着才显示 —— 开着是常态，状态栏只放需要你据以行动的东西
        if sandbox_mod.effective()[0] == "off":
            parts.append("无沙箱")
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
                # 清空就是清空 留着半截进度的话下一轮它又从旧清单接着说
                progress.clear(workspace)
                agent.resumed = {}
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
            elif cmd == "/sandbox":
                print(_sandbox(arg))
            elif cmd == "/helper":
                print(_helper(agent, arg))
            elif cmd == "/jobs":
                print(_jobs(agent, arg))
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


def _helper(agent: Feiyu, arg: str) -> str:
    entries = keys.load()
    if arg in ("off", "none", "无"):
        agent.set_helper("")
        return ui.notice("旁活改回用主模型")
    if arg:
        if arg not in entries:
            return ui.error(f"清单里没有 {arg}，/model 看有哪些")
        agent.set_helper(arg)
        return ui.notice(f"摘要、子 agent、网页提炼改用 {arg}（{entries[arg]['model']}）")
    if not agent.helper:
        return ui.notice("旁活现在用主模型 /helper <名字> 换一条便宜的，/helper off 改回来")
    return ui.notice(f"旁活用 {agent.helper} /helper off 改回主模型")


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
    # 摘要本身不往屏幕上倒 —— 压缩就是为了省地方，压完再全文打一遍是自相矛盾
    # 压完还剩多少要等下一次请求才知道，不编一个数字出来
    return ui.notice("压缩完成 原来占了 %s，现在换成了一段摘要，接着说就行 "
                     % ui.tokens(before))


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
            + ui.paint("grey", "%s   %s   %s%s" % (
                e["model"], e.get("base_url") or "(厂商默认地址)", keys.mask(e["key"]),
                "   看得了图" if e.get("vision") else ""))
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
        # 探测不出来，只能问 标错了的后果是发图过去接口直接报错
        vision = ask("这个模型能看图吗 y/N: ").lower().startswith("y")
        # 出厂那个 8192 对现在的模型普遍偏小：写一个长文件就撞上限，
        # 半截的 JSON 参数还会把这轮调用废掉 填不填由用户定，探测不出来
        cap = _ask_cap(ask)
        print(ui.notice("下面三项是价格，人民币每百万 token，留空就只显示 token 数不算钱"))
        price = _ask_price(ask)
    except (KeyboardInterrupt, EOFError):
        print()
        return ui.notice("已取消")
    if not model or not secret:
        return ui.error("模型 id 和 key 都不能为空，已取消")
    if kind not in ("openai", "anthropic"):
        return ui.error(f"接口类型只能是 openai 或 anthropic，收到 {kind}，已取消")
    keys.add(name, base_url=base_url, model=model, key=secret, kind=kind,
             price=price, vision=vision, max_tokens=cap)
    tail = "，已带价格" if price else "，未填价格（界面上显示 token 数）"
    if vision:
        tail += "，标了能看图"
    if cap:
        tail += "，单轮输出上限 %d" % cap
    return ui.notice(f"已保存{tail} 用 /model {name} 切过去")


def _ask_cap(ask) -> int | None:
    """单轮输出上限 查不出来只能问：填大了接口报错，填小了长文件写不完 """
    raw = ask("单轮输出上限 max_tokens（留空用 %d）: " % OpenAICompatBackend.MAX_TOKENS)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        print(ui.error(f"「{raw}」不是整数，这项跳过"))
        return None
    if value < 256:
        print(ui.error("太小了（至少 256），这项跳过"))
        return None
    return value


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
    # 清单没做完就把进度留下，下次进这个目录时它自己知道上次干到哪
    # 放在三条路径共同的收尾里，一次覆盖全部；全做完时 save 自己会把旧的清掉
    progress.save(agent.toolbox.root, agent.task, agent.toolbox.todos.items)
    # 后台命令跟着 feiyu 一起收摊 留着的话用户关掉窗口之后那些进程还在跑，
    # 而他已经没有任何句柄能找到它们了
    agent.toolbox.lsp.close()
    left = agent.toolbox.jobs.running()
    if left:
        agent.toolbox.jobs.kill_all()
        print(ui.notice("停掉了 %d 个还在跑的后台命令" % len(left)))
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


_SANDBOX_DESC = {
    "off": "不沙箱，bash 想读写哪儿就读写哪儿",
    "workspace": "工作区外只读，密钥目录挖空，联网照旧",
    "strict": "同 workspace，另外断网",
}


def _sandbox(arg: str) -> str:
    if arg:
        if arg not in sandbox_mod.LEVELS:
            return ui.error("只能是 %s" % " / ".join(sandbox_mod.LEVELS))
        sandbox_mod.set_level(arg)
        mode, why = sandbox_mod.effective()
        if mode != arg:
            return ui.warn("设成 %s 了，但现在生效不了：%s" % (arg, why))
        return ui.notice("bash 沙箱：%s —— %s" % (arg, _SANDBOX_DESC[arg]))

    mode, why = sandbox_mod.effective()
    rows = []
    for name in sandbox_mod.LEVELS:
        tick = ui.paint("brown", " ●") if name == mode else "  "
        label = ui.bold(name.ljust(11)) if name == mode else ui.paint("grey", name.ljust(11))
        rows.append("   " + tick + " " + label + ui.paint("grey", _SANDBOX_DESC[name]))
    rows.append("")
    if why:
        rows.append(ui.warn("现在没在沙箱里跑：" + why))
    else:
        rows.append(ui.notice("靠 bubblewrap 的 mount namespace，不是字符串匹配"))
    # 终端不渲染 markdown，强调只能靠 ui.bold
    rows.append("  " + ui.bold("写是真隔离") + ui.paint("grey", "，工作区外一律只读"))
    rows.append("  " + ui.bold("读只是挖空了几个目录")
                + ui.paint("grey", "，整个文件系统仍然可读，别当成读也隔离了"))
    rows.append(ui.notice("钩子和 verify: 的命令不进沙箱——那是你自己写的，不是模型产出的 "))
    rows.append(ui.notice("/sandbox <档位> 切换 · FEIYU_SANDBOX 环境变量可以临时压过配置"))
    return "\n".join(rows)


def _jobs(agent: Feiyu, arg: str) -> str:
    """后台命令跑在你自己机器上，得有个不经过模型也能看和停的地方 """
    pool = agent.toolbox.jobs
    if arg in ("kill", "stop"):
        stopped = len(pool.running())
        pool.kill_all()
        return ui.notice("停掉了 %d 个" % stopped) if stopped else ui.notice("没有在跑的")
    if arg:
        job = pool.get(arg)
        if job is None:
            return ui.error("没有编号为 %s 的后台命令" % arg)
        job.kill()
        return ui.notice("已停掉 #%s" % arg)

    items = pool.all()
    if not items:
        return ui.notice("没有后台命令 让它跑长任务时会用到（bash 的 background）")
    rows = []
    for job in items:
        tick = ui.paint("brown", " ●") if job.running else ui.paint("grey", " ○")
        rows.append("   " + tick + " " + ui.bold("#%d" % job.id) + "  "
                    + ui.paint("grey", ui.clip(job.status() + "  " + job.command,
                                               ui.width() - 12)))
    rows.append("")
    rows.append(ui.notice("/jobs <编号> 停掉一个 · /jobs kill 全停 · 退出时会自动全停"))
    return "\n".join(rows)


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


# 接口报错时给一句能照着做的话 原样甩 JSON 的话用户只能干瞪眼
_API_HINTS = (
    ("401", "key 不对或者过期了 /key 看看这条配置，或者 /key add 重新加一条"),
    ("403", "这个 key 没有访问权限，确认一下它开通了这个模型"),
    ("404", "接口说没有这个模型 id 各家退役很勤，去厂商后台看现在叫什么，"
            "再 /key add 改一条"),
    ("429", "被限流了 等一会儿再试，或者 /model 换一条配置"),
    ("insufficient", "余额不够了"),
    ("Connection", "连不上接口 检查网络，或者这条配置的 base_url 写错了"),
    ("Timeout", "接口超时了 网络不稳，或者这次要它做的事太重"),
)


def _explain(exc: Exception) -> str:
    text = "%s: %s" % (type(exc).__name__, exc)
    for probe, hint in _API_HINTS:
        if probe.lower() in text.lower():
            return hint
    return ""


def _run_turn(agent: Feiyu, prompt: str) -> None:
    guard = ui.EscInterrupt()
    try:
        with guard:
            agent.send(prompt)
    except KeyboardInterrupt:
        print(ui.warn("已中断"))
    except Exception as exc:
        hint = _explain(exc)
        if hint:
            print(ui.error(hint))
            print(ui.notice(ui.clip(str(exc), 200)))
        else:
            print(ui.error("出错了：%s" % exc))


if __name__ == "__main__":
    raise SystemExit(main())
