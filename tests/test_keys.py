"""模型清单的存取 存储路径是 import 时算好的，用例里必须换掉 STORE / STATE """

import os
import tempfile
import unittest

from feiyu import keys
class TestHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        keys.STORE = os.path.join(self.tmp.name, "models.json")
        keys.STATE = os.path.join(self.tmp.name, "state.json")
        keys.save({"big": {"kind": "openai", "model": "b", "key": "k"},
                   "small": {"kind": "openai", "model": "s", "key": "k"}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_设了就能读回来_跟_last_互不覆盖(self):
        keys.set_last("big")
        keys.set_helper("small")
        self.assertEqual(keys.helper(), "small")
        self.assertEqual(keys.last(), "big")
        keys.set_last("small")
        self.assertEqual(keys.helper(), "small")

    def test_limit_字段能存能读(self):
        keys.add("w", base_url="", model="w", key="k", limit=32000)
        self.assertEqual(keys.load()["w"]["limit"], 32000)

    def test_学到的上限跟着配置走(self):
        keys.learn_limit("big", 60000)
        self.assertEqual(keys.learned_limit("big"), 60000)
        self.assertEqual(keys.learned_limit("small"), 0)
        keys.remove("big")
        self.assertEqual(keys.learned_limit("big"), 0)
        # 重新填过配置，旧的学习结果作废
        keys.learn_limit("small", 60000)
        keys.add("small", base_url="", model="s", key="k")
        self.assertEqual(keys.learned_limit("small"), 0)

    def test_指向的配置被删了就当没设(self):
        keys.set_helper("small")
        keys.remove("small")
        self.assertEqual(keys.helper(), "")


class TestTheme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        keys.STORE = os.path.join(self.tmp.name, "models.json")
        keys.STATE = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_记住主题(self):
        self.assertEqual(keys.theme(), "")
        keys.set_theme("swiss")
        self.assertEqual(keys.theme(), "swiss")


class TestEffort(unittest.TestCase):
    """推理强度 + 思不思考：存进模型配置，也能在会话里临时改

    **档位不许自己发明。** anthropic 的 `output_config.effort` 就是
    low / medium / high / xhigh / max 五档，别家客户端分七档是它自己的叫法，
    喂给接口会被 400 顶回来。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for attr in ("STORE", "STATE"):
            before = getattr(keys, attr)
            self.addCleanup(setattr, keys, attr, before)
            setattr(keys, attr, os.path.join(self.tmp.name, attr + ".json"))

    def test_五档_一档不多一档不少(self):
        from feiyu.providers import EFFORTS, DEFAULT_EFFORT
        self.assertEqual(EFFORTS, ("low", "medium", "high", "xhigh", "max"))
        self.assertIn(DEFAULT_EFFORT, EFFORTS)

    def test_存得进读得出(self):
        keys.add("甲", base_url="https://例子/v1", model="m", key="k",
                 effort="max", thinking=False)
        got = keys.load()["甲"]
        self.assertEqual(got["effort"], "max")
        self.assertIs(got["thinking"], False)

    def test_没填就是_None_不是_False(self):
        """None = 没表态（按默认走），False = 明确要求关掉 —— 两者不能混 """
        keys.add("甲", base_url="https://例子/v1", model="m", key="k")
        got = keys.load()["甲"]
        self.assertIsNone(got["effort"])
        self.assertIsNone(got["thinking"])

    def test_建后端时读进去(self):
        from feiyu.providers import build_backend
        b = build_backend({"kind": "openai", "model": "m", "key": "k",
                           "base_url": "https://例子/v1",
                           "effort": "low", "thinking": False},
                          system="", tools=[])
        self.assertEqual(b.effort, "low")
        self.assertIs(b.thinking_on, False)

    def test_没填时_openai_那边不发这个参数(self):
        """多数 openai 兼容模型压根不认 reasoning_effort，乱发会让本来能用的直接 400 """
        from feiyu.providers import build_backend
        b = build_backend({"kind": "openai", "model": "m", "key": "k",
                           "base_url": "https://例子/v1"}, system="", tools=[])
        self.assertEqual(b.effort, "")

    def test_绕过_init_的假后端也读得到(self):
        """测试里的假后端不走 __init__（那会去建真客户端）——
        所以这两个必须是类属性，否则 run_turn 里一读就 AttributeError """
        from feiyu.providers import AnthropicBackend, OpenAICompatBackend
        for cls in (AnthropicBackend, OpenAICompatBackend):
            with self.subTest(cls=cls.__name__):
                self.assertIsInstance(cls.effort, str)
                self.assertIsInstance(cls.thinking_on, bool)
