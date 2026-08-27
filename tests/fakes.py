"""测试用的假后端 真实 API 一个都不打 """

import json

from feiyu.providers import OpenAICompatBackend, ToolCall, TurnResult


class FakeBackend(OpenAICompatBackend):
    """按脚本逐轮返回 每轮是 (要说的话, [ToolCall...])

    不走 __init__（那会去建 OpenAI 客户端），messages 结构和真的一致，
    所以「历史配不配对」这类断言在它身上有意义
    """

    def __init__(self, script, *, truncated=False):
        self.model = "fake"
        self.system = "system"
        self.tools = []
        self.messages = [{"role": "system", "content": "system"}]
        self.script = list(script)
        self.truncated = truncated
        self.asked = []
        self.progress = []      # 喂出去的 (工具名, 半截参数)，用来验参数流接没接上
        self._reset_usage()

    def set_system(self, text):
        self.system = text

    def list_models(self):
        return []

    def ask(self, prompt, max_tokens=2000):
        self.asked.append(prompt)
        return "这是摘要"

    def run_turn(self, on_text, on_tool=None):
        text, calls = self.script.pop(0) if self.script else ("说完了", [])
        if text:
            on_text(text)
        for c in calls:
            # 真后端是一段一段喂的，这里喂两段，够验证「参数流上得来」
            raw = json.dumps(c.input, ensure_ascii=False)
            for cut in (len(raw) // 2, len(raw)):
                self.progress.append((c.name, raw[:cut]))
                if on_tool:
                    on_tool(c.name, raw[:cut])
        entry = {"role": "assistant", "content": text or None}
        if calls:
            entry["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                for c in calls
            ]
        self.messages.append(entry)
        return TurnResult(text=text, tool_calls=list(calls), truncated=self.truncated)


def call(cid, name="bash", **args):
    return ToolCall(id=cid, name=name, input=args or {"command": "true"})


def unpaired(messages):
    """历史里有几个 tool_use 没配上结果 大于 0 就是接口会 400 的形状 """
    want = {c["id"] for m in messages for c in (m.get("tool_calls") or [])}
    got = {m.get("tool_call_id") for m in messages if m["role"] == "tool"}
    return want - got
