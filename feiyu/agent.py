import difflib
import re
import subprocess
import time
from pathlib import Path

from . import ui
from . import hooks as hooks_mod
from . import keys, memory, permissions, schema as schema_mod
from .providers import ToolOutput, build_backend
from .tools import TOOL_SCHEMAS, Toolbox
from .trace import Trace

SYSTEM_PROMPT = """你是 Feiyu，一个在真实终端工作区里干活的编程 agent 你帮用户\
开发、调试、重构和理解代码 

工作方式：
- 改动之前先用工具了解项目 编辑前先读文件、搜索，不要凭空猜测内容 
- 改动要聚焦 优先用 edit_file，而不是重写整个文件 不要添加任务没要求的功能、\
抽象或错误处理 
- 改完代码要验证：用 bash 跑测试或相关命令 
- 写的代码要贴合周围的风格、命名和约定 
- 执行会改变状态的命令（删除、git push、安装）之前，先确认有足够依据支持这个\
具体操作 

沟通：
- 工具调用之间默认保持安静 只在有发现、要改变方向或遇到阻碍时说一句话 
- 做完后用一两句话说清结果，先说发生了什么 不用逐个复述你改过的文件，用户一直\
在看 
- 遇到小选择（起个名字、定个默认值、两种等价做法选哪个），自己挑个合理的并说明，\
别停下来问 涉及范围变化或破坏性操作时，先问 """


# 上下文接近上限时自动压缩 具体上限可以在 models.json 里给某条配置写 "limit"
DEFAULT_CONTEXT_LIMIT = 128_000
COMPACT_RATIO = 0.75

# 一次任务里最多连续调多少轮工具 纯保险：模型卡在某个循环里时不至于一直转下去
MAX_ROUNDS = 25


# 工作区里放一份约定给它读 按这个顺序找第一个存在的
CONTEXT_FILES = ("FEIYU.md", "AGENTS.md", "CLAUDE.md")
MAX_CONTEXT_CHARS = 8000

# 约定文件里单起一行的 `verify: <命令>`，改完代码自动跑它
# 要求顶格且整行只有这一句，免得正文里随口提一嘴 verify 就被当成配置
VERIFY_LINE = re.compile(r"^verify:[ \t]*`?([^`\n]+?)`?[ \t]*$", re.M | re.I)
# 校验输出回灌给模型时截多长 报错通常在头尾，中间的堆栈没那么重要
VERIFY_MAX_CHARS = 4000
VERIFY_TIMEOUT = 300


SCORE_LINE = re.compile(r"评分[:：]\s*(\d+(?:\.\d+)?)\s*/\s*10")


def find_score(text: str) -> float | None:
    hit = SCORE_LINE.search(text or "")
    return float(hit.group(1)) if hit else None


def _clip_middle(text: str, limit: int) -> str:
    """报错通常在头尾，中间的堆栈没那么重要 """
    if len(text) <= limit:
        return text
    return text[:limit // 2] + "\n\n…（中间省略）…\n\n" + text[-limit // 2:]


def find_verify(text: str) -> str:
    hit = VERIFY_LINE.search(text or "")
    return hit.group(1).strip() if hit else ""


def load_project_context(workspace: Path) -> tuple[str, str]:
    """返回 (文件名, 内容)，找不到返回两个空串 """
    for name in CONTEXT_FILES:
        target = workspace / name
        try:
            if not target.is_file():
                continue
            text = target.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return name, text[:MAX_CONTEXT_CHARS]
    return "", ""


# 权限模式 会改变状态的三个工具受管，读类工具永远放行
MODES = {
    "default": ("默认", "允许列表里的直接跑，其余问你一声"),
    "auto": ("自动", "所有工具调用直接执行，不打断"),
    "readonly": ("只读", "只让它读文件、列目录、搜索、抓网页，写和执行一律拒绝"),
}
MODE_ORDER = ("default", "auto", "readonly")
# confirm 模式下要问一声的：改本地状态的，加上会把内容发到外网的
# remember 也在里面：它写的东西会进以后每一次的系统提示，而内容可能间接来自网页，
# 不问一声的话就是一条能跨会话的注入通道 好在它很少调，问一次不烦
GUARDED = {"bash", "write_file", "edit_file", "web_fetch", "remember"}
# readonly 模式下直接拒绝的 web_fetch 不改任何东西，所以只读模式放行
# remember 要拒：它不动工作区，但确实往磁盘上留了东西，只读就该是真的什么都不留
WRITES = {"bash", "write_file", "edit_file", "remember"}
# 跑完这两个才触发自动校验 bash 也能改文件（sed -i），但把它算进来的话
# 每次 ls / git status 之后都要跑一遍测试，代价远大于收益
VERIFY_AFTER = {"write_file", "edit_file"}


# 不告诉它跑在什么模型上的话，被问起来它会自己编一个（实测 deepseek 会说自己是 Claude）
IDENTITY = (
    "\n\n关于你自己：你当前使用的模型是 {model}（配置名 {name}） "
    "用户问起你是什么模型时照这个回答，不要猜，也不要说成别家的模型 "
)


# 回答风格：接在 SYSTEM_PROMPT 后面的一段补充指令
STYLES = {
    "default": ("默认", ""),
    "concise": (
        "简洁",
        "\n\n再压缩一些：能一句话说完就不要两句 除非用户问，否则不解释实现细节 ",
    ),
    "explanatory": (
        "详细",
        "\n\n每次改动后多说一两句为什么这么改、放弃了什么方案，让用户看懂取舍 ",
    ),
    "teaching": (
        "教学",
        "\n\n把用户当成正在学这块的人：动手前先讲清思路，改完解释涉及的概念和为什么这样写 "
        "遇到值得一提的坑就顺带说明 ",
    ),
}

REVIEW_PROMPT = """你是独立的验收人 下面是一个编程 agent 接到的任务和它最后交出的改动 \
你**没有**参与过这个任务，也看不到它的思考过程——这是故意的，你只看产出 

按这几点给结论：
1. 任务要求的事情做到了没有 有没有漏掉的部分 
2. 改动本身有没有 bug、有没有破坏原有行为 
3. 有没有做任务没要求的事（多加的功能、多写的抽象、没必要的重构）

最后单起一行给一个 0-10 的总分，写成 `评分: N/10` 8 分以上表示可以交付 
挑毛病要具体到文件和行为，别写"建议进一步完善"这种空话 没问题就直说没问题 """

# 评分 agent 看到的产出：优先 git diff，没有版本库就退回列改过的文件
REVIEW_MAX_CHARS = 40_000


class Feiyu:
    def __init__(self, workspace: Path, *, model: str = "", trace: Trace | None = None):
        self.toolbox = Toolbox(workspace, ask_model=self._ask_model,
                               on_remember=self._refresh_system)
        self.context_file, self.context_text = load_project_context(workspace)
        self.trace = trace or Trace(workspace, enabled=False)
        self.verify = find_verify(self.context_text)
        self.verified_ok = None      # 最近一次校验过没有：True/False/None（没跑过）
        self.hooks: list = []
        self.hooks_note = ""
        self.approve_hooks = None    # 交互层塞进来：(路径, 条数) -> bool
        self.touched: list[str] = []      # 这次运行改过哪些文件，/review 要用
        self.before: dict[str, str] = {}  # 它们被动之前长什么样，没有 git 时靠这个算 diff
        self.style = "default"
        self.mode = "default"
        self.approve = None      # 由界面层塞进来：(工具名, 参数) -> "yes"/"no"/"always"
        self.name = ""           # 当前用的是 /key 里的哪一条
        self.context_limit = DEFAULT_CONTEXT_LIMIT
        self.backend = None
        if model:
            self.use_model(model)

    @property
    def ready(self) -> bool:
        return self.backend is not None

    def _ask_model(self, prompt: str) -> str:
        """给 web_fetch 的 prompt 模式用：读完页面只回答问题，原文不进主上下文 """
        if self.backend is None:
            return ""
        try:
            return (self.backend.ask(prompt) or "").strip()
        except Exception:
            return ""

    def _refresh_system(self) -> None:
        """身份和风格都会变，每次变完重拼一遍系统提示 """
        if self.backend is None:
            return
        text = SYSTEM_PROMPT + IDENTITY.format(model=self.model, name=self.name) + STYLES[self.style][1]
        if self.context_text:
            # 项目约定放最后，比通用指令更具体，冲突时以它为准
            text += "\n\n这个项目在 %s 里写了自己的约定，优先照它来：\n\n%s" % (
                self.context_file, self.context_text)
        # 自己记的笔记排在人写的约定后面：约定是规矩，笔记只是背景
        text += memory.as_prompt(memory.load(self.toolbox.root))
        self.backend.set_system(text)

    def reload_context(self) -> None:
        self.context_file, self.context_text = load_project_context(self.toolbox.root)
        self.verify = find_verify(self.context_text)
        self._refresh_system()

    def load_hooks(self) -> str:
        """项目钩子是从仓库带下来的命令，要用户点头才载入 返回给人看的一句话 """
        self.hooks, self.hooks_note = hooks_mod.load(
            self.toolbox.root, approve=self.approve_hooks)
        self.trace.write("hooks", count=len(self.hooks) or None,
                         note=self.hooks_note or None)
        return self.hooks_note

    @property
    def all_hooks(self) -> list:
        """配置里的钩子，加上 `verify:` 合成的那条

        verify 就是「改完文件跑个命令」，本来就是 PostToolUse 的特例
        合成成钩子而不是各走各的，省得两套机制各写一遍、行为还不一样
        """
        found = list(self.hooks)
        if self.verify:
            found.append(hooks_mod.Hook(
                "PostToolUse",
                {"matcher": "Write|Update", "command": self.verify,
                 "timeout": VERIFY_TIMEOUT},
                "verify:"))
        return found

    def _fire(self, event: str, tool: str = "", subject: str = "",
              extra: dict | None = None) -> list:
        """跑这个时机上所有匹配的钩子，返回结果列表 """
        chosen = hooks_mod.pick(self.all_hooks, event, tool, subject)
        results = []
        for hook in chosen:
            payload = {"event": event, "workspace": str(self.toolbox.root),
                       "tool": tool, "subject": subject}
            payload.update(extra or {})
            began = time.monotonic()
            outcome = hooks_mod.run(hook, payload, self.toolbox.root)
            ms = round((time.monotonic() - began) * 1000)
            self.trace.write("hook", event=event, command=hook.command,
                             code=outcome.code, ms=ms)
            if hook.source == "verify:":
                self.verified_ok = outcome.ok
            if outcome.ok:
                print(ui.notice("钩子通过：%s" % ui.clip(hook.command, 60)))
            else:
                print(ui.warn("钩子拦下了（退出码 %d）：%s" % (
                    outcome.code, ui.clip(hook.command, 60))))
            print()
            results.append(outcome)
        return results

    @property
    def tokens(self) -> int:
        """累计计费量 """
        return getattr(self.backend, "tokens_total", 0)

    @property
    def context(self) -> int:
        """最近一次请求的规模，即当前对话占了多少上下文 """
        return getattr(self.backend, "tokens_context", 0)

    @property
    def cost(self) -> float | None:
        """累计花费（人民币） 价格表里没有这个模型时返回 None """
        if not getattr(self.backend, "priced", False):
            return None
        return getattr(self.backend, "cost", 0.0)

    def reset(self) -> None:
        self.backend.reset()

    @property
    def model(self) -> str:
        return self.backend.model if self.backend else ""

    def list_models(self) -> list[str]:
        return self.backend.list_models() if self.backend else []

    def use_model(self, name: str) -> bool:
        """切到 /key 里的某一条 换了接口就得换客户端，所以整个后端重建

        消息格式一样的话（都是 openai 兼容 / 都是 anthropic）历史搬过去，
        否则只能丢掉——两边的 messages 结构不通用
        """
        entry = keys.load().get(name)
        if entry is None:
            return False
        old = self.backend
        fresh = build_backend(entry, system=SYSTEM_PROMPT, tools=TOOL_SCHEMAS)
        if old is not None:
            same = type(fresh) is type(old)
            if same:
                fresh.messages = old.messages
                fresh.tokens_context = old.tokens_context
            fresh.tokens_total = old.tokens_total
            fresh.cost = old.cost
            fresh.priced = old.priced
        self.backend = fresh
        self.name = name
        self.context_limit = int(entry.get("limit") or DEFAULT_CONTEXT_LIMIT)
        keys.set_last(name)
        self._refresh_system()
        return True

    def cycle_mode(self) -> str:
        self.mode = MODE_ORDER[(MODE_ORDER.index(self.mode) + 1) % len(MODE_ORDER)]
        return self.mode

    def set_style(self, name: str) -> None:
        self.style = name
        self._refresh_system()

    def send(self, user_message: str) -> str:
        if self.backend is None:
            raise RuntimeError("还没有可用的模型 先用 /key add 添加一个，再用 /model 选中 ")
        blocked, note = self._before_prompt(user_message)
        if blocked:
            return note
        self.backend.add_user_message(user_message + note)
        rounds = 0
        while True:
            stream = ui.Stream()
            stream.start()
            try:
                # 中断/报错时也必须停掉 spinner 线程，否则它会继续往屏幕上糊帧
                turn = self.backend.run_turn(on_text=stream.write)
            except KeyboardInterrupt:
                # 流到一半被打断，run_turn 没来得及把 assistant 写进历史
                # 不补的话已经说出口的话丢掉，而且角色不再交替
                self.trace.write("interrupt", where="stream")
                self._note_interrupted(stream.text)
                raise
            finally:
                stream.finish()

            self.trace.write("turn", round=rounds, tokens=self.context,
                             cost=self.cost, truncated=turn.truncated or None,
                             calls=[c.name for c in turn.tool_calls] or None)
            if turn.truncated:
                print(ui.warn("这一轮的输出撞上了长度上限，是半截的"))
                print()
            if not turn.tool_calls:
                self._fire("Stop", extra={"text": turn.text})
                self._maybe_compact()
                return turn.text

            if stream.wrote_text:
                print()
            outputs: list[ToolOutput] = []
            changed = False
            try:
                changed = self._run_tools(turn.tool_calls, outputs)
            finally:
                # 每个 tool_use 都必须配一条结果，中断也不例外 少一条的话
                # 历史里就留下没配对的调用，之后每次请求都会被接口 400 掉
                self._fill_missing(turn.tool_calls, outputs)
                if changed:
                    self._after_tools(changed, outputs)
                self.backend.add_tool_results(outputs)
            rounds += 1
            if rounds >= MAX_ROUNDS:
                return self._stop_runaway(rounds)
            self._maybe_compact(mid_turn=True)

    INTERRUPTED = (
        "用户按 esc 打断了这次调用，它没有跑完，可能已经改了一部分东西 "
        "不要自动重试，先说一句你停在哪儿，等用户给下一步 "
    )

    def _note_interrupted(self, said: str) -> None:
        """把打断前已经说出口的话写回历史，保住角色交替 """
        text = (said or "").strip()
        self.backend.messages.append({
            "role": "assistant",
            "content": (text + "\n\n[用户在这里打断了]") if text else "[回答被用户打断]",
        })

    def _fill_missing(self, calls, outputs: list) -> None:
        done = {o.id for o in outputs}
        outputs.extend(
            ToolOutput(id=c.id, content=self.INTERRUPTED, is_error=True)
            for c in calls if c.id not in done
        )

    # 放行 / 拒绝的三种结果：(是否放行, 是否从此全放, 给人看的短语, 给模型看的说明)
    _PASS = (True, False, "", "")
    _ALL = (True, True, "", "")

    def restore(self, data: dict) -> bool:
        """从存盘恢复 系统提示会按当前的身份/风格/项目约定重新拼，不用存盘里的那份 """
        name = data.get("model") or ""
        if not name or not self.use_model(name):
            return False
        if data.get("style") in STYLES:
            self.style = data["style"]
        if data.get("mode") in MODES:
            self.mode = data["mode"]
        b = self.backend
        b.messages = data["messages"]
        b.tokens_total = int(data.get("tokens_total") or 0)
        b.tokens_context = int(data.get("tokens_context") or 0)
        b.cost = float(data.get("cost") or 0.0)
        b.priced = bool(data.get("priced", True))
        self._refresh_system()
        return True

    def compact(self) -> str:
        """把历史交给模型总结成一段交接说明，替换掉原文 返回摘要，失败返回空串 """
        if self.backend is None:
            return ""
        summary = (self.backend.summarize() or "").strip()
        if not summary:
            return ""
        self.backend.compact_to(summary)
        self.backend.tokens_context = 0
        return summary

    def changes(self) -> str:
        """这次运行的最终产出 有 git 就拿 diff，没有就把改过的文件读出来 """
        try:
            done = subprocess.run(
                ["git", "diff", "HEAD"], cwd=self.toolbox.root,
                capture_output=True, text=True, timeout=30,
            )
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout[:REVIEW_MAX_CHARS]
        except (OSError, subprocess.SubprocessError):
            pass
        if not self.touched:
            return ""
        parts = []
        for path in self.touched:
            try:
                after = self._resolve_text(path)
            except (OSError, ValueError) as exc:
                parts.append("===== %s =====\n（读不出来：%s）" % (path, exc))
                continue
            before = self.before.get(path, "")
            diff = "".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=path + "（改动前）", tofile=path + "（改动后）"))
            parts.append(diff or "===== %s =====\n（内容没变）" % path)
        return "\n\n".join(parts)[:REVIEW_MAX_CHARS]

    def review(self, task: str) -> str:
        """开一个只看产出的模型给这次改动打分

        **必须是全新的后端实例**：复用 self.backend 的话它带着整段对话历史，
        等于让它自己批改自己的作业，分数没有参考价值
        """
        if self.backend is None:
            return ""
        body = self.changes()
        if not body.strip():
            return ""
        entry = keys.load().get(self.name)
        if entry is None:
            return ""
        judge = build_backend(entry, system=REVIEW_PROMPT, tools=[])
        prompt = "%s\n\n===== 任务 =====\n%s\n\n===== 交出的改动 =====\n%s" % (
            REVIEW_PROMPT, task or "（没有记录任务描述）", body)
        try:
            verdict = (judge.ask(prompt, max_tokens=4000) or "").strip()
        except Exception as exc:
            return "评分没跑成：%s" % exc
        self.trace.write("review", score=find_score(verdict), chars=len(body))
        return verdict

    def finish_json(self, spec: dict) -> tuple[object, list[str]]:
        """任务做完后再要一轮，让它按 schema 吐 JSON 返回 (值, 问题列表)

        单起一轮不带工具的请求，而不是一开始就要求它边干活边想着输出格式——
        后者会让它在整个任务里都分心去凑格式
        """
        if self.backend is None:
            return None, ["还没有可用的模型"]
        self.backend.add_user_message(schema_mod.describe(spec))
        for attempt in range(schema_mod.MAX_REPAIR + 1):
            stream = ui.Stream()
            stream.start()
            try:
                turn = self.backend.run_turn(on_text=stream.write)
            finally:
                stream.finish()
            value, bad = schema_mod.extract(turn.text)
            problems = [bad] if bad else schema_mod.validate(value, spec)
            self.trace.write("json", attempt=attempt, ok=not problems,
                             problems=problems or None)
            if not problems:
                return value, []
            if attempt == schema_mod.MAX_REPAIR:
                return value, problems
            print(ui.warn("输出不合 schema，让它重出一次：%s" % "；".join(problems[:3])))
            self.backend.add_user_message(
                "刚才那份输出不满足 schema：\n- %s\n\n重新只输出一个合规的 JSON 对象，"
                "不要解释 " % "\n- ".join(problems))
        return None, ["重试了 %d 次仍然不合 schema" % schema_mod.MAX_REPAIR]

    def _stop_runaway(self, rounds: int) -> str:
        """连续调了太多轮工具还没收敛，收手 历史补一条 assistant 让角色保持交替 """
        note = (
            "连续调用了 %d 轮工具还没做完，我先停下来 "
            "可能是我卡在某个循环里了，也可能这个任务需要拆小一点 " % rounds
        )
        self.backend.messages.append({"role": "assistant", "content": note})
        print(ui.warn("已达到 %d 轮工具调用上限，自动停下 再说一句话可以让它接着做 " % rounds))
        print()
        return note

    def _maybe_compact(self, *, mid_turn: bool = False) -> None:
        if self.backend is None or self.context <= self.context_limit * COMPACT_RATIO:
            return
        print(ui.notice("上下文快满了（%s / %s），正在压缩…" % (
            ui.tokens(self.context), ui.tokens(self.context_limit))))
        if self.compact():
            print(ui.notice("已压缩成一段摘要，早先的原文不再重发"))
            if mid_turn:
                # compact_to 收尾是 assistant，任务还没做完，得再补一条 user 把话接上
                self.backend.add_user_message("（以上是压缩后的进展摘要）接着把刚才的任务做完 ")
        else:
            print(ui.warn("压缩没成功，历史照旧 需要的话用 /clear 清空"))
        print()

    def _gate(self, call, allow_all: bool):
        if self.mode == "readonly" and call.name in WRITES:
            return (False, False, "只读模式已拒绝",
                    "当前是只读模式，不允许写文件或执行命令 要做这一步，用户得先按 shift+tab "
                    "或用 /mode 切到 default（按允许列表）或 auto（直接执行） "
                    "别自己编模式名字，就说这两个 ")

        ruling = permissions.verdict(call.name, call.input)
        if ruling == "deny":
            return (False, False, "被权限规则拒绝",
                    "这条调用被用户的权限规则挡下了（可能涉及密钥或私钥路径） "
                    "别绕道去试别的写法，把你想做什么告诉用户，让他决定 ")
        if ruling == "allow" or self.mode == "auto" or call.name not in GUARDED or allow_all:
            return self._PASS
        if self.mode == "readonly":
            return self._PASS

        answer = self.approve(call.name, call.input) if self.approve else "no"
        if answer == "always":
            return self._ALL
        if answer == "remember":
            permissions.add_allow(permissions.suggest(call.name, call.input))
            return self._PASS
        if answer == "yes":
            return self._PASS
        return (False, False, "用户拒绝",
                "用户拒绝了这次调用 换个做法，或者先问清楚他想怎么做 ")

    def _run_tools(self, calls, outputs: list[ToolOutput]):
        """结果追加进 outputs 中断时调用方要靠它知道跑到哪一个了

        返回这一批里最后一次真的改到代码的 (工具名, 主参数)，没改到就是 None
        PostToolUse 的 matcher 要拿它来匹配
        """
        allow_all = False
        changed = None
        for call in calls:
            print(ui.tool_call(call.name, call.input), flush=True)
            if call.error:
                print(ui.tool_result("参数没解析出来", [], is_error=True))
                print()
                self.trace.write("tool", name=call.name, ok=False, why="参数没解析出来")
                outputs.append(ToolOutput(id=call.id, content=call.error, is_error=True))
                continue
            veto = self._pre_tool(call)
            if veto:
                print(ui.tool_result("钩子拦下", [], is_error=True))
                print()
                outputs.append(ToolOutput(id=call.id, content=veto, is_error=True))
                continue
            ok, always, label, reason = self._gate(call, allow_all)
            allow_all = allow_all or always
            if not ok:
                print(ui.tool_result(label, [], is_error=True))
                print()
                self.trace.write("tool", name=call.name, args=call.input,
                                 ok=False, why=label)
                outputs.append(ToolOutput(id=call.id, content=reason, is_error=True))
                continue
            if call.name in VERIFY_AFTER:
                self._snapshot(call)
            began = time.monotonic()
            result = self.toolbox.run(call.name, call.input)
            summary = result.summary or result.content.splitlines()[0][:80]
            print(ui.tool_result(summary, result.detail, is_error=result.is_error))
            print()
            self.trace.write("tool", name=call.name, args=call.input,
                             ok=not result.is_error, summary=summary,
                             ms=round((time.monotonic() - began) * 1000))
            if call.name == "remember" and not result.is_error:
                # 它写的东西会进以后每一次系统提示，日志里必须留底
                self.trace.write("remember", fact=call.input.get("fact"))
            if not result.is_error and call.name in VERIFY_AFTER:
                changed = (call.name, permissions.subject(call.name, call.input))
                self._remember_touched(call)
            outputs.append(
                ToolOutput(id=call.id, content=result.content, is_error=result.is_error)
            )
        return changed

    def _pre_tool(self, call) -> str:
        """PreToolUse 拦下就返回给模型看的说明 放在权限门**之前**：
        反正要被拦，没必要先烦用户点一次确认 """
        subject = permissions.subject(call.name, call.input)
        blocked = [r for r in self._fire("PreToolUse", tool=call.name, subject=subject)
                   if not r.ok]
        if not blocked:
            return ""
        return ("这次调用被项目的钩子挡下了 别绕过去，把情况告诉用户 原因：\n" +
                "\n".join("`%s`（退出码 %d）%s" % (r.hook.command, r.code, r.output)
                           for r in blocked))

    def _remember_touched(self, call) -> None:
        """记下改过哪些文件 /review 拿它当"最终产出"的清单 """
        path = call.input.get("path")
        if path and path not in self.touched:
            self.touched.append(path)

    def _snapshot(self, call) -> None:
        """动手之前存一份原样 没有 git 的工作区靠它算出真正的 diff

        不存的话 changes() 只能把改完的整个文件交出去，评分的模型看不到
        改动前是什么样，会把"文件本来就只有一个函数"读成"它把别的都删了"——
        实测踩过，评分因此被判到 4/10
        """
        path = call.input.get("path")
        if not path or path in self.before:
            return
        try:
            self.before[path] = self._resolve_text(path)
        except (OSError, ValueError):
            self.before[path] = ""

    def _resolve_text(self, path: str) -> str:
        target = self.toolbox._resolve(path)
        return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""

    def _before_prompt(self, text: str) -> tuple[bool, str]:
        """UserPromptSubmit：拦下就不发，放行时把 stdout 当额外背景带上 """
        results = self._fire("UserPromptSubmit", extra={"prompt": text})
        blocked = [r for r in results if not r.ok]
        if blocked:
            note = "钩子拦下了这次提问：\n" + "\n".join(r.output for r in blocked)
            print(ui.error(note))
            print()
            return True, note
        extra = [r.output for r in results if r.output]
        if not extra:
            return False, ""
        return False, "\n\n（钩子附带的背景信息，不是用户说的话）\n" + "\n".join(extra)

    def _after_tools(self, changed: bool, outputs: list[ToolOutput]) -> None:
        """PostToolUse：没过就把输出并进最后一条工具结果

        挂在工具结果里而不是另起一条 user 消息：两种后端的消息结构不通用，
        而 tool_result 是唯一两边都能安全追加内容的地方
        """
        if not outputs:
            return
        results = self._fire("PostToolUse", tool=changed[0], subject=changed[1])
        failed = [r for r in results if not r.ok]
        if not failed:
            return
        notes = "\n\n".join(
            "命令 `%s` **没有通过**（退出码 %d）输出如下：\n\n%s" % (
                r.hook.command, r.code, _clip_middle(r.output, VERIFY_MAX_CHARS))
            for r in failed)
        last = outputs[-1]
        outputs[-1] = ToolOutput(
            id=last.id,
            content=last.content + (
                "\n\n---\n\n改动之后自动跑了检查，先把下面这些修好再往下做，别绕过去 \n\n"
                + notes),
            is_error=True,
        )
