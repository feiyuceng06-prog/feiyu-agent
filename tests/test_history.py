"""历史瘦身 断言盯着两件事：**省下了东西**，以及**配对没被弄坏**

折叠只换 tool_result 的内容，不删消息 少一条配对结果不是"下次凑合能跑"，
是整个会话废掉（0.12.0 那个坑），所以每条用例都要顺带验一次形状
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from feiyu import agent as agent_mod, history
from feiyu.agent import Feiyu
from feiyu.tools import ToolResult
from tests.fakes import FakeBackend, call, unpaired


def openai_history(*bodies):
    """openai 兼容那边的形状：assistant 带 tool_calls，结果是独立的 tool 消息 """
    msgs = [{"role": "system", "content": "s"}]
    for i, body in enumerate(bodies):
        cid = "c%d" % i
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": cid, "content": body})
    return msgs


def anthropic_history(*bodies):
    """anthropic 那边：tool_result 是 user 消息 content 列表里的块 """
    msgs = []
    for i, body in enumerate(bodies):
        cid = "c%d" % i
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": cid, "name": "read_file", "input": {}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": body}]})
    return msgs


class TestSlots(unittest.TestCase):
    def test_两种后端的形状都认得(self):
        for build in (openai_history, anthropic_history):
            found = [(s.id, s.text) for s in history.slots(build("甲", "乙"))]
            self.assertEqual(found, [("c0", "甲"), ("c1", "乙")], build.__name__)

    def test_不去动_assistant_自己说的话(self):
        msgs = [{"role": "assistant", "content": "这是正文不是工具结果"}]
        self.assertEqual(list(history.slots(msgs)), [])

    def test_带图的结果跳过(self):
        """content 是列表说明里面有图，图另有 without_images 管 """
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c0",
             "content": [{"type": "text", "text": "看图"}]}]}]
        self.assertEqual(list(history.slots(msgs)), [])


class TestExpire(unittest.TestCase):
    def test_换掉内容但配对不动(self):
        msgs = openai_history("一整份文件" * 500, "另一份")
        before = unpaired(msgs)
        self.assertEqual(history.expire(msgs, {"c0"}, "过期了"), 1)
        self.assertEqual(unpaired(msgs), before)          # 形状没坏
        self.assertNotIn("一整份文件", msgs[2]["content"])
        self.assertEqual(msgs[4]["content"], "另一份")     # 没被点名的不动

    def test_折叠过的不会再折叠一次(self):
        msgs = openai_history("原文")
        history.expire(msgs, {"c0"}, "过期了")
        once = msgs[2]["content"]
        self.assertEqual(history.expire(msgs, {"c0"}, "又过期了"), 0)
        self.assertEqual(msgs[2]["content"], once)

    def test_没点名的_id_一条都不动(self):
        msgs = openai_history("甲")
        self.assertEqual(history.expire(msgs, set(), "过期了"), 0)
        self.assertEqual(history.expire(msgs, {"不存在"}, "过期了"), 0)


class TestTrim(unittest.TestCase):
    def test_只压靠前的大块(self):
        msgs = openai_history("大" * 5000, "小", "大" * 5000)
        # keep_last=2 只保住最后一条 tool（连同它前面那条 assistant）
        done = history.trim(msgs, keep_last=2, limit=1000, keep=100)
        self.assertEqual(done, 1)
        self.assertTrue(msgs[2]["content"].startswith(history.MARK))
        self.assertEqual(len(msgs[6]["content"]), 5000)   # 最近的原样留着

    def test_压完还留着头和尾(self):
        msgs = openai_history("开头" + "填" * 5000 + "结尾")
        history.trim(msgs, keep_last=0, limit=1000, keep=100)
        body = msgs[2]["content"]
        self.assertIn("开头", body)
        self.assertIn("结尾", body)
        self.assertLess(len(body), 1000)

    def test_短的不动(self):
        msgs = openai_history("短")
        self.assertEqual(history.trim(msgs, keep_last=0, limit=1000, keep=100), 0)
        self.assertEqual(msgs[2]["content"], "短")


class TestFoldInAgent(unittest.TestCase):
    """端到端：真的跑一轮工具，看历史有没有瘦下来 """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # 每行都不一样，edit_file 的 old_str 才唯一得了
        (self.root / "big.py").write_text(
            "\n".join("x%d = %d" % (i, i) for i in range(400)), encoding="utf-8")
        self.agent = Feiyu(self.root)
        self.agent.name = "fake"
        self.agent.mode = "auto"

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, script):
        self.agent.backend = FakeBackend(script)
        # **折叠现在是有闸的**（0.60.0）：会缓存的后端上折叠是赔本买卖，
        # 所以只在"这后端不缓存"或"上下文过半"时才动手。这些用例测的是折叠
        # 本身，先把闸打开——装成一个不缓存的后端。
        self.agent.backend.tokens_in = 10 ** 6
        self.agent.backend.tokens_hit = 0
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.send("干活")
        return self.agent.backend.messages

    def bodies(self, msgs):
        return [m["content"] for m in msgs if m.get("role") == "tool"]

    def test_同一个文件读两次_第一次的原文不再留在历史里(self):
        msgs = self.run_script([
            ("", [call("a", "read_file", path="big.py")]),
            ("", [call("b", "read_file", path="big.py")]),
            ("看完了", []),
        ])
        first, second = self.bodies(msgs)
        self.assertTrue(first.startswith(history.MARK))
        self.assertIn("重新读一次", first)
        self.assertIn("x0 = 0", second)         # 最新那份完整留着
        self.assertEqual(unpaired(msgs), set())

    def test_改过之后旧的读取也过期(self):
        msgs = self.run_script([
            ("", [call("a", "read_file", path="big.py")]),
            ("", [call("b", "edit_file", path="big.py",
                       old_str="x0 = 0", new_str="x0 = 9")]),
            ("改完了", []),
        ])
        self.assertTrue(self.bodies(msgs)[0].startswith(history.MARK))
        self.assertIn("被改过", self.bodies(msgs)[0])
        self.assertEqual(unpaired(msgs), set())

    def test_同一个文件不同范围都留着(self):
        """大文件分段读：读了前一段再读后一段，前一段不能折 """
        msgs = self.run_script([
            ("", [call("a", "read_file", path="big.py", offset=1, limit=100)]),
            ("", [call("b", "read_file", path="big.py", offset=200, limit=50)]),
            ("看完了", []),
        ])
        first, second = self.bodies(msgs)
        self.assertIn("x0 = 0", first)
        self.assertIn("x199 = 199", second)

    def test_同一范围再读一次才折_改过之后各段全折(self):
        msgs = self.run_script([
            ("", [call("a", "read_file", path="big.py", offset=1, limit=100)]),
            ("", [call("b", "read_file", path="big.py", offset=200, limit=50)]),
            ("", [call("c", "read_file", path="big.py", offset=1, limit=100)]),
            ("", [call("d", "edit_file", path="big.py", old_str="x0 = 0", new_str="x0 = 9")]),
            ("改完了", []),
        ])
        bodies = self.bodies(msgs)
        self.assertTrue(bodies[0].startswith(history.MARK))
        self.assertIn("同一段", bodies[0])
        self.assertTrue(bodies[1].startswith(history.MARK))     # 改过之后全折
        self.assertTrue(bodies[2].startswith(history.MARK))
        self.assertEqual(unpaired(msgs), set())

    def test_不同文件之间互不影响(self):
        (self.root / "other.py").write_text("y = 2\n", encoding="utf-8")
        msgs = self.run_script([
            ("", [call("a", "read_file", path="big.py")]),
            ("", [call("b", "read_file", path="other.py")]),
            ("看完了", []),
        ])
        self.assertIn("x0 = 0", self.bodies(msgs)[0])
        self.assertEqual(unpaired(msgs), set())

    def test_写法不同但指同一个文件(self):
        """模型写 ./big.py 和 big.py 指的是同一份，折叠要认得出来 """
        msgs = self.run_script([
            ("", [call("a", "read_file", path="big.py")]),
            ("", [call("b", "read_file", path="./big.py")]),
            ("看完了", []),
        ])
        self.assertTrue(self.bodies(msgs)[0].startswith(history.MARK))

    def test_读失败不登记(self):
        msgs = self.run_script([
            ("", [call("a", "read_file", path="没有这个.py")]),
            ("", [call("b", "read_file", path="big.py")]),
            ("看完了", []),
        ])
        self.assertEqual(unpaired(msgs), set())

    def test_压缩之后登记要清空(self):
        self.agent.backend = FakeBackend([])
        self.agent._reads["/x"] = ["a"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.compact()
        self.assertEqual(self.agent._reads, {})


if __name__ == "__main__":
    unittest.main()


class TestShrink(unittest.TestCase):
    def test_急救时最近的和折叠过的都压(self):
        msgs = openai_history("A" * 5000, "B" * 5000)
        history.trim(msgs, keep_last=0, limit=1000, keep=1500)     # 折叠过一次，仍然 3000+
        self.assertEqual(history.shrink(msgs, limit=2000, keep=300), 2)
        for body in (m["content"] for m in msgs if m["role"] == "tool"):
            self.assertLess(len(body), 2000)
            self.assertTrue(body.startswith(history.MARK))

    def test_幂等(self):
        msgs = openai_history("A" * 5000)
        history.shrink(msgs, limit=2000, keep=300)
        self.assertEqual(history.shrink(msgs, limit=2000, keep=300), 0)


class TestFoldSearch(TestFoldInAgent):
    def test_同样的参数再搜一次_旧结果折叠(self):
        msgs = self.run_script([
            ("", [call("a", "search", regex="x1 = 1")]),
            ("", [call("b", "search", regex="x1 = 1")]),
            ("看完了", []),
        ])
        first, second = self.bodies(msgs)
        self.assertTrue(first.startswith(history.MARK))
        self.assertIn("search", first)
        self.assertIn("x1 = 1", second)
        self.assertEqual(unpaired(msgs), set())

    def test_参数不同的不折叠(self):
        msgs = self.run_script([
            ("", [call("a", "search", regex="x1 = 1")]),
            ("", [call("b", "search", regex="x2 = 2")]),
            ("看完了", []),
        ])
        self.assertFalse(self.bodies(msgs)[0].startswith(history.MARK))


class TestAge(unittest.TestCase):
    """bash 输出按轮数老化：过了 AGE_ROUNDS 批只留开头，最近的不动 """

    def test_留开头_标记_幂等(self):
        msgs = openai_history("A" * 1000)
        self.assertEqual(history.age(msgs, {"c0": "过期了"}, keep=100), 1)
        body = msgs[2]["content"]
        self.assertTrue(body.startswith(history.MARK))
        self.assertIn("A" * 100, body)
        self.assertNotIn("A" * 101, body)
        self.assertIn("900", body)
        self.assertEqual(history.age(msgs, {"c0": "过期了"}, keep=100), 0)

    def test_不在名单里的不动(self):
        msgs = anthropic_history("B" * 1000)
        self.assertEqual(history.age(msgs, {"zz": "过期了"}, keep=100), 0)
        self.assertEqual(msgs[1]["content"][0]["content"], "B" * 1000)


class TestAgeInAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.mode = "auto"
        big = "L" * 900
        self.agent.toolbox.run = lambda name, args: ToolResult(big, summary="ok")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, n):
        from feiyu import agent as agent_mod
        script = [("", [call("c%d" % i, "bash", command="ls -la %d" % i)]) for i in range(n)]
        script.append(("完了", []))
        self.agent.backend = FakeBackend(script)
        # 折叠有闸了（0.60.0），这条测的是折叠本身 —— 装成不缓存的后端把闸打开
        self.agent.backend.tokens_in = 10 ** 6
        self.agent.backend.tokens_hit = 0
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.send("干活")
        return [m["content"] for m in self.agent.backend.messages if m.get("role") == "tool"]

    def test_老的折_近的留(self):
        from feiyu.agent import AGE_ROUNDS
        bodies = self.run_script(AGE_ROUNDS + 2)
        self.assertTrue(bodies[0].startswith(history.MARK))
        self.assertIn("ls -la 0", bodies[0])
        self.assertTrue(bodies[1].startswith(history.MARK))
        for body in bodies[2:]:
            self.assertFalse(body.startswith(history.MARK))
        self.assertEqual(unpaired(self.agent.backend.messages), set())

    def test_短输出不登记(self):
        self.agent.toolbox.run = lambda name, args: ToolResult("短", summary="ok")
        from feiyu.agent import AGE_ROUNDS
        bodies = self.run_script(AGE_ROUNDS + 3)
        self.assertTrue(all(b == "短" for b in bodies))

    def test_清空历史后登记也清(self):
        from feiyu.agent import AGE_ROUNDS
        self.run_script(2)
        self.assertTrue(self.agent._aging)
        self.agent.reset()
        self.assertFalse(self.agent._aging)


class TestFoldGate(unittest.TestCase):
    """折叠什么时候才划算

    前缀缓存的规矩是"改了第 k 条，k 之后全部作废"，而 expire / age 改的都是
    靠前的消息。实测一份 40 轮的历史：age 省 10.9k token 但下一轮花费涨 4.4 倍，
    expire 涨 7.6 倍。所以会缓存的后端上**不到紧要关头不折**。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.backend = FakeBackend([])

    def seen(self, got, hit):
        self.agent.backend.tokens_in = got
        self.agent.backend.tokens_hit = hit

    def test_样本太少时先当它会缓存(self):
        """头一两轮本来就没有可命中的前缀，按"不缓存"办的话
        会在会话最开头折得最狠 —— 正好是最不该折的时候 """
        self.seen(1000, 0)
        self.assertTrue(self.agent._caching())
        self.assertFalse(self.agent._should_fold())

    def test_看出来不缓存就放开折(self):
        self.seen(agent_mod.CACHING_SAMPLE * 2, 0)
        self.assertFalse(self.agent._caching())
        self.assertTrue(self.agent._should_fold())

    def test_命中率高就不折(self):
        self.seen(agent_mod.CACHING_SAMPLE * 2, agent_mod.CACHING_SAMPLE * 2)
        self.assertTrue(self.agent._caching())
        self.assertFalse(self.agent._should_fold())

    def test_上下文过半了照折不误(self):
        """快撞窗口了，这时候省地方比省钱要紧 """
        self.seen(agent_mod.CACHING_SAMPLE * 2, agent_mod.CACHING_SAMPLE * 2)
        self.agent.backend.tokens_context = int(
            self.agent.context_limit * agent_mod.FOLD_AFTER) + 1
        self.assertTrue(self.agent._should_fold())

    def test_不折的时候登记要留着(self):
        """留着才能等紧张了一起折 —— pop 掉的话那几条就再也折不了了 """
        self.seen(agent_mod.CACHING_SAMPLE * 2, agent_mod.CACHING_SAMPLE * 2)
        self.agent._reads["/x.py"] = ["c1"]
        self.agent._expire("/x.py", "过期了")
        self.assertEqual(self.agent._reads.get("/x.py"), ["c1"])
        # 紧张起来之后同一条就折得动了
        self.agent.backend.tokens_context = self.agent.context_limit
        self.agent._expire("/x.py", "过期了")
        self.assertNotIn("/x.py", self.agent._reads)
