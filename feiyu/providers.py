import json
import os
from dataclasses import dataclass, field

from . import pricing


# 两个 SDK 自带的重试次数（429 / 5xx / 连接错误，指数退避）
# 只管请求发出之前；流中途断掉由 agent.send() 那边接
RETRIES = 4

SUMMARY_PROMPT = (
    "下面是一段人和编程 agent 的对话记录 把它压缩成一段交接说明，"
    "让接手的人只看这段就能继续干活 必须保留：用户的目标和明确要求、"
    "已经改过哪些文件和改了什么、跑过什么命令得到什么结果、"
    "还没做完的部分、以及踩过的坑 不要写客套话，不要复述原文 "
)


def _field(block, name: str, default: str = ""):
    """存盘恢复回来的 content block 是 dict，SDK 直出的是对象 两种都要能取值 """
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _brief(value, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    error: str = ""      # 参数没解析出来时填这里，工具不会真的跑


@dataclass
class TurnResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False      # 撞上 max_tokens，这轮的输出是半截的


@dataclass
class ToolOutput:
    id: str
    content: str
    is_error: bool = False


def build_backend(entry: dict, *, system: str, tools: list[dict]):
    """按一条模型配置建后端 配置全部来自 keys.py """
    cls = AnthropicBackend if entry.get("kind") == "anthropic" else OpenAICompatBackend
    backend = cls(
        system=system,
        tools=tools,
        model=entry["model"],
        base_url=entry.get("base_url"),
        api_key=entry["key"],
    )
    # 用户在 /key add 里填的价格优先于内置价格表
    backend.rates = _as_rates(entry.get("price"))
    return backend


def _as_rates(price) -> tuple[float, float, float] | None:
    if not isinstance(price, dict):
        return None
    try:
        return (float(price["hit"]), float(price["input"]), float(price["output"]))
    except (KeyError, TypeError, ValueError):
        return None



class Billing:
    """两个后端共用的用量与花费统计

    tokens_context 是最近一次请求的规模，tokens_total 是累计计费量
    价格表里没有这个模型时 priced 置 False，界面上退回显示 token 数
    """

    rates: tuple[float, float, float] | None = None

    def _reset_usage(self) -> None:
        self.tokens_total = 0
        self.tokens_context = 0
        self.cost = 0.0
        self.priced = True

    def _charge(self, hit: int, miss: int, out: int) -> None:
        self.tokens_context = hit + miss + out
        self.tokens_total += self.tokens_context
        spent = pricing.cost(self.rates, hit, miss, out)
        if spent is None:
            self.priced = False
        else:
            self.cost += spent


class AnthropicBackend(Billing):
    MAX_TOKENS = 64_000
    EFFORT = "xhigh"

    def __init__(self, *, system: str, tools: list[dict], model: str,
                 base_url: str | None = None, api_key: str | None = None):
        import anthropic

        # SDK 默认重试 2 次；agent 一跑就是几十轮请求，多给两次更划算
        self.client = anthropic.Anthropic(
            max_retries=RETRIES,
            **{k: v for k, v in (("base_url", base_url), ("api_key", api_key)) if v}
        )
        self.system = system
        self.tools = tools
        self.model = model
        self.messages: list[dict] = []
        # total 是累计计费量（每轮都要重发全部历史，所以会一直涨）；
        # context 是最近一次请求的规模，也就是"这段对话现在多大"
        self._reset_usage()

    def reset(self) -> None:
        self.messages = []
        self._reset_usage()

    def set_system(self, text: str) -> None:
        self.system = text

    def transcript(self) -> str:
        rows = []
        for msg in self.messages:
            content = msg["content"]
            if isinstance(content, str):
                rows.append("%s：%s" % (msg["role"], content))
                continue
            for block in content:
                kind = _field(block, "type")
                if kind == "text":
                    rows.append("assistant：" + (_field(block, "text") or ""))
                elif kind == "tool_use":
                    rows.append("assistant 调用 %s(%s)" % (
                        _field(block, "name"), _brief(_field(block, "input", {}))))
                elif kind == "tool_result":
                    rows.append("工具返回：" + _brief(_field(block, "content")))
        return "\n".join(rows)

    def ask(self, prompt: str, max_tokens: int = 2000) -> str:
        """一次性提问，不带工具也不进对话历史 """
        reply = self.client.messages.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in reply.content if b.type == "text")

    def summarize(self) -> str:
        return self.ask(SUMMARY_PROMPT + "\n\n" + self.transcript())

    def compact_to(self, summary: str) -> None:
        self.messages = [
            {"role": "user", "content": "以下是我们之前对话的交接摘要：\n\n" + summary},
            {"role": "assistant", "content": "好的，我接着往下做 "},
        ]

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list(limit=100).data)
        except Exception:
            return []

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, outputs: list[ToolOutput]) -> None:
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": o.id,
                "content": o.content,
                "is_error": o.is_error,
            }
            for o in outputs
        ]
        self.messages.append({"role": "user", "content": blocks})

    def run_turn(self, on_text) -> TurnResult:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            system=self.system,
            thinking={"type": "adaptive"},
            output_config={"effort": self.EFFORT},
            tools=self.tools,
            messages=self.messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    on_text(event.delta.text)
            response = stream.get_final_message()

        self.messages.append({"role": "assistant", "content": response.content})
        usage = getattr(response, "usage", None)
        if usage is not None:
            hit = getattr(usage, "cache_read_input_tokens", 0) or 0
            miss = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            self._charge(hit, miss, usage.output_tokens or 0)

        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in response.content
            if b.type == "tool_use"
        ]
        if response.stop_reason == "refusal" and not text:
            text = "[已拒绝继续处理该请求 ]"
        return TurnResult(text=text, tool_calls=calls,
                          truncated=response.stop_reason == "max_tokens")


class OpenAICompatBackend(Billing):
    MAX_TOKENS = 8_192

    def __init__(self, *, system: str, tools: list[dict], model: str,
                 base_url: str | None = None, api_key: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "使用 DeepSeek/Kimi 需要 openai 这个包 \n"
                "请先安装：pip install openai"
            ) from exc

        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=RETRIES)
        self.model = model
        self.system = system
        self.tools = _to_openai_tools(tools)
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self._reset_usage()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system}]
        self._reset_usage()

    def set_system(self, text: str) -> None:
        self.system = text
        # 历史里的第一条就是 system，就地换掉，对话不受影响
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0] = {"role": "system", "content": text}
        else:
            self.messages.insert(0, {"role": "system", "content": text})

    def transcript(self) -> str:
        rows = []
        for msg in self.messages:
            if msg["role"] == "system":
                continue
            if msg.get("content"):
                label = "工具返回" if msg["role"] == "tool" else msg["role"]
                rows.append("%s：%s" % (label, _brief(msg["content"], 400)))
            for call in msg.get("tool_calls") or []:
                rows.append("assistant 调用 %s(%s)" % (
                    call["function"]["name"], _brief(call["function"]["arguments"])))
        return "\n".join(rows)

    def ask(self, prompt: str, max_tokens: int = 2000) -> str:
        """一次性提问，不带工具也不进对话历史 """
        reply = self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return reply.choices[0].message.content or ""

    def summarize(self) -> str:
        return self.ask(SUMMARY_PROMPT + "\n\n" + self.transcript())

    def compact_to(self, summary: str) -> None:
        self.messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": "以下是我们之前对话的交接摘要：\n\n" + summary},
            {"role": "assistant", "content": "好的，我接着往下做 "},
        ]

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception:
            return []

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, outputs: list[ToolOutput]) -> None:
        for o in outputs:
            content = f"错误: {o.content}" if o.is_error else o.content
            self.messages.append(
                {"role": "tool", "tool_call_id": o.id, "content": content}
            )

    def run_turn(self, on_text) -> TurnResult:
        stream = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            tools=self.tools,
            messages=self.messages,
            stream=True,
            stream_options={"include_usage": True},
        )

        text = ""
        finish = ""
        pending: dict[int, dict] = {}
        for chunk in stream:
            # 带 usage 的那个收尾 chunk 没有 choices
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                hit = getattr(usage, "prompt_cache_hit_tokens", None)
                if hit is None:
                    # 有些兼容接口把命中数放在 prompt_tokens_details.cached_tokens
                    details = getattr(usage, "prompt_tokens_details", None)
                    hit = getattr(details, "cached_tokens", 0) if details else 0
                hit = hit or 0
                prompt = usage.prompt_tokens or 0
                self._charge(hit, max(0, prompt - hit), usage.completion_tokens or 0)
            if not chunk.choices:
                continue
            finish = chunk.choices[0].finish_reason or finish
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                on_text(delta.content)
                text += delta.content
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = pending.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments

        ordered = [pending[i] for i in sorted(pending)]

        assistant: dict = {"role": "assistant", "content": text or None}
        if ordered:
            assistant["tool_calls"] = [
                {
                    "id": s["id"],
                    "type": "function",
                    "function": {"name": s["name"], "arguments": s["args"] or "{}"},
                }
                for s in ordered
            ]
        self.messages.append(assistant)

        calls = []
        for slot in ordered:
            parsed, bad = _parse_args(slot["args"])
            calls.append(ToolCall(id=slot["id"], name=slot["name"], input=parsed, error=bad))
        return TurnResult(text=text, tool_calls=calls, truncated=finish == "length")


def _to_openai_tools(schemas: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        }
        for s in schemas
    ]


def _parse_args(raw: str) -> tuple[dict, str]:
    """返回 (参数, 出错说明) 解析不了时说明非空，调用方不要真的去跑这个工具

    参数是流式拼起来的，撞上 max_tokens 就会断在 JSON 中间 直接当成空参数跑，
    工具只会报一句 KeyError，模型不知道自己被截断了，多半原样重试一遍
    """
    if not raw:
        return {}, ""
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError:
        return {}, (
            "这次调用的参数不是完整的 JSON，多半是输出太长被截断了 "
            "工具没有执行 重来一次，把内容拆成几次写，"
            "或者改用 edit_file 只替换要改的那一段 "
        )
