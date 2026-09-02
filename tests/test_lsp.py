"""symbol 工具与最小 LSP 客户端 真打服务器的用例只在装了 pyright 的机器上跑 """

import tempfile
import unittest
from pathlib import Path

from feiyu import lsp
from feiyu.tools import Toolbox

有pyright = unittest.skipUnless(lsp.which("pyright-langserver"), "没装 pyright")


class TestPosition(unittest.TestCase):
    def test_整词匹配_不认前后缀(self):
        text = "helper_name = 1\nx = helper(2)\n"
        self.assertEqual(lsp.find_position(text, "helper", None), (1, 4))

    def test_给了行号只看那一行(self):
        text = "helper(1)\nhelper(2)\n"
        self.assertEqual(lsp.find_position(text, "helper", 2), (1, 0))
        self.assertIsNone(lsp.find_position(text, "helper", 3))

    def test_没装的语言返回_None(self):
        self.assertIsNone(lsp.server_for(Path("x.zig")))


class TestNoServer(unittest.TestCase):
    def test_没有服务器时明说并让它退回_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.zig").write_text("x")
            out = Toolbox(root).run("symbol", {"action": "definition", "path": "x.zig", "name": "x"})
        self.assertTrue(out.is_error)
        self.assertIn("search", out.content)


@有pyright
class TestPyright(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text(
            "def helper(x):\n    return x\n\nclass K:\n    def m(self):\n        return helper(1)\n")
        (self.root / "b.py").write_text("from a import helper\nprint(helper(2))\nhelper_name = 'helper'\n")
        self.box = Toolbox(self.root)

    def tearDown(self):
        self.box.lsp.close()
        self.tmp.cleanup()

    def test_跨文件找定义(self):
        out = self.box.run("symbol", {"action": "definition", "path": "b.py", "name": "helper", "line": 2})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("a.py:1:def helper(x):", out.content)

    def test_引用按语义_不把同名字符串和前缀算进来(self):
        out = self.box.run("symbol", {"action": "references", "path": "a.py", "name": "helper", "line": 1})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("a.py:6:", out.content)
        self.assertIn("b.py:2:", out.content)
        self.assertNotIn("helper_name", out.content)

    def test_文件改了之后再问_用的是新内容(self):
        self.box.run("symbol", {"action": "references", "path": "a.py", "name": "helper", "line": 1})
        (self.root / "a.py").write_text("def helper(x):\n    return x\n")
        out = self.box.run("symbol", {"action": "references", "path": "a.py", "name": "helper", "line": 1})
        self.assertNotIn("a.py:6:", out.content)

    def test_服务器进程退出时被杀掉(self):
        self.box.run("symbol", {"action": "definition", "path": "a.py", "name": "helper"})
        proc = next(iter(self.box.lsp.clients.values())).proc
        self.box.lsp.close()
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())
