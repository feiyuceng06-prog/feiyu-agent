import argparse
import contextlib
import json
import re
import sys
from pathlib import Path

from . import hooks as hooks_mod
from . import ide as ide_mod
from . import mcp as mcp_mod
from . import serve as serve_mod
from . import skills as skills_mod
from . import bots as bots_mod
from . import credits, enabled, keys, memory, permissions, pricing, profiles, progress
from . import sandbox as sandbox_mod
from . import schema as schema_mod, session, trace, ui
from . import __version__
from .agent import (CONTEXT_FILES, MAX_ROUNDS, MODE_ORDER, MODES, STYLES, Feiyu,
                    find_score)
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
        "--max-rounds",
        type=int,
        default=None,
        metavar="N",
        help="连续调多少轮工具之后停下来问一句（默认 %d，0 是不限） "
             "非交互场景没人能回答，到了就是硬停 —— 跑 benchmark 要调大 "
             "也可以写进项目约定文件：单起一行 `max-rounds: N` " % MAX_ROUNDS,
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="元",
        help="这次任务最多花多少钱，到了就停（0 是不限） 只有填过价格的模型算得出来 "
             "也可以写进项目约定文件：单起一行 `budget: 5` ",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="跑完再开一个只看产出的模型给改动打分 ",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="挂在 stdio 上说 JSON，给桌面端用 别手动跑，跑 feiyu-desktop ",
    )
    parser.add_argument(
        "--own-window",
        action="store_true",
        help=argparse.SUPPRESS,     # 桌面端自己传的，不给人看
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="直接跑一个任务，跑完就退出 ",
    )
    args = parser.parse_args()

    # 主题要在画任何东西之前生效，欢迎框是打进 scrollback 的，之后换不了
    ui.set_theme(keys.theme() or "default")
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
    # **这两个要装在第一个 return 上面。** main() 有三条路径（一次性任务 / 管道 /
    # 交互），前两条都是 return —— 0.11.0 把 agent.approve 装在 TUI 那一段里，
    # 结果 `feiyu "改一下 x"` 每个写操作都被当成"用户拒绝"，README 演示的用法根本改不动文件。
    # `is not None` 不是 `if`：0 现在是"不限"这个明确的选择，用真值判的话
    # `--max-rounds 0` 会被当成没给，约定文件里的数原样留着
    if args.max_rounds is not None:
        agent.max_rounds = args.max_rounds
        agent.limit_cli.add("max_rounds")
    if args.budget is not None:
        agent.budget = args.budget
        agent.limit_cli.add("budget")
    tracer.write("run_start", version=__version__, model=agent.model,
                 name=agent.name, mode=agent.mode, verify=agent.verify or None,
                 max_rounds=agent.max_rounds, budget=agent.budget or None,
                 task=" ".join(args.prompt) if args.prompt else None)

    if args.serve:
        # 装配全在 serve() 里：它要用自己那套走协议的回调去问人，
        # 而下面这些 ui.confirm 版本在没有终端的地方一律返回"拒绝"
        return _finish(agent, tracer, serve_mod.serve(
            agent, workspace, tracer, args.resume, trusted=args.own_window))

    # 一次性任务也要能答应确认 装在早退分支后面的话 approve 一直是 None，
    # `feiyu "改一下 x"` 的每个写操作都会被当成"用户拒绝"（0.11.0 及以前就是这样）
    # 管道模式下 stdin 不是终端，ui.confirm 自己会返回 no——没人能回答
    # 编辑器桥（见 feiyu/ide.py）：在 nvim / VS Code 里跑时，写文件和编辑的确认
    # 变成编辑器原生的左右对照 diff，接受/拒绝在那边按。没有编辑器时 route 回 None，
    # 底下那句终端确认照旧 —— 桥的任何毛病都不该影响 agent 本身。
    # Link 而不是 connect()：编辑器不一定比 feiyu 先起来（先在这个终端里跑 feiyu、
    # 回头才打开 nvim 是常事），中途还可能重启。只连一次的话那种顺序整场都没有 diff，
    # 而且是静默的 —— 人不会知道自己在裸奔。它在每次要确认写操作时看一眼，节流重试。
    def _bridge_changed(editor):
        print(ui.notice(
            "已连上 %s：写文件 / 编辑会在编辑器里开成 diff 等你决定" % editor
            if editor else "编辑器断开了：写文件 / 编辑退回终端确认"), file=sys.stderr)

    link = ide_mod.Link(str(workspace), on_change=_bridge_changed)

    def _approve(name, args):
        answer = link.route(name, args, agent.toolbox.root)
        return answer if answer is not None else ui.confirm(ui.ask_text(name, args))

    agent.approve = _approve
    agent.approve_plan = lambda plan: ui.confirm("按这个方案动手吗？", simple=True) == "yes"
    # 同理装在这里：ask_choice 自己会认出非终端并返回空串，工具收到空串就让模型自己定
    agent.ask_user = ui.ask_choice
    # 项目钩子是仓库里带下来的命令，第一次见到（和内容变了之后）必须让用户点头
    agent.approve_hooks = lambda path, count: ui.ask_yes_no(
        "%s 里有 %d 条项目钩子，它们会在这台机器上执行 要启用吗？" % (hooks_mod.PROJECT, count))
    if args.mode:
        agent.set_mode(args.mode)      # 一律走 set_mode：它要重拼系统提示
    # 项目 MCP 配置同理：仓库里带下来的命令，第一次见到必须让用户点头
    agent.approve_mcp = lambda path, names: ui.ask_yes_no(
        "%s 里配置了 MCP 服务器 %s，它们会在这台机器上启动 要启用吗？" % (
            mcp_mod.PROJECT, "、".join(names)))
    # 项目技能是仓库里带下来的指令，而且是模型自己决定加载的：跟钩子同一类注入通道
    agent.approve_skills = lambda folder, names: ui.ask_yes_no(
        "%s 里有 %d 份项目技能（%s），它们会进它的上下文 要启用吗？" % (
            skills_mod.PROJECT_SKILLS, len(names), "、".join(names[:6])
            + ("…" if len(names) > 6 else "")))
    note = agent.load_hooks()
    mcp_note = agent.load_mcp()
    if mcp_note:
        print(ui.notice(mcp_note), file=sys.stderr)
    skills_note = agent.load_skills()
    if skills_note:
        print(ui.notice(skills_note), file=sys.stderr)
    # 自定义命令不用同意：用户不敲就不会生效，敲了等于他自己把那段字打了一遍
    commands = {c.name: c for c in skills_mod.load_commands(workspace)}
    ui.EXTRA_SLASH.clear()
    ui.EXTRA_SLASH.update({"/" + n: c.description for n, c in commands.items()})
    # 带了上次的半截进度就说一声 不说的话下次开工它突然提起一件旧事，
    # 用户不知道这话是哪来的
    if progress.as_prompt(agent.resumed):
        print(ui.notice("上次在这个目录的任务还没做完（%s），清单已经带给它了 "
                        "/clear 可以清掉 " % agent.resumed.get("when", "上次")),
              file=sys.stderr)

    if note:
        print(ui.notice(note), file=sys.stderr)

    # **会话装配要放在早退之前**，跟 0.12.0 那条 `main()` 三条路径的规矩一样。
    # 之前只有 TUI 那段做，于是一次性任务既不 --resume 也不存盘 ——
    # 而撞轮数上限时代码自己印的提示正是「feiyu --resume 接着做」，指向一个不存在的东西。
    saved = session.load(workspace)
    resumed = bool(args.resume and saved and agent.restore(saved))
    agent.session_id = session.latest_id(workspace) if resumed else session.new_id()

    if args.prompt:
        task = " ".join(args.prompt)
        if spec is None:
            code = _run_turn(agent, task)
            session.save(workspace, agent, agent.session_id)
            _print_review(agent, task, args.review)
            return _finish(agent, tracer, code)
        # schema 模式下 stdout 只能有 JSON，人看的东西整体挪到 stderr
        with contextlib.redirect_stdout(sys.stderr):
            code = _run_turn(agent, task)
            session.save(workspace, agent, agent.session_id)
            _print_review(agent, task, args.review)
            value, problems = agent.finish_json(spec)
        # 这一轮本来就炸了的话，schema 不满足只是它的后果，报前者更有用
        if code or problems:
            if problems:
                print("错误：输出不满足 schema\n  " + "\n  ".join(problems), file=sys.stderr)
            return _finish(agent, tracer, code or 1)
        json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _finish(agent, tracer, 0)
    if not sys.stdin.isatty():
        return _finish(agent, tracer, _piped(agent, workspace))

    ui.STYLE_NAMES[:] = list(STYLES)
    ui.MODE_NAMES[:] = list(MODES)
    ui.MODEL_NAMES = lambda: list(keys.load())
    ui.BOT_NAMES = lambda: [b.name for b in bots_mod.load()]
    ui.MENTION_ROOT = workspace
    ui.enter_screen()

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
        expanded = ""
        if text.startswith("/"):
            head, _, rest = text.partition(" ")
            # 内置命令优先：同名的自定义命令不能把 /clear 顶掉
            custom = commands.get(head[1:]) if head not in ui.SLASH else None
            if custom is not None:
                expanded = skills_mod.expand(custom, rest.strip())
                print(ui.notice("%s → %s（%s）" % (head, custom.description, custom.source)))
                print()
        if expanded:
            # 展开完就是用户自己说的那段话，往下按普通输入走
            text = expanded
        elif text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd == "/exit":
                return _finish(agent, tracer, 0)
            if cmd == "/help":
                print(_help())
            elif cmd == "/clear":
                agent.reset()
                session.drop(workspace, agent.session_id)
                agent.session_id = session.new_id()
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
            elif cmd == "/effort":
                print(_effort(agent, arg))
            elif cmd == "/theme":
                print(_theme(arg))
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
            elif cmd == "/profile":
                print(_profile(agent, arg))
            elif cmd == "/bot":
                print(_bot(agent, workspace, arg))
            elif cmd == "/helper":
                print(_helper(agent, arg))
            elif cmd == "/jobs":
                print(_jobs(agent, arg))
            elif cmd == "/mcp":
                print(_mcp(agent))
            elif cmd == "/skills":
                print(_skills(agent, commands, arg))
            elif cmd == "/undo":
                said = agent.undo()
                print(ui.notice(said + "（下一句话时会告诉它）") if said
                      else ui.warn("没有可退的改动 只能退 write_file / edit_file 写的，"
                                   "它用 bash 改的文件要自己用 git 恢复"))
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
        session.save(workspace, agent, agent.session_id)
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


def _profile(agent: Feiyu, arg: str) -> str:
    """`/profile` 看有哪些人格，`/profile <名字>` 换一份，`/profile off` 摘掉 """
    found = profiles.load()
    if arg in ("off", "none", "无"):
        agent.set_profile("")
        return ui.notice("已摘掉人格设定")
    if arg:
        if not agent.set_profile(arg):
            return ui.error("没有叫 %s 的配置档案 现有：%s"
                            % (arg, "、".join(p.name for p in found) or "（一份都没有）"))
        one = profiles.get(arg)
        return ui.notice("已换成「%s」%s" % (arg, "，模型跟着换成 " + one.model if one.model else ""))
    if not found:
        return ui.notice(
            "还没有配置档案 一份档案就是一个 .md，放 %s/ 下："
            "开头写 --- name / description / model ---，正文就是人格设定（每一轮都会发，别写太长）"
            % profiles.DIR)
    rows = []
    for one in found:
        tick = ui.paint("brown", " ●") if one.name == agent.profile else "  "
        name = ui.pad(one.name, 16)
        label = ui.bold(name) if one.name == agent.profile else ui.paint("grey", name)
        note = one.description + (" · " + one.model if one.model else "")
        rows.append("   " + tick + " " + label + ui.paint("grey", note))
    rows += ["", ui.notice("/profile <名字> 换一份，/profile off 摘掉")]
    return "\n".join(rows)


def _bot(agent: Feiyu, workspace: Path, arg: str) -> str:
    """`/bot` 看有哪些，`/bot <名字>` 开一段新对话套上它，`/bot off` 摘掉

    **跟 `/profile` 的区别就在"开一段新的"**：档案是往正在聊的这段里换人格，
    机器人是拿这套人格从头开一段。所以这里要存旧的、换 id，走的是
    `serve.new_session` 那条路（旧的留在盘上，不是 `/clear`）。
    """
    found = bots_mod.load()
    if arg and arg not in ("off", "none", "无") and bots_mod.get(arg) is None:
        return ui.error("没有叫 %s 的机器人 现有：%s"
                        % (arg, "、".join(b.name for b in found) or "（一个都没有）"))
    if arg:
        session.save(workspace, agent, agent.session_id)
        agent.reset()
        agent.session_id = session.new_id()
        agent.start_bot("" if arg in ("off", "none", "无") else arg)
        if not agent.bot:
            return ui.notice("已摘掉机器人，开了一段新对话")
        one = bots_mod.get(agent.bot)
        bits = [b for b in (one.model, one.mode and "%s 模式" % one.mode) if b]
        return ui.notice("开了一段新对话，用「%s」%s"
                         % (agent.bot, ("（%s）" % "、".join(bits)) if bits else ""))
    if not found:
        return ui.notice(
            "还没有机器人 一个机器人就是一个 .md，放 %s/ 下："
            "开头写 --- name / description / model / mode / style ---，正文是人格设定 "
            "跟档案的区别：机器人是点一下就用这套人格开一段新对话，还能带上模型和权限模式"
            % bots_mod.DIR)
    rows = []
    for one in found:
        tick = ui.paint("brown", " ●") if one.name == agent.bot else "  "
        name = ui.pad(one.name, 16)
        label = ui.bold(name) if one.name == agent.bot else ui.paint("grey", name)
        note = " · ".join(x for x in (one.description, one.model,
                                      one.mode and one.mode + " 模式") if x)
        rows.append("   " + tick + " " + label + ui.paint("grey", note))
    rows += ["", ui.notice("/bot <名字> 用它开一段新对话，/bot off 摘掉")]
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
    if not any(m.get("role") != "system" for m in agent.backend.messages):
        return ui.notice("这段对话还是空的，没什么可压缩的")
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
        return _key_add(agent)
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
            + ui.paint("grey", "%s   %s   %s%s%s" % (
                e["model"], e.get("base_url") or "(厂商默认地址)", keys.mask(e["key"]),
                "   看得了图" if e.get("vision") else "", _limit_note(name, e)))
        )
    rows += ["", ui.notice("/key add 添加 · /key rm <名字> 删除 · /model <名字> 切过去")]
    return "\n".join(rows)


def _limit_note(name: str, entry: dict) -> str:
    if entry.get("limit"):
        return "   窗口 %s" % ui.tokens(int(entry["limit"]))
    learned = keys.learned_limit(name)
    return "   窗口 ~%s（撞墙学到的）" % ui.tokens(learned) if learned else ""


def _key_add(agent: Feiyu) -> str:
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
        limit = _ask_limit(ask)
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
             price=price, vision=vision, max_tokens=cap, limit=limit)
    tail = "，已带价格" if price else "，未填价格（界面上显示 token 数）"
    if limit:
        tail += "，上下文窗口 %s" % ui.tokens(limit)
    if vision:
        tail += "，标了能看图"
    if cap:
        tail += "，单轮输出上限 %d" % cap
    # 改的是正在用的那条、或者清单原来是空的：当场重建后端
    # 只写盘不重建的话，客户端还挂着上一次建它时的 base_url 和 key，
    # 这一轮改的东西本次会话一律不生效，而 /model 同名又会早退，用户只能重启
    if name == agent.name or not agent.ready:
        agent.use_model(name)
        return ui.notice(f"已保存{tail} 已经在用 {name} 了，改的东西当场生效")
    return ui.notice(f"已保存{tail} 用 /model {name} 切过去")


def _ask_limit(ask) -> int | None:
    """上下文窗口 留空按 128k 算，撞过一次墙会自动记住真实的上限 """
    raw = ask("上下文窗口 token 数（留空按 128k，撞过一次会自己记住）: ")
    if not raw:
        return None
    try:
        value = int(raw.lower().rstrip("k")) * (1000 if raw.lower().endswith("k") else 1)
    except ValueError:
        print(ui.error(f"「{raw}」不是整数，这项跳过"))
        return None
    return value if value > 0 else None


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
    # anthropic 直连写缓存按输入价的 1.25 倍收，别家多半不另收 不内置倍率，问一句
    raw = ask("  缓存写入价（留空按输入价算）: ")
    if raw:
        try:
            out["write"] = float(raw)
        except ValueError:
            print(ui.error(f"「{raw}」不是数字，写入价按输入价算"))
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


# 给人看的档位名 —— **接口认的是英文那几个**，中文只在界面上出现
EFFORT_LABEL = {"low": "低", "medium": "中", "high": "高",
                "xhigh": "极高", "max": "最高"}


def _effort(agent: Feiyu, arg: str) -> str:
    """看或改推理强度 只对认这个参数的后端有意义 """
    from .providers import EFFORTS
    b = agent.backend
    if b is None:
        return ui.error("还没有可用的模型 ")
    arg = arg.strip().lower()
    # 中文也收：界面上显示的是中文，用户多半照着打
    for key, label in EFFORT_LABEL.items():
        if arg == label:
            arg = key
            break
    if arg in ("on", "off", "开", "关"):
        b.thinking_on = arg in ("on", "开")
        note = ""
        # **两个方向都要挡。** 改档位时会挡一次（下面），但"当前已经是 xhigh，
        # 现在把思考关掉"走的是这条路 —— 不挡的话下一次请求直接 400，
        # 而那时候用户早就忘了自己关过思考
        if not b.thinking_on and getattr(b, "effort", "") in ("xhigh", "max"):
            b.effort = "high"
            note = " 顺带把强度降到 high —— xhigh / max 配上「不思考」接口会 400 "
        return ui.notice("思考已%s（这一段对话内有效）%s"
                         % ("打开" if b.thinking_on else "关掉", note))
    if arg:
        if arg not in EFFORTS:
            return ui.error("没有 %s 这一档 anthropic 认的是：%s"
                            % (arg, "、".join("%s（%s）" % (k, EFFORT_LABEL[k])
                                              for k in EFFORTS)))
        # 关掉思考只在 high 及以下能用：xhigh / max 配 disabled 会被接口 400
        if not b.thinking_on and arg in ("xhigh", "max"):
            b.thinking_on = True
            note = " 顺带把思考打开了 —— xhigh / max 配上「不思考」接口会 400 "
        else:
            note = ""
        b.effort = arg
        return ui.notice("推理强度已切到 %s（%s）%s 只影响这一段对话；"
                         "要长期改就 /key 重加一次 " % (arg, EFFORT_LABEL[arg], note))
    rows = ["   思考：" + ("开" if getattr(b, "thinking_on", True) else "关"), ""]
    now = getattr(b, "effort", "")
    for key in EFFORTS:
        tick = ui.paint("brown", " ●") if key == now else "  "
        name = (ui.bold(key.ljust(8)) if key == now
                else ui.paint("grey", key.ljust(8)))
        rows.append("   " + tick + " " + name + ui.paint("grey", EFFORT_LABEL[key]))
    if not now:
        rows.append("")
        rows.append(ui.paint("grey", "   （这条配置没设，按接口自己的默认走）"))
    rows.append("")
    rows.append(ui.notice(
        "/effort <档位> 切换，/effort 关 不思考 越高越肯想、也越贵 "
        "编程和 agent 类任务 xhigh 通常最划算 "
        "openai 兼容的接口多数不认这个参数，填了可能直接报错 "))
    return "\n".join(rows)


def _mcp(agent: Feiyu) -> str:
    reg = agent.toolbox.mcp
    if not reg.servers and not reg.errors:
        return ui.notice("没有配置 MCP 服务器 写在 %s 或项目的 %s 里，格式跟 Claude Code 一样："
                         '{"mcpServers": {"名字": {"command": "…", "args": […]}}}，'
                         '远端的写 {"type": "http", "url": "…", "headers": {…}}'
                         % (mcp_mod.GLOBAL, mcp_mod.PROJECT))
    rows = [ui.notice("MCP 服务器")]
    for name, server in reg.servers.items():
        alive = server.alive()
        rows.append("   " + (ui.bold(name) if alive else ui.paint("red", name + "（已退出）"))
                    + ui.paint("grey", "  " + server.label))
        for tool in server.tools:
            tag = " 只读" if mcp_mod.safe_name(name, tool["name"]) in reg.readonly else ""
            desc = ui.clip(" ".join(str(tool.get("description") or "").split()), ui.width() - 40)
            rows.append("      " + ui.paint("brown", tool["name"]) + ui.paint("grey", tag + "  " + desc))
    for name, why in reg.errors.items():
        rows.append("   " + ui.paint("red", name) + ui.paint("grey", "  没起来：" + why))
    rows.append("")
    rows.append(ui.notice("规则写 Mcp(服务器:*) 放行整个服务器；改了配置要重启 feiyu"))
    return "\n".join(rows)


def _theme(arg: str) -> str:
    if arg:
        if not ui.set_theme(arg):
            return ui.error("没有 %s 这个主题，可选：%s" % (arg, "、".join(ui.THEMES)))
        keys.set_theme(arg)
        return ui.notice("界面主题已切到 %s 上面已经画出来的不会重画，从下一条开始生效 "
                         % ui.THEMES[arg]["label"])
    rows = []
    for key, spec in ui.THEMES.items():
        tick = ui.paint("brown", " ●") if key == ui.THEME else "  "
        name = ui.bold(key.ljust(12)) if key == ui.THEME else ui.paint("grey", key.ljust(12))
        rows.append("   " + tick + " " + name + ui.paint("grey", spec["label"]))
    rows.append("")
    rows.append(ui.notice("/theme <名字> 切换 只换配色和记号，布局不动 "))
    return "\n".join(rows)


def _skills(agent: Feiyu, commands: dict, arg: str = "") -> str:
    """`/skills` 看清单，`/skills <名字>` 开关一份

    开关这件事桌面端有个面板，但**终端得有一条路能把关掉的东西开回来** ——
    只在图形界面里能开的话，没有图形界面的机器上那份技能就永远回不来了。
    """
    found = sorted(agent.toolbox.skills.values(), key=lambda s: s.name)
    arg = arg.strip()
    if arg:
        names = {s.name for s in found}
        if arg not in names:
            return ui.warn("没有叫 %s 的技能 现有：%s" % (arg, "、".join(sorted(names)) or "（一份都没有）"))
        now = enabled.set_on("skills", arg, not enabled.on("skills", arg))
        agent.apply_toggles()
        return ui.notice("技能 %s 已%s" % (arg, "打开" if now else "关掉"))
    if not found and not commands:
        return ui.notice(
            "还没有技能和自定义命令 一份技能就是一个 .md：全局放 %s/，"
            "项目放 %s/；开头可以写 --- name / description ---，正文是给它的指令 "
            "命令换成 %s/，敲 /名字 触发，正文里的 $ARGUMENTS 换成你跟在后面的话 "
            % (skills_mod.GLOBAL_SKILLS, skills_mod.PROJECT_SKILLS, skills_mod.PROJECT_COMMANDS))
    rows = []
    if found:
        rows.append(ui.notice("技能（它自己按需读，用 skill 工具；/skills <名字> 开关一份）"))
        for one in found:
            # 关掉的才标出来 —— 常态不占位置，异常才出现
            state = "" if enabled.on("skills", one.name) else "（已关）"
            pack = "（包，%d 个附属文件）" % len(one.extras) if one.extras else ""
            rows.append("    " + ui.bold(ui.pad(one.name, 16))
                        + ui.paint("red" if state else "grey",
                                   "%s%s%s · %s" % (state, one.description, pack, one.source)))
    if commands:
        if rows:
            rows.append("")
        rows.append(ui.notice("命令（你敲 /名字 触发）"))
        for name in sorted(commands):
            one = commands[name]
            rows.append("    " + ui.bold(ui.pad("/" + name, 16))
                        + ui.paint("grey", "%s · %s" % (one.description, one.source)))
    if agent.skills_note:
        rows.append("")
        rows.append(ui.warn(agent.skills_note))
    return "\n".join(rows)


def _help() -> str:
    rows = [ui.notice("命令")]
    for cmd, desc in {**ui.SLASH, **ui.EXTRA_SLASH}.items():
        # 最长的命令 /compact / /context 正好 8 个字符，ljust(8) 会让名字和说明贴在一起
        rows.append("    " + ui.bold(ui.pad(cmd, 10)) + ui.paint("grey", desc))
    rows.append("")
    rows.append(ui.notice("按键    ⏎ 发送 · \\⏎ 换行 · shift+tab 切模式 · esc 中断当前回答 · ctrl-d 退出"))
    rows.append(ui.notice("带文件  句子里写 @路径（Tab 补全），发出去之前就替它读好，省一次往返"))
    rows.append(ui.notice("版本    feiyu " + __version__))
    return "\n".join(rows)


def _finish(agent: Feiyu, tracer, code: int) -> int:
    # 清单没做完就把进度留下，下次进这个目录时它自己知道上次干到哪
    # 放在三条路径共同的收尾里，一次覆盖全部；全做完时 save 自己会把旧的清掉
    progress.save(agent.toolbox.root, agent.task, agent.toolbox.todos.items)
    # 后台命令跟着 feiyu 一起收摊 留着的话用户关掉窗口之后那些进程还在跑，
    # 而他已经没有任何句柄能找到它们了
    agent.toolbox.lsp.close()
    agent.toolbox.mcp.close()
    agent.toolbox.shell_reset()     # /tmp 里那份 shell 状态，里头是整份环境
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
    total_in = total_hit = 0
    priced = False
    import time as _time
    for run in sorted(runs.values(), key=lambda r: r["start"])[-10:]:
        when = _time.strftime("%m-%d %H:%M", _time.localtime(run["start"]))
        money = pricing.yuan(run["cost"]) if run["priced"] else "—"
        task = ui.clip(run["task"] or "（交互）", 30)
        # 最慢那一轮显在最后：整条 run 的时长里有多少是"等模型"，看这个数最直接
        slow = "%.0fs" % (run["slowest"] / 1000) if run.get("slowest") else "—"
        rows.append("   " + ui.paint("grey", "%s  %-30s %4s轮 %7s %5s %7s %8s" % (
            when, task, run["turns"], ui.tokens(run["tokens"]),
            _hit_rate(run["hit"], run["input"]), "最慢" + slow, money)))
        total_tokens += run["tokens"]
        total_cost += run["cost"]
        total_in += run["input"]
        total_hit += run["hit"]
        priced = priced or run["priced"]
    # 接口那边的账 —— 跟上面按你填的价格估出来的花费不是一回事，所以单独一行
    entry = keys.load().get(agent.name or "")
    if entry:
        # **只读缓存**：这条路是同步的，走代理偶尔要八秒，
        # 让终端为一个锦上添花的数字卡八秒是本末倒置 顺手在后台刷一次，
        # 下次 /cost 就有了
        got, why = credits.look(entry, cached=True)
        if why.startswith("还没查到"):
            agent.refresh_credits()
        if got:
            bits = ["余额 " + got.shown()]
            if got.total is not None:
                bits.append("充值 %s%s" % (got.unit, got.total))
            if got.used is not None:
                bits.append("已用 %s%s" % (got.unit, round(got.used, 4)))
            rows += ["", "   " + ui.paint("grey", "接口那边：" + " · ".join(bits))]
        elif why:
            rows += ["", "   " + ui.paint("grey", "接口那边：" + ui.clip(why, 60))]
    rows += ["", "   " + ui.bold("合计  %s  %s  缓存命中 %s" % (
        ui.tokens(int(total_tokens)), pricing.yuan(total_cost) if priced else "未配价格",
        _hit_rate(total_hit, total_in)))]
    if agent.ready and agent.tokens:
        b = agent.backend
        seen, hit = getattr(b, "tokens_in", 0), getattr(b, "tokens_hit", 0)
        rows.append("   " + ui.paint("grey", "本次会话 %s · 缓存命中 %s" % (
            ui.tokens(agent.tokens), _hit_rate(hit, seen))))
        if seen > 20000 and not hit:
            rows.append("   " + ui.paint("grey",
                "一次都没命中：这个接口不缓存，或者不回报命中数 "
                "每轮重发的历史都按原价算，长任务的开销主要在这儿"))
    rows += ["", ui.notice("缓存命中是重发的历史里按缓存价算的那部分，高就说明长任务不贵")]
    rows += [ui.notice("完整日志 %s · FEIYU_NO_LOG=1 可以关掉记录" % trace.DIR)]
    return "\n".join(rows)


def _hit_rate(hit: int, total: int) -> str:
    return ("%d%%" % round(100 * hit / total)) if total else "—"


def _piped(agent: Feiyu, workspace) -> int:
    """stdin 不是终端时（管道、脚本）：一行一个任务，不画框 """
    worst = 0
    for line in sys.stdin:
        task = line.strip()
        if task and task != "/exit":
            code = _run_turn(agent, task)
            worst = max(worst, code)
            session.save(workspace, agent, agent.session_id)
            # **被打断就整个停下，别接着跑下一条。**
            # 原来这里是一路跑到底的：`cat 活儿.txt | feiyu --mode auto` 中途
            # 按 ctrl-c，只打断了当前这一条，后面几条照样执行 —— 而 auto 模式下
            # 那意味着它接着改文件、接着跑命令。**人按 ctrl-c 的意思是"全停"。**
            # 出错（1）不停：一条失败不代表后面几条也做不了，那是另一回事。
            if code == 130:
                print(ui.warn("已中断，剩下的任务不再执行"))
                break
    return worst


# 接口报错时给一句能照着做的话 原样甩 JSON 的话用户只能干瞪眼
# 按 HTTP 状态码给的话 **要认状态码本身，不能拿 "401" 去整条消息里搜** ——
# 接口的报错里带着 request id、余额、时间戳，随便哪个数字串里出现 401
# 就会告诉用户"key 过期了"，把人往错方向指（0.26.0 说 404 那次就是这个教训）
_API_CODES = {
    401: "key 不对或者过期了 /key 看看这条配置，或者 /key add 重新加一条",
    402: "余额不够了 状态栏那个额度点一下能现查一次，或者 /model 换一条配置\n"
         "  跑长任务前先看一眼额度：中途烧完的话它当场停，而停在哪一步不会写在任何地方",
    403: "这个 key 没有访问权限，确认一下它开通了这个模型",
    404: "接口说没有这个东西 两种可能：模型 id 不对（各家退役很勤，"
         "去厂商后台看现在叫什么），或者 base_url 多写了一段路径"
         "（多数接口填到 /v1 就行）/key 看看这条配置",
    429: "被限流了 等一会儿再试，或者 /model 换一条配置",
}
# 这几条本来就是按措辞认的，状态码指望不上（各家把它塞在 400 里）
_API_WORDS = (
    ("insufficient", "余额不够了 状态栏那个额度点一下能现查一次"),
    # openrouter 的 402 说的是 "requires more credits"，一个 insufficient 都没有 ——
    # 402 已经进了 `_API_CODES`，这条是给那些**把它塞进 400** 的接口兜底
    ("requires more credits", "余额不够了 状态栏那个额度点一下能现查一次"),
    ("exceed the limit", "这一轮发过去的内容超过了模型的上下文上限 "
                         "/compact 压一压，或者 /key add 给这条配置填上 limit"),
    ("Connection", "连不上接口 检查网络，或者这条配置的 base_url 写错了"),
    ("Timeout", "接口超时了 网络不稳，或者这次要它做的事太重"),
)
_CODE_IN_TEXT = re.compile(r"error code:?\s*(\d{3})", re.I)


def _status_of(exc: Exception) -> int | None:
    """这个报错是几号 优先问 SDK 自己（两种后端的异常都带 status_code）"""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    found = _CODE_IN_TEXT.search(str(exc))
    return int(found.group(1)) if found else None


def _explain(exc: Exception) -> str:
    """把接口报错翻成一句能照着做的话 认不出就返回空串，照旧只给原文 """
    text = "%s: %s" % (type(exc).__name__, exc)
    # 措辞在前：一条 400 里写着"余额不足"，说余额比说"请求有问题"有用得多
    for probe, hint in _API_WORDS:
        if probe.lower() in text.lower():
            return hint
    return _API_CODES.get(_status_of(exc), "")


def _run_turn(agent: Feiyu, prompt: str) -> int:
    """跑一轮，返回退出码：0 做完了，1 出错，130 被打断

    **「没做成」必须能从退出码看出来。** 原来这里把异常接住、打印完就算完，
    而一次性任务那条路一律 `return _finish(..., 0)` —— 于是接口挂掉
    （余额耗尽 / key 过期 / 被限流）跟顺利做完，在退出码上一模一样。

    屏幕前有人时无所谓，他看得见那句红字；**没人看屏幕的场景里这个区别就是全部**：
    0.72.0 那轮 24 道基线跑到一半 openrouter 余额见底，11 次 trial 全部当场退出、
    退出码 0，于是判分那边把它们记成「没解决」，解决率被白白压低 ——
    是事后翻 stdout 才发现的，而 stdout 正好是超时那几次没留下的东西。

    130 是 shell 惯例（被 SIGINT 停掉）。交互里这个返回值没人看，REPL 照旧接着转。
    """
    guard = ui.EscInterrupt()
    try:
        with guard:
            agent.send(prompt)
    except KeyboardInterrupt:
        print(ui.warn("已中断"))
        return 130
    except Exception as exc:
        hint = _explain(exc)
        if hint:
            print(ui.error(hint))
            print(ui.notice(ui.clip(str(exc), 200)))
        else:
            print(ui.error("出错了：%s" % exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
