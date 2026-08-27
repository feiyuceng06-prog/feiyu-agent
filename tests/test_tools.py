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
