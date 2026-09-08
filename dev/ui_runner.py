"""挂假后端跑真主程序，给 ui_drive.py 用 脚本化的一轮：列文件、读文件、改文件（要确认）、跑测试、收尾带表格和代码块

环境变量：FEIYU_UI_THEME 主题、FEIYU_UI_WS 工作区（默认 dev/ws，会被写）、FEIYU_UI_STATE 状态目录
"""
import json, os, sys, tempfile
S = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(S)
sys.path.insert(0, ROOT)
os.environ["FEIYU_NO_LOG"] = "1"
from feiyu import keys, permissions, session, memory, progress, agent as agent_mod
from feiyu.providers import ToolCall
from tests.fakes import FakeBackend

st = os.environ.get("FEIYU_UI_STATE") or tempfile.mkdtemp(prefix="feiyu-ui-")
keys.STORE = os.path.join(st, "models.json"); keys.STATE = os.path.join(st, "state.json")
permissions.STORE = os.path.join(st, "permissions.json")
session.DIR = os.path.join(st, "sessions"); memory.DIR = os.path.join(st, "memory")
progress.DIR = os.path.join(st, "progress")
os.makedirs(st, exist_ok=True)
with open(keys.STATE, "w") as f:
    json.dump({"theme": os.environ.get("FEIYU_UI_THEME", "default")}, f)
with open(keys.STORE, "w") as f:
    json.dump({"deepseek-v4": {"kind": "openai", "model": "deepseek-v4-flash", "key": "sk-xxxxxxxxxxxx",
                               "base_url": "http://x", "price": {"hit": 0.2, "input": 1, "output": 2}}}, f)

SCRIPT = [
    ("好的，我先看一下项目里有什么。", [ToolCall(id="c1", name="list_files", input={"pattern": "*.py"}),
                                   ToolCall(id="c2", name="read_file", input={"path": "calc.py"})]),
    ("看完了。`calc.py` 里现在只有 **add** 和 **sub** 两个函数，测试只覆盖了 `add`。我打算：\n\n"
     "- 在 `calc.py` 末尾加一个 `mul(a, b)`\n- 给 `test_calc.py` 补一条用例\n- 跑一遍测试确认\n\n"
     "先加函数：", [ToolCall(id="c3", name="edit_file", input={"path": "calc.py", "old_str": "    return a - b\n",
                       "new_str": "    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"})]),
    ("", [ToolCall(id="c4", name="bash", input={"command": "python -m unittest -q 2>&1 | tail -3"})]),
    ("搞定了。改动如下：\n\n| 文件 | 改了什么 |\n|---|---|\n| `calc.py` | 新增 `mul` |\n| `test_calc.py` | 没动，测试仍然全绿 |\n\n"
     "用法示例：\n\n```python\nfrom calc import mul\nprint(mul(6, 7))  # 42\n```\n\n"
     "> 注意：`mul` 没有做类型检查，传字符串进去会按 Python 的语义重复拼接。", []),
]

SCRIPT_EN = [
    ("Let me look at the project first.", [ToolCall(id="c1", name="list_files", input={"pattern": "*.py"}),
                                           ToolCall(id="c2", name="read_file", input={"path": "calc.py"})]),
    ("`calc.py` has **add** and **sub**; the tests only cover `add`. Plan:\n\n"
     "- add `mul(a, b)` at the end of `calc.py`\n- run the tests\n\nAdding the function:",
     [ToolCall(id="c3", name="edit_file", input={"path": "calc.py", "old_str": "    return a - b\n",
               "new_str": "    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"})]),
    ("", [ToolCall(id="c4", name="bash", input={"command": "python -m unittest -q 2>&1 | tail -3"})]),
    ("Done. Summary:\n\n| File | Change |\n|---|---|\n| `calc.py` | added `mul` |\n| `test_calc.py` | untouched, still green |\n\n"
     "Usage:\n\n```python\nfrom calc import mul\nprint(mul(6, 7))  # 42\n```\n\n"
     "> Note: `mul` does no type checking; strings follow Python's repeat semantics.", []),
]
if os.environ.get("FEIYU_UI_LANG") == "en":
    SCRIPT = SCRIPT_EN

class Fake(FakeBackend):
    def __init__(self, *a, **k):
        super().__init__(list(SCRIPT))
        self.model = "deepseek-v4-flash"   # 状态栏显示的是 backend.model，别让它写 fake
    def run_turn(self, on_text, on_tool=None, on_think=None):
        import time
        text, calls = self.script[0] if self.script else ("说完了", [])
        # 逐字慢放，让流式渲染真的走一遍
        self.script = self.script[1:] if self.script else []
        for ch in text:
            on_text(ch); time.sleep(0.004)
        entry = {"role": "assistant", "content": text or None}
        if calls:
            entry["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.input)}} for c in calls]
        self.messages.append(entry)
        # 假装接口报了用量
        self._charge(6000, 1800, 300)
        from feiyu.providers import TurnResult
        return TurnResult(text=text, tool_calls=list(calls), truncated=False)

agent_mod.build_backend = lambda *a, **k: Fake()
from feiyu.__main__ import main
sys.argv = ["feiyu", "-w", os.environ.get("FEIYU_UI_WS") or os.path.join(S, "ws")]
raise SystemExit(main())
