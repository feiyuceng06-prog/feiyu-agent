"""两个后端里跟真实网络无关的部分：参数解析、transcript、计价 """

import json
import types
import unittest

from feiyu import providers
from feiyu.providers import (AnthropicBackend, OpenAICompatBackend, ToolOutput,
                             _field, _parse_args)


class Block:
    """冒充 SDK 的 content block（直出时是对象，存盘回来是 dict）"""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestParseArgs(unittest.TestCase):
    def test_正常的_json(self):
        self.assertEqual(_parse_args('{"path":"a.py"}'), ({"path": "a.py"}, ""))

    def test_空串不算错(self):
        self.assertEqual(_parse_args(""), ({}, ""))

    def test_截断的_json_要报错而不是静默变空(self):
        # 参数是流式拼起来的，撞上 max_tokens 就断在中间
        # 当成空参数跑下去，模型只会看到一句 KeyError，不知道自己被截断了
        args, err = _parse_args('{"path":"a.py","content":"def f(')
        self.assertEqual(args, {})
        self.assertTrue(err)
        self.assertIn("截断", err)

    def test_不是对象的_json_也报错(self):
        self.assertEqual(_parse_args("[1,2]")[0], [1, 2])


class TestField(unittest.TestCase):
    """存盘恢复回来的 block 是 dict 只认对象的话，`--resume` 之后
    transcript 会拿到一份空记录，压缩出来的摘要是废的 """

    def test_对象(self):
        self.assertEqual(_field(Block(type="text", text="你好"), "text"), "你好")

    def test_字典(self):
        self.assertEqual(_field({"type": "text", "text": "你好"}, "text"), "你好")

    def test_缺字段给默认值(self):
        self.assertEqual(_field({}, "text"), "")
        self.assertEqual(_field({}, "input", {}), {})


class TestAnthropicTranscript(unittest.TestCase):
    def setUp(self):
        self.b = AnthropicBackend.__new__(AnthropicBackend)

    def test_sdk_对象(self):
        self.b.messages = [
            {"role": "user", "content": "改一下 build.mjs"},
            {"role": "assistant", "content": [
                Block(type="text", text="我先读一下"),
                Block(type="tool_use", id="t1", name="read_file", input={"path": "build.mjs"}),
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "import fs"}]},
        ]
        text = self.b.transcript()
        self.assertIn("我先读一下", text)
        self.assertIn("read_file", text)
        self.assertIn("import fs", text)

    def test_存盘恢复回来的字典(self):
        self.b.messages = [
            {"role": "user", "content": "改一下 build.mjs"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "我先读一下"},
                {"type": "tool_use", "id": "t1", "name": "read_file",
                 "input": {"path": "build.mjs"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "import fs"}]},
        ]
        text = self.b.transcript()
        self.assertIn("我先读一下", text)      # 0.11.0 这里是空的
        self.assertIn("read_file", text)
        self.assertIn("import fs", text)

    def test_压缩后重建成两条且以_assistant_收尾(self):
        self.b.messages = [{"role": "user", "content": "a"}]
        self.b.compact_to("摘要内容")
        self.assertEqual([m["role"] for m in self.b.messages], ["user", "assistant"])
        self.assertIn("摘要内容", self.b.messages[0]["content"])


class TestOpenAITranscript(unittest.TestCase):
    def setUp(self):
        self.b = OpenAICompatBackend.__new__(OpenAICompatBackend)
        self.b.system = "system"

    def test_跳过系统提示带上工具调用(self):
        self.b.messages = [
            {"role": "system", "content": "别写进 transcript"},
            {"role": "user", "content": "读一下"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "import os"},
        ]
        text = self.b.transcript()
        self.assertNotIn("别写进", text)
        self.assertIn("read_file", text)
        self.assertIn("import os", text)

    def test_换系统提示是就地替换(self):
        self.b.messages = [{"role": "system", "content": "旧的"},
                           {"role": "user", "content": "a"}]
        self.b.set_system("新的")
        self.assertEqual(self.b.messages[0]["content"], "新的")
        self.assertEqual(len(self.b.messages), 2)

    def test_没有系统提示时插到最前面(self):
        self.b.messages = [{"role": "user", "content": "a"}]
        self.b.set_system("新的")
        self.assertEqual(self.b.messages[0]["role"], "system")

    def test_压缩保留系统提示(self):
        self.b.messages = [{"role": "system", "content": "system"},
                           {"role": "user", "content": "a"}]
        self.b.compact_to("摘要内容")
        self.assertEqual([m["role"] for m in self.b.messages],
                         ["system", "user", "assistant"])

    def test_工具结果里的错误要标出来(self):
        self.b.messages = []
        self.b.add_tool_results([ToolOutput(id="c1", content="炸了", is_error=True),
                                 ToolOutput(id="c2", content="没事")])
        self.assertTrue(self.b.messages[0]["content"].startswith("错误"))
        self.assertEqual(self.b.messages[1]["content"], "没事")


class _Fn:
    def __init__(self, name=None, arguments=None):
        self.name, self.arguments = name, arguments


class _TC:
    def __init__(self, index, tid=None, name=None, arguments=None):
        self.index, self.id, self.function = index, tid, _Fn(name, arguments)


class _Delta:
    def __init__(self, content=None, tool_calls=None, reasoning=None):
        self.content, self.tool_calls = content, tool_calls
        self.reasoning_content = reasoning


class _Choice:
    def __init__(self, delta, finish=None):
        self.delta, self.finish_reason = delta, finish


class _Chunk:
    def __init__(self, delta, finish=None):
        self.choices, self.usage = [_Choice(delta, finish)], None


class TestOpenAIStream(unittest.TestCase):
    """参数是一段一段来的，边来边往上报 —— 写一个大文件那十几秒不能只有 spinner """

    def backend(self, chunks):
        b = object.__new__(providers.OpenAICompatBackend)
        b.model, b.system, b.tools = "m", "s", []
        b.max_tokens = providers.OpenAICompatBackend.MAX_TOKENS
        b.messages = [{"role": "system", "content": "s"}]
        b.thinking = False
        b._reset_usage()
        b.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Cp", (), {
            "create": staticmethod(lambda **kw: iter(chunks))})()})()})()
        return b

    def test_参数边流边上报(self):
        chunks = [
            _Chunk(_Delta(tool_calls=[_TC(0, "c1", "write_file", '{"path": "a.py"')])),
            _Chunk(_Delta(tool_calls=[_TC(0, arguments=', "content": "x = 1')])),
            _Chunk(_Delta(tool_calls=[_TC(0, arguments='"}')]), finish="tool_calls"),
        ]
        seen = []
        turn = self.backend(chunks).run_turn(on_text=lambda t: None,
                                             on_tool=lambda n, p: seen.append((n, p)))
        self.assertEqual(len(seen), 3)
        self.assertEqual({n for n, _ in seen}, {"write_file"})
        # 每次报的都是「到目前为止」的完整片段，越来越长
        self.assertEqual([len(p) for _, p in seen], sorted(len(p) for _, p in seen))
        self.assertEqual(turn.tool_calls[0].input, {"path": "a.py", "content": "x = 1"})

    def test_两个_chunk_都带_usage_只算一次(self):
        """DeepSeek 有时在收尾前后两个 chunk 上都带 usage，数值一样
        逐个算的话累计量和花费翻倍 —— 日志里几次 run 的总量正好是各轮之和的两倍 """
        usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=50,
                                      prompt_cache_hit_tokens=600)
        last = _Chunk(_Delta(content="好"), finish="stop")
        last.usage = usage
        tail = _Chunk(_Delta())
        tail.choices, tail.usage = [], usage
        b = self.backend([_Chunk(_Delta(content="你")), last, tail])
        b.rates = (1.0, 10.0, 100.0)
        b.run_turn(on_text=lambda t: None)
        self.assertEqual(b.tokens_total, 1050)
        self.assertEqual(b.tokens_context, 1050)
        self.assertAlmostEqual(b.cost, (600 * 1 + 400 * 10 + 50 * 100) / 1e6)

    def test_截断的参数不能原样留在历史里(self):
        """半截 JSON 留在历史里 = 之后每一次请求都 400，用户实测撞到过

        接口把 tool_calls 转成 tool_use.input 时要 json.loads，解析不出来就
        `Provider returned error`，而这条坏消息每轮都重发，说什么都救不回来
        """
        chunks = [_Chunk(_Delta(tool_calls=[
            _TC(0, "c1", "write_file", '{"path": "a.py", "content": "写到一半就断了')]),
            finish="length")]
        b = self.backend(chunks)
        turn = b.run_turn(on_text=lambda t: None)
        self.assertTrue(turn.truncated)
        # 模型要知道自己被截断了，否则原样重试一遍
        self.assertTrue(turn.tool_calls[0].error)
        raw = b.messages[-1]["tool_calls"][0]["function"]["arguments"]
        json.loads(raw)          # 合法就是全部要求

    def test_没截断的参数原样留着(self):
        chunks = [_Chunk(_Delta(tool_calls=[_TC(0, "c1", "bash", '{"command": "ls"}')]),
                         finish="tool_calls")]
        b = self.backend(chunks)
        b.run_turn(on_text=lambda t: None)
        raw = b.messages[-1]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(json.loads(raw), {"command": "ls"})

    def test_不传回调也照常跑(self):
        chunks = [_Chunk(_Delta(tool_calls=[_TC(0, "c1", "bash", '{"command": "ls"}')]),
                         finish="tool_calls")]
        turn = self.backend(chunks).run_turn(on_text=lambda t: None)
        self.assertEqual(turn.tool_calls[0].input, {"command": "ls"})

    def test_思考过程原样带回历史(self):
        # DeepSeek v4 思考模式下不回传就是 400，第二轮请求直接发不出去
        chunks = [_Chunk(_Delta(reasoning="先想一想")),
                  _Chunk(_Delta(reasoning="再想想")),
                  _Chunk(_Delta(content="答案"), finish="stop")]
        b = self.backend(chunks)
        b.run_turn(on_text=lambda t: None)
        self.assertEqual(b.messages[-1]["reasoning_content"], "先想一想再想想")
        self.assertEqual(b.messages[-1]["content"], "答案")

    def test_没有思考过程就不带这个字段(self):
        # 不认这个字段的接口收到它可能会报错，没吐过就别塞
        chunks = [_Chunk(_Delta(content="答案"), finish="stop")]
        b = self.backend(chunks)
        b.run_turn(on_text=lambda t: None)
        self.assertNotIn("reasoning_content", b.messages[-1])

    def test_思考混着传会被接口拒掉_所以要补齐(self):
        """要么条条都有要么条条都没有 混着就是 400，而混着几乎必然发生 """
        b = self.backend([_Chunk(_Delta(content="好"), finish="stop")])
        b.messages += [
            {"role": "assistant", "content": None, "reasoning_content": "想了想",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "look", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": "这轮没想，直接调"},      # 缺 reasoning
        ]
        b.run_turn(on_text=lambda t: None)
        missing = [m for m in b.messages
                   if m["role"] == "assistant" and "reasoning_content" not in m]
        self.assertEqual(missing, [])

    def test_从没思考过就不要凭空加这个字段(self):
        # 不认这个字段的接口收到多余字段可能报错
        b = self.backend([_Chunk(_Delta(content="好"), finish="stop")])
        b.messages.append({"role": "assistant", "content": "说了句话"})
        b.run_turn(on_text=lambda t: None)
        self.assertTrue(all("reasoning_content" not in m for m in b.messages))

    def test_恢复会话时也认得出思考模式(self):
        # --resume 回来 thinking 标志是 False，得从历史里重新看出来
        b = self.backend([_Chunk(_Delta(content="好"), finish="stop")])
        b.messages += [
            {"role": "assistant", "content": "早先想过", "reasoning_content": "嗯"},
            {"role": "assistant", "content": "后来没想"},
        ]
        b.thinking = False
        b.run_turn(on_text=lambda t: None)
        self.assertTrue(b.thinking)
        self.assertIn("reasoning_content", b.messages[-2])

    def test_正文照常走_on_text(self):
        chunks = [_Chunk(_Delta(content="你好")), _Chunk(_Delta(content="世界"), finish="stop")]
        said = []
        turn = self.backend(chunks).run_turn(on_text=said.append)
        self.assertEqual("".join(said), "你好世界")
        self.assertEqual(turn.text, "你好世界")


class TestBilling(unittest.TestCase):
    def setUp(self):
        self.b = OpenAICompatBackend.__new__(OpenAICompatBackend)
        self.b._reset_usage()

    def test_context_是最近一次请求_total_是累计(self):
        self.b.rates = (0.5, 2.0, 8.0)
        self.b._charge(100, 100, 50)
        self.assertEqual(self.b.tokens_context, 250)
        self.b._charge(200, 100, 50)
        self.assertEqual(self.b.tokens_context, 350)
        self.assertEqual(self.b.tokens_total, 600)

    def test_没配价格就不报金额(self):
        self.b.rates = None
        self.b._charge(100, 100, 50)
        self.assertFalse(self.b.priced)

    def test_缓存写入按第四项算_没填按输入价(self):
        self.b.rates = (1.0, 2.0, 4.0, 2.5)
        self.b._charge(0, 100, 0, write=1_000_000)
        self.assertAlmostEqual(self.b.cost, 2.5 + 100 * 2.0 / 1e6)
        self.assertEqual(self.b.tokens_context, 1_000_100)
        self.b._reset_usage()
        self.b.rates = (1.0, 2.0, 4.0)
        self.b._charge(0, 0, 0, write=1_000_000)
        self.assertAlmostEqual(self.b.cost, 2.0)

    def test_配了价格就累加(self):
        self.b.rates = (1.0, 1.0, 1.0)
        self.b._charge(1_000_000, 0, 0)
        self.assertAlmostEqual(self.b.cost, 1.0)


class TestRates(unittest.TestCase):
    def test_三项齐全(self):
        self.assertEqual(providers._as_rates({"hit": 0.5, "input": 2, "output": 8}),
                         (0.5, 2.0, 8.0))

    def test_写入价是可选的第四项(self):
        self.assertEqual(providers._as_rates({"hit": 0.5, "input": 2, "output": 8, "write": 2.5}),
                         (0.5, 2.0, 8.0, 2.5))
        self.assertEqual(providers._as_rates({"hit": 0.5, "input": 2, "output": 8, "write": None}),
                         (0.5, 2.0, 8.0))

    def test_缺一项就当没配(self):
        self.assertIsNone(providers._as_rates({"hit": 0.5, "input": 2}))

    def test_不是数字就当没配(self):
        self.assertIsNone(providers._as_rates({"hit": "免费", "input": 2, "output": 8}))

    def test_不是字典就当没配(self):
        self.assertIsNone(providers._as_rates(None))


class TestToolSchemas(unittest.TestCase):
    def test_转成_openai_的函数格式(self):
        from feiyu.tools import TOOL_SCHEMAS
        out = providers._to_openai_tools(TOOL_SCHEMAS)
        self.assertEqual(len(out), len(TOOL_SCHEMAS))
        for item in out:
            self.assertEqual(item["type"], "function")
            self.assertIn("parameters", item["function"])
            self.assertTrue(item["function"]["description"])


class _Stream:
    """假的流式上下文 events 放完就给 final """

    def __init__(self, final, events=()):
        self.final, self.events = final, events

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


class _Final:
    def __init__(self, content=(), stop="end_turn"):
        self.content, self.stop_reason, self.usage = list(content), stop, None


class TestAnthropicCache(unittest.TestCase):
    """system / tools / 历史每轮都原样重发，不打断点就是每轮按原价重算一遍 """

    def backend(self, messages=None, cache=True):
        b = AnthropicBackend.__new__(AnthropicBackend)
        b.system = "你是 Feiyu"
        b.tools = [{"name": "read_file"}, {"name": "bash"}]
        b.model, b.max_tokens = "claude-opus-5", 64_000
        b.messages = messages if messages is not None else []
        b.cache = cache
        b._reset_usage()
        return b

    def test_system_和_tools_各打一个断点(self):
        b = self.backend()
        self.assertEqual(b._cached_system()[0]["cache_control"], providers.CACHE_MARK)
        tools = b._cached_tools()
        self.assertNotIn("cache_control", tools[0])
        self.assertEqual(tools[-1]["cache_control"], providers.CACHE_MARK)

    def test_打断点不能改坏原来的_tools(self):
        b = self.backend()
        b._cached_tools()
        self.assertNotIn("cache_control", b.tools[-1])

    def test_历史断点不超过接口认的个数(self):
        msgs = []
        for i in range(6):
            msgs.append({"role": "assistant", "content": [
                Block(type="tool_use", id="t%d" % i, name="read_file", input={})]})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t%d" % i, "content": "ok"}]})
        b = self.backend(msgs)
        for _ in range(3):          # 反复重挂，断点不能一轮涨一个
            b._mark_messages()
        marked = [blk for m in msgs for blk in m["content"]
                  if isinstance(blk, dict) and "cache_control" in blk]
        self.assertEqual(len(marked), providers.CACHE_KEEP)
        # 挂在最后那几条上：前面的历史靠前缀匹配自然命中
        self.assertIs(marked[-1], msgs[-1]["content"][-1])

    def test_不碰_sdk_对象那条(self):
        msgs = [{"role": "assistant", "content": [Block(type="text", text="好")]}]
        b = self.backend(msgs)
        b._mark_messages()
        self.assertFalse(hasattr(msgs[0]["content"][0], "cache_control"))

    def test_关掉之后要把留下的断点清干净(self):
        msgs = [{"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}]
        b = self.backend(msgs)
        b._mark_messages()
        self.assertIn("cache_control", msgs[0]["content"][0])
        b.cache = False
        b._mark_messages()
        self.assertNotIn("cache_control", msgs[0]["content"][0])
        self.assertEqual(b._cached_system(), "你是 Feiyu")
        self.assertNotIn("cache_control", b._cached_tools()[-1])

    def test_接口不认就降级重来一次(self):
        b = self.backend()
        tries = []

        def stream(**kw):
            tries.append(kw)
            if len(tries) == 1:
                raise ValueError("invalid_request_error: cache_control is not supported")
            return _Stream(_Final([Block(type="text", text="好了")]))

        b.client = type("C", (), {"messages": type("M", (), {
            "stream": staticmethod(stream)})()})()
        out = b.run_turn(on_text=lambda t: None)
        self.assertEqual(out.text, "好了")
        self.assertFalse(b.cache)
        self.assertEqual(len(tries), 2)
        self.assertIsInstance(tries[1]["system"], str)   # 第二次不带断点

    def test_吐过字就不能重发(self):
        """重发会让模型把刚说的话再说一遍 —— 跟流中途断掉是同一条规矩 """
        b = self.backend()
        tries, said = [], []

        def stream(**kw):
            tries.append(kw)
            return _Stream(_Final([]), events=_talk_then_fail())

        b.client = type("C", (), {"messages": type("M", (), {
            "stream": staticmethod(stream)})()})()
        with self.assertRaises(ValueError):
            b.run_turn(on_text=said.append)
        self.assertEqual(len(tries), 1)
        self.assertEqual(said, ["已经说了"])
        self.assertTrue(b.cache)        # 没关掉：这个错不该赖到缓存头上

    def test_别的报错不要误判成缓存问题(self):
        self.assertFalse(providers._cache_rejected(ValueError("model not found")))
        self.assertTrue(providers._cache_rejected(ValueError("cache_control unsupported")))


def _talk_then_fail():
    """先吐一句正文再断掉 """
    yield type("E", (), {
        "type": "content_block_delta",
        "delta": type("D", (), {"type": "text_delta", "text": "已经说了"})()})()
    raise ValueError("cache_control is not supported")


if __name__ == "__main__":
    unittest.main()
