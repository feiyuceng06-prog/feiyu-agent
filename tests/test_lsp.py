"""symbol 工具与最小 LSP 客户端 真打服务器的用例只在装了 pyright 的机器上跑 """

import tempfile
import unittest
from unittest.mock import patch
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

    def test_引用结果只读显示范围内的文件且每份只读一次(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.py"
            result = root / "result.py"
            ignored = root / "ignored.py"
            source.write_text("needle\n", encoding="utf-8")
            result.write_text("found\n", encoding="utf-8")
            class Client:
                def open(self, *args): pass
                def locate(self, *args):
                    return ([{"path": result, "line": 0}] * lsp.MAX_RESULTS +
                            [{"path": ignored, "line": 0}])
            box = Toolbox(root)
            reads = []
            original = Path.read_text
            def read(path, *args, **kwargs):
                reads.append(path)
                return original(path, *args, **kwargs)
            with patch.object(lsp, "server_for", return_value=["fake"]), \
                 patch.object(box.lsp, "get", return_value=Client()), \
                 patch.object(Path, "read_text", read):
                out = box.run("symbol", {"action": "references", "path": "source.py", "name": "needle"})
            self.assertFalse(out.is_error, out.content)
            self.assertEqual(reads.count(result), 1)
            self.assertNotIn(ignored, reads)
            self.assertIn("还有更多", out.content)


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


class TestOutline(unittest.TestCase):
    """文件骨架：几百行以上的文件先定位再读

    一个 2526 行的文件整份读进来是九万多字符、每一轮还要重发；
    骨架只有四千多（**省 96%**）。系统提示里「大文件先定位再读」那条
    一直没有配套工具，这就是它。
    """

    def setUp(self):
        from feiyu import lsp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = self.root / "m.py"
        self.f.write_text(
            "import os\n\n\nCAP = 10\n\n\nclass Box:\n    def __init__(self, n):\n"
            "        self.n = n\n\n    def grow(self, by):\n        total = self.n + by\n"
            "        return total\n\n\ndef helper(x):\n    return x * 2\n",
            encoding="utf-8")
        if lsp.server_for(self.f) is None:
            raise unittest.SkipTest("没装 pyright")
        self.pool = lsp.Pool(self.root)
        self.addCleanup(self.pool.close)

    def rows(self):
        return self.pool.get(self.f).outline(self.f, self.f.read_text(encoding="utf-8"))

    def test_出得来类和函数_带行号(self):
        got = {r["name"]: r for r in self.rows()}
        self.assertEqual(got["Box"]["kind"], "class")
        self.assertEqual(got["Box"]["line"], 7)
        self.assertEqual(got["helper"]["kind"], "function")
        self.assertEqual(got["grow"]["kind"], "method")

    def test_参数和局部变量一律不要(self):
        """**pyright 把它们也报成 symbol** —— 一个几千行的文件里那是几百条噪音，
        正好淹掉要找的东西 """
        names = {r["name"] for r in self.rows()}
        for noise in ("n", "by", "x", "total", "self"):
            self.assertNotIn(noise, names, "参数/局部变量混进骨架了：%s" % noise)

    def test_模块层常量留着(self):
        """配置、上限这类是找代码时的路标 """
        names = {r["name"] for r in self.rows()}
        self.assertIn("CAP", names)

    def test_方法要比类缩进一级(self):
        got = {r["name"]: r for r in self.rows()}
        self.assertEqual(got["Box"]["depth"], 0)
        self.assertEqual(got["grow"]["depth"], got["Box"]["depth"] + 1)
        self.assertEqual(got["helper"]["depth"], 0)

    def test_按行号排序(self):
        lines = [r["line"] for r in self.rows()]
        self.assertEqual(lines, sorted(lines))

    def test_行号从_1_起(self):
        """LSP 的行号是 0 起始，给人和模型看的必须是 1 起始 —— 差一行就对不上 read_file """
        text = self.f.read_text(encoding="utf-8").splitlines()
        for r in self.rows():
            self.assertIn(r["name"], text[r["line"] - 1],
                          "%s 说在第 %d 行，那一行是 %r" % (r["name"], r["line"], text[r["line"]-1]))


class TestClose(unittest.TestCase):
    """关语言服务器要收尸、关管道 —— `--serve` 一跑一整天，它死一次重起一次，
    只 kill 不收的话每次漏一对 fd，还挂着一个僵尸 """

    def test_收了尸也关了管道(self):
        import subprocess
        client = object.__new__(lsp.Client)
        client.proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE)
        client.close()
        self.assertIsNotNone(client.proc.returncode)
        self.assertTrue(client.proc.stdin.closed)
        self.assertTrue(client.proc.stdout.closed)

    def test_死了的那个先关掉再重起(self):
        import types
        import unittest.mock
        closed = []

        class Dead:
            proc = types.SimpleNamespace(poll=lambda: 1)

            def close(self):
                closed.append(1)

        pool = lsp.Pool(Path("/tmp"))
        pool.clients["pyright-langserver"] = Dead()
        with unittest.mock.patch.object(lsp, "server_for",
                                        return_value=["pyright-langserver", "--stdio"]), \
             unittest.mock.patch.object(lsp, "Client", lambda argv, root: "新起的"):
            self.assertEqual(pool.get(Path("/tmp/x.py")), "新起的")
        self.assertEqual(closed, [1])
