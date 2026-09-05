"""开关表：哪些技能 / 工具 / MCP 服务器是开着的

两条不变量：**默认全开**（文件里只记关掉的，所以新装的东西天生就在），
以及 **`exit_plan` 关不掉**（它是计划模式唯一的出口，关掉那一轮就转不出来）。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from feiyu import enabled


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        before = enabled.STORE
        enabled.STORE = os.path.join(self.tmp.name, "enabled.json")
        self.addCleanup(setattr, enabled, "STORE", before)

    def stored(self):
        try:
            with open(enabled.STORE, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return None


class TestDefaults(Base):
    def test_没有文件就是全开(self):
        self.assertTrue(enabled.on("skills", "随便一个"))
        self.assertTrue(enabled.on("tools", "bash"))
        self.assertEqual(enabled.disabled("tools"), set())

    def test_文件坏了也当全开(self):
        Path(enabled.STORE).write_text("{ 这不是 json", encoding="utf-8")
        self.assertTrue(enabled.on("tools", "bash"))

    def test_认不出的键当空(self):
        Path(enabled.STORE).write_text('{"tools": "不是列表"}', encoding="utf-8")
        self.assertEqual(enabled.disabled("tools"), set())


class TestToggle(Base):
    def test_关掉再打开(self):
        enabled.set_on("tools", "web_fetch", False)
        self.assertFalse(enabled.on("tools", "web_fetch"))
        self.assertTrue(enabled.on("tools", "bash"))     # 别的不受影响
        enabled.set_on("tools", "web_fetch", True)
        self.assertTrue(enabled.on("tools", "web_fetch"))

    def test_只存关掉的那些(self):
        enabled.set_on("skills", "甲", False)
        self.assertEqual(self.stored(), {"skills": ["甲"]})
        # 全开回来之后那条键整个去掉，不留一个空列表
        enabled.set_on("skills", "甲", True)
        self.assertEqual(self.stored(), {})

    def test_exit_plan_关不掉(self):
        enabled.set_on("tools", "exit_plan", False)
        self.assertTrue(enabled.on("tools", "exit_plan"))
        self.assertNotIn("exit_plan", enabled.disabled("tools"))

    def test_文件权限是_0600(self):
        enabled.set_on("tools", "bash", False)
        self.assertEqual(os.stat(enabled.STORE).st_mode & 0o777, 0o600)

    def test_认不出的类别不写盘(self):
        enabled.set_on("插件", "x", False)
        self.assertIsNone(self.stored())
