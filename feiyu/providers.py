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


# anthropic 的缓存断点 openai 兼容那边是服务端自动缓存，不用也不能声明
CACHE_MARK = {"type": "ephemeral"}
# 历史上留几个滚动断点 接口一共只认 4 个，system 和 tools 各占一个，
# 剩下两个留给历史：新的那个负责这一轮写入，旧的那个保证上一轮写的那份一定命中
CACHE_KEEP = 2


def _cache_rejected(exc: Exception) -> bool:
    """这个错是不是"接口不认 cache_control"

    官方接口一定认，但用户多半连的是中转站，那边未必透传 认出来就降级重来一次，
    不降级的话这一轮 400，而 400 一次整个会话就废了
    """
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and code not in (400, 404, 422):
        return False
    return "cache" in str(exc).lower()


# 图片进了历史就一直在 摘要和存盘里只留这句，base64 原样带上是几十万字的垃圾
IMAGE_NOTE = "（这里有一张图，原始数据没有保留）"


def without_images(value):
    """把 content 里的图片块换成一句说明，其余原样

    两种后端的形状都认：anthropic 是 tool_result 里的 image 块，
    openai 兼容是跟在后面那条 user 消息里的 image_url
    """
    if isinstance(value, dict):
        kind = value.get("type")
        if kind in ("image", "image_url"):
            return {"type": "text", "text": IMAGE_NOTE}
        return {k: without_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [without_images(v) for v in value]
    return value


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
    image: tuple[str, str] | None = None      # (media_type, base64)


def build_backend(entry: dict, *, system: str, tools: list[dict]):
    """按一条模型配置建后端 配置全部来自 keys.py """
    cls = AnthropicBackend if entry.get("kind") == "anthropic" else OpenAICompatBackend
    backend = cls(
        system=system,
        tools=tools,
        model=entry["model"],
        base_url=entry.get("base_url"),
        api_key=entry["key"],
        max_tokens=entry.get("max_tokens"),
    )
    # 能不能看图没法探测，只能按用户在 /key add 里填的来
    backend.vision = bool(entry.get("vision"))
    # 用户在 /key add 里填的价格优先于内置价格表
    backend.rates = _as_rates(entry.get("price"))
    return backend


def _as_rates(price) -> tuple | None:
    if not isinstance(price, dict):
        return None
    try:
        rates = (float(price["hit"]), float(price["input"]), float(price["output"]))
        if price.get("write") is not None:
            rates += (float(price["write"]),)
        return rates
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

    def _charge(self, hit: int, miss: int, out: int, *, write: int = 0,
                context: bool = True) -> None:
        """context=False 是旁路请求（摘要、网页提炼）：要算钱，但不是"对话现在多大"
        算进 context 的话，一次网页提炼就能把压缩阈值假撞上
        write 是新写进缓存的输入（anthropic 的 cache_creation），价可能跟普通输入不同 """
        size = hit + miss + out + write
        if context:
            self.tokens_context = size
        self.tokens_total += size
        spent = pricing.cost(self.rates, hit, miss, out, write)
        if spent is None:
            self.priced = False
        else:
            self.cost += spent


class AnthropicBackend(Billing):
    MAX_TOKENS = 64_000
    EFFORT = "xhigh"

    def __init__(self, *, system: str, tools: list[dict], model: str,
                 base_url: str | None = None, api_key: str | None = None,
                 max_tokens: int | None = None):
        import anthropic

        # SDK 默认重试 2 次；agent 一跑就是几十轮请求，多给两次更划算
        self.client = anthropic.Anthropic(
            max_retries=RETRIES,
            **{k: v for k, v in (("base_url", base_url), ("api_key", api_key)) if v}
        )
        self.system = system
        self.tools = tools
        self.model = model
        self.max_tokens = int(max_tokens or self.MAX_TOKENS)
        self.messages: list[dict] = []
        # 撞上不认 cache_control 的接口会自动关掉，之后这个会话都不再打断点
        self.cache = True
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
                    rows.append("工具返回：" + _brief(without_images(_field(block, "content"))))
        return "\n".join(rows)

    def ask(self, prompt: str, max_tokens: int = 2000) -> str:
        """一次性提问，不带工具也不进对话历史 """
        reply = self.client.messages.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(reply, "usage", None)
        if usage is not None:
            self._charge(getattr(usage, "cache_read_input_tokens", 0) or 0,
                         usage.input_tokens or 0, usage.output_tokens or 0, context=False)
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
                "content": self._result_content(o),
                "is_error": o.is_error,
            }
            for o in outputs
        ]
        self.messages.append({"role": "user", "content": blocks})

    @staticmethod
    def _result_content(o: ToolOutput):
        """图片可以直接躺在 tool_result 里，不用另起一条消息 """
        if not o.image:
            return o.content
        media, data = o.image
        return [
            {"type": "text", "text": o.content},
            {"type": "image",
             "source": {"type": "base64", "media_type": media, "data": data}},
        ]

    def _cached_system(self):
        """system 每轮原样重发，打个断点让它按缓存计价

        风格、模式、memory 一变这里就换一份，缓存跟着作废——那本来就该作废
        """
        if not self.cache or not self.system:
            return self.system
        return [{"type": "text", "text": self.system, "cache_control": CACHE_MARK}]

    def _cached_tools(self):
        """只在最后一项上挂 缓存按前缀算，一个断点就盖住整个 tools 块 """
        if not self.cache or not self.tools:
            return self.tools
        return self.tools[:-1] + [{**self.tools[-1], "cache_control": CACHE_MARK}]

    def _mark_messages(self) -> None:
        """在最近几条 user 消息末尾挂滚动断点，让历史也走缓存

        **只挂 dict 形式的块**：assistant 那条 content 是 SDK 直出的对象，
        塞不进字段；而 user 消息（tool_result 那些）本来就是我们自己拼的 dict
        每轮重挂之前要先清干净，不清的话断点会一轮涨一个，很快超过接口认的 4 个
        """
        spots = []
        for msg in self.messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            blocks = [b for b in content if isinstance(b, dict)]
            for block in blocks:
                block.pop("cache_control", None)
            if msg.get("role") == "user" and blocks:
                spots.append(blocks[-1])
        if not self.cache:
            return
        for block in spots[-CACHE_KEEP:]:
            block["cache_control"] = CACHE_MARK

    def run_turn(self, on_text, on_tool=None) -> TurnResult:
        said = []

        def watch(text):
            said.append(1)
            on_text(text)

        try:
            return self._turn(watch, on_tool)
        except Exception as exc:
            # 断点是请求参数，不认的话错在发出的那一刻，一个字都还没吐出来，重发是安全的
            if self.cache and not said and _cache_rejected(exc):
                self.cache = False
                return self._turn(on_text, on_tool)
            raise

    def _turn(self, on_text, on_tool=None) -> TurnResult:
        self._mark_messages()
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._cached_system(),
            thinking={"type": "adaptive"},
            output_config={"effort": self.EFFORT},
            tools=self._cached_tools(),
            messages=self.messages,
        ) as stream:
            name, partial = "", ""
            for event in stream:
                if event.type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    name = _field(block, "name") if _field(block, "type") == "tool_use" else ""
                    partial = ""
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    on_text(event.delta.text)
                elif (event.type == "content_block_delta"
                      and event.delta.type == "input_json_delta" and on_tool and name):
                    partial += event.delta.partial_json or ""
                    on_tool(name, partial)
            response = stream.get_final_message()

        self.messages.append({"role": "assistant", "content": response.content})
        usage = getattr(response, "usage", None)
        if usage is not None:
            hit = getattr(usage, "cache_read_input_tokens", 0) or 0
            written = getattr(usage, "cache_creation_input_tokens", 0) or 0
            self._charge(hit, usage.input_tokens or 0, usage.output_tokens or 0, write=written)

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
    # 没在 /key 里填 max_tokens 时的保底 往高了写不安全：有些兼容接口
    # 收到超过自己上限的值直接报错 要写长文件的模型（Claude、Kimi K2）
    # 得在 /key 里按它自己的上限填一个，否则每次都撞上长度上限半途而废
    MAX_TOKENS = 8_192

    def __init__(self, *, system: str, tools: list[dict], model: str,
                 base_url: str | None = None, api_key: str | None = None,
                 max_tokens: int | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "使用 DeepSeek/Kimi 需要 openai 这个包 \n"
                "请先安装：pip install openai"
            ) from exc

        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=RETRIES)
        self.model = model
        self.max_tokens = int(max_tokens or self.MAX_TOKENS)
        self.system = system
        self.tools = _to_openai_tools(tools)
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.thinking = False       # 这段对话里出现过思考内容没有
        self._reset_usage()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system}]
        self.thinking = False
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
                rows.append("%s：%s" % (label, _brief(without_images(msg["content"]), 400)))
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
        usage = getattr(reply, "usage", None)
        if usage is not None:
            self._charge(0, usage.prompt_tokens or 0, usage.completion_tokens or 0,
                         context=False)
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
        # tool 角色的 content 只能是纯文本，图片只能另起一条 user 跟在后面
        # 放在所有 tool 结果之后，配对关系不受影响
        shots = [o for o in outputs if o.image]
        if shots:
            parts = []
            for o in shots:
                parts.append({"type": "text", "text": o.content})
                parts.append({"type": "image_url", "image_url": {
                    "url": "data:%s;base64,%s" % o.image}})
            self.messages.append({"role": "user", "content": parts})

    def _mind_thinking(self) -> None:
        """思考模式下 reasoning_content 要么条条 assistant 都有、要么条条都没有

        混着就是 400（`must be passed back to the API`），而混着几乎必然发生：
        模型不是每轮都想（有时直接说话 + 调工具），中断补的那条 assistant 更是
        我们自己造的 所以在发请求这个唯一的收口上把缺的补成空串
        实测：补空串接受，全删也接受，只有混着不行
        """
        seen = [m for m in self.messages if m.get("role") == "assistant"]
        self.thinking = self.thinking or any("reasoning_content" in m for m in seen)
        if not self.thinking:
            return
        for m in seen:
            m.setdefault("reasoning_content", "")

    def run_turn(self, on_text, on_tool=None) -> TurnResult:
        self._mind_thinking()
        stream = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            tools=self.tools,
            messages=self.messages,
            stream=True,
            stream_options={"include_usage": True},
        )

        text = ""
        reasoning = ""
        finish = ""
        pending: dict[int, dict] = {}
        usage = None
        for chunk in stream:
            # 带 usage 的那个收尾 chunk 没有 choices 只记住最后一份、循环结束再算账：
            # DeepSeek 有时会在两个 chunk 上都带 usage（数值一样），见一份算一份
            # 的话累计量和花费恰好翻倍 —— 日志里几次 run 的总量正好是各轮之和的两倍
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            finish = chunk.choices[0].finish_reason or finish
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                on_text(delta.content)
                text += delta.content
            # 思考过程要原样带回去 DeepSeek v4 的思考模式下不回传就是 400：
            # "The `reasoning_content` in the thinking mode must be passed back"
            # 只有它自己吐过才会带上，所以不会往不认这个字段的接口上乱塞
            if getattr(delta, "reasoning_content", None):
                reasoning += delta.reasoning_content
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = pending.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
                if on_tool and slot["name"]:
                    on_tool(slot["name"], slot["args"])

        if usage is not None:
            hit = getattr(usage, "prompt_cache_hit_tokens", None)
            if hit is None:
                # 有些兼容接口把命中数放在 prompt_tokens_details.cached_tokens
                details = getattr(usage, "prompt_tokens_details", None)
                hit = getattr(details, "cached_tokens", 0) if details else 0
            hit = hit or 0
            prompt = usage.prompt_tokens or 0
            self._charge(hit, max(0, prompt - hit), usage.completion_tokens or 0)

        ordered = [pending[i] for i in sorted(pending)]

        assistant: dict = {"role": "assistant", "content": text or None}
        if reasoning:
            assistant["reasoning_content"] = reasoning
            self.thinking = True
        elif self.thinking:
            # 这一轮它没想，但这段对话已经在思考模式里了，缺了就是混着
            assistant["reasoning_content"] = ""
        calls = []
        for slot in ordered:
            parsed, bad = _parse_args(slot["args"])
            calls.append(ToolCall(id=slot["id"], name=slot["name"], input=parsed, error=bad))
            if bad:
                # 半截 JSON 不能留在历史里 下一轮原样发回去，接口把 tool_calls
                # 转成 tool_use.input 时解析不出来，从此**每一次**请求都 400
                # （中转到 anthropic 时报 "Provider returned error"），
                # 用户说什么都救不回来 跟 0.12.0 那条没配对的 tool_use 同一类：
                # 历史里绝不能留下接口不接受的形状
                slot["args"] = "{}"

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
            "工具没有执行 重来一次：写文件的话先写前半段，再用 write_file 的 append "
            "接着写后半段；或者改用 edit_file 只替换要改的那一段 "
        )
