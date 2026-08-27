"""ui 里能脱离终端断言的部分 画框那些要看真实渲染，见 CLAUDE.md 的验证方式一节

这里盯死一个坑：**量宽度前必须剥 ANSI**，get_cwidth 会把转义序列当可见字符
（box 和 top_border 各犯过一次）
"""

import contextlib
import io
import unittest

from feiyu import ui


class Colored(unittest.TestCase):
    """_COLOR 是按 isatty 定的，测试里不是终端就一律不上色，
    颜色相关的断言会全部变成空转 """

    def setUp(self):
        self._saved = ui._COLOR
        ui._COLOR = True

    def tearDown(self):
        ui._COLOR = self._saved


class TestWidth(Colored):
    def test_颜色码不占宽度(self):
        painted = ui.paint("brown", "abc")
        self.assertEqual(ui.visible(painted), 3)

    def test_粗体也不占(self):
        self.assertEqual(ui.visible(ui.bold("abc")), 3)

    def test_cjk_算两格(self):
        self.assertEqual(ui.visible("你好"), 4)

    def test_中英混排(self):
        self.assertEqual(ui.visible("ab你好"), 6)

    def test_剥了色码之后再量(self):
        # 直接 len() 的话这里会得到一个大得离谱的数
        painted = ui.paint("brown", "你好")
        self.assertGreater(len(painted), 2)          # 确认真的上了色
        self.assertEqual(ui.visible(painted), 4)
        self.assertLess(ui.visible(painted), len(painted))


class TestClip(Colored):
    def test_短的不动(self):
        self.assertEqual(ui.clip("abc", 10), "abc")

    def test_按显示宽度截而不是字符数(self):
        self.assertLessEqual(ui.visible(ui.clip("你好世界你好世界", 6)), 6)

    def test_截过的带省略号(self):
        self.assertTrue(ui.clip("abcdefghij", 5).endswith("…"))

    def test_极小的上限不崩(self):
        for limit in (0, 1, 2):
            ui.clip("你好世界", limit)


class TestTokens(unittest.TestCase):
    def test_小数字原样(self):
        self.assertEqual(ui.tokens(999), "999")

    def test_上千用_k(self):
        self.assertEqual(ui.tokens(1500), "1.5k")

    def test_整千不留小数点(self):
        self.assertEqual(ui.tokens(2000), "2k")


class TestLabels(unittest.TestCase):
    def test_工具名有中文标签(self):
        from feiyu.tools import TOOL_SCHEMAS
        for schema in TOOL_SCHEMAS:
            self.assertNotEqual(ui.tool_label(schema["name"]), schema["name"], schema["name"])

    def test_不认识的名字原样返回(self):
        self.assertEqual(ui.tool_label("没这个工具"), "没这个工具")

    def test_权限规则名覆盖了所有工具(self):
        from feiyu import permissions
        from feiyu.tools import TOOL_SCHEMAS
        for schema in TOOL_SCHEMAS:
            self.assertIn(schema["name"], permissions.RULE_NAME, schema["name"])


class TestStream(unittest.TestCase):
    def test_攒下原文供中断时补历史(self):
        s = ui.Stream()
        # 不是终端时 write 会直接吐到 stdout，测试里挡掉
        with contextlib.redirect_stdout(io.StringIO()):
            s.write("你好 ")
            s.write("world")
        self.assertEqual(s.text, "你好 world")
        self.assertTrue(s.wrote_text)

    def test_一个字都没写(self):
        s = ui.Stream()
        self.assertEqual(s.text, "")
        self.assertFalse(s.wrote_text)

    def test_空串不算写过(self):
        s = ui.Stream()
        with contextlib.redirect_stdout(io.StringIO()):
            s.write("")
        self.assertFalse(s.wrote_text)


class TestSlashCommands(unittest.TestCase):
    """0.5.0 用区间切片重写 _model 时把夹在中间的 _mode 一起删掉了，
    /mode 崩了才发现 每个命令都要有分支、有实现 """

    def setUp(self):
        import re
        from pathlib import Path
        self.src = Path(__file__).resolve().parent.parent.joinpath(
            "feiyu/__main__.py").read_text(encoding="utf-8")
        self.re = re

    def test_每个命令都有分支(self):
        handled = set(self.re.findall(r'cmd == "(/\w+)"', self.src))
        self.assertEqual(handled, set(ui.SLASH))

    def test_调用到的函数都定义了(self):
        called = set(self.re.findall(r"print\((_\w+)\(", self.src))
        defined = set(self.re.findall(r"^def (_\w+)\(", self.src, self.re.M))
        self.assertEqual(called - defined, set())

    def test_确认回调装在早退分支之前(self):
        # 装在后面的话 `feiyu "改一下 x"` 的每个写操作都是「用户拒绝」
        approve = self.src.index("agent.approve =")
        early = self.src.index("if args.prompt:")
        self.assertLess(approve, early)


if __name__ == "__main__":
    unittest.main()
