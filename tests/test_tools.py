"""七个工具 网络那个只测拦截逻辑，不真的出网 """

import json
import os
import pathlib
import subprocess
import sys
import time
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from feiyu import enabled, tools
from feiyu.tools import Toolbox


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        before = enabled.STORE
        enabled.STORE = str(self.root / "enabled.json")
        self.addCleanup(setattr, enabled, "STORE", before)
        self.box = Toolbox(self.root)
        self.addCleanup(self.box.shell_reset)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, seen=True):
        """seen=False 模拟「文件在那儿但 agent 没读过」，用来测新鲜度那道闸 """
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if seen:
            self.box.files.record(p)
        else:
            self.box.files.forget(p)
        return p


class TestResolve(Base):
    def test_工作区内的路径可以(self):
        self.assertEqual(self.box._resolve("a/b.py"), self.root / "a" / "b.py")

    def test_根目录本身可以(self):
        self.assertEqual(self.box._resolve("."), self.root)

    def test_点点越界被挡(self):
        with self.assertRaises(ValueError):
            self.box._resolve("../secrets.txt")

    def test_绝对路径越界被挡(self):
        with self.assertRaises(ValueError):
            self.box._resolve("/etc/passwd")

    def test_软链越界被挡(self):
        os.symlink("/etc", self.root / "escape")
        with self.assertRaises(ValueError):
            self.box._resolve("escape/passwd")

    def test_绕一圈回到工作区内可以(self):
        self.write("a/b.py", "x")
        self.assertEqual(self.box._resolve("a/../a/b.py"), self.root / "a" / "b.py")


class TestReadFile(Base):
    def test_带行号(self):
        self.write("a.py", "import os\nprint(1)\n")
        out = self.box.run("read_file", {"path": "a.py"})
        self.assertFalse(out.is_error)
        self.assertIn("1\timport os", out.content)
        self.assertIn("2\tprint(1)", out.content)

    def test_偏移和条数(self):
        self.write("a.py", "\n".join("line%d" % i for i in range(1, 11)))
        out = self.box.run("read_file", {"path": "a.py", "offset": 3, "limit": 2})
        self.assertIn("3\tline3", out.content)
        self.assertIn("4\tline4", out.content)
        self.assertNotIn("line5", out.content)

    def test_空文件(self):
        self.write("a.py", "")
        self.assertIn("空文件", self.box.run("read_file", {"path": "a.py"}).content)

    def test_二进制明确报错不返回乱码(self):
        (self.root / "a.bin").write_bytes(b"\x00\x01\x02\xff" * 100)
        out = self.box.run("read_file", {"path": "a.bin"})
        self.assertTrue(out.is_error)
        self.assertIn("二进制", out.content)

    def test_越界读被挡(self):
        out = self.box.run("read_file", {"path": "../../etc/passwd"})
        self.assertTrue(out.is_error)
        self.assertIn("超出", out.content)

    def test_不存在的文件报错但不崩(self):
        self.assertTrue(self.box.run("read_file", {"path": "没有.py"}).is_error)


class TestWriteEdit(Base):
    def test_新建带上父目录(self):
        out = self.box.run("write_file", {"path": "a/b/c.py", "content": "x = 1\n"})
        self.assertFalse(out.is_error)
        self.assertEqual((self.root / "a/b/c.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertIn("新建", out.summary)

    def test_覆盖已有文件(self):
        self.write("a.py", "旧的")
        self.assertIn("覆盖", self.box.run(
            "write_file", {"path": "a.py", "content": "新的"}).summary)

    def test_整写提示挂在结果里_开关开时(self):
        """走真正的 write_file 那条路，不是只测那个纯函数 """
        import os
        os.environ["FEIYU_REWRITE_HINT"] = "on"
        self.addCleanup(lambda: os.environ.pop("FEIYU_REWRITE_HINT", None))
        旧 = "\n".join("第 %d 行" % i for i in range(200))
        self.write("big.txt", 旧)
        out = self.box.run("write_file", {"path": "big.txt", "content": 旧 + "\n补一行"})
        self.assertIn("edit_file", out.content)
        self.assertFalse(out.is_error)      # 提示不是错，标了它会去"修"没坏的东西
        self.assertIn("覆盖", out.summary)   # 摘要照旧，提示只进 content

    def test_新建的文件不提示(self):
        import os
        os.environ["FEIYU_REWRITE_HINT"] = "on"
        self.addCleanup(lambda: os.environ.pop("FEIYU_REWRITE_HINT", None))
        长的 = "\n".join("第 %d 行" % i for i in range(200))
        self.assertNotIn("edit_file", self.box.run(
            "write_file", {"path": "新的.txt", "content": 长的}).content)

    def test_开关关着时一个字都不加(self):
        import os
        os.environ.pop("FEIYU_REWRITE_HINT", None)
        旧 = "\n".join("第 %d 行" % i for i in range(200))
        self.write("big.txt", 旧)
        self.assertNotIn("edit_file", self.box.run(
            "write_file", {"path": "big.txt", "content": 旧 + "\n补一行"}).content)

    def test_编辑要求唯一匹配(self):
        self.write("a.py", "x = 1\nx = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertTrue(out.is_error)
        self.assertIn("2 处", out.content)  # 匹配多处要报数
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 1\nx = 1\n")

    def test_找不到就报错(self):
        self.write("a.py", "x = 1\n")
        self.assertTrue(self.box.run(
            "edit_file", {"path": "a.py", "old_str": "y = 2", "new_str": "z"}).is_error)

    def test_正常编辑带_diff(self):
        self.write("a.py", "x = 1\ny = 2\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 9"})
        self.assertFalse(out.is_error)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 9\ny = 2\n")
        kinds = {kind for kind, _ in out.detail}
        self.assertIn("-", kinds)
        self.assertIn("+", kinds)

    def test_越界写被挡(self):
        out = self.box.run("write_file", {"path": "../evil.py", "content": "x"})
        self.assertTrue(out.is_error)
        self.assertFalse((self.root.parent / "evil.py").exists())

    def test_replace_all_全换(self):
        self.write("a.py", "x = 1\nx = 1\nx = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1",
                                         "new_str": "x = 2", "replace_all": True})
        self.assertFalse(out.is_error)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 2\nx = 2\nx = 2\n")
        self.assertIn("3 处", out.summary)

    def test_匹配多处时提示_replace_all(self):
        self.write("a.py", "x = 1\nx = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertIn("replace_all", out.content)

    def test_新旧一样直接拒(self):
        self.write("a.py", "x = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 1"})
        self.assertTrue(out.is_error)

    def test_空_old_str_直接拒(self):
        self.write("a.py", "x = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "", "new_str": "y"})
        self.assertTrue(out.is_error)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_编辑不存在的文件给出明确指引(self):
        out = self.box.run("edit_file", {"path": "没有.py", "old_str": "a", "new_str": "b"})
        self.assertTrue(out.is_error)
        self.assertIn("write_file", out.content)


class TestFreshness(Base):
    """写之前对版本：没读过、读过之后被改过，都要拦下 """

    def test_没读过就覆盖被拦(self):
        self.write("a.py", "重要内容", seen=False)
        out = self.box.run("write_file", {"path": "a.py", "content": "新的"})
        self.assertTrue(out.is_error)
        self.assertIn("read_file", out.content)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "重要内容")

    def test_没读过就编辑被拦(self):
        self.write("a.py", "x = 1\n", seen=False)
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertTrue(out.is_error)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_读过之后就能写(self):
        self.write("a.py", "x = 1\n", seen=False)
        self.box.run("read_file", {"path": "a.py"})
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertFalse(out.is_error)

    def test_读过之后被外部改动就拦下(self):
        p = self.write("a.py", "x = 1\ny = 2\n", seen=False)
        self.box.run("read_file", {"path": "a.py"})
        p.write_text("x = 1\ny = 2\n用户自己加的一行\n", encoding="utf-8")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 9"})
        self.assertTrue(out.is_error)
        self.assertIn("改动", out.content)
        self.assertIn("用户自己加的一行", p.read_text(encoding="utf-8"))

    def test_覆盖同样要挡外部改动(self):
        p = self.write("a.py", "旧的", seen=False)
        self.box.run("read_file", {"path": "a.py"})
        p.write_text("用户改过的", encoding="utf-8")
        out = self.box.run("write_file", {"path": "a.py", "content": "新的"})
        self.assertTrue(out.is_error)
        self.assertEqual(p.read_text(encoding="utf-8"), "用户改过的")

    def test_重新读一遍就解除(self):
        p = self.write("a.py", "x = 1\n", seen=False)
        self.box.run("read_file", {"path": "a.py"})
        p.write_text("x = 1\nz = 3\n", encoding="utf-8")
        self.assertTrue(self.box.run(
            "edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 9"}).is_error)
        self.box.run("read_file", {"path": "a.py"})
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 9"})
        self.assertFalse(out.is_error)
        self.assertEqual(p.read_text(encoding="utf-8"), "x = 9\nz = 3\n")

    def test_自己写完还能接着编辑(self):
        self.box.run("write_file", {"path": "a.py", "content": "x = 1\n"})
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertFalse(out.is_error)

    def test_新建文件不受影响(self):
        out = self.box.run("write_file", {"path": "全新.py", "content": "x = 1\n"})
        self.assertFalse(out.is_error)

    def test_内容没变的_touch_不算改动(self):
        p = self.write("a.py", "x = 1\n", seen=False)
        self.box.run("read_file", {"path": "a.py"})
        os.utime(p, (0, 0))
        p.write_text("x = 1\n", encoding="utf-8")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertFalse(out.is_error)

    def test_读不了的二进制不要求先读(self):
        p = self.root / "a.bin"
        p.write_bytes(b"\x00\x01\x02\x00")
        out = self.box.run("write_file", {"path": "a.bin", "content": "文本"})
        self.assertFalse(out.is_error)


class TestListCap(Base):
    """没有上限的话，在 ~ 里跑一次是三十多万个文件、好几秒 """

    def test_到上限就停(self):
        self.addCleanup(setattr, tools, "LIST_CAP", tools.LIST_CAP)
        tools.LIST_CAP = 10
        for i in range(40):
            self.write("f%02d.txt" % i, "x")
        out = self.box.run("list_files", {"pattern": "**/*"})
        self.assertEqual(len(out.content.splitlines()[0:10]), 10)
        self.assertIn("已截断", out.summary)
        self.assertIn("只列了前 10 个", out.content)

    def test_没到上限就不标截断(self):
        self.write("a.py", "x")
        out = self.box.run("list_files", {"pattern": "**/*"})
        self.assertNotIn("截断", out.summary)
        self.assertNotIn("只列了前", out.content)

    def test_忽略的目录压根不进去走(self):
        """剪枝要发生在目录一级 交给 glob 吐完再一个个筛掉的话，
        本仓库实测是 8455 次 stat、178 毫秒，而真正要列的只有 54 个文件 """
        for i in range(30):
            self.write(".venv/lib/pkg%d/mod.py" % i, "x")
        self.write("a.py", "x")
        visited = []
        real_walk = tools.os.walk
        def spy(top, *a, **kw):
            for row in real_walk(top, *a, **kw):
                visited.append(row[0])
                yield row
        self.addCleanup(setattr, tools.os, "walk", real_walk)
        tools.os.walk = spy
        out = self.box.run("list_files", {"pattern": "**/*"})
        self.assertEqual(out.content.splitlines(), ["a.py"])
        self.assertFalse([d for d in visited if ".venv" in d], "进了 .venv")

    def test_上限改小也照样停(self):
        """**名字不能跟上面那条重**：同名的话后定义的会把先定义的整个盖掉，
        那条里"截断必须标出来"的断言就一次都没跑过（ruff F811 抓出来的）"""
        self.addCleanup(setattr, tools, "LIST_CAP", tools.LIST_CAP)
        tools.LIST_CAP = 5
        for i in range(60):
            self.write("g%02d.txt" % i, "x")
        out = self.box.run("list_files", {"pattern": "**/*"})
        self.assertEqual(len([l for l in out.content.splitlines() if l.endswith(".txt")]), 5)

    def test_模式照旧管用(self):
        self.write("a.py", "x")
        self.write("b.md", "x")
        self.write("sub/c.py", "x")
        both = self.box.run("list_files", {"pattern": "**/*.py"}).content.splitlines()
        self.assertEqual(sorted(both), ["a.py", "sub/c.py"])
        top = self.box.run("list_files", {"pattern": "*.py"}).content.splitlines()
        self.assertEqual(top, ["a.py"])

    def test_无匹配(self):
        self.assertIn("无匹配", self.box.run("list_files", {"pattern": "*.没有"}).summary)

    def test_pattern_匹配不靠_3_13_才有的_API(self):
        """**`PurePath.full_match` 是 3.13 才有的**，而 pyproject / install.sh /
        README 三处都说支持 3.10 起 —— 用它的那半年里，3.13 以下的 list_files
        整个是坏的（一句 `'PosixPath' object has no attribute 'full_match'`），
        而本机跑测试看不出来：本机是 3.14。

        所以这条直接断言**不碰那个方法**。语义另有下面那条按表逐条比。
        """
        self.write("a.py", "x")
        called = []
        # 3.13 以下压根没有这个属性，所以取不到时装一个照样会被记下来的替身 ——
        # 这条用例本身要能在**所有**支持的版本上跑
        real = getattr(pathlib.PurePath, "full_match", None)

        def spy(self, *a, **kw):
            called.append(1)
            return real(self, *a, **kw) if real else False

        if real is None:
            self.addCleanup(delattr, pathlib.PurePath, "full_match")
        else:
            self.addCleanup(setattr, pathlib.PurePath, "full_match", real)
        pathlib.PurePath.full_match = spy
        self.box.run("list_files", {"pattern": "**/*.py"})
        self.assertEqual(called, [], "又用上 full_match 了，3.13 以下会整个坏掉")

    def test_pattern_的语义(self):
        """`**` 跨任意层且可以是零层，`*` / `?` 不跨 `/`，整条路径要全匹配

        这张表是拿 3.14 的 `full_match` 逐条对出来的 —— 换实现时照它比，
        别凭印象改（"`**/*.py` 要不要匹配根目录下的 a.py" 这种问题，
        答错一次就是「搜不到自己仓库里的文件」）。
        """
        for name in ("a.py", "abc.py", "b.md", "a.c", "x.h",
                     "sub/c.py", "src/b.py", "src/deep/x.py"):
            self.write(name, "x")
        table = {
            "**/*.py": ["a.py", "abc.py", "src/b.py", "src/deep/x.py", "sub/c.py"],
            "*.py": ["a.py", "abc.py"],
            "src/*.py": ["src/b.py"],
            "src/**/*.py": ["src/b.py", "src/deep/x.py"],
            "src/**": ["src/b.py", "src/deep/x.py"],
            "a?c.py": ["abc.py"],
            "*.[ch]": ["a.c", "x.h"],
            "**/c.py": ["sub/c.py"],
        }
        for pattern, want in table.items():
            with self.subTest(pattern=pattern):
                got = self.box.run("list_files", {"pattern": pattern}).content.splitlines()
                self.assertEqual(sorted(got), want)


class TestListSearch(Base):
    def setUp(self):
        super().setUp()
        self.write("a.py", "import os\n")
        self.write("src/b.py", "import sys\nimport os\n")
        self.write("node_modules/junk.py", "import os\n")
        self.write(".git/config", "import os\n")

    def test_列文件跳过噪音目录(self):
        out = self.box.run("list_files", {})
        self.assertIn("a.py", out.content)
        self.assertIn("src/b.py", out.content)
        self.assertNotIn("node_modules", out.content)
        self.assertNotIn(".git", out.content)

    def test_按模式过滤(self):
        self.assertNotIn("a.py\n", self.box.run("list_files", {"pattern": "src/*.py"}).content)

    def test_搜索带文件名和行号(self):
        out = self.box.run("search", {"regex": r"import sys"})
        self.assertIn("src/b.py:1:", out.content)

    def test_搜索跳过噪音目录(self):
        out = self.box.run("search", {"regex": r"import os"})
        self.assertNotIn("node_modules", out.content)

    def test_无匹配不报错(self):
        out = self.box.run("search", {"regex": "绝对找不到的东西"})
        self.assertFalse(out.is_error)
        self.assertIn("无匹配", out.content)

    def test_正则写坏了报错但不崩(self):
        self.assertTrue(self.box.run("search", {"regex": "["}).is_error)


class TestBash(Base):
    def test_跑命令拿输出和退出码(self):
        out = self.box.run("bash", {"command": "echo 你好"})
        self.assertFalse(out.is_error)
        self.assertIn("你好", out.content)
        self.assertIn("退出码: 0", out.content)

    def test_失败的退出码算错误(self):
        out = self.box.run("bash", {"command": "exit 3"})
        self.assertTrue(out.is_error)
        self.assertIn("退出码: 3", out.content)

    def test_cd_跨调用保持(self):
        (self.root / "sub").mkdir()
        self.box.run("bash", {"command": "cd sub"})
        self.assertEqual(self.box.cwd, self.root / "sub")
        self.assertIn("sub", self.box.run("bash", {"command": "pwd"}).content)

    def test_cd_出工作区会被弹回(self):
        self.box.run("bash", {"command": "cd /tmp"})
        self.assertEqual(self.box.cwd, self.root)

    def test_超时报错不挂死(self):
        out = self.box.run("bash", {"command": "sleep 5", "timeout": 1})
        self.assertTrue(out.is_error)

    def test_密钥类环境变量不传给子进程(self):
        os.environ["FEIYU_TEST_API_KEY"] = "机密"
        os.environ["FEIYU_TEST_PLAIN"] = "普通"
        try:
            out = self.box.run("bash", {"command": "echo $FEIYU_TEST_API_KEY-$FEIYU_TEST_PLAIN"})
            self.assertNotIn("机密", out.content)
            self.assertIn("普通", out.content)
        finally:
            del os.environ["FEIYU_TEST_API_KEY"], os.environ["FEIYU_TEST_PLAIN"]

    def test_ssh_agent_要留着(self):
        # 摘掉的话 git push 会断
        self.assertIn("SSH_AUTH_SOCK", tools._SECRET_KEEP)


class TestShellState(Base):
    """export / source / 函数要跨调用保持

    之前每条 bash 是个新进程，只有 cwd 靠「把 pwd 写进文件」跨了过去。
    `_venv_hint` 那个提示就是为这事加的 —— 它的 docstring 记着实测结果：
    裸 python 报 ImportError 之后**花了五轮翻环境**。提示是绕，这里是修。
    """

    def test_export_跨调用保持(self):
        self.box.run("bash", {"command": "export FOO=bar"})
        self.assertIn("bar", self.box.run("bash", {"command": "echo $FOO"}).content)

    def test_函数跨调用保持(self):
        self.box.run("bash", {"command": "greet() { echo hi-$1; }"})
        self.assertIn("hi-world", self.box.run("bash", {"command": "greet world"}).content)

    def test_source_进来的环境跨调用保持(self):
        """venv 的 activate 干的就是改 PATH + 设 VIRTUAL_ENV，这里拿个假的量同一件事 """
        (self.root / "act").write_text(
            'export VIRTUAL_ENV=/假/venv\nexport PATH="/假/venv/bin:$PATH"\n',
            encoding="utf-8")
        self.box.run("bash", {"command": "source act"})
        out = self.box.run("bash", {"command": "echo $VIRTUAL_ENV; echo $PATH"})
        self.assertIn("/假/venv", out.content)
        self.assertIn("/假/venv/bin:", out.content)

    def test_值里有换行也存得回来(self):
        """**这条守的是实现选择。** dump 时用 `grep -v` 过滤那几个变量会把多行值
        按行切开，剩下的引号对不上，整份 dump 就 source 不回来 —— 不是这一个变量
        丢了，是**全部状态一起丢**。所以那几个要在子 shell 里 unset 掉再 export -p。
        """
        self.box.run("bash", {"command": "export MULTI=$'a\nb'"})
        out = self.box.run("bash", {"command": 'printf "[%s]" "$MULTI"; echo; echo 还活着=$?'})
        self.assertIn("[a\nb]", out.content)

    def test_cd_和_export_互不干扰(self):
        """dump 里要是把 PWD 带上，下一条命令的 $PWD 就会指到旧地方 ——
        而 `pwd` 内建读的正是 $PWD，cwd 跟踪会跟着一起错
        """
        (self.root / "sub").mkdir()
        self.box.run("bash", {"command": "export FOO=bar && cd sub"})
        out = self.box.run("bash", {"command": 'echo "$FOO@$(basename "$PWD")"'})
        self.assertIn("bar@sub", out.content)
        self.assertEqual(self.box.cwd, self.root / "sub")

    def test_dump_里没有_PWD(self):
        """**两种格式都要查。** `export -p` 在 bash 下印 `declare -x FOO=`，
        在 POSIX 模式（以 sh 的名字起的 bash）下印 `export FOO=` —— 只查一种的话
        换个 shell 这条就变成假绿的，我第一版正是这么错的。
        """
        self.box.run("bash", {"command": "true"})
        text = Path(self.box.shell_state).read_text(encoding="utf-8")
        for name in tools._STATE_SKIP:
            self.assertNotIn("declare -x %s=" % name, text)
            self.assertNotIn("\nexport %s=" % name, "\n" + text)

    def test_跑的是_bash_不是_POSIX_模式的_sh(self):
        """工具叫 bash、说明里写的是 shell 命令，那就得真是 bash：
        POSIX 模式下 `source 相对路径` 不搜当前目录，venv 的 activate 会 file not found
        """
        (self.root / "act").write_text("export FROM_SOURCE=yes\n", encoding="utf-8")
        out = self.box.run("bash", {"command": "source act && echo $FROM_SOURCE"})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("yes", out.content)

    def test_reset_把状态清空(self):
        self.box.run("bash", {"command": "export FOO=bar"})
        out = self.box.run("bash", {"command": 'echo "[$FOO]"', "reset": True})
        self.assertIn("[]", out.content)

    def test_状态文件是_0600(self):
        """里头是整份导出的环境，任务自己 export 的东西也在里面 """
        self.box.run("bash", {"command": "true"})
        mode = os.stat(self.box.shell_state).st_mode & 0o777
        self.assertEqual(mode, 0o600, oct(mode))

    def test_没跑过_bash_就不建文件(self):
        self.assertIsNone(self.box.shell_state)

    def test_状态文件被删了也不崩(self):
        self.box.run("bash", {"command": "export FOO=bar"})
        os.unlink(self.box.shell_state)
        out = self.box.run("bash", {"command": "echo 还能跑"})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("还能跑", out.content)

    def test_命令自己_exit_也要记下状态(self):
        """**`cd /x; exit 0` 之前是静默丢的。** 收尾那几行写在命令后面，命令一 exit
        就执行不到 —— cwd 和 export 全不记，而模型以为自己已经进去了。
        现在挂在 EXIT trap 上。
        """
        (self.root / "sub").mkdir()
        out = self.box.run("bash", {"command": "export A=1; cd sub; exit 7"})
        self.assertIn("退出码: 7", out.content)       # 退出码不能被 trap 吃掉
        self.assertEqual(self.box.cwd, self.root / "sub")
        self.assertIn("A=[1]", self.box.run("bash", {"command": 'echo A=[$A]'}).content)

    def test_set_e_中途失败也要记下状态(self):
        """同一个根因的另一半：`set -e` 让 shell 在命令中间就中断 """
        (self.root / "sub").mkdir()
        out = self.box.run("bash", {"command": "set -e; export B=2; cd sub; false; echo 不该到这"})
        self.assertTrue(out.is_error)
        self.assertNotIn("不该到这", out.content)
        self.assertEqual(self.box.cwd, self.root / "sub")
        self.assertIn("B=[2]", self.box.run("bash", {"command": 'echo B=[$B]'}).content)

    def test_存状态的那个函数不许漏进状态里(self):
        """它是实现细节，漏进去会被一轮轮 source 回来，还带着已经删掉的临时文件路径 """
        self.box.run("bash", {"command": "true"})
        self.assertNotIn("__feiyu_save",
                         Path(self.box.shell_state).read_text(encoding="utf-8"))

    def test_半份状态宁可整份不要(self):
        """`> 文件` 是先截断再写，写一半被 SIGKILL（超时那条路）就留下半份。
        半份照样非空，`source` 的语法错又被 `2>/dev/null` 吞掉 —— 结果是一部分
        变量回来了、函数静默没了。**而且会传染**：下一份 dump 从这个缺了的环境里取，
        缺口就永久焊死。所以宁可整份丢掉。
        """
        self.box.run("bash", {"command": "export ZZ=在; f() { echo 函数在; }"})
        p = Path(self.box.shell_state)
        lines = p.read_text(encoding="utf-8").splitlines(True)
        p.write_text("".join(lines[:-3]) + lines[-3][:len(lines[-3]) // 2],
                     encoding="utf-8")
        out = self.box.run("bash", {"command": 'echo "ZZ=[$ZZ]"'})
        self.assertIn("ZZ=[]", out.content)

    def test_哨兵是最后一行(self):
        """判"完整"靠的就是它在不在最后一行 —— 有函数时 `declare -f` 排在后面，
        哨兵必须在那之后追加，不然这道闸永远关着
        """
        self.box.run("bash", {"command": "g() { echo x; }"})
        text = Path(self.box.shell_state).read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[-1], tools._STATE_END)

    def test_reset_和_background_一起给时_reset_不能被吃掉(self):
        self.box.run("bash", {"command": "export A=1"})
        self.box.run("bash", {"command": 'echo "A=[$A]" > out.txt',
                              "background": True, "reset": True})
        seen = self.root / "out.txt"
        for _ in range(100):
            if seen.is_file() and seen.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)
        self.assertEqual(seen.read_text(encoding="utf-8").strip(), "A=[]")

    def test_后台任务继承状态但不写回(self):
        """前面刚 activate 了 venv，挂后台跑的 pytest 也该在那个 venv 里；
        但后台跟后面的命令是并行的，它 export 的东西不该漏给它们
        """
        self.box.run("bash", {"command": "export FOO=bar"})
        self.box.run("bash", {
            "command": 'echo "$FOO" > seen.txt; export LEAKED=yes',
            "background": True})
        seen = self.root / "seen.txt"
        for _ in range(100):
            if seen.is_file() and seen.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)
        self.assertEqual(seen.read_text(encoding="utf-8").strip(), "bar")
        out = self.box.run("bash", {"command": 'echo "[$LEAKED]"'})
        self.assertIn("[]", out.content)


class TestWebGuards(Base):
    def test_内网地址被挡(self):
        for host in ("localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1"):
            self.assertTrue(tools._blocked_host(host), host)

    def test_公网地址放行(self):
        self.assertEqual(tools._blocked_host("example.com"), "")

    def test_有一条公网记录就放行(self):
        """DNS 被污染时公网域名会拿到一条内网记录 日志里 www.google.com 被拦过两次：
        A 记录正常，AAAA 记录是 2001::1（Teredo 前缀，Python 算 is_private）
        """
        mixed = [(2, 1, 6, "", ("104.244.42.197", 0)), (10, 1, 6, "", ("2001::1", 0, 0, 0))]
        with unittest.mock.patch.object(tools.socket, "getaddrinfo", return_value=mixed):
            self.assertEqual(tools._blocked_host("www.example.com"), "")

    def test_全是内网记录才拦(self):
        allpriv = [(2, 1, 6, "", ("127.0.0.1", 0)), (10, 1, 6, "", ("::1", 0, 0, 0))]
        with unittest.mock.patch.object(tools.socket, "getaddrinfo", return_value=allpriv):
            self.assertTrue(tools._blocked_host("internal.example"))

    def test_解析不出地址不拦(self):
        with unittest.mock.patch.object(tools.socket, "getaddrinfo", return_value=[]):
            self.assertEqual(tools._blocked_host("nothing.example"), "")

    def test_逃生门(self):
        os.environ["FEIYU_ALLOW_LOCAL_FETCH"] = "1"
        try:
            self.assertEqual(tools._blocked_host("127.0.0.1"), "")
        finally:
            del os.environ["FEIYU_ALLOW_LOCAL_FETCH"]

    def test_只认_http_和_https(self):
        for url in ("file:///etc/passwd", "ftp://x.com", "gopher://x"):
            out = self.box.run("web_fetch", {"url": url})
            self.assertTrue(out.is_error, url)

    def test_内网地址给出的是拒绝而不是抓取失败(self):
        out = self.box.run("web_fetch", {"url": "http://127.0.0.1:1/x"})
        self.assertTrue(out.is_error)
        self.assertIn("内网", out.summary)


class TestFetchNote(Base):
    def test_反爬和要登录给的是人话(self):
        for code, word in ((401, "登录"), (403, "爬虫"), (429, "限流"), (404, "不存在")):
            note = tools._fetch_note("HTTP %d" % code)
            self.assertIn("web_search", note, code)
            self.assertIn(word, note, code)

    def test_认不出的状态码一个字不加(self):
        # 跟 _sandbox_note 一个规矩：加错了比不加更误导
        self.assertEqual(tools._fetch_note("HTTP 500"), "")
        self.assertEqual(tools._fetch_note("抓取失败"), "")
        self.assertEqual(tools._fetch_note("内网地址已拦"), "")


class TestSearchUrlHint(Base):
    def test_认得出手搓的搜索引擎地址(self):
        for url in ("https://www.google.com/search?q=python+asyncio",
                    "https://www.bing.com/search?q=x",
                    "https://www.baidu.com/s?wd=x",
                    "https://duckduckgo.com/?q=x"):
            self.assertTrue(tools._is_search_url(url), url)

    def test_引擎首页和普通站点不算(self):
        for url in ("https://www.google.com/", "https://example.com/search?id=3",
                    "https://news.ycombinator.com/"):
            self.assertFalse(tools._is_search_url(url), url)

    def test_抓搜索结果页会提示改用_web_search_且只提示一次(self):
        # 真出网会慢也不稳，让 _fetch_page 直接返回失败：提示挂在失败那条路上
        with unittest.mock.patch.object(
                Toolbox, "_fetch_page", return_value=(None, "HTTP 403 Forbidden", "HTTP 403")):
            first = self.box.run("web_fetch", {"url": "https://www.google.com/search?q=x"})
            self.assertIn("web_search", first.content)
            self.assertIn("反爬", first.content)          # 403 的说明也在
            second = self.box.run("web_fetch", {"url": "https://www.bing.com/search?q=y"})
            self.assertNotIn("别拿 web_fetch 抓引擎的结果页", second.content)


class TestSymbolHint(Base):
    def _search(self, regex, **extra):
        return self.box.run("search", {"regex": regex, **extra})

    def test_搜裸标识符会提示_symbol(self):
        self.write("a.py", "def handle_timeout():\n    pass\n")
        with unittest.mock.patch.object(tools.lsp, "server_for", return_value=["pyright"]):
            out = self._search("handle_timeout")
        self.assertIn("symbol", out.content)
        self.assertIn("handle_timeout", out.content)

    def test_只提示一次(self):
        self.write("a.py", "def foo_bar():\n    pass\n")
        with unittest.mock.patch.object(tools.lsp, "server_for", return_value=["pyright"]):
            self._search("foo_bar")
            out = self._search("foo_bar")
        self.assertNotIn("symbol 工具", out.content)

    def test_带正则记号的不提示(self):
        """那是在搜文本，不是在找符号 """
        self.write("a.py", "def foo():\n    pass\n")
        with unittest.mock.patch.object(tools.lsp, "server_for", return_value=["pyright"]):
            for regex in (r"def foo", r"foo\(", r"^foo", "fo"):
                out = self._search(regex)
                if not out.is_error and "无匹配" not in out.summary:
                    self.assertNotIn("symbol 工具", out.content, regex)

    def test_没有语言服务器就不提示(self):
        self.write("a.txt", "handle_timeout\n")
        with unittest.mock.patch.object(tools.lsp, "server_for", return_value=None):
            out = self._search("handle_timeout")
        self.assertNotIn("symbol 工具", out.content)

    def test_从命中行里取路径只认匹配行(self):
        # 带 context 时上下文行用 `-` 隔开，路径里本来也可能有 `-`
        hits = ["my-file.py-11-    x = 1", "my-file.py:12:def foo():"]
        self.assertEqual(tools._first_hit_path(hits, False), "my-file.py")
        self.assertEqual(tools._first_hit_path(["a/b.py"], True), "a/b.py")
        self.assertIsNone(tools._first_hit_path(["--"], False))


class TestDocuments(Base):
    def _odf(self, name, body):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("content.xml", body)
        return path

    def test_ods_一行一行不是一格一行(self):
        # 套用「每个 p 一行」会把每个单元格拆成一行，表格结构全散
        body = ('<?xml version="1.0"?><office:document-content '
                'xmlns:office="urn:o" xmlns:table="urn:t" xmlns:text="urn:x">'
                '<table:table><table:table-row>'
                '<table:table-cell><text:p>姓名</text:p></table:table-cell>'
                '<table:table-cell><text:p>年龄</text:p></table:table-cell>'
                '</table:table-row><table:table-row>'
                '<table:table-cell><text:p>小明</text:p></table:table-cell>'
                '<table:table-cell><text:p>18</text:p></table:table-cell>'
                '</table:table-row></table:table></office:document-content>')
        text = tools.read_document(self._odf("a.ods", body))
        self.assertIn("姓名\t年龄", text)
        self.assertIn("小明\t18", text)

    def test_ods_处理重复列(self):
        body = ('<?xml version="1.0"?><office:document-content '
                'xmlns:office="urn:o" xmlns:table="urn:t" xmlns:text="urn:x">'
                '<table:table><table:table-row>'
                '<table:table-cell><text:p>a</text:p></table:table-cell>'
                '<table:table-cell table:number-columns-repeated="3"><text:p></text:p></table:table-cell>'
                '<table:table-cell><text:p>b</text:p></table:table-cell>'
                '</table:table-row></table:table></office:document-content>')
        self.assertIn("a\t\t\t\tb", tools.read_document(self._odf("a.ods", body)))

    def test_odt_一段一行(self):
        body = ('<?xml version="1.0"?><office:document-content '
                'xmlns:office="urn:o" xmlns:text="urn:x">'
                '<text:h>标题</text:h><text:p>正文一</text:p><text:p>正文二</text:p>'
                '</office:document-content>')
        text = tools.read_document(self._odf("a.odt", body))
        self.assertEqual(text.split("\n")[:3], ["标题", "正文一", "正文二"])

    def test_认不出的后缀返回_None(self):
        self.write("a.py", "x")
        self.assertIsNone(tools.read_document(self.root / "a.py"))

    def test_坏掉的_zip_不崩(self):
        (self.root / "a.docx").write_bytes("这不是 zip".encode("utf-8"))
        self.assertIsNone(tools.read_document(self.root / "a.docx"))


class TestDispatch(Base):
    def test_未知工具名(self):
        out = self.box.run("不存在的工具", {})
        self.assertTrue(out.is_error)
        self.assertIn("未知工具", out.content)

    def test_少参数报错但不崩(self):
        out = self.box.run("bash", {})
        self.assertTrue(out.is_error)
        # 裸的 KeyError: 'command' 模型看不出是自己漏了参数
        self.assertIn("缺少参数 command", out.content)
        self.assertNotIn("KeyError", out.content)
        self.assertIn("缺少参数", self.box.run("search", {"path": "."}).summary)
        self.assertTrue(self.box.run("read_file", {}).is_error)

    def test_每个_schema_都有对应实现(self):
        for schema in tools.TOOL_SCHEMAS:
            self.assertTrue(hasattr(self.box, "_tool_" + schema["name"]), schema["name"])


if __name__ == "__main__":
    unittest.main()


class TestBinaryDetection(Base):
    """判二进制只看开头 4096 字节，**必须用增量解码器**

    直接 decode 切片会从多字节字符中间切断：中文一个字 3 字节，
    第 4096 字节落在字符中间的概率约 2/3 实测本项目的 feiyu/agent.py
    就因此被判成二进制读不了
    """

    def test_长中文文件不是二进制(self):
        for pad in range(4090, 4100):
            path = self.write("中文%d.py" % pad, "# " + "中" * (pad // 3) + "\nx = 1\n")
            self.assertFalse(tools._looks_binary(path.read_bytes()), pad)

    def test_边界正好切断中文字符(self):
        # 造一个第 4096 字节必然落在中文字符中间的文件
        raw = ("a" * 4095 + "中" * 10).encode("utf-8")
        self.assertFalse(tools._looks_binary(raw))

    def test_真的二进制还是认得出(self):
        self.assertTrue(tools._looks_binary(b"\x00\x01\x02" * 100))
        self.assertTrue(tools._looks_binary(b"\xff\xfe\xff\xfe" * 100))

    def test_读得出带中文的长文件(self):
        self.write("长.py", "# " + "中" * 2000 + "\nx = 1\n")
        out = self.box.run("read_file", {"path": "长.py"})
        self.assertFalse(out.is_error)
        self.assertIn("x = 1", out.content)

    def test_读得出本项目自己的源码(self):
        # 这条是回归：agent.py 恰好长到会踩中这个坑
        raw = Path(__file__).resolve().parent.parent.joinpath(
            "feiyu/agent.py").read_bytes()
        self.assertFalse(tools._looks_binary(raw))


class TestTimeout(Base):
    """超时后**必须把已经打印的东西给模型** —— 那是"卡在哪一步"的唯一线索

    不接住 TimeoutExpired 的话，模型只会看到一句
    "TimeoutExpired: Command [...] timed out"，沙箱一开还会把整条 bwrap
    命令行灌进上下文
    """

    def test_保住超时前的输出(self):
        out = self.box.run("bash", {"command": "echo 跑到第 3 步了; sleep 30", "timeout": 1})
        self.assertTrue(out.is_error)
        self.assertIn("跑到第 3 步了", out.content)

    def test_stderr_也保住(self):
        out = self.box.run("bash", {"command": "echo 出错了 >&2; sleep 30", "timeout": 1})
        self.assertIn("出错了", out.content)

    def test_说清楚是超时(self):
        out = self.box.run("bash", {"command": "sleep 30", "timeout": 1})
        self.assertIn("超时", out.summary)
        self.assertIn("1 秒", out.content)

    def test_没有输出时也有个交代(self):
        out = self.box.run("bash", {"command": "sleep 30", "timeout": 1})
        self.assertTrue(out.is_error)
        self.assertIn("一个字都没输出", out.content)

    def test_不把整条命令行吐给模型(self):
        # 沙箱开着时那串 argv 有二三十项，又长又没用
        out = self.box.run("bash", {"command": "sleep 30", "timeout": 1})
        self.assertNotIn("--ro-bind", out.content)
        self.assertNotIn("TimeoutExpired", out.content)


class TestSearchTruncation(Base):
    """截断必须说出来 只报「200 处匹配」的话模型会以为搜全了，
    「这个函数在哪些地方被调用」之类的结论就是错的 """

    def many(self, n):
        for i in range(n):
            self.write("f%03d.py" % i, "\n".join(["需要找的东西"] * 5))

    def test_没到上限不标截断(self):
        self.many(3)
        out = self.box.run("search", {"regex": "需要找的东西"})
        self.assertNotIn("截断", out.summary)
        self.assertEqual(out.summary, "15 处匹配")

    def test_到上限要标出来(self):
        self.many(60)      # 300 处，超过 200
        out = self.box.run("search", {"regex": "需要找的东西"})
        self.assertIn("截断", out.summary)
        self.assertIn("还有更多没列出来", out.content)

    def test_截断后条数就是上限(self):
        self.many(60)
        out = self.box.run("search", {"regex": "需要找的东西"})
        self.assertEqual(out.content.count("需要找的东西"), tools.SEARCH_CAP)

    def test_rg_不认的正则退回自己扫(self):
        # rg 是 Rust 正则，不支持反向引用；不能因为它挑食就把搜索判死
        self.write("a.py", "abc abc\n")
        out = self.box.run("search", {"regex": r"(\w+) \1"})
        self.assertFalse(out.is_error)
        self.assertIn("a.py:1", out.content)

    def test_正则写错当场报(self):
        out = self.box.run("search", {"regex": "["})
        self.assertTrue(out.is_error)

    def test_跳过噪音目录(self):
        self.write("node_modules/x.py", "需要找的东西\n")
        self.write("好的.py", "需要找的东西\n")
        out = self.box.run("search", {"regex": "需要找的东西"})
        self.assertNotIn("node_modules", out.content)
        self.assertIn("好的.py", out.content)


class TestSearchOptions(Base):
    """上下文行和只列文件名 —— 两条路径（rg / 纯 Python）行为必须一样，
    不然装没装 rg 会给出不同的答案 """

    SRC = "一\n二\n三\n目标\n五\n六\n七\n"

    def no_rg(self):
        """逼它走兜底那条：rg 的正则挑食，退回 Python 扫是常态不是异常 """
        real = tools._shutil.which
        tools._shutil.which = lambda name: None
        self.addCleanup(setattr, tools._shutil, "which", real)

    def both_paths(self):
        yield "rg"
        self.no_rg()
        yield "python"

    def test_带上下文的两条路径结果一致(self):
        self.write("a.py", self.SRC)
        seen = {}
        for which in self.both_paths():
            out = self.box.run("search", {"regex": "目标", "context": 2})
            seen[which] = out.content
        self.assertEqual(seen["rg"], seen["python"])
        body = seen["rg"]
        self.assertIn("a.py:4:目标", body)        # 匹配行用冒号
        self.assertIn("a.py-2-二", body)          # 上下文行用短横
        self.assertNotIn("一", body)              # 窗口之外的不带

    def test_只列文件名的两条路径结果一致(self):
        self.write("a.py", self.SRC)
        self.write("b.py", self.SRC)
        self.write("c.py", "别的内容\n")
        seen = {}
        for which in self.both_paths():
            out = self.box.run("search", {"regex": "目标", "files_only": True})
            seen[which] = sorted(out.content.splitlines())
        self.assertEqual(seen["rg"], seen["python"])
        self.assertEqual(seen["rg"], ["a.py", "b.py"])
        self.assertIn("个文件", self.box.run(
            "search", {"regex": "目标", "files_only": True}).summary)

    def test_不给参数时两条路径也要一致(self):
        """装没装 rg 不该让输出长得不一样 """
        self.write("a.py", "    缩进的目标\n")
        seen = {}
        for which in self.both_paths():
            out = self.box.run("search", {"regex": "目标"})
            seen[which] = out.content
            self.assertEqual(out.summary, "1 处匹配")
        self.assertEqual(seen["rg"], seen["python"])
        self.assertEqual(seen["rg"], "a.py:1:    缩进的目标")

    def test_上下文行数封顶(self):
        """不封顶的话一句 context=200 就把整个文件拉进历史 """
        self.write("a.py", "\n".join("第%d行" % i for i in range(100)))
        out = self.box.run("search", {"regex": "第50行", "context": 999})
        self.assertLessEqual(len(out.content.splitlines()),
                             tools.SEARCH_MAX_CONTEXT * 2 + 1)

    def test_挨着的两处不重复列同一行(self):
        self.no_rg()
        self.write("a.py", "目标\n中间\n目标\n")
        out = self.box.run("search", {"regex": "目标", "context": 1})
        self.assertEqual(out.content.count("中间"), 1)

    def test_隔开的两处之间有分隔线(self):
        self.no_rg()
        self.write("a.py", "目标\n" + "\n".join(["填充"] * 8) + "\n目标\n")
        self.assertIn("--", self.box.run(
            "search", {"regex": "目标", "context": 1}).content)

    def test_带上下文时报的是行数不是匹配处数(self):
        self.write("a.py", self.SRC)
        self.assertIn("行", self.box.run(
            "search", {"regex": "目标", "context": 2}).summary)


class TestWebCache(unittest.TestCase):
    def setUp(self):
        tools._web_cache.clear()

    def tearDown(self):
        tools._web_cache.clear()

    def test_超过上限会清掉最老的(self):
        # 缓存里存的是整页正文，不设上限一次会话抓几十个站就是几十兆常驻
        import time as t
        for i in range(tools._WEB_CACHE_MAX + 10):
            tools._web_cache["u%d" % i] = (t.time() + i, "正文", "u", "text/html")
        tools._prune_web_cache()
        self.assertLessEqual(len(tools._web_cache), tools._WEB_CACHE_MAX)
        self.assertIn("u%d" % (tools._WEB_CACHE_MAX + 9), tools._web_cache)

    def test_过期的会被清掉(self):
        import time as t
        tools._web_cache["旧"] = (t.time() - tools._WEB_CACHE_TTL - 1, "正文", "u", "")
        tools._web_cache["新"] = (t.time(), "正文", "u", "")
        tools._prune_web_cache()
        self.assertNotIn("旧", tools._web_cache)
        self.assertIn("新", tools._web_cache)


class TestLooseEdit(Base):
    """edit_file 对不上时先容忍空白差异，再不行也要指出最接近的位置 —— 省掉一轮整份重读 """

    def test_缩进差一级也能唯一命中_替换进去跟着文件的缩进(self):
        self.write("a.py", "def f():\n    if x:\n        return 1\n    return 0\n")
        out = self.box.run("edit_file", {
            "path": "a.py", "old_str": "if x:\n    return 1", "new_str": "if y:\n    return 2"})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("空白/缩进", out.content)
        self.assertEqual((self.root / "a.py").read_text(),
                         "def f():\n    if y:\n        return 2\n    return 0\n")

    def test_去掉空白后匹配多处仍然拒绝(self):
        self.write("a.py", "  x = 1\nx = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        # 精确匹配本来就有两处，走的是原来的 replace_all 提示；这里测的是精确 0 处、宽松多处
        self.write("b.py", "  y = 1\n\ty = 1\n")
        out = self.box.run("edit_file", {"path": "b.py", "old_str": "   y = 1", "new_str": "y = 2"})
        self.assertTrue(out.is_error)
        self.assertIn("好几处", out.content)
        self.assertEqual((self.root / "b.py").read_text(), "  y = 1\n\ty = 1\n")

    def test_完全找不到时给出最接近的行号(self):
        self.write("a.py", "\n".join("line%d = %d" % (i, i) for i in range(30)) + "\n")
        out = self.box.run("edit_file", {
            "path": "a.py", "old_str": "line17 == 17", "new_str": "z"})
        self.assertTrue(out.is_error)
        self.assertIn("第 18 行附近", out.content)
        self.assertIn("18\tline17 = 17", out.content)

    def test_八竿子打不着就直说没有相近的行(self):
        self.write("a.py", "alpha\nbeta\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "zzzzzzzzzz", "new_str": "q"})
        self.assertTrue(out.is_error)
        self.assertIn("没有跟它相近的行", out.content)


class TestAppend(Base):
    def test_append_接在末尾_不盖掉前面的(self):
        self.write("a.txt", "one\n")
        out = self.box.run("write_file", {"path": "a.txt", "content": "two\n", "append": True})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("追加", out.content)
        self.assertEqual((self.root / "a.txt").read_text(), "one\ntwo\n")

    def test_append_到不存在的文件就是新建(self):
        out = self.box.run("write_file", {"path": "n.txt", "content": "x", "append": True})
        self.assertFalse(out.is_error)
        self.assertEqual((self.root / "n.txt").read_text(), "x")

    def test_没读过的文件_append_也拦(self):
        self.write("a.txt", "one\n", seen=False)
        out = self.box.run("write_file", {"path": "a.txt", "content": "two", "append": True})
        self.assertTrue(out.is_error)
        self.assertEqual((self.root / "a.txt").read_text(), "one\n")

    def test_追加之后再追加_版本对得上(self):
        self.write("a.txt", "one\n")
        self.box.run("write_file", {"path": "a.txt", "content": "two\n", "append": True})
        out = self.box.run("write_file", {"path": "a.txt", "content": "three\n", "append": True})
        self.assertFalse(out.is_error, out.content)
        self.assertIn("共 4 行", out.content)


class TestNativeHint(Base):
    def test_grep_提醒用_search_且只提醒一次(self):
        self.write("a.txt", "hello\n")
        first = self.box.run("bash", {"command": "grep hello a.txt"})
        self.assertIn("search", first.content)
        second = self.box.run("bash", {"command": "cat a.txt"})
        self.assertNotIn("read_file 工具", second.content)

    def test_带管道和cd的复合查找也认(self):
        """日志里模型写的几乎全是这种，只认裸命令的话提示从来没触发过 """
        self.write("sub/a.txt", "hello\n")
        out = self.box.run("bash", {"command": 'cd sub && grep -n "hello" a.txt | head -5'})
        self.assertIn("search", out.content)
        self.assertTrue(self.box.hinted)

    def test_有一段在干别的就不算(self):
        self.write("a.txt", "hello\n")
        out = self.box.run("bash", {"command": "cat a.txt > b.txt"})
        self.assertNotIn("提示", out.content)
        out = self.box.run("bash", {"command": "ls -la && cp a.txt c.txt"})
        self.assertNotIn("提示", out.content)
        self.assertFalse(self.box.hinted)

    def test_普通命令不提醒(self):
        out = self.box.run("bash", {"command": "echo hi"})
        self.assertNotIn("提示", out.content)
        self.assertFalse(self.box.hinted)


def _git(root, *argv):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                          cwd=root, capture_output=True, text=True, check=True)


class TestGit(Base):
    """git 工具在真仓库上跑 只测四个动作的语义，不测 git 本身 """

    def setUp(self):
        super().setUp()
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "config", "user.email", "t@t")
        self.write("a.txt", "one\n")
        _git(self.root, "add", "a.txt")
        _git(self.root, "commit", "-q", "-m", "first")

    def git(self, **args):
        return self.box.run("git", args)

    def test_不是仓库时说清楚(self):
        other = tempfile.TemporaryDirectory()
        try:
            out = Toolbox(Path(other.name)).run("git", {"action": "status"})
            self.assertTrue(out.is_error)
            self.assertIn("不是 git 仓库", out.content)
        finally:
            other.cleanup()

    def test_status_干净和有改动(self):
        self.assertIn("干净", self.git(action="status").content)
        self.write("a.txt", "two\n")
        self.write("new.txt", "n\n")
        out = self.git(action="status")
        self.assertIn(" M a.txt", out.content)
        self.assertIn("?? new.txt", out.content)
        self.assertEqual(out.summary, "2 处改动")

    def test_diff_未暂存_已暂存_按路径(self):
        self.write("a.txt", "two\n")
        out = self.git(action="diff")
        self.assertIn("-one", out.content)
        self.assertIn("+two", out.content)
        self.assertIn("没有已暂存的改动", self.git(action="diff", staged=True).content)
        _git(self.root, "add", "a.txt")
        self.assertIn("+two", self.git(action="diff", staged=True).content)
        self.assertIn("没有改动", self.git(action="diff", paths=["zzz"]).content)

    def test_diff_太长只给开头并说明(self):
        self.write("a.txt", "\n".join("line %d" % i for i in range(3000)) + "\n")
        out = self.git(action="diff")
        self.assertIn("太长", out.content)
        self.assertIn("已截断", out.summary)
        self.assertLess(len(out.content), tools.GIT_DIFF_CAP + 2000)

    def test_log(self):
        out = self.git(action="log")
        self.assertIn("first", out.content)
        self.assertEqual(out.summary, "1 条提交")

    def test_commit_默认只带已跟踪的_新文件要指名(self):
        self.write("a.txt", "two\n")
        self.write("new.txt", "n\n")
        out = self.git(action="commit", message="改 a")
        self.assertFalse(out.is_error, out.content)
        self.assertIn("a.txt", out.content)
        self.assertIn("没提交的新文件：new.txt", out.content)
        self.assertIn("?? new.txt", self.git(action="status").content)
        out = self.git(action="commit", message="加 new", paths=["new.txt"])
        self.assertFalse(out.is_error, out.content)
        self.assertIn("干净", self.git(action="status").content)
        self.assertEqual(self.git(action="log").summary, "3 条提交")

    def test_没改动就不提交(self):
        out = self.git(action="commit", message="空")
        self.assertTrue(out.is_error)
        self.assertIn("新文件不会自动带上", out.content)
        self.assertEqual(self.git(action="log").summary, "1 条提交")

    def test_没写信息就拒(self):
        self.write("a.txt", "two\n")
        out = self.git(action="commit", message="  ")
        self.assertTrue(out.is_error)

    def test_路径不能出工作区(self):
        out = self.git(action="diff", paths=["../x"])
        self.assertTrue(out.is_error)

    def test_bash_跑_git_status_会提醒用_git_工具(self):
        out = self.box.run("bash", {"command": "git status"})
        self.assertIn("git 工具", out.content)

    def test_commit_预览列出提交信息和文件(self):
        self.write("a.txt", "two\n")
        rows = tools.preview("git", {"action": "commit", "message": "改 a"}, self.root)
        self.assertIn("提交信息：改 a", rows[0][1])
        self.assertTrue(any("a.txt" in r[1] for r in rows))


class TestBatchEdit(Base):
    def test_一次改多处_按顺序应用(self):
        self.write("a.py", "a = 1\nb = 2\nc = 3\n")
        out = self.box.run("edit_file", {"path": "a.py", "edits": [
            {"old_str": "a = 1", "new_str": "a = 10"},
            {"old_str": "c = 3", "new_str": "c = 30"},
            {"old_str": "a = 10", "new_str": "a = 100"},     # 看得到前一处的结果
        ]})
        self.assertFalse(out.is_error, out.content)
        self.assertEqual((self.root / "a.py").read_text(), "a = 100\nb = 2\nc = 30\n")
        self.assertIn("3 处改动", out.content)

    def test_有一处对不上就整个不写(self):
        self.write("a.py", "a = 1\nb = 2\n")
        out = self.box.run("edit_file", {"path": "a.py", "edits": [
            {"old_str": "a = 1", "new_str": "a = 10"},
            {"old_str": "zzz", "new_str": "y"},
        ]})
        self.assertTrue(out.is_error)
        self.assertIn("第 2 处", out.content)
        self.assertIn("一处都没写进文件", out.content)
        self.assertEqual((self.root / "a.py").read_text(), "a = 1\nb = 2\n")

    def test_批量里也能用_replace_all_和宽松匹配(self):
        self.write("a.py", "x = 1\nx = 1\n    y = 2\n")
        out = self.box.run("edit_file", {"path": "a.py", "edits": [
            {"old_str": "x = 1", "new_str": "x = 9", "replace_all": True},
            {"old_str": "\ty = 2", "new_str": "\ty = 8"},      # tab 抄成了四个空格
        ]})
        self.assertFalse(out.is_error, out.content)
        self.assertEqual((self.root / "a.py").read_text(), "x = 9\nx = 9\n    y = 8\n")
        self.assertIn("第 2 处：（old_str 的空白", out.content)

    def test_什么都不给就报参数错(self):
        self.write("a.py", "a\n")
        out = self.box.run("edit_file", {"path": "a.py"})
        self.assertTrue(out.is_error)
        self.assertIn("edits", out.content)

    def test_预览按整批算_diff(self):
        self.write("a.py", "a = 1\nb = 2\n")
        rows = tools.preview("edit_file", {"path": "a.py", "edits": [
            {"old_str": "a = 1", "new_str": "a = 10"},
            {"old_str": "b = 2", "new_str": "b = 20"},
        ]}, self.root)
        joined = "\n".join(r[1] for r in rows)
        self.assertIn("a = 10", joined)
        self.assertIn("b = 20", joined)


class TestUndo(Base):
    """/undo 退掉 write_file / edit_file 最近一次改动 退回去的内容登记进 Watcher """

    def test_覆盖后能退回(self):
        self.write("a.py", "x = 1\n")
        self.box.run("write_file", {"path": "a.py", "content": "x = 2\n"})
        said = self.box.undo()
        self.assertIn("a.py", said)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 1\n")
        # 退回去之后它不用重读也能接着写：Watcher 记的是退回去的那版
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 3"})
        self.assertFalse(out.is_error)

    def test_新建的退掉就是删掉(self):
        self.box.run("write_file", {"path": "new.py", "content": "y = 1\n"})
        self.assertTrue((self.root / "new.py").exists())
        self.assertIn("删掉", self.box.undo())
        self.assertFalse((self.root / "new.py").exists())

    def test_连着退_按倒序(self):
        self.write("a.py", "v1\n")
        self.box.run("edit_file", {"path": "a.py", "old_str": "v1", "new_str": "v2"})
        self.box.run("edit_file", {"path": "a.py", "old_str": "v2", "new_str": "v3"})
        self.box.undo()
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "v2\n")
        self.box.undo()
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "v1\n")
        self.assertIsNone(self.box.undo())

    def test_被拦下的写不进栈(self):
        self.write("a.py", "重要", seen=False)
        self.box.run("write_file", {"path": "a.py", "content": "新的"})
        self.assertIsNone(self.box.undo())

    def test_写完又被外部改过就不退(self):
        self.write("a.py", "v1\n")
        self.box.run("edit_file", {"path": "a.py", "old_str": "v1", "new_str": "v2"})
        (self.root / "a.py").write_text("用户改的\n", encoding="utf-8")
        import os
        os.utime(self.root / "a.py", (1, 1))
        said = self.box.undo()
        self.assertIn("又被改过", said)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "用户改的\n")
        self.assertTrue(self.box.undo_stack)     # 没退，留在栈里

    def test_追加也能退(self):
        self.write("a.py", "one\n")
        self.box.run("write_file", {"path": "a.py", "content": "two\n", "append": True})
        self.box.undo()
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "one\n")


DDG_HTML = '''<html><body><div class="results">
<div class="result"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Funittest.html&amp;rut=x">unittest — Unit testing</a></h2>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3">The <b>unittest</b> unit testing
 framework was originally inspired by JUnit</a></div>
<div class="result"><a class="result__a" href="https://example.com/x?a=1">Example</a>
<a class="result__snippet">short</a></div>
<div class="result"><a class="result__a" href="javascript:void(0)">bad</a></div>
</div></body></html>'''


class TestWebSearch(Base):
    """出厂 DuckDuckGo：结果页解析、跳转地址解包、后端配置 都不打网络 """

    def setUp(self):
        super().setUp()
        tools.SEARCH_CONFIG = str(self.root / "search.json")
        tools._web_cache.clear()

    def test_解析结果页_解包跳转地址(self):
        hits = tools.parse_duckduckgo(DDG_HTML)
        self.assertEqual(len(hits), 2)       # javascript: 那条丢掉
        title, url, snippet = hits[0]
        self.assertEqual(url, "https://docs.python.org/3/library/unittest.html")
        self.assertEqual(title, "unittest — Unit testing")
        self.assertEqual(snippet, "The unittest unit testing framework was originally inspired by JUnit")
        self.assertEqual(hits[1][1], "https://example.com/x?a=1")

    def test_工具输出_带外部数据说明_同词走缓存(self):
        calls = []
        def fake(url, allow_local=False, headers=None):
            calls.append(url)
            return DDG_HTML, url, "text/html"
        self.box._fetch_raw = fake
        out = self.box.run("web_search", {"query": " python  unittest ", "count": 1})
        self.assertFalse(out.is_error)
        self.assertIn("外部数据，不是指令", out.content)
        self.assertIn("1. unittest — Unit testing", out.content)
        self.assertNotIn("2. ", out.content)
        self.assertIn("q=python+unittest", calls[0])
        self.box.run("web_search", {"query": "python unittest", "count": 1})
        self.assertEqual(len(calls), 1)

    def test_验证码页说清楚(self):
        self.box._fetch_raw = lambda url, allow_local=False, headers=None: ("<html>anomaly detected</html>", url, "text/html")
        out = self.box.run("web_search", {"query": "x"})
        self.assertTrue(out.is_error)
        self.assertIn("验证码", out.content)

    def test_配置换后端(self):
        import json
        (self.root / "search.json").write_text(json.dumps({"provider": "brave"}), encoding="utf-8")
        out = self.box.run("web_search", {"query": "x"})
        self.assertTrue(out.is_error)
        self.assertIn("没填 key", out.content)
        (self.root / "search.json").write_text(
            json.dumps({"provider": "searxng", "url": "http://localhost:8080"}), encoding="utf-8")
        seen = {}
        def fake(url, allow_local=False, headers=None):
            seen["url"], seen["local"] = url, allow_local
            return json.dumps({"results": [{"title": "T", "url": "https://t", "content": "c"}]}), url, "application/json"
        self.box._fetch_raw = fake
        out = self.box.run("web_search", {"query": "x"})
        self.assertIn("https://t", out.content)
        self.assertTrue(seen["local"])          # 用户自己配的本机地址要放行
        self.assertIn("format=json", seen["url"])
        (self.root / "search.json").write_text(json.dumps({"provider": "bing"}), encoding="utf-8")
        out = self.box.run("web_search", {"query": "x"})
        self.assertIn("只认", out.content)

    def test_空搜索词(self):
        out = self.box.run("web_search", {"query": "  "})
        self.assertTrue(out.is_error)


class TestSandboxNote(unittest.TestCase):
    """报错原文认得出是沙箱造成的才追加说明；认不出就一个字不加 """

    def test_只读文件系统(self):
        self.assertIn("只读", tools._sandbox_note("sh: x: Read-only file system", "workspace"))

    def test_ssh_远端在沙箱里挂了要说明_ssh_被挖空(self):
        note = tools._sandbox_note("Host key verification failed.\nfatal: Could not read from remote repository.",
                                   "workspace")
        self.assertIn(".ssh", note)
        self.assertIn("https", note)

    def test_strict_档断网要说明(self):
        note = tools._sandbox_note("fatal: unable to access 'https://x/': Could not resolve host: x", "strict")
        self.assertIn("不联网", note)

    def test_workspace_档联网照旧_解析失败不算沙箱的事(self):
        self.assertEqual("", tools._sandbox_note("Could not resolve host: x", "workspace"))

    def test_普通报错不加话(self):
        self.assertEqual("", tools._sandbox_note("Traceback ...\nKeyError: 'x'", "strict"))


class TestVenvHint(Base):
    """裸 python 撞缺模块、工作区有 .venv 时指一下解释器；别的情况一个字不加 """

    def _venv(self):
        (self.root / ".venv" / "bin").mkdir(parents=True)
        (self.root / ".venv" / "bin" / "python").write_text("")

    def test_有_venv_裸_python_缺模块要提示(self):
        self._venv()
        note = tools._venv_hint("python -m pytest -q", "ModuleNotFoundError: No module named 'httpx'", self.root)
        self.assertIn(".venv/bin/python", note)

    def test_已经用了_venv_不提示(self):
        self._venv()
        self.assertEqual("", tools._venv_hint(".venv/bin/python -m pytest", "ImportError: x", self.root))

    def test_没有_venv_不提示(self):
        self.assertEqual("", tools._venv_hint("python x.py", "ModuleNotFoundError: x", self.root))

    def test_不是缺模块的错不提示(self):
        self._venv()
        self.assertEqual("", tools._venv_hint("python x.py", "AssertionError: 1 != 2", self.root))

    def test_不是_python_命令不提示(self):
        self._venv()
        self.assertEqual("", tools._venv_hint("node x.js", "Error: Cannot find module 'x'", self.root))

    def test_走完整的_bash_工具也带提示(self):
        self._venv()
        out = self.box.run("bash", {"command": "python3 -c 'import definitely_missing_mod_xyz'"})
        self.assertTrue(out.is_error)
        self.assertIn(".venv/bin/python", out.content)


class _Reply:
    """够 `with _opener.open(...) as r: r.read()` 用就行 """

    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *_):
        return self.raw


class TestImageGenerate(Base):
    """出图 不打网络：把 _opener.open 换掉 """

    PIXEL = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)

    def setUp(self):
        super().setUp()
        before = tools.IMAGE_CONFIG
        tools.IMAGE_CONFIG = str(self.root / "images.json")
        self.addCleanup(setattr, tools, "IMAGE_CONFIG", before)

    def config(self, **extra):
        import json
        (self.root / "images.json").write_text(
            json.dumps({"base_url": "https://relay.example/v1", "key": "sk-x", **extra}),
            encoding="utf-8")

    def reply(self, payload):
        import json
        return unittest.mock.patch.object(
            tools._opener, "open", return_value=_Reply(json.dumps(payload).encode()))

    def test_没配就说去哪儿配(self):
        out = self.box.run("image_generate", {"prompt": "一只狗"})
        self.assertTrue(out.is_error)
        self.assertIn("images.json", out.content)

    def test_没写描述(self):
        self.config()
        out = self.box.run("image_generate", {"prompt": "   "})
        self.assertTrue(out.is_error)

    def test_b64_存进工作区_结果带路径(self):
        import base64
        import json
        self.config(model="gpt-image-2", size="1024x1024")
        payload = {"data": [{"b64_json": base64.b64encode(self.PIXEL).decode()}]}
        with self.reply(payload) as opened:
            out = self.box.run("image_generate", {"prompt": " 一只  狗 ", "size": "512x512"})
        self.assertFalse(out.is_error, out.content)
        shown = json.loads(out.content)["image"]
        self.assertTrue(shown.startswith(tools.IMAGE_DIR + "/"))
        self.assertEqual((self.root / shown).read_bytes(), self.PIXEL)
        # 前端就是靠 shows 把图画出来的，detail 里不要再重复一条路径
        self.assertEqual(out.shows, shown)
        self.assertEqual(out.detail, [])
        # 描述压平了空白，参数里的 size 压过配置里的
        sent = json.loads(opened.call_args[0][0].data)
        self.assertEqual(sent["prompt"], "一只 狗")
        self.assertEqual(sent["size"], "512x512")
        self.assertEqual(sent["model"], "gpt-image-2")
        # 写完要登记，不然它下一次想改这张图会被"没读过"拦下
        self.assertTrue(self.box.files.seen(self.root / shown))

    def test_同一秒出两张不互相覆盖(self):
        import base64
        self.config()
        payload = {"data": [{"b64_json": base64.b64encode(self.PIXEL).decode()}]}
        with self.reply(payload):
            a = self.box.run("image_generate", {"prompt": "甲"})
            b = self.box.run("image_generate", {"prompt": "乙"})
        self.assertNotEqual(a.shows, b.shows)
        self.assertTrue((self.root / b.shows).is_file())

    def test_只回地址时再抓一次(self):
        self.config()
        import json
        pages = [_Reply(json.dumps({"data": [{"url": "https://cdn.example/x.png"}]}).encode()),
                 _Reply(self.PIXEL)]
        with unittest.mock.patch.object(tools._opener, "open", side_effect=pages):
            out = self.box.run("image_generate", {"prompt": "一只狗"})
        self.assertFalse(out.is_error, out.content)
        self.assertEqual((self.root / out.shows).read_bytes(), self.PIXEL)

    def test_地址指到内网就不抓(self):
        self.config()
        import json
        payload = {"data": [{"url": "http://169.254.169.254/latest/meta-data/"}]}
        with self.reply(payload):
            out = self.box.run("image_generate", {"prompt": "一只狗"})
        self.assertTrue(out.is_error)
        self.assertIn("169.254.169.254", out.content)

    def test_认不出的返回不当成功(self):
        self.config()
        with self.reply({"data": [{"revised_prompt": "..."}]}):
            out = self.box.run("image_generate", {"prompt": "一只狗"})
        self.assertTrue(out.is_error)
        with self.reply({"error": {"message": "no quota"}}):
            out = self.box.run("image_generate", {"prompt": "一只狗"})
        self.assertTrue(out.is_error)
        self.assertIn("no quota", out.content)


class TestMissingFile(Base):
    """**读不到的文件要说"没有这个文件"，不能归成别的错。**

    原来一个字都没判：文本文件靠 `read_bytes()` 抛出去（模型看到一句裸的
    FileNotFoundError），图片更糟 —— 走进 `_read_image` 之后被归成「图片打不开」。
    实测日志里同一个不存在的 .png 被反复读了 **23 次**，占 read_file 全部失败的 85%：
    模型读不出"这文件根本不在"，就只会换着参数接着试，每一次都是一个整轮往返。
    """

    def read(self, path):
        return self.box.run("read_file", {"path": path})

    def test_不存在的图片说的是文件不存在(self):
        got = self.read("nope.png")
        self.assertTrue(got.is_error)
        self.assertEqual(got.summary, "文件不存在")

    def test_不存在的文本文件也一样(self):
        self.assertEqual(self.read("nope.py").summary, "文件不存在")

    def test_指出最像的那个名字(self):
        self.write("app.py", "x = 1\n")
        got = self.read("aap.py")
        self.assertIn("app.py", got.content)

    def test_上一级目录都不在时说出来(self):
        self.assertIn("deep", self.read("deep/x.md").content)

    def test_目录不是文件(self):
        os.mkdir(os.path.join(self.tmp.name, "src"))
        got = self.read("src")
        self.assertTrue(got.is_error)
        self.assertIn("list_files", got.content)

    def test_真在的文件照读不误(self):
        self.write("app.py", "x = 1\n")
        self.assertFalse(self.read("app.py").is_error)


class TestOddFiles(Base):
    """命名管道会把整个进程挂死 —— 而且是**没有任何输出**地挂死

    打开一个没有另一端的 FIFO，read 和 write 都会一直阻塞。界面上一个字都没有，
    `STREAM_TIMEOUT` 也不响（那条管的是模型的流，这儿还没发出去）。
    实测 read_file / write_file 各能这么挂住一次，跟 0.58.0 那次「为什么卡死了」
    是同一种毛病。这几条用例都带 timeout：挂住的话是**卡死**不是失败，
    所以要在子进程里跑，别把整个测试跑挂在这儿。
    """

    def fifo(self, name="pipe.txt"):
        path = self.root / name
        try:
            os.mkfifo(path)
        except (AttributeError, OSError):
            self.skipTest("这个平台建不了 FIFO")
        return path

    def _in_child(self, tool, args):
        """真跑一遍，但跑在子进程里、带超时 —— 没修好的话这儿会超时而不是挂死"""
        code = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from feiyu.tools import Toolbox\n"
            "b = Toolbox(Path(sys.argv[1]))\n"
            "r = b.run(sys.argv[2], json.loads(sys.argv[3]))\n"
            "print(r.summary or '')\n")
        done = subprocess.run(
            [sys.executable, "-c", code, str(self.root), tool, json.dumps(args)],
            capture_output=True, text=True, timeout=20)
        return done.stdout.strip()

    def test_读命名管道要报错不能挂住(self):
        self.fifo()
        self.assertEqual(self._in_child("read_file", {"path": "pipe.txt"}), "不是普通文件")

    def test_写命名管道要报错不能挂住(self):
        self.fifo()
        self.assertEqual(
            self._in_child("write_file", {"path": "pipe.txt", "content": "x"}),
            "不是普通文件")

    def test_改命名管道说的是它不是普通文件不是不存在(self):
        """只说"不存在"的话它会转头去 write_file，那条路一样挂住 """
        self.fifo()
        self.assertEqual(
            self._in_child("edit_file",
                           {"path": "pipe.txt", "old_str": "a", "new_str": "b"}),
            "不是普通文件")

    def test_普通文件照旧读得动(self):
        self.write("ok.txt", "一行\n")
        self.assertIn("读取", self._in_child("read_file", {"path": "ok.txt"}))


class TestDiagnose(Base):
    """改完立刻知道语法坏没坏 —— 不然要等模型自己去跑测试，那至少一整轮往返

    两条边界是这块的全部设计：
    1. **只查语法，不报语言服务器的诊断**（A/B 量出来是负收益）。
    2. **报「你把它弄坏了」，不是「它是坏的」** —— 本来就解析不了的文件
       （半截的、模板、故意写的片段）这次没弄更坏，就不该赖在它头上。
    """

    def test_写出语法错的文件要当场拦下(self):
        out = self.box.run("write_file", {"path": "a.py", "content": "def f(:\n"})
        self.assertTrue(out.is_error, out.content)
        self.assertIn("语法错误", out.content)
        self.assertIn("第 1 行", out.content)

    def test_写对的文件不该报任何东西(self):
        out = self.box.run("write_file", {"path": "a.py", "content": "x = 1\n"})
        self.assertFalse(out.is_error, out.content)
        self.assertNotIn("语法错误", out.content)
        self.assertNotIn("⚠", out.content)

    def test_本来就坏的文件_这次没弄更坏就不赖模型(self):
        """夹具、半截文件、模板都可能解析不了 —— 让它去修一个它没造成的问题是错的 """
        self.write("a.py", "def f(:\n", seen=True)
        out = self.box.run("edit_file", {
            "path": "a.py", "old_str": "def f(:", "new_str": "def g(:"})
        self.assertFalse(out.is_error, out.content)
        self.assertNotIn("语法错误", out.content)

    def test_把好文件改坏了要报(self):
        self.write("a.py", "x = 1\n", seen=True)
        out = self.box.run("edit_file", {
            "path": "a.py", "old_str": "x = 1", "new_str": "def f(:"})
        self.assertTrue(out.is_error, out.content)
        self.assertIn("语法错误", out.content)

    def test_覆盖写也比对改动前(self):
        self.write("a.py", "def f(:\n", seen=True)
        out = self.box.run("write_file", {"path": "a.py", "content": "def g(:\n"})
        self.assertFalse(out.is_error, out.content)

    def test_非_python_不做本地语法检查(self):
        out = self.box.run("write_file", {"path": "a.txt", "content": "def f(:\n"})
        self.assertFalse(out.is_error, out.content)

    def test_不报语言服务器的诊断(self):
        """**这条是那次 A/B 的结论，别顺手把它改绿。** 整文件的诊断报出来过，
        12 道题配对比出来轮数 +53%、token +72%、解决率没动 —— 它把模型引去修
        自己没造成的问题。语法过得去的文件，写完就只该说"写了多少行"。
        """
        out = self.box.run("write_file", {
            "path": "a.py", "content": "def f():\n    return 没定义过的名字\n"})
        self.assertFalse(out.is_error, out.content)
        self.assertNotIn("语言服务器", out.content)
        self.assertNotIn("⚠", out.content)

    def test_不碰语言服务器_所以它挂了也不影响写文件(self):
        def boom(target):
            raise RuntimeError("不该走到这里")
        self.box.lsp.get = boom
        out = self.box.run("write_file", {"path": "a.py", "content": "x = 1\n"})
        self.assertFalse(out.is_error, out.content)
        out = self.box.run("write_file", {"path": "b.py", "content": "def f(:\n"})
        self.assertTrue(out.is_error, "语法检查该照旧在，它不依赖语言服务器")


class Test整写提示(unittest.TestCase):
    """整写一个已存在的文件、内容跟原文大面积重合时，在结果里说一句改用 edit_file

    依据是 6 道题 157 轮的 trace：墙钟 91-98% 花在等模型，每轮耗时跟输出字数的
    相关系数 +0.98，而 16 次 write_file 里 7 次「覆盖」共 3315 行（新建的 3 倍），
    三道题各有一个文件被反复整写。**默认关着**，等 A/B 说它有用再谈默认开。
    """

    def setUp(self):
        self.old = os.environ.get("FEIYU_REWRITE_HINT")
        self.addCleanup(lambda: os.environ.__setitem__("FEIYU_REWRITE_HINT", self.old)
                        if self.old is not None else os.environ.pop("FEIYU_REWRITE_HINT", None))

    def test_默认是关的(self):
        os.environ.pop("FEIYU_REWRITE_HINT", None)
        self.assertFalse(tools.rewrite_hint_on())
        os.environ["FEIYU_REWRITE_HINT"] = "on"
        self.assertTrue(tools.rewrite_hint_on())

    def test_大面积重合才提醒(self):
        old = "\n".join("第 %d 行" % i for i in range(200))
        加了十行 = old + "\n" + "\n".join("新的 %d" % i for i in range(10))
        got = tools._mostly_rewrite(old, 加了十行)
        self.assertIn("edit_file", got)
        self.assertIn("95%", got)

    def test_几乎全换掉的不提醒(self):
        """大改本来就该整写，提醒它反而是错的 """
        old = "\n".join("旧 %d" % i for i in range(200))
        new = "\n".join("完全不同 %d" % i for i in range(200))
        self.assertEqual(tools._mostly_rewrite(old, new), "")

    def test_短文件不啰嗦(self):
        """几十行的文件整写本来就不贵 """
        old = "\n".join("行 %d" % i for i in range(20))
        self.assertEqual(tools._mostly_rewrite(old, old + "\n新的"), "")

    def test_按多重集合算不是按集合(self):
        """同一行重复出现 N 次，只能抵扣 N 次 —— 用 set 的话
        一份「全是空行」的新内容会被算成 100% 重合
        """
        old = "唯一的一行\n" + "\n".join("行 %d" % i for i in range(199))
        new = "\n".join(["唯一的一行"] * 200)
        self.assertEqual(tools._mostly_rewrite(old, new), "")
