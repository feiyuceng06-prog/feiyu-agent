"""会话存盘 存的是完整对话原文，所以既要能存回来，也要 0600 """

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from feiyu import keys as keys_mod
from feiyu import session
from tests.fakes import FakeBackend


class Agentish:
    """session.save 只用到这几样，不必真起一个 Feiyu """
    def __init__(self, backend, name="fake"):
        self.backend = backend
        self.name = name
        self.style = "concise"
        self.mode = "readonly"
        self.ready = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = session.DIR
        session.DIR = os.path.join(self.tmp.name, "sessions")
        self.ws = Path(self.tmp.name) / "ws"

    def tearDown(self):
        session.DIR = self._saved
        self.tmp.cleanup()


class TestRoundTrip(Base):
    def make(self):
        b = FakeBackend([])
        b.messages += [{"role": "user", "content": "你好"},
                       {"role": "assistant", "content": "在"}]
        b.tokens_total, b.tokens_context, b.cost, b.priced = 500, 300, 1.25, True
        return b

    def test_存了能读回来(self):
        session.save(self.ws, Agentish(self.make()))
        data = session.load(self.ws)
        self.assertEqual(data["model"], "fake")
        self.assertEqual(data["style"], "concise")
        self.assertEqual(data["mode"], "readonly")
        self.assertEqual(data["tokens_total"], 500)
        self.assertEqual(data["cost"], 1.25)
        self.assertEqual(len(data["messages"]), 3)

    def test_权限是_600(self):
        session.save(self.ws, Agentish(self.make()))
        mode = stat.S_IMODE(os.stat(session._path(self.ws)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_不同工作区互不干扰(self):
        session.save(self.ws, Agentish(self.make()))
        other = Path(self.tmp.name) / "另一个"
        self.assertIsNone(session.load(other))

    def test_只有系统提示时不存(self):
        session.save(self.ws, Agentish(FakeBackend([])))
        self.assertIsNone(session.load(self.ws))

    def test_没有模型时不存(self):
        a = Agentish(self.make())
        a.ready = False
        session.save(self.ws, a)
        self.assertIsNone(session.load(self.ws))

    def test_删掉(self):
        session.save(self.ws, Agentish(self.make()))
        session.drop(self.ws)
        self.assertIsNone(session.load(self.ws))

    def test_删不存在的不崩(self):
        session.drop(self.ws)

    def test_坏文件当作没有(self):
        os.makedirs(session.DIR, exist_ok=True)
        with open(session._path(self.ws), "w", encoding="utf-8") as f:
            f.write("{ 坏了")
        self.assertIsNone(session.load(self.ws))


class TestPlain(Base):
    class Obj:
        def __init__(self, data):
            self._data = data
        def model_dump(self):
            return self._data

    def test_摊平_sdk_对象(self):
        # anthropic 的 messages 里是 SDK 对象，不摊平 json.dump 会炸
        out = session._plain({"role": "assistant",
                              "content": [self.Obj({"type": "text", "text": "你好"})]})
        self.assertEqual(out["content"][0], {"type": "text", "text": "你好"})
        json.dumps(out)

    def test_嵌套的也摊(self):
        out = session._plain([self.Obj({"a": self.Obj({"b": 1})})])
        self.assertEqual(out, [{"a": {"b": 1}}])

    def test_普通结构原样(self):
        self.assertEqual(session._plain({"a": [1, "x", None]}), {"a": [1, "x", None]})

    def test_存盘会先摊平(self):
        b = FakeBackend([])
        b.messages.append({"role": "assistant",
                           "content": [self.Obj({"type": "text", "text": "你好"})]})
        session.save(self.ws, Agentish(b))
        self.assertIn("你好", json.dumps(session.load(self.ws)["messages"], ensure_ascii=False))


class TestDescribe(Base):
    def test_有模型名和条数(self):
        text = session.describe({"model": "kimi", "messages": [1, 2, 3], "saved_at": 0})
        self.assertIn("kimi", text)
        self.assertIn("3 条消息", text)

    def test_字段缺了也不崩(self):
        self.assertIn("?", session.describe({}))


class TestKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._store, self._state = keys_mod.STORE, keys_mod.STATE
        keys_mod.STORE = os.path.join(self.tmp.name, "models.json")
        keys_mod.STATE = os.path.join(self.tmp.name, "state.json")
        keys_mod._DIR = self.tmp.name

    def tearDown(self):
        keys_mod.STORE, keys_mod.STATE = self._store, self._state
        self.tmp.cleanup()

    def test_加了能读回来(self):
        keys_mod.add("kimi", base_url="https://x", model="kimi-k2", key="sk-abc")
        entry = keys_mod.load()["kimi"]
        self.assertEqual(entry["model"], "kimi-k2")
        self.assertEqual(entry["key"], "sk-abc")

    def test_密钥文件是_600(self):
        keys_mod.add("kimi", base_url="https://x", model="kimi-k2", key="sk-abc")
        self.assertEqual(stat.S_IMODE(os.stat(keys_mod.STORE).st_mode), 0o600)

    def test_删掉(self):
        keys_mod.add("kimi", base_url="https://x", model="k", key="sk")
        self.assertTrue(keys_mod.remove("kimi"))
        self.assertFalse(keys_mod.remove("kimi"))
        self.assertEqual(keys_mod.load(), {})

    def test_上次用的会记下来(self):
        keys_mod.add("a", base_url="", model="m", key="k")
        keys_mod.add("b", base_url="", model="m", key="k")
        keys_mod.set_last("b")
        self.assertEqual(keys_mod.pick_default(), "b")

    def test_上次那条被删了就退回第一条(self):
        keys_mod.add("a", base_url="", model="m", key="k")
        keys_mod.add("b", base_url="", model="m", key="k")
        keys_mod.set_last("b")
        keys_mod.remove("b")
        self.assertEqual(keys_mod.pick_default(), "a")

    def test_清单为空时没有默认(self):
        self.assertEqual(keys_mod.pick_default(), "")

    def test_遮住密钥(self):
        masked = keys_mod.mask("sk-1234567890abcdef")
        self.assertNotIn("234567890abc", masked)

    def test_没有内置厂商或价格表(self):
        # 模型 id 退役很勤，写死的表迟早给出错的报价
        self.assertEqual(keys_mod.load(), {})


if __name__ == "__main__":
    unittest.main()
