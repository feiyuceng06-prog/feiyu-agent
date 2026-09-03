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
