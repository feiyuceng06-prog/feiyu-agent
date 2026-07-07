import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .providers import ToolOutput, build_backend
from .tools import TOOL_SCHEMAS, Toolbox

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


class Feiyu:
    def __init__(
        self,
        workspace: Path,
        *,
        provider: str = "anthropic",
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.toolbox = Toolbox(workspace)
        self.backend = build_backend(
            provider,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            model=model,
            base_url=base_url,
        )

    def send(self, user_message: str) -> str:
        self.backend.add_user_message(user_message)
        while True:
            out = StreamOut()
            out.start()
            turn = self.backend.run_turn(on_text=out.write)
            out.finish()
            if turn.text:
                print()

            if not turn.tool_calls:
                return turn.text

            outputs = self._run_tools(turn.tool_calls)
            self.backend.add_tool_results(outputs)

    def _run_tools(self, calls) -> list[ToolOutput]:
        outputs = []
        for call in calls:
            print(f"\033[2m→ {call.name}({_preview(call.input)})\033[0m", flush=True)
            result = self.toolbox.run(call.name, call.input)
            outputs.append(
                ToolOutput(id=call.id, content=result.content, is_error=result.is_error)
            )
        return outputs


class StreamOut:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, *, spinner_delay: float = 0.2, tick: float = 0.01):
        self._tty = sys.stdout.isatty()
        self._buf: deque[str] = deque()
        self._lock = threading.Lock()
        self._first = False
        self._done = False
        self._spinner_delay = spinner_delay
        self._tick = tick
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if self._tty:
            self._thread.start()

    def write(self, text: str) -> None:
        if not text:
            return
        if not self._tty:
            print(text, end="", flush=True)
            return
        with self._lock:
            self._buf.extend(text)

    def finish(self) -> None:
        if not self._tty:
            return
        with self._lock:
            self._done = True
        self._thread.join()

    def _run(self) -> None:
        frame = 0
        while not self._first:
            with self._lock:
                if self._buf:
                    self._first = True
                    break
                done = self._done
            if done:
                return
            if time.monotonic() - self._start >= self._spinner_delay:
                sys.stdout.write(f"\r\033[2m{self.FRAMES[frame]} 思考中\033[0m")
                sys.stdout.flush()
                frame = (frame + 1) % len(self.FRAMES)
            time.sleep(0.08)

        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

        while True:
            with self._lock:
                backlog = len(self._buf)
                n = min(backlog, max(1, backlog // 8))
                chunk = "".join(self._buf.popleft() for _ in range(n))
                drained = not self._buf and self._done
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
            if drained:
                return
            time.sleep(self._tick)

        if spinning:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


def _preview(args: dict) -> str:
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text = text.replace("\n", "\\n")
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)
