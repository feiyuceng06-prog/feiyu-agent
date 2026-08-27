"""子 agent：派出去摸情况，只把结论带回来

解决的是上下文问题 「这个仓库里哪些地方处理了超时」这种问题，自己搜要翻十几个
文件、几百行原文，全进主对话就把上下文撑爆了 派个子 agent 去看，主对话只收到
它写的一段结论

**工具集限死在只读三件**（read_file / list_files / search），这不是省事，是三个
性质一起来的：
- 它不会改任何东西，所以不需要中途向用户要确认（子 agent 跑在工作线程里，
  没法读 stdin）
- 因此它天然可并行，`task` 能进 PARALLEL_SAFE
- 没有 web_fetch，就没有「子 agent 读了个网页然后被诱导去干别的」这条注入路径

代价是它做不了需要联网或者动手的活 那些让主 agent 自己做
"""

TOOLS = ("read_file", "list_files", "search")
MAX_ROUNDS = 12
MAX_REPORT = 6000

SYSTEM = """你是被派出来摸情况的 有人给你一个问题，你去代码库里查清楚，然后写一段回答 

你只能读：read_file、list_files、search 改不了任何东西，也上不了网 

怎么干：
- 先用 search / list_files 把范围缩小，再读关键的那几个文件 别一上来就整个读一遍 
- 查到够回答问题就停，不要顺手把周边都摸一遍 

怎么答：
- 直接写结论 派你来的人看不到你读过什么，所以该说的事实要写在回答里 
- **凡是提到代码的地方都带上 `文件:行号`**，他要照着去看 
- 没查到就说没查到，别猜 查到的东西跟问题不完全对得上，也说清楚差在哪 
- 不用汇报你的过程，不用写「我搜索了……」 """


def describe(prompt: str, limit: int = 60) -> str:
    return " ".join((prompt or "").split())[:limit]


def run(prompt: str, *, entry: dict, workspace, trace=None) -> tuple[str, list, dict]:
    """跑一个子 agent 返回 (结论, 给人看的过程行, 用量)

    过程行不当场打印而是带回去：并行跑几个子 agent 时，各自往屏幕上写会交叉成
    一团 由调用方在主线程按顺序画
    """
    from .providers import ToolOutput, build_backend
    from .tools import TOOL_SCHEMAS, Toolbox

    schemas = [s for s in TOOL_SCHEMAS if s["name"] in TOOLS]
    backend = build_backend(entry, system=SYSTEM, tools=schemas)
    # 自己的 Toolbox：cwd、清单这些状态跟主 agent 和别的子 agent 都不共享
    box = Toolbox(workspace)
    backend.add_user_message(prompt)

    lines: list[tuple[str, str]] = []
    for _ in range(MAX_ROUNDS):
        turn = backend.run_turn(on_text=lambda _t: None)
        if not turn.tool_calls:
            report = (turn.text or "").strip()
            break
        outputs = []
        for call in turn.tool_calls:
            if call.name not in TOOLS:
                outputs.append(ToolOutput(
                    id=call.id, is_error=True,
                    content="子 agent 只能用 %s" % "、".join(TOOLS)))
                continue
            result = box.run(call.name, call.input)
            lines.append(("", "%s %s" % (
                call.name, _target(call.input))[:100] + " → " + (result.summary or "")))
            outputs.append(ToolOutput(id=call.id, content=result.content,
                                      is_error=result.is_error))
        backend.add_tool_results(outputs)
    else:
        report = "（查了 %d 轮还没收敛，先停了 问题可能太大，拆小一点再派）" % MAX_ROUNDS

    if not report:
        report = "（什么都没查到）"
    # 子 agent 的用量不进主对话，但照样是要付钱的 —— 不带回去就成了看不见的开销
    used = {"tokens": getattr(backend, "tokens_total", 0),
            "cost": getattr(backend, "cost", 0.0),
            "priced": getattr(backend, "priced", False)}
    if trace is not None:
        trace.write("subagent", prompt=prompt, rounds=len(lines), **used)
    return report[:MAX_REPORT], lines, used


def _target(args: dict) -> str:
    for key in ("path", "regex", "pattern"):
        if args.get(key):
            return str(args[key])
    return ""
