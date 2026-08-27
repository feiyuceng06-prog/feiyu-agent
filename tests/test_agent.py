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
        def boom(on_text):
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
        def boom(on_text):
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
        self.assertEqual(seen, ["auto", "readonly", "default"])

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
