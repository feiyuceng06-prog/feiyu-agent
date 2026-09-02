"""子 agent

存在的理由是上下文隔离：子 agent 翻了十几个文件、几百行原文，
主对话只收到一段结论 所以最要紧的两条断言是——**原文不进主历史**、
**用量照样算钱**（看不见的开销比看得见的更危险）
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from feiyu import agent as agent_mod
from feiyu import subagent
from feiyu.agent import Feiyu
from feiyu.providers import ToolCall, TurnResult
from feiyu.tools import TOOL_SCHEMAS, ToolResult
from tests.fakes import FakeBackend, call, unpaired
from tests.test_agent import quiet


class FakeSub(FakeBackend):
    """冒充子 agent 的后端 build_backend 会被换成返回它 """
    made: list = []

    def __init__(self, script, tokens=1000, cost=0.05):
        super().__init__(script)
        self.tokens_total = tokens
        self.cost = cost
        self.priced = True
        FakeSub.made.append(self)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text("def handle():\n    timeout = 30\n", encoding="utf-8")
        self.agent = Feiyu(self.root)
        self.agent.name = "fake"
        self.agent.mode = "auto"
        FakeSub.made = []
        self._real = subagent.__dict__.get("_build")

    def tearDown(self):
        self.tmp.cleanup()

    def fake_sub(self, script):
        """让 subagent.run 用假后端，并且给它一条假的模型配置 """
        import feiyu.providers as providers
        self._saved = providers.build_backend
        providers.build_backend = lambda entry, **kw: FakeSub(script)
        self.addCleanup(lambda: setattr(providers, "build_backend", self._saved))
        import feiyu.keys as keys
        self._keys = keys.load
        keys.load = lambda: {"fake": {"model": "m", "key": "k", "kind": "openai"}}
        self.addCleanup(lambda: setattr(keys, "load", self._keys))

    def use(self, calls):
        self.agent.backend = FakeBackend([("", calls), ("好了", [])])
        return self.agent.backend

    def results(self, backend):
        return [m["content"] for m in backend.messages if m["role"] == "tool"]


class TestToolset(unittest.TestCase):
    def test_只有只读四件(self):
        # 不带 web_fetch：子 agent 跑在工作线程里没法要确认，
        # 而且那是「读了网页然后被诱导去干别的」的注入路径
        self.assertEqual(subagent.TOOLS, ("read_file", "list_files", "search", "symbol"))

    def test_这几个都真实存在(self):
        names = {s["name"] for s in TOOL_SCHEMAS}
        for tool in subagent.TOOLS:
            self.assertIn(tool, names)

    def test_不给它写和执行(self):
        for banned in ("bash", "write_file", "edit_file", "task", "remember"):
            self.assertNotIn(banned, subagent.TOOLS)

    def test_系统提示要求带行号(self):
        # 派它来的人看不到它读过什么，事实必须写在回答里
        self.assertIn("文件:行号", subagent.SYSTEM)


class TestRun(Base):
    def test_把结论带回来(self):
        self.fake_sub([("超时处理在 a.py:2", [])])
        report, lines, used = subagent.run(
            "哪里处理了超时", entry={"model": "m", "key": "k"}, workspace=self.root)
        self.assertEqual(report, "超时处理在 a.py:2")

    def test_它自己会用工具(self):
        self.fake_sub([
            ("", [ToolCall(id="s1", name="search", input={"regex": "timeout"})]),
            ("在 a.py:2", []),
        ])
        report, lines, used = subagent.run(
            "查超时", entry={"model": "m", "key": "k"}, workspace=self.root)
        self.assertEqual(report, "在 a.py:2")
        self.assertTrue(any("search" in text for _, text in lines))

    def test_不给它的工具会被挡(self):
        self.fake_sub([
            ("", [ToolCall(id="s1", name="bash", input={"command": "rm -rf /"})]),
            ("好吧", []),
        ])
        subagent.run("试试", entry={"model": "m", "key": "k"}, workspace=self.root)
        sub = FakeSub.made[0]
        refused = [m for m in sub.messages if m["role"] == "tool"]
        self.assertIn("只能用", refused[0]["content"])

    def test_轮数到上限会收手(self):
        loop = [("", [ToolCall(id="s%d" % i, name="search", input={"regex": "x"})])
                for i in range(subagent.MAX_ROUNDS + 3)]
        self.fake_sub(loop)
        report, _, _ = subagent.run(
            "转不出来", entry={"model": "m", "key": "k"}, workspace=self.root)
        self.assertIn("还没收敛", report)

    def test_什么都没答也有个交代(self):
        self.fake_sub([("", [])])
        report, _, _ = subagent.run(
            "问题", entry={"model": "m", "key": "k"}, workspace=self.root)
        self.assertTrue(report)

    def test_报告过长会截断(self):
        self.fake_sub([("长" * 20000, [])])
        report, _, _ = subagent.run(
            "问题", entry={"model": "m", "key": "k"}, workspace=self.root)
        self.assertLessEqual(len(report), subagent.MAX_REPORT)

    def test_带回用量(self):
        self.fake_sub([("答案", [])])
        _, _, used = subagent.run(
            "问题", entry={"model": "m", "key": "k"}, workspace=self.root)
        self.assertEqual(used["tokens"], 1000)
        self.assertAlmostEqual(used["cost"], 0.05)


class TestIsolation(Base):
    def test_原文不进主对话(self):
        # 这就是子 agent 存在的全部理由
        self.fake_sub([
            ("", [ToolCall(id="s1", name="read_file", input={"path": "a.py"})]),
            ("超时设在 a.py:2", []),
        ])
        b = self.use([call("c1", "task", prompt="哪里处理了超时")])
        quiet(self.agent.send, "查一下超时")
        history = "".join(str(m.get("content")) for m in b.messages)
        self.assertIn("超时设在 a.py:2", history)      # 结论进来了
        self.assertNotIn("timeout = 30", history)      # 原文没进来

    def test_子_agent_看不到主对话(self):
        self.fake_sub([("答案", [])])
        b = self.use([call("c1", "task", prompt="独立的问题")])
        b.messages.append({"role": "user", "content": "主对话里的秘密"})
        quiet(self.agent.send, "派个人去查")
        sub = FakeSub.made[0]
        self.assertNotIn("秘密", "".join(str(m.get("content")) for m in sub.messages))

    def test_自己的_toolbox(self):
        # cwd、清单这些状态不能跟主 agent 共享
        self.agent.toolbox.todos.replace([{"text": "主 agent 的清单"}])
        self.fake_sub([("答案", [])])
        self.use([call("c1", "task", prompt="问题")])
        quiet(self.agent.send, "派个人")
        self.assertEqual(len(self.agent.toolbox.todos.items), 1)


class TestBilling(Base):
    def test_用量算进主计数(self):
        # 看不见的开销比看得见的更危险
        self.fake_sub([("答案", [])])
        self.use([call("c1", "task", prompt="问题")])
        before = self.agent.tokens
        quiet(self.agent.send, "派个人")
        self.assertGreaterEqual(self.agent.tokens - before, 1000)

    def test_花费也算进去(self):
        self.fake_sub([("答案", [])])
        b = self.use([call("c1", "task", prompt="问题")])
        b.priced = True
        quiet(self.agent.send, "派个人")
        self.assertGreaterEqual(self.agent.cost, 0.05)


class TestInLoop(Base):
    def test_空问题报错(self):
        out = self.agent.toolbox.run("task", {"prompt": "  "})
        self.assertTrue(out.is_error)

    def test_没有模型配置时不崩(self):
        import feiyu.keys as keys
        saved = keys.load
        keys.load = lambda: {}
        try:
            report, lines = self.agent._spawn("问题")
            self.assertIn("派不出去", report)
        finally:
            keys.load = saved

    def test_过程带回来给主线程画(self):
        # 并行跑几个子 agent 时各自打印会交叉成一团
        self.fake_sub([
            ("", [ToolCall(id="s1", name="search", input={"regex": "x"})]),
            ("答案", []),
        ])
        out = self.agent.toolbox.run("task", {"prompt": "问题"})
        self.assertFalse(out.is_error)
        self.assertTrue(out.detail)

    def test_能并行派几个(self):
        self.assertIn("task", agent_mod.PARALLEL_SAFE)

    def test_并行派出去历史仍然配对(self):
        self.fake_sub([("答案", [])] * 20)
        b = self.use([call("c1", "task", prompt="问题一"),
                      call("c2", "task", prompt="问题二"),
                      call("c3", "task", prompt="问题三")])
        quiet(self.agent.send, "派三个人")
        self.assertEqual(unpaired(b.messages), set())
        self.assertEqual(len(self.results(b)), 3)


if __name__ == "__main__":
    unittest.main()


class TestSharedLsp(Base):
    """symbol 走主 Toolbox 的语言服务器：一个子 agent 一个 Toolbox，各起一个进程太贵 """

    def test_传进来的客户端真被用上(self):
        from feiyu.tools import Toolbox
        seen = {}
        real = Toolbox.run

        def run(box, name, args):
            if name == "symbol":
                seen["lsp"] = box.lsp
                return ToolResult("没装", is_error=True)
            return real(box, name, args)
        Toolbox.run = run
        self.addCleanup(lambda: setattr(Toolbox, "run", real))
        self.fake_sub([("看", [ToolCall("s", "symbol", {"action": "references", "path": "a.py",
                                                       "name": "handle"})]), ("完", [])])
        spy = object()
        subagent.run("handle 在哪被调用", entry={"model": "m", "key": "k"},
                     workspace=self.root, lsp=spy)
        self.assertIs(seen.get("lsp"), spy)

    def test_主_agent_派活时把自己的传过去(self):
        self.fake_sub([("完", [])])
        got = {}
        real = subagent.run

        def spy(prompt, **kw):
            got.update(kw)
            return real(prompt, **kw)
        agent_mod.subagent_mod.run = spy
        self.addCleanup(lambda: setattr(agent_mod.subagent_mod, "run", real))
        self.agent._spawn("问题")
        self.assertIs(got.get("lsp"), self.agent.toolbox.lsp)
