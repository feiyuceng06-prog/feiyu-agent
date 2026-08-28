"""ask_user：**答案要能改变它接下来怎么做**，问不到人时也不能卡住

跟 confirm 的关键区别：confirm 问的是"准不准"，这个问的是"怎么做"，
所以没人回答时不能当成"拒绝"——那会让它停在原地
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from feiyu import permissions, ui
from feiyu.agent import GUARDED, PARALLEL_SAFE, WRITES, Feiyu
from feiyu.tools import TOOL_SCHEMAS, Toolbox
from tests.fakes import FakeBackend, call, unpaired


class TestTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.asked = []

    def tearDown(self):
        self.tmp.cleanup()

    def box(self, answer="", **kw):
        def ask(question, options):
            self.asked.append((question, options))
            return answer
        return Toolbox(Path(self.tmp.name), ask_user=ask, **kw)

    def test_答复原样回给模型(self):
        out = self.box("只改 writer").run(
            "ask_user", {"question": "两处一起改吗？", "options": ["一起改", "只改 writer"]})
        self.assertFalse(out.is_error)
        self.assertIn("只改 writer", out.content)
        self.assertEqual(self.asked[0][0], "两处一起改吗？")

    def test_没人能回答时要让它自己定_而不是当成拒绝(self):
        """管道 / 一次性任务里没人能答 回成"被拒绝"的话它会停在原地反复问 """
        out = Toolbox(Path(self.tmp.name)).run("ask_user", {"question": "选哪个？"})
        self.assertIn("自己挑一个合理的做法", out.content)

    def test_用户没答也要让它往下走(self):
        out = self.box("").run("ask_user", {"question": "选哪个？"})
        self.assertFalse(out.is_error)          # 不是错误，是"你自己定"
        self.assertIn("别再问同一件事", out.content)

    def test_空问题拒掉(self):
        self.assertTrue(self.box("x").run("ask_user", {"question": "  "}).is_error)

    def test_选项里的空值被滤掉(self):
        self.box("甲").run("ask_user", {"question": "?", "options": ["甲", "", "  ", "乙"]})
        self.assertEqual(self.asked[0][1], ["甲", "乙"])

    def test_选项不是数组也不能崩(self):
        out = self.box("甲").run("ask_user", {"question": "?", "options": "甲"})
        self.assertFalse(out.is_error)
        self.assertEqual(self.asked[0][1], [])


class TestWiring(unittest.TestCase):
    def test_三个集合都不进(self):
        """进 GUARDED = 问你之前先问你「要不要问你」；进 WRITES = 只读模式下
        连问一句都不行；进 PARALLEL_SAFE = 跑在工作线程里，那儿读不了 stdin """
        for group in (GUARDED, WRITES, PARALLEL_SAFE):
            self.assertNotIn("ask_user", group)

    def test_default_模式下不会被拦着问(self):
        """不在 GUARDED 里，_gate 就直接放行——用不着用户先点头"允许它问你" """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        agent = Feiyu(Path(tmp.name))
        agent.mode = "default"
        agent.approve = lambda name, args: "no"      # 真被问到就是失败
        ok, _, _, _ = agent._gate(
            type("C", (), {"name": "ask_user", "input": {"question": "?"}})(), False)
        self.assertTrue(ok)

    def test_出厂放行列表里有它(self):
        self.assertIn("Ask(*)", permissions.DEFAULT_ALLOW)

    def test_有中文标签和显示参数(self):
        self.assertEqual(ui.tool_label("ask_user"), "Ask")
        self.assertEqual(ui.tool_target("ask_user", {"question": "选哪个？"}), "选哪个？")

    def test_schema_在册(self):
        self.assertIn("ask_user", {s["name"] for s in TOOL_SCHEMAS})

    def test_非终端时_ask_choice_返回空串(self):
        """没有 EscInterrupt 守卫就是没人能回答，不能在那儿等输入 """
        self.assertEqual(ui.ask_choice("选哪个？", ["甲", "乙"]), "")


class TestInLoop(unittest.TestCase):
    """端到端：问一句、拿到答复、接着干，历史形状还得是对的 """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.mode = "auto"

    def tearDown(self):
        self.tmp.cleanup()

    def test_问完能接着往下做(self):
        self.agent.ask_user = lambda q, o: "选第二个"
        self.agent.backend = FakeBackend([
            ("", [call("a", "ask_user", question="选哪个？", options=["甲", "乙"])]),
            ("按你说的做完了", []),
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            out = self.agent.send("干活")
        self.assertEqual(out, "按你说的做完了")
        answers = [m["content"] for m in self.agent.backend.messages if m.get("role") == "tool"]
        self.assertIn("选第二个", answers[0])
        self.assertEqual(unpaired(self.agent.backend.messages), set())

    def test_界面层没装回调时不会卡住(self):
        self.agent.backend = FakeBackend([
            ("", [call("a", "ask_user", question="选哪个？")]),
            ("我自己定了", []),
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            out = self.agent.send("干活")
        self.assertEqual(out, "我自己定了")


if __name__ == "__main__":
    unittest.main()
