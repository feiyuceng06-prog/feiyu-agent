"""agent 循环 大部分断言盯的是同一件事：**历史必须始终是接口收得下的形状**

tool_use 少一条配对结果、角色不交替，下一次请求就是 400，会话再也发不出去
（DeepSeek 实测 "An assistant message with 'tool_calls' must be followed by
tool messages responding to each 'tool_call_id'"）而且损坏状态会被存盘带走
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from feiyu import agent as agent_mod
from feiyu.agent import Feiyu
from feiyu.tools import ToolResult
from tests.fakes import FakeBackend, call, unpaired


def quiet(fn, *a, **kw):
    """agent 会往屏幕上画东西，测试里不看 """
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.mode = "auto"
        self.ran = []

    def tearDown(self):
        self.tmp.cleanup()

    def use(self, script, **kw):
        self.agent.backend = FakeBackend(script, **kw)
        return self.agent.backend

    def tools_ok(self):
        def run(name, args):
            self.ran.append((name, args))
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run

    def tool_results(self, backend):
        return [m["content"] for m in backend.messages if m["role"] == "tool"]

    def tools_interrupt_at(self, nth):
        """跑到第 nth 次调用时抛 KeyboardInterrupt（0 表示第一次就抛）"""
        count = {"n": 0}
        def run(name, args):
            if count["n"] >= nth:
                raise KeyboardInterrupt()
            count["n"] += 1
            self.ran.append((name, args))
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run


class TestInterruptKeepsHistoryValid(Base):
    def test_第一个工具就被打断(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        self.assertEqual(unpaired(b.messages), set())

    def test_打断落在第二个工具上(self):
        b = self.use([("", [call("c1"), call("c2")])])
        self.tools_interrupt_at(1)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        self.assertEqual(unpaired(b.messages), set())
        # 第一个真的跑过了，结果要保住；第二个补的是「被打断」
        results = [m for m in b.messages if m["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertIn("打断", results[1]["content"])

    def test_补的结果是错误状态(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        # openai 后端把 is_error 拼成「错误: 」前缀
        self.assertTrue(b.messages[-1]["content"].startswith("错误"))

    def test_模型正说话时被打断(self):
        b = self.use([])
        def boom(on_text, on_tool=None):
            on_text("我正在读 build.mjs")
            raise KeyboardInterrupt()
        b.run_turn = boom
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "看看")
        # 已经说出口的话要留住，而且角色要接着交替
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertIn("我正在读 build.mjs", b.messages[-1]["content"])

    def test_一个字都没说就被打断(self):
        b = self.use([])
        def boom(on_text, on_tool=None):
            raise KeyboardInterrupt()
        b.run_turn = boom
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "看看")
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertTrue(b.messages[-1]["content"])

    def test_打断之后还能接着聊(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "跑个长命令")
        b.script = [("好的", [])]
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "算了"), "好的")
        self.assertEqual(unpaired(b.messages), set())

    def test_角色始终交替(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        b.add_user_message("下一句")
        roles = [m["role"] for m in b.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])


class TestToolArgProgress(Base):
    """写一个大文件那十几秒里，参数流要接到 Stream 上，不能只有 spinner """

    def test_把_stream_的_progress_传下去(self):
        seen = []
        b = self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好", [])])
        real = b.run_turn
        def run_turn(on_text, on_tool=None):
            seen.append(on_tool)
            return real(on_text, on_tool)
        b.run_turn = run_turn
        self.tools_ok()
        quiet(self.agent.send, "写个文件")
        self.assertTrue(seen)
        for fn in seen:
            self.assertEqual(getattr(fn, "__name__", ""), "progress")
            fn("write_file", '{"path": "a.py"')      # 拿真参数调一下，别在真跑时才崩


class TestToolLoop(Base):
    def test_没有工具调用就结束(self):
        self.use([("答案是 42", [])])
        self.assertEqual(quiet(self.agent.send, "问题"), "答案是 42")

    def test_工具结果回灌后继续(self):
        b = self.use([("", [call("c1")]), ("看完了", [])])
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "读一下"), "看完了")
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(unpaired(b.messages), set())

    def test_到轮数上限会停下并补一条_assistant(self):
        # 不补的话角色不交替，下一轮请求直接报错
        b = self.use([("", [call("c%d" % i)]) for i in range(agent_mod.MAX_ROUNDS + 2)])
        self.tools_ok()
        out = quiet(self.agent.send, "停不下来的任务")
        self.assertIn("停下来", out)
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertEqual(len(self.ran), agent_mod.MAX_ROUNDS)

    def test_参数没解析出来就不执行工具(self):
        bad = call("c1")
        bad.error = "参数不是完整的 JSON"
        b = self.use([("", [bad]), ("重来了", [])])
        self.tools_ok()
        quiet(self.agent.send, "写个文件")
        self.assertEqual(self.ran, [])                       # 工具一次都没跑
        self.assertEqual(unpaired(b.messages), set())        # 但结果照样配上了
        self.assertIn("JSON", self.tool_results(b)[0])

    def test_撞上长度上限会提示(self):
        self.use([("半截话", [])], truncated=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.agent.send("写点长的")
        self.assertIn("长度上限", buf.getvalue())

    def test_没有模型时报错(self):
        self.agent.backend = None
        with self.assertRaises(RuntimeError):
            self.agent.send("你好")


class TestGate(Base):
    def setUp(self):
        super().setUp()
        self.use([("", [call("c1")]), ("好", [])])
        self.tools_ok()

    def test_只读模式拒绝写类工具(self):
        self.agent.mode = "readonly"
        for name, args in (("bash", {"command": "ls"}),
                           ("write_file", {"path": "a", "content": "x"}),
                           ("edit_file", {"path": "a", "old_str": "x", "new_str": "y"})):
            ok = self.agent._gate(call("c", name, **args), False)[0]
            self.assertFalse(ok, name)

    def test_只读模式放行读类工具(self):
        self.agent.mode = "readonly"
        for name, args in (("read_file", {"path": "a"}),
                           ("list_files", {"pattern": "*"}),
                           ("search", {"regex": "x"}),
                           ("web_fetch", {"url": "https://x.com"})):
            self.assertTrue(self.agent._gate(call("c", name, **args), False)[0], name)

    def test_自动模式一律放行(self):
        self.agent.mode = "auto"
        self.assertTrue(self.agent._gate(call("c", "bash", command="rm -rf x"), False)[0])

    def test_默认模式没人应答就是拒绝(self):
        self.agent.mode = "default"
        self.agent.approve = None
        ok, _, _, reason = self.agent._gate(call("c", "bash", command="rm -rf x"), False)
        self.assertFalse(ok)
        self.assertIn("拒绝", reason)

    def test_本轮都允许只问一次(self):
        self.agent.mode = "default"
        asked = []
        self.agent.approve = lambda n, a: (asked.append(n), "always")[1]
        b = self.use([("", [call("c1", "bash", command="rm a"),
                            call("c2", "bash", command="rm b")]), ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "删两个文件")
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(self.ran), 2)

    def test_拒绝的调用把原因回给模型(self):
        self.agent.mode = "default"
        self.agent.approve = lambda n, a: "no"
        b = self.use([("", [call("c1", "bash", command="rm -rf x")]), ("那算了", [])])
        self.tools_ok()
        quiet(self.agent.send, "删掉")
        self.assertEqual(self.ran, [])
        self.assertEqual(unpaired(b.messages), set())
        self.assertIn("拒绝", self.tool_results(b)[0])


class TestCompact(Base):
    def test_压缩后重建成两条(self):
        b = self.use([])
        b.messages += [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.assertEqual(self.agent.compact(), "这是摘要")
        self.assertEqual([m["role"] for m in b.messages], ["system", "user", "assistant"])
        self.assertEqual(b.tokens_context, 0)

    def test_循环内压缩要补一条_user(self):
        # compact_to 收尾是 assistant，任务没做完还要接着调 run_turn，
        # 不补 user 的话角色不交替
        b = self.use([])
        b.tokens_context = self.agent.context_limit
        quiet(self.agent._maybe_compact, mid_turn=True)
        self.assertEqual(b.messages[-1]["role"], "user")

    def test_收尾压缩不补_user(self):
        b = self.use([])
        b.tokens_context = self.agent.context_limit
        quiet(self.agent._maybe_compact)
        self.assertEqual(b.messages[-1]["role"], "assistant")

    def test_没到阈值不压(self):
        b = self.use([])
        b.tokens_context = int(self.agent.context_limit * 0.5)
        before = list(b.messages)
        quiet(self.agent._maybe_compact)
        self.assertEqual(b.messages, before)

    def test_摘要为空时历史照旧(self):
        b = self.use([])
        b.ask = lambda *a, **kw: "   "
        b.messages.append({"role": "user", "content": "a"})
        before = list(b.messages)
        self.assertEqual(self.agent.compact(), "")
        self.assertEqual(b.messages, before)


class TestProjectContext(Base):
    def test_按顺序找第一个存在的(self):
        root = Path(self.tmp.name)
        (root / "CLAUDE.md").write_text("claude 的约定", encoding="utf-8")
        (root / "AGENTS.md").write_text("agents 的约定", encoding="utf-8")
        name, text = agent_mod.load_project_context(root)
        self.assertEqual(name, "AGENTS.md")          # AGENTS 排在 CLAUDE 前面
        self.assertEqual(text, "agents 的约定")

    def test_空文件跳过(self):
        root = Path(self.tmp.name)
        (root / "AGENTS.md").write_text("   \n", encoding="utf-8")
        (root / "CLAUDE.md").write_text("有内容", encoding="utf-8")
        self.assertEqual(agent_mod.load_project_context(root)[0], "CLAUDE.md")

    def test_没有就返回两个空串(self):
        self.assertEqual(agent_mod.load_project_context(Path(self.tmp.name)), ("", ""))

    def test_超长要截断(self):
        root = Path(self.tmp.name)
        (root / "FEIYU.md").write_text("x" * 20000, encoding="utf-8")
        self.assertEqual(len(agent_mod.load_project_context(root)[1]),
                         agent_mod.MAX_CONTEXT_CHARS)

    def test_约定会进系统提示(self):
        root = Path(self.tmp.name)
        (root / "FEIYU.md").write_text("只用制表符缩进", encoding="utf-8")
        b = self.use([])
        self.agent.reload_context()
        self.assertIn("只用制表符缩进", b.system)


class TestModes(Base):
    def test_shift_tab_循环(self):
        self.agent.mode = "default"
        seen = [self.agent.cycle_mode() for _ in agent_mod.MODE_ORDER]
        self.assertEqual(seen, ["auto", "plan", "readonly", "default"])

    def test_风格会改系统提示(self):
        b = self.use([])
        self.agent.set_style("teaching")
        self.assertIn(agent_mod.STYLES["teaching"][1].strip()[:10], b.system)

    def test_两个集合的划分(self):
        # web_fetch 会把内容发出去，要问；但它不改状态，只读模式放行
        self.assertIn("web_fetch", agent_mod.GUARDED)
        self.assertNotIn("web_fetch", agent_mod.WRITES)
        self.assertTrue(agent_mod.WRITES <= agent_mod.GUARDED)


if __name__ == "__main__":
    unittest.main()


class TestVerifyConfig(unittest.TestCase):
    def test_单起一行才算配置(self):
        self.assertEqual(agent_mod.find_verify("# 项目\n\nverify: npm test\n\n别的"), "npm test")

    def test_剥掉反引号(self):
        self.assertEqual(agent_mod.find_verify("verify: `pytest -q`"), "pytest -q")

    def test_行中间提一嘴不算(self):
        # 正文里随口说到 verify: 不该被当成配置
        self.assertEqual(agent_mod.find_verify("我们用 verify: 这个词表示校验"), "")

    def test_大小写不敏感(self):
        self.assertEqual(agent_mod.find_verify("Verify: make check"), "make check")

    def test_没配就是空串(self):
        self.assertEqual(agent_mod.find_verify("什么都没有"), "")
        self.assertEqual(agent_mod.find_verify(""), "")

    def test_从约定文件里读出来(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "FEIYU.md").write_text("verify: make test\n", encoding="utf-8")
            self.assertEqual(Feiyu(Path(tmp)).verify, "make test")


class TestVerifyLoop(Base):
    def setUp(self):
        super().setUp()
        self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好了", [])])
        self.tools_ok()

    def test_只有写文件才触发(self):
        # bash 也能改文件，但每次 ls / git status 后都跑测试代价太大
        self.assertIn("write_file", agent_mod.VERIFY_AFTER)
        self.assertIn("edit_file", agent_mod.VERIFY_AFTER)
        self.assertNotIn("bash", agent_mod.VERIFY_AFTER)

    def test_通过时不打扰模型(self):
        b = self.agent.backend
        self.agent.verify = "true"
        quiet(self.agent.send, "写个文件")
        self.assertTrue(self.agent.verified_ok)
        self.assertNotIn("没有通过", self.tool_results(b)[0])

    def test_没过就把报错回灌(self):
        b = self.agent.backend
        self.agent.verify = "echo 三个测试挂了 >&2; exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertFalse(self.agent.verified_ok)
        result = self.tool_results(b)[0]
        self.assertIn("没有通过", result)
        self.assertIn("三个测试挂了", result)

    def test_没过的结果标成错误(self):
        b = self.agent.backend
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertTrue(b.messages[-2]["content"].startswith("错误")
                        or "没有通过" in self.tool_results(b)[0])

    def test_回灌之后历史仍然配对(self):
        b = self.agent.backend
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertEqual(unpaired(b.messages), set())

    def test_没配就不跑(self):
        self.agent.verify = ""
        quiet(self.agent.send, "写个文件")
        self.assertIsNone(self.agent.verified_ok)

    def test_命令跑不起来也不崩(self):
        self.agent.verify = "绝对没有这个命令 --xyz"
        quiet(self.agent.send, "写个文件")
        self.assertFalse(self.agent.verified_ok)

    def test_只读工具不触发(self):
        self.use([("", [call("c1", "read_file", path="a.py")]), ("好了", [])])
        self.tools_ok()
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "读个文件")
        self.assertIsNone(self.agent.verified_ok)

    def test_被拒的写不触发(self):
        self.agent.mode = "default"
        self.agent.approve = lambda n, a: "no"
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertIsNone(self.agent.verified_ok)


class TestReviewHelpers(Base):
    def test_评分能认出来(self):
        self.assertEqual(agent_mod.find_score("结论如下\n\n评分: 8/10"), 8.0)
        self.assertEqual(agent_mod.find_score("评分：9.5/10"), 9.5)
        self.assertIsNone(agent_mod.find_score("还行吧"))

    def test_没有改动就不评(self):
        self.use([])
        self.assertEqual(self.agent.review("任务"), "")

    def test_没有_git_时算出真的_diff(self):
        # 只给改完的整份文件，评分的模型看不到改动前的样子，
        # 会把"这文件本来就只有一个函数"读成"它把别的都删了"（实测踩过）
        (Path(self.tmp.name) / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        self.agent.touched = ["a.py"]
        self.agent.before = {"a.py": "x = 0\ny = 2\n"}
        body = self.agent.changes()
        self.assertIn("-x = 0", body)
        self.assertIn("+x = 1", body)
        self.assertNotIn("-y = 2", body)        # 没动的行不该出现在减号里

    def test_新建的文件整份都是新增(self):
        (Path(self.tmp.name) / "新.py").write_text("a\n", encoding="utf-8")
        self.agent.touched = ["新.py"]
        self.agent.before = {"新.py": ""}
        self.assertIn("+a", self.agent.changes())

    def test_读不出来的文件不崩(self):
        self.agent.touched = ["没有这个.py"]
        self.assertIn("没有这个.py", self.agent.changes())

    def test_动手前会存一份原样(self):
        (Path(self.tmp.name) / "a.py").write_text("原来的\n", encoding="utf-8")
        self.use([("", [call("c1", "edit_file", path="a.py",
                             old_str="原来的", new_str="改过的")]), ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "改一下")
        self.assertEqual(self.agent.before["a.py"], "原来的\n")

    def test_写文件会记进改动清单(self):
        self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "写个文件")
        self.assertEqual(self.agent.touched, ["a.py"])

    def test_同一个文件不重复记(self):
        self.use([("", [call("c1", "write_file", path="a.py", content="x"),
                        call("c2", "edit_file", path="a.py", old_str="x", new_str="y")]),
                  ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "改两次")
        self.assertEqual(self.agent.touched, ["a.py"])


class TestFinishJson(Base):
    SPEC = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}

    def test_一次就对(self):
        self.use([('{"ok": true}', [])])
        self.assertEqual(quiet(self.agent.finish_json, self.SPEC), ({"ok": True}, []))

    def test_裹了代码块也认(self):
        self.use([('```json\n{"ok": false}\n```', [])])
        self.assertEqual(quiet(self.agent.finish_json, self.SPEC)[0], {"ok": False})

    def test_不合规会让它重出(self):
        b = self.use([('{"ok": "yes"}', []), ('{"ok": true}', [])])
        value, problems = quiet(self.agent.finish_json, self.SPEC)
        self.assertEqual(value, {"ok": True})
        self.assertEqual(problems, [])

    def test_一直不合规就报出问题(self):
        self.use([('{"不对": 1}', [])] * (4))
        value, problems = quiet(self.agent.finish_json, self.SPEC)
        self.assertTrue(problems)

    def test_没有模型时报错(self):
        self.agent.backend = None
        self.assertTrue(quiet(self.agent.finish_json, self.SPEC)[1])


class TestTransient(unittest.TestCase):
    """判断一个错值不值得重试 不绑 SDK 的异常类：两种后端，
    openai 还是可选依赖，硬 import 会把 anthropic-only 的装法弄坏 """

    def make(self, name, code=None):
        exc = type(name, (Exception,), {})()
        if code is not None:
            exc.status_code = code
        return exc

    def test_按状态码认(self):
        for code in (429, 500, 502, 503, 504, 408):
            self.assertTrue(agent_mod.transient(self.make("Whatever", code)), code)

    def test_客户端错误不重试(self):
        for code in (400, 401, 403, 404, 422):
            self.assertFalse(agent_mod.transient(self.make("Whatever", code)), code)

    def test_按类名认连接类错误(self):
        for name in ("APIConnectionError", "APITimeoutError", "RateLimitError"):
            self.assertTrue(agent_mod.transient(self.make(name)), name)

    def test_认得出子类(self):
        base = type("APIConnectionError", (Exception,), {})
        self.assertTrue(agent_mod.transient(type("我的错", (base,), {})()))

    def test_普通异常不重试(self):
        self.assertFalse(agent_mod.transient(ValueError("随便")))


class TestStreamRetry(Base):
    def setUp(self):
        super().setUp()
        # 别让测试真的睡两秒
        self._wait = agent_mod.RETRY_WAIT
        agent_mod.RETRY_WAIT = 0
        self.addCleanup(lambda: setattr(agent_mod, "RETRY_WAIT", self._wait))

    def boom(self, times, exc_factory, text=""):
        """前 times 次抛错，之后正常返回 """
        state = {"n": 0}
        b = self.use([("答完了", [])])
        real = b.run_turn
        def run_turn(on_text, on_tool=None):
            if state["n"] < times:
                state["n"] += 1
                if text:
                    on_text(text)
                raise exc_factory()
            return real(on_text, on_tool)
        b.run_turn = run_turn
        return b, state

    def rate_limit(self):
        exc = type("RateLimitError", (Exception,), {})()
        exc.status_code = 429
        return exc

    def test_还没吐字就重试(self):
        b, state = self.boom(1, self.rate_limit)
        self.assertEqual(quiet(self.agent.send, "问题"), "答完了")
        self.assertEqual(state["n"], 1)

    def test_重试有上限(self):
        b, state = self.boom(99, self.rate_limit)
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        self.assertEqual(state["n"], agent_mod.STREAM_RETRIES + 1)

    def test_已经吐字了就不重试(self):
        # 重发会让模型把刚说的话再说一遍
        b, state = self.boom(99, self.rate_limit, text="我先看一下这个文件")
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        self.assertEqual(state["n"], 1)

    def test_已经吐的字要留在历史里(self):
        b, _ = self.boom(99, self.rate_limit, text="我先看一下")
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertIn("我先看一下", b.messages[-1]["content"])

    def test_报错后角色仍然交替(self):
        # 不补 assistant 的话，下次请求两条连着的 user，跟中断那个坑一样
        b, _ = self.boom(99, self.rate_limit)
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        b.add_user_message("再说一句")
        roles = [m["role"] for m in b.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_不该重试的错直接抛(self):
        bad = lambda: ValueError("参数写错了")
        b, state = self.boom(99, bad)
        with self.assertRaises(ValueError):
            quiet(self.agent.send, "问题")
        self.assertEqual(state["n"], 1)

    def test_重试计数每轮清零(self):
        # 第一轮重试过，不该影响后面几轮的额度
        self.assertEqual(agent_mod.STREAM_RETRIES, 2)
