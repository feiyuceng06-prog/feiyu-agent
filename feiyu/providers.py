import json
import os
from dataclasses import dataclass, field

from . import pricing


# 两个 SDK 自带的重试次数（429 / 5xx / 连接错误，指数退避）
# 只管请求发出之前；流中途断掉由 agent.send() 那边接
RETRIES = 4

# 一次流式请求里，**两个 chunk 之间最多能静多久**（秒）。不是一整轮的上限：
# 只要它一直在吐字，一轮想跑多久跑多久。
#
# 传一个裸浮点数下去，两个 SDK 都转成 `httpx.Timeout(它)`，connect / read / write / pool
# 全是这个数，而 read 在流式请求里量的正是相邻两次收到数据的间隔。
#
# **默认就是两个 SDK 原本的 600 秒，这里只是把它写明白、并且让人能改。**
# 别想当然地往小了调：实测过一轮 622 秒的请求（中转站 + 本地代理，
# 思考型模型 19k 输入），它一直在断断续续地吐字，只是慢 —— 调到三分钟
# 会把这种本来会成功的请求掐死。要它快点失败就把这个数调小，
# 撞上"明明还在跑却被掐了"就调大。
#
# 超时抛出来之后走 agent.send() 那条「还没吐字就重发、吐了字就不重发」（0.20.0），
# `APITimeoutError` / `ReadTimeout` 已经在 RETRY_NAMES 里。
def _stream_timeout() -> float:
    try:
        said = float(os.environ.get("FEIYU_STREAM_TIMEOUT") or 0)
    except ValueError:
        said = 0
    return said if said > 0 else 600.0


STREAM_TIMEOUT = _stream_timeout()

SUMMARY_PROMPT = (
    "下面是一段人和编程 agent 的对话记录 把它压缩成一段交接说明，"
    "让接手的人只看这段就能继续干活 必须保留：用户的目标和明确要求、"
    "已经改过哪些文件和改了什么、跑过什么命令得到什么结果（跑测试和构建的命令"
    "连解释器路径一起原样抄下来，比如 .venv/bin/python -m pytest）、"
    "用户交代的限制（哪些文件不能动、不许做什么）、"
    "还没做完的部分、以及踩过的坑 不要写客套话，不要复述原文 "
)


def _field(block, name: str, default: str = ""):
    """存盘恢复回来的 content block 是 dict，SDK 直出的是对象 两种都要能取值 """
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


# anthropic 的缓存断点
CACHE_MARK = {"type": "ephemeral"}
# 历史上留几个滚动断点 接口一共只认 4 个，system 和 tools 各占一个，
# 剩下两个留给历史：新的那个负责这一轮写入，旧的那个保证上一轮写的那份一定命中
CACHE_KEEP = 2


def _wants_marks(model: str) -> bool:
    """走 openai 兼容格式的 Claude（OpenRouter 的 `anthropic/claude-*`、中转站的 `claude-*`）
    **照样得自己打断点**，服务端不会替它缓存 OpenAI / DeepSeek / Qwen 是自动缓存，不用打

    实测（2026-09-23，haiku-4.5 经 OpenRouter，1.5 万 token 的前缀连发两次）：
    不打断点两次都命中 0、都按全价；system 上打一个，第二次整段按缓存价，
    $0.0156 → $0.0016 在这之前经 OpenRouter 跑的 Claude 每一轮都在付全价
    """
    return "claude" in (model or "").lower()


def _with_mark(msg: dict) -> dict:
    """返回挂了断点的**副本**，原消息不动 断点只能挂在文本块上，
    content 是字符串的先摊成一个文本块；最后一块是图的话挂在它前面那个文本块上"""
    content = msg.get("content")
    if isinstance(content, str) and content:
        return {**msg, "content": [{"type": "text", "text": content, "cache_control": CACHE_MARK}]}
    if not isinstance(content, list):
        return msg
    parts = [dict(p) if isinstance(p, dict) else p for p in content]
    for part in reversed(parts):
        if isinstance(part, dict) and part.get("type") == "text":
            part["cache_control"] = CACHE_MARK
            return {**msg, "content": parts}
    return msg


def _split_usage(usage) -> tuple[int, int, int, int]:
    """(命中, 没命中, 输出, 新写进缓存) 命中数两家放的地方不一样：DeepSeek 是
    `prompt_cache_hit_tokens`，openai / OpenRouter 是 `prompt_tokens_details.cached_tokens`

    **写进缓存的那部分要单独拿出来**（OpenRouter 的 `cache_write_tokens`）：
    它包含在 prompt_tokens 里，但 Claude 按输入价的 1.25 倍收 当成普通输入算的话，
    打了断点的每一轮都少算一截
    """
    details = getattr(usage, "prompt_tokens_details", None)
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if hit is None:
        hit = getattr(details, "cached_tokens", 0) if details else 0
    hit = hit or 0
    write = (getattr(details, "cache_write_tokens", 0) if details else 0) or 0
    prompt = usage.prompt_tokens or 0
    return hit, max(0, prompt - hit - write), usage.completion_tokens or 0, write


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
    effort = str(entry.get("effort") or "").strip().lower()
    if effort:
        backend.effort = effort
    # 没填就是开着 —— 思考是现在这些模型的常态，而且 anthropic 那边
    # 关掉它在 xhigh/max 上直接 400
    if entry.get("thinking") is not None:
        backend.thinking_on = bool(entry["thinking"])
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
        # 累计输入里有多少是按缓存价算的 这个比例决定"要不要更狠地折叠历史"：
        # 命中率高的话重发的历史几乎不要钱，折叠反而会把后面的缓存前缀作废
        self.tokens_in = 0
        self.tokens_hit = 0
        self.last_in = 0
        self.last_hit = 0

    last_out = 0

    def _charge(self, hit: int, miss: int, out: int, *, write: int = 0,
                context: bool = True) -> None:
        """context=False 是旁路请求（摘要、网页提炼）：要算钱，但不是"对话现在多大"
        算进 context 的话，一次网页提炼就能把压缩阈值假撞上
        write 是新写进缓存的输入（anthropic 的 cache_creation），价可能跟普通输入不同 """
        size = hit + miss + out + write
        if context:
            self.tokens_context = size
            self.last_in = hit + miss + write
            self.last_hit = hit
            # **出了多少字要记下来。** 只记输入的话，一轮跑了十分钟事后没法归因：
            # 分不清是"想/说了很多"还是"接口就是慢"（真为这个查了半天）
            self.last_out = out
        self.tokens_total += size
        self.tokens_in += hit + miss + write
        self.tokens_hit += hit
        spent = pricing.cost(self.rates, hit, miss, out, write)
        if spent is None:
            self.priced = False
        else:
            self.cost += spent


# 推理强度 —— anthropic 官方是这五档（`output_config.effort`，GA，不用 beta 头）
# 接口默认是 `high`；**我们默认 `xhigh`**，因为文档明确说它是编程和 agent 类任务
# 的最佳档，也是 Claude Code 的默认。往上还有 `max`：正确性比钱重要时才用。
#
# **别自己发明档位。** 见过的客户端有分成七档的（最小/低/中/高/极高/最高/超高），
# 那是它自己的叫法，喂给接口会被 400 顶回来。
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "xhigh"


class AnthropicBackend(Billing):
    MAX_TOKENS = 64_000
    EFFORT = DEFAULT_EFFORT
    # **放类属性不放 __init__**：测试里的假后端和 tests/fakes.py 都绕过 __init__
    # 构造（那会去建真客户端），放实例上的话 run_turn 里一读就 AttributeError
    effort = DEFAULT_EFFORT
    thinking_on = True

    def __init__(self, *, system: str, tools: list[dict], model: str,
                 base_url: str | None = None, api_key: str | None = None,
                 max_tokens: int | None = None):
        import anthropic

        # SDK 默认重试 2 次；agent 一跑就是几十轮请求，多给两次更划算
        self.client = anthropic.Anthropic(
            max_retries=RETRIES, timeout=STREAM_TIMEOUT,
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

    def set_tools(self, schemas: list[dict]) -> None:
        self.tools = list(schemas)

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

    def run_turn(self, on_text, on_tool=None, on_think=None) -> TurnResult:
        said = []

        def watch(text):
            said.append(1)
            on_text(text)

        try:
            return self._turn(watch, on_tool, on_think)
        except Exception as exc:
            # 断点是请求参数，不认的话错在发出的那一刻，一个字都还没吐出来，重发是安全的
            if self.cache and not said and _cache_rejected(exc):
                self.cache = False
                return self._turn(on_text, on_tool, on_think)
            raise

    def _turn(self, on_text, on_tool=None, on_think=None) -> TurnResult:
        self._mark_messages()
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._cached_system(),
            # 关掉思考只在 high 及以下能用：xhigh / max 配 disabled 会 400
            thinking=({"type": "adaptive"} if self.thinking_on
                      else {"type": "disabled"}),
            output_config={"effort": self.effort},
            tools=self._cached_tools(),
            messages=self.messages,
        ) as stream:
            name, partial, thought = "", "", 0
            for event in stream:
                if event.type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    name = _field(block, "name") if _field(block, "type") == "tool_use" else ""
                    partial = ""
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    on_text(event.delta.text)
                elif (event.type == "content_block_delta"
                      and event.delta.type == "thinking_delta" and on_think):
                    piece = event.delta.thinking or ""
                    thought += len(piece)
                    on_think(thought, piece)
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
    # openai 兼容那套叫 `reasoning_effort`，档位各家不一样（而且多数模型压根
    # 不认这个参数）—— **所以默认空着不发**，只有用户在 /key 里明确填了才带上，
    # 由接口自己去校验。乱发的后果是本来能用的模型直接 400。
    # 同样放类属性：假后端绕过 __init__（理由见 AnthropicBackend 那条）
    effort = ""
    thinking_on = True
    cache = False
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

        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=RETRIES,
                             timeout=STREAM_TIMEOUT)
        self.model = model
        self.max_tokens = int(max_tokens or self.MAX_TOKENS)
        self.system = system
        self.tools = _to_openai_tools(tools)
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.thinking = False       # 这段对话里出现过思考内容没有
        # 要不要自己打缓存断点 接口不认的话 `_open` 会关掉它
        self.cache = _wants_marks(model)
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

    def set_tools(self, schemas: list[dict]) -> None:
        self.tools = _to_openai_tools(schemas)

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
            hit, miss, out, write = _split_usage(usage)
            self._charge(hit, miss, out, write=write, context=False)
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

    def _marked(self) -> list[dict]:
        """发出去的那份历史，挂好缓存断点 **不改 self.messages** —— 存盘的是它，
        而会话中途换成不认 cache_control 的模型（DeepSeek 直连）时，留在里面的标记
        会跟着发过去

        system 上一个：anthropic 的前缀顺序是 tools → system → messages，
        一个断点连工具表一起盖住 历史上两个滚动的，挂在最近两批「人这一侧」
        （user / tool 连着的一段）的末尾 —— 跟 AnthropicBackend 的 CACHE_KEEP 同一个道理：
        新的那个负责这一轮写入，旧的那个正好是上一轮写到的位置，一定命中
        """
        out = list(self.messages)
        side = ("user", "tool")
        ends = [i for i, m in enumerate(out)
                if m.get("role") in side
                and (i + 1 == len(out) or out[i + 1].get("role") not in side)]
        spots = ends[-CACHE_KEEP:]
        if out and out[0].get("role") == "system":
            spots.insert(0, 0)
        for i in spots:
            out[i] = _with_mark(out[i])
        return out

    def _open(self, extra: dict):
        """发出这一轮的请求 打了断点而接口不认（中转站未必透传）就去掉重发一次

        判「是不是断点惹的」不看报错文字（`_cache_rejected` 那种）：openai 兼容那边
        不认的是 content 写成数组这个形状，报错里未必提到 cache 所以换个判法 ——
        去掉断点重发，成了就是它，这个会话以后都不打；**照样挂就不是它**，
        断点留着、原样报错，别为一个无关的 400 把缓存永久关掉（那样每轮贵十倍，而且没人看得见）
        """
        args = dict(model=self.model, max_tokens=self.max_tokens, tools=self.tools,
                    stream=True, stream_options={"include_usage": True}, **extra)
        if not self.cache:
            return self.client.chat.completions.create(messages=self.messages, **args)
        try:
            return self.client.chat.completions.create(messages=self._marked(), **args)
        except Exception as exc:
            if getattr(exc, "status_code", None) not in (400, 404, 422):
                raise
        reply = self.client.chat.completions.create(messages=self.messages, **args)
        self.cache = False
        return reply

    def run_turn(self, on_text, on_tool=None, on_think=None) -> TurnResult:
        self._mind_thinking()
        extra = {"reasoning_effort": self.effort} if self.effort else {}
        stream = self._open(extra)

        text = ""
        reasoning = ""
        # OpenRouter 的思考文本（`delta.reasoning`）只给人看、不进历史，所以单独攒
        shown = 0
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
            piece = getattr(delta, "reasoning_content", None)
            if piece:
                reasoning += piece
            else:
                # **OpenRouter 把思考放在 `reasoning`**（Claude / GPT / Qwen 全是这个字段），
                # 只认 `reasoning_content` 的话，经它跑的模型想十分钟屏幕上一个字都没有 ——
                # 0.58.0 的字数和 0.77.0 的轨迹对它们从来没亮过（2026-09-23 实测三家都是 0 次）
                # 不进历史：它那边不要求带回去，实测带不带下一轮都一样
                piece = getattr(delta, "reasoning", None)
                if not isinstance(piece, str):
                    piece = ""
                shown += len(piece)
            # **想的过程也要让人看见。** 不报的话思考型模型在那儿想十分钟，
            # 屏幕上一个字都没有 —— 跟死了长得一模一样（实测过一轮 622 秒，
            # 全在这儿；数据一直在流，所以连超时都不会响）
            if piece and on_think:
                on_think(len(reasoning) + shown, piece)
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
            hit, miss, out, write = _split_usage(usage)
            self._charge(hit, miss, out, write=write)

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
