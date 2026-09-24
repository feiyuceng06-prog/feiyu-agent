"""MCP 客户端 真起一个假服务器（tests/mcp_server.py），不打网络

HTTP 那套用 tests/mcp_http_server.py，绑 127.0.0.1 的临时端口，一样不出网 """

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from feiyu import enabled, keys, mcp, permissions
from feiyu.agent import Feiyu
from tests import mcp_http_server as http_server
from tests.fakes import FakeBackend, call

SERVER = str(Path(__file__).resolve().parent / "mcp_server.py")


def spec():
    return {"command": sys.executable, "args": [SERVER]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        mcp.GLOBAL = os.path.join(self.tmp.name, "mcp.json")
        mcp.TRUST = os.path.join(self.tmp.name, "trusted-mcp.json")
        permissions.STORE = os.path.join(self.tmp.name, "permissions.json")
        permissions.MCP_SUBJECTS.clear()
        self.reg = None

    def tearDown(self):
        if self.reg is not None:
            self.reg.close()
        self.tmp.cleanup()

    def write_global(self, servers):
        with open(mcp.GLOBAL, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": servers}, f)

    def write_project(self, servers):
        os.makedirs(self.root / ".feiyu", exist_ok=True)
        with open(self.root / mcp.PROJECT, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": servers}, f)

    def load(self, **kw):
        self.reg = mcp.Registry(self.root)
        note = self.reg.load(**kw)
        return self.reg, note


class TestRegistry(Base):
    def test_起来_列工具_schema形状(self):
        self.write_global({"fake": spec()})
        reg, note = self.load()
        self.assertIn("fake（5 个工具）", note)
        self.assertEqual(set(reg.tools), {"mcp_fake_echo", "mcp_fake_write_note", "mcp_fake_fail", "mcp_fake_slow",
                          "mcp_fake_getenv"})
        for s in reg.schemas():
            self.assertEqual(set(s), {"name", "description", "input_schema"})
            self.assertEqual(s["input_schema"]["type"], "object")
            self.assertTrue(s["description"].startswith("[MCP fake]"))
        self.assertIn("mcp_fake_echo", reg.readonly)
        self.assertNotIn("mcp_fake_write_note", reg.readonly)

    def test_调用_回答服务器的反向请求_带外部数据说明(self):
        self.write_global({"fake": spec()})
        reg, _ = self.load()
        text, failed = reg.call("mcp_fake_echo", {"text": "hi"})
        self.assertFalse(failed)
        self.assertTrue(text.startswith(mcp.NOTE % "fake"))
        self.assertIn("echo: hi", text)
        # 第二次不再有反向请求，照样通
        text, _ = reg.call("mcp_fake_echo", {"text": "again"})
        self.assertIn("echo: again", text)

    def test_isError_和超时(self):
        self.write_global({"fake": spec()})
        reg, _ = self.load()
        text, failed = reg.call("mcp_fake_fail", {})
        self.assertTrue(failed)
        self.assertIn("坏了", text)
        old = mcp.CALL_TIMEOUT
        mcp.CALL_TIMEOUT = 0.3
        try:
            text, failed = reg.call("mcp_fake_slow", {})
        finally:
            mcp.CALL_TIMEOUT = old
        self.assertTrue(failed)
        self.assertIn("没回复", text)

    def test_起不来的说清楚_别的照常(self):
        self.write_global({"fake": spec(), "bad": {"command": "/no/such/binary"},
                           "web": {"type": "sse", "url": "http://x"}})
        reg, note = self.load()
        self.assertIn("fake", reg.servers)
        self.assertIn("bad", reg.errors)
        self.assertIn("传输只认 stdio 和 http", reg.errors["web"])
        self.assertIn("没起来", note)

    def test_没配置就什么都不做(self):
        reg, note = self.load()
        self.assertEqual(note, "")
        self.assertEqual(reg.schemas(), [])

    def test_项目配置要点头_按内容hash记住(self):
        self.write_project({"fake": spec()})
        reg, note = self.load(approve=None)
        self.assertEqual(reg.servers, {})
        self.assertIn("未启用", note)
        reg.close()
        asked = []
        reg, note = self.load(approve=lambda path, names: asked.append(names) or True)
        self.assertEqual(asked, [["fake"]])
        self.assertIn("fake", reg.servers)
        reg.close()
        # 同意过、内容没变：不再问
        reg, _ = self.load(approve=lambda *a: self.fail("不该再问"))
        self.assertIn("fake", reg.servers)
        reg.close()
        # 内容变了：重新问
        self.write_project({"fake": spec(), "fake2": spec()})
        reg, _ = self.load(approve=lambda path, names: False)
        self.assertEqual(reg.servers, {})

    def test_stdio的env里也认环境变量(self):
        """`tools._child_env()` 把带 KEY/TOKEN 的变量摘掉了（防模型 echo 出来），
        于是指望从环境里拿密钥的 stdio 服务器静默拿不到。显式写 ${VAR} 就能拿到 ——
        密钥既不落配置文件，也不进模型手里。
        """
        os.environ["FEIYU_TEST_STDIO_TOKEN"] = "s3cret"
        self.addCleanup(os.environ.pop, "FEIYU_TEST_STDIO_TOKEN", None)
        spec_with = dict(spec())
        spec_with["env"] = {"PASSED_IN": "${FEIYU_TEST_STDIO_TOKEN}"}
        self.write_global({"fake": spec_with})
        reg, _ = self.load()
        self.assertIn("fake", reg.servers)
        # 摘过密钥的那份环境里没有它 —— 所以不展开的话子进程也拿不到
        from feiyu import tools as tools_mod
        self.assertNotIn("FEIYU_TEST_STDIO_TOKEN", tools_mod._child_env())
        # 问子进程自己：它手上那个值是什么。**这才算验到**，
        # 光比 argv 的话，就算展开成字面量 "${...}" 也一样会过
        text, failed = reg.call("mcp_fake_getenv", {"name": "PASSED_IN"})
        self.assertFalse(failed)
        self.assertIn("s3cret", text)
        self.assertNotIn("${", text)

    def test_stdio的env里变量没设就不起(self):
        os.environ.pop("FEIYU_TEST_STDIO_MISSING", None)
        spec_bad = dict(spec())
        spec_bad["env"] = {"X": "${FEIYU_TEST_STDIO_MISSING}"}
        self.write_global({"fake": spec_bad})
        reg, _ = self.load()
        self.assertNotIn("fake", reg.servers)
        self.assertIn("FEIYU_TEST_STDIO_MISSING", reg.errors["fake"])

    def test_关掉之后进程没了(self):
        self.write_global({"fake": spec()})
        reg, _ = self.load()
        proc = reg.servers["fake"].io.proc
        reg.close()
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())
        self.reg = None


class TestHttp(Base):
    """streamable-http 传输 服务器是本地临时端口上的假货，不出网 """

    def setUp(self):
        super().setUp()
        self.fakes = []

    def tearDown(self):
        for f in self.fakes:
            f.close()
        super().tearDown()

    def fake(self, **kw):
        got = http_server.Fake(**kw)
        self.fakes.append(got)
        return got

    def test_json形状的回复(self):
        server = self.fake()
        self.write_global({"web": {"type": "http", "url": server.url}})
        reg, note = self.load()
        self.assertIn("web", reg.servers)
        self.assertIn("web（2 个工具）", note)
        text, failed = reg.call("mcp_web_echo", {"text": "你好"})
        self.assertFalse(failed)
        self.assertIn("你好", text)

    def test_sse形状的回复也认(self):
        server = self.fake(flavor="sse")
        self.write_global({"web": {"type": "http", "url": server.url}})
        reg, _ = self.load()
        text, failed = reg.call("mcp_web_echo", {"text": "流式"})
        self.assertFalse(failed)
        self.assertIn("流式", text)

    def test_没写type但有url也当http(self):
        server = self.fake()
        self.write_global({"web": {"url": server.url}})
        reg, _ = self.load()
        self.assertIn("web", reg.servers)

    def test_isError照样传上来(self):
        server = self.fake()
        self.write_global({"web": {"type": "http", "url": server.url}})
        reg, _ = self.load()
        text, failed = reg.call("mcp_web_fail", {})
        self.assertTrue(failed)
        self.assertIn("炸了", text)

    def test_initialize之后每条都带session(self):
        server = self.fake()
        self.write_global({"web": {"type": "http", "url": server.url}})
        reg, _ = self.load()
        reg.call("mcp_web_echo", {"text": "x"})
        # 第一条（initialize）之前还没有 session，之后每条都得带上
        self.assertNotIn("mcp-session-id", server.seen[0])
        self.assertTrue(all(h.get("mcp-session-id") == http_server.SESSION
                            for h in server.seen[1:]))

    def test_headers带上去了_环境变量会展开(self):
        server = self.fake(api_key="s3cret")
        os.environ["FEIYU_TEST_KEY"] = "s3cret"
        try:
            self.write_global({"web": {"type": "http", "url": server.url,
                                       "headers": {"x-api-key": "${FEIYU_TEST_KEY}"}}})
            reg, _ = self.load()
            self.assertIn("web", reg.servers)
            self.assertEqual(server.seen[0].get("x-api-key"), "s3cret")
        finally:
            del os.environ["FEIYU_TEST_KEY"]

    def test_环境变量没设就不起_并且说是哪个(self):
        server = self.fake()
        os.environ.pop("FEIYU_TEST_MISSING", None)
        self.write_global({"web": {"type": "http", "url": server.url,
                                   "headers": {"x-api-key": "${FEIYU_TEST_MISSING}"}}})
        reg, _ = self.load()
        self.assertNotIn("web", reg.servers)
        self.assertIn("FEIYU_TEST_MISSING", reg.errors["web"])

    def test_认证不过的说法要能看懂(self):
        server = self.fake(api_key="right")
        self.write_global({"web": {"type": "http", "url": server.url,
                                   "headers": {"x-api-key": "wrong"}}})
        reg, _ = self.load()
        self.assertNotIn("web", reg.servers)
        self.assertIn("不认这个身份", reg.errors["web"])

    def test_连不上的不拖死别的(self):
        good = self.fake()
        self.write_global({"web": {"type": "http", "url": good.url},
                           "dead": {"type": "http", "url": "http://127.0.0.1:1/mcp"}})
        reg, _ = self.load()
        self.assertIn("web", reg.servers)
        self.assertIn("dead", reg.errors)

    def test_重定向不能把POST悄悄变成GET(self):
        """urllib 默认跟随重定向，302 还会把 POST 降成 GET —— body 就没了。

        真实场景是端点前面挂了个 http→https 或者补斜杠的跳转。**必须报出来**，
        悄悄降级的话服务器收到一个没有身子的 GET，表现是莫名其妙的挂起或报错。
        """
        good = self.fake()
        server = self.fake(redirect_to=good.url)
        self.write_global({"web": {"type": "http", "url": server.url}})
        reg, _ = self.load()
        self.assertNotIn("web", reg.servers)
        self.assertIn("重定向", reg.errors["web"])

    def test_url得是http开头(self):
        self.write_global({"web": {"type": "http", "url": "file:///etc/passwd"}})
        reg, _ = self.load()
        self.assertIn("http/https", reg.errors["web"])

    def test_label只印url不印凭证(self):
        server = self.fake(api_key="s3cret")
        self.write_global({"web": {"type": "http", "url": server.url,
                                   "headers": {"x-api-key": "s3cret"}}})
        reg, _ = self.load()
        label = reg.servers["web"].label
        self.assertEqual(label, server.url)
        self.assertNotIn("s3cret", label)

    def test_关掉会去销会话(self):
        server = self.fake()
        self.write_global({"web": {"type": "http", "url": server.url}})
        reg, _ = self.load()
        reg.close()
        self.reg = None
        self.assertTrue(server.deleted)


class TestToolboxAndAgent(Base):
    def setUp(self):
        super().setUp()
        keys.STORE = os.path.join(self.tmp.name, "models.json")
        keys.STATE = os.path.join(self.tmp.name, "state.json")
        self.write_global({"fake": spec()})
        self.agent = Feiyu(self.root)
        self.agent.name = "fake"
        self.agent.mode = "auto"
        self.agent.backend = FakeBackend([])
        self.agent.load_mcp()
        self.reg = self.agent.toolbox.mcp

    def test_工具挂到后端_系统提示提到(self):
        names = [t["function"]["name"] for t in self.agent.backend.tools]
        self.assertIn("mcp_fake_echo", names)
        self.assertIn("read_file", names)
        self.assertIn("mcp_", self.agent.backend.system)

    def test_通过Toolbox调(self):
        out = self.agent.toolbox.run("mcp_fake_echo", {"text": "x"})
        self.assertFalse(out.is_error)
        self.assertIn("echo: x", out.content)
        self.assertIn("echo: x", out.summary)

    def test_只读模式只放自报只读的(self):
        self.agent.set_mode("readonly")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.agent._gate(call("a", "mcp_fake_echo", text="1"), False)[0])
            self.assertFalse(self.agent._gate(call("b", "mcp_fake_write_note", body="1"), False)[0])

    def test_权限规则写Mcp(self):
        self.assertEqual(permissions.subject("mcp_fake_write_note", {}), "fake write_note")
        self.assertEqual(permissions.suggest("mcp_fake_write_note", {}), "Mcp(fake:*)")
        self.assertTrue(permissions.matches("Mcp(fake:*)", "mcp_fake_write_note", "fake write_note"))
        self.assertFalse(permissions.matches("Mcp(other:*)", "mcp_fake_write_note", "fake write_note"))
        self.assertEqual(permissions.verdict("mcp_fake_write_note", {}), "ask")
        permissions.add_allow("Mcp(fake:*)")
        self.assertEqual(permissions.verdict("mcp_fake_write_note", {}), "allow")

    def test_default模式下非只读的要问_只读的不问(self):
        self.agent.set_mode("default")
        self.agent.approve = lambda name, args: "no"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.agent._gate(call("a", "mcp_fake_echo", text="1"), False)[0])
            self.assertFalse(self.agent._gate(call("b", "mcp_fake_write_note", body="1"), False)[0])

    def test_一轮端到端(self):
        self.agent.backend = FakeBackend([
            ("", [call("c1", "mcp_fake_echo", text="ping")]),
            ("好了", []),
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.send("试试")
        bodies = [m["content"] for m in self.agent.backend.messages if m.get("role") == "tool"]
        self.assertIn("echo: ping", bodies[0])


class TestMcpToggle(Base):
    """在面板里关掉一台 MCP 服务器：它的工具不再挂给模型

    **关掉不等于不起进程** —— 起不起写在 mcp.json 里，改了要重启。
    这儿关的是"要不要把工具挂出去"，面板上那句话也是这么说的。
    """

    def setUp(self):
        super().setUp()
        before = enabled.STORE
        enabled.STORE = os.path.join(self.tmp.name, "enabled.json")
        self.addCleanup(setattr, enabled, "STORE", before)
        keys.STORE = os.path.join(self.tmp.name, "models.json")
        keys.STATE = os.path.join(self.tmp.name, "state.json")
        self.write_global({"假": spec()})
        self.agent = Feiyu(self.root)
        self.agent.backend = FakeBackend([])
        self.agent.load_mcp()
        self.addCleanup(self.agent.toolbox.mcp.close)

    def names(self):
        return [t["name"] for t in self.agent.tool_schemas()]

    def test_默认挂着(self):
        self.assertTrue([n for n in self.names() if n.startswith("mcp_")])

    def test_关掉之后一个都不挂(self):
        enabled.set_on("mcp", "假", False)
        self.assertEqual([n for n in self.names() if n.startswith("mcp_")], [])
        self.assertIn("bash", self.names())          # 内置的不受影响

    def test_关掉之后系统提示里也不提它(self):
        self.agent._refresh_system()
        self.assertIn("假", self.agent.backend.system)
        enabled.set_on("mcp", "假", False)
        self.agent._refresh_system()
        self.assertNotIn("外部 MCP 服务器", self.agent.backend.system)
