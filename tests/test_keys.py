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

    def test_指向的配置被删了就当没设(self):
        keys.set_helper("small")
        keys.remove("small")
        self.assertEqual(keys.helper(), "")
