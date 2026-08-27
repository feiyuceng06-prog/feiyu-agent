"""七个工具 网络那个只测拦截逻辑，不真的出网 """

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from feiyu import tools
from feiyu.tools import Toolbox


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.box = Toolbox(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
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

    def test_编辑要求唯一匹配(self):
        self.write("a.py", "x = 1\nx = 1\n")
        out = self.box.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"})
        self.assertTrue(out.is_error)
        self.assertIn("2 处", out.content)
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


class TestWebGuards(Base):
    def test_内网地址被挡(self):
        for host in ("localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1"):
            self.assertTrue(tools._blocked_host(host), host)

    def test_公网地址放行(self):
        self.assertEqual(tools._blocked_host("example.com"), "")

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
        self.assertTrue(self.box.run("bash", {}).is_error)
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
