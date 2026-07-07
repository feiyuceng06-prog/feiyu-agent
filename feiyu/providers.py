import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class TurnResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolOutput:
    id: str
    content: str
    is_error: bool = False


@dataclass
class ProviderSpec:
    kind: str
    default_model: str
    api_key_env: str
    base_url: str | None = None


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        kind="anthropic",
        default_model="claude-opus-4-8",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "deepseek": ProviderSpec(
        kind="openai",
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
    ),
    "kimi": ProviderSpec(
        kind="openai",
        default_model="kimi-k2.6",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
    ),
}


def build_backend(
    provider: str,
    *,
    system: str,
    tools: list[dict],
    model: str | None = None,
    base_url: str | None = None,
):
    try:
        spec = PROVIDERS[provider]
    except KeyError:
        known = "、".join(PROVIDERS)
        raise ValueError(f"未知的 provider {provider!r}（可用：{known}）") from None
    model = model or spec.default_model
    base_url = base_url or spec.base_url
    if spec.kind == "anthropic":
        return AnthropicBackend(system=system, tools=tools, model=model)
    return OpenAICompatBackend(
        system=system, tools=tools, model=model, base_url=base_url, api_key_env=spec.api_key_env
    )


class AnthropicBackend:
    MAX_TOKENS = 64_000
    EFFORT = "xhigh"

    def __init__(self, *, system: str, tools: list[dict], model: str):
        import anthropic

        self.client = anthropic.Anthropic()
        self.system = system
        self.tools = tools
        self.model = model
        self.messages: list[dict] = []

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

        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in response.content
            if b.type == "tool_use"
        ]
        if response.stop_reason == "refusal" and not text:
            text = "[已拒绝继续处理该请求 ]"
        return TurnResult(text=text, tool_calls=calls)


class OpenAICompatBackend:
    MAX_TOKENS = 8_192

    def __init__(self, *, system: str, tools: list[dict], model: str, base_url: str | None, api_key_env: str):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "使用 DeepSeek/Kimi 需要 openai 这个包 \n"
                "请先安装：pip install openai"
            ) from exc

        import os

        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} 未设置")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.tools = _to_openai_tools(tools)
        self.messages: list[dict] = [{"role": "system", "content": system}]

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
        )

        text = ""
        pending: dict[int, dict] = {}
        for chunk in stream:
            if not chunk.choices:
                continue
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

        calls = [
            ToolCall(id=s["id"], name=s["name"], input=_parse_args(s["args"]))
            for s in ordered
        ]
        return TurnResult(text=text, tool_calls=calls)


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


def _parse_args(raw: str) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
