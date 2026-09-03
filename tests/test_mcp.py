"""MCP 客户端 真起一个假服务器（tests/mcp_server.py），不打网络 """

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from feiyu import keys, mcp, permissions
from feiyu.agent import Feiyu
from feiyu.tools import Toolbox
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
        self.assertIn("fake（4 个工具）", note)
        self.assertEqual(set(reg.tools), {"mcp_fake_echo", "mcp_fake_write_note", "mcp_fake_fail", "mcp_fake_slow"})
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
        self.assertIn("只支持 stdio", reg.errors["web"])
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

    def test_关掉之后进程没了(self):
        self.write_global({"fake": spec()})
        reg, _ = self.load()
        proc = reg.servers["fake"].proc
        reg.close()
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())
        self.reg = None


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
