"""加模型时现场拉的那张模型清单（providers.list_models + serve 的 model_list）

用一个绑在 127.0.0.1 临时端口上的假接口，不出网。要守住的三件事：
请求头对得上两种接口；**不跟重定向**（跟过去会把 key 带到别的 host 上）；
存着的 key 只发给它原来的那个地址。
"""

import http.server
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from feiyu import enabled, keys, permissions, providers, serve
from feiyu import tools as tools_mod
from feiyu.agent import Feiyu


class _Api(http.server.BaseHTTPRequestHandler):
    seen = []          # (path, 头) —— 测试里看它收到了什么

    def log_message(self, *args):
        pass

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        port = self.server.server_address[1]
        if self.path == "/v1/models":
            self._json(200, {"data": [{"id": "甲/a"}, {"id": "乙/b", "context_length": 8192},
                                      {"id": "甲/a"}, {"id": ""}, {"name": "没有 id"},
                                      {"id": "丙", "context_length": True}]})
        elif self.path == "/anth/v1/models?limit=1000":
            self._json(200, {"data": [{"id": "claude-x", "type": "model"}]})
        elif self.path == "/bad/v1/models":
            self._json(401, {"error": "invalid key"})
        elif self.path == "/nope/v1/models":
            self._json(404, {"error": "not found"})
        elif self.path == "/empty/v1/models":
            self._json(200, {"data": []})
        elif self.path == "/html/v1/models":
            raw = b"<html>login</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == "/hop/v1/models":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/evil/v1/models" % port)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json(200, {"data": [{"id": "不该到这儿"}]})


class Base(unittest.TestCase):
    def setUp(self):
        # 本机常年带着代理变量，不摘的话连 127.0.0.1 也会被送去代理
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in list(os.environ):
            if name.lower().endswith("_proxy"):
                del os.environ[name]
        _Api.seen = []
        self.api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
        threading.Thread(target=self.api.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        self.addCleanup(self.api.server_close)
        self.addCleanup(self.api.shutdown)
        self.base = "http://127.0.0.1:%d" % self.api.server_address[1]


class TestListModels(Base):
    def test_openai_兼容的头和清单(self):
        got = providers.list_models("openai", self.base + "/v1/", "sk-a1")
        self.assertEqual(got, [{"id": "甲/a"}, {"id": "乙/b", "context": 8192}, {"id": "丙"}])
        path, head = _Api.seen[-1]
        self.assertEqual(path, "/v1/models")
        self.assertEqual(head["authorization"], "Bearer sk-a1")

    def test_没有_key_也能问_不带那个头(self):
        """OpenRouter 的 /models 是公开的，没填 key 也该能列 """
        providers.list_models("openai", self.base + "/v1", "")
        self.assertNotIn("authorization", _Api.seen[-1][1])

    def test_anthropic_的头和地址(self):
        got = providers.list_models("anthropic", self.base + "/anth", "sk-b2")
        self.assertEqual(got, [{"id": "claude-x"}])
        head = _Api.seen[-1][1]
        self.assertEqual(head["x-api-key"], "sk-b2")
        self.assertEqual(head["anthropic-version"], "2023-06-01")
        self.assertNotIn("authorization", head)

    def test_地址拼法(self):
        self.assertEqual(providers.models_url("anthropic", ""),
                         "https://api.anthropic.com/v1/models?limit=1000")
        self.assertEqual(providers.models_url("anthropic", "https://x/v1/"),
                         "https://x/v1/models?limit=1000")
        self.assertEqual(providers.models_url("openai", ""), "https://api.openai.com/v1/models")
        self.assertEqual(providers.models_url("openai", "https://api.deepseek.com/"),
                         "https://api.deepseek.com/models")

    def test_不跟重定向_key_不会被带过去(self):
        with self.assertRaises(ValueError) as caught:
            providers.list_models("openai", self.base + "/hop/v1", "sk-secret")
        self.assertIn("/evil/v1/models", str(caught.exception))
        self.assertNotIn("/evil/v1/models", [p for p, _ in _Api.seen])

    def test_报错说人话而且不带_key(self):
        for where, word in (("/bad/v1", "key 不对"), ("/nope/v1", "/models"),
                            ("/empty/v1", "直接填模型 id"), ("/html/v1", "不是 JSON")):
            with self.subTest(where=where):
                with self.assertRaises(ValueError) as caught:
                    providers.list_models("openai", self.base + where, "sk-secret")
                self.assertIn(word, str(caught.exception))
                self.assertNotIn("sk-secret", str(caught.exception))

    def test_key_里混进全角字符先说(self):
        for bad in ("sk-a1\u3000", "sk-，a1", "sk a1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                providers.list_models("openai", self.base + "/v1", bad)
            self.assertIn("复制", str(caught.exception))
        self.assertEqual(_Api.seen, [])

    def test_地址不是_http_就不发(self):
        for bad in ("file:///etc/passwd", "ftp://x/v1", "api.deepseek.com"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                providers.list_models("openai", bad, "sk-secret")
        self.assertEqual(_Api.seen, [])

    def test_连不上(self):
        self.api.shutdown()
        self.api.server_close()
        with self.assertRaises(ValueError) as caught:
            providers.list_models("openai", self.base + "/v1", "sk")
        self.assertIn("连不上", str(caught.exception))


class TestServeModelList(Base):
    class Box(serve.Server):
        def __init__(self, agent, out, *, trusted):
            self.agent = agent
            self.trusted = trusted
            self.busy = False
            self.commands = {}
            self._level = None
            self.workspace = agent.toolbox.root
            self.proto = serve.Protocol(out)

        def push_status(self): pass
        def push_settings(self): pass
        def push_help(self): pass

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for mod, attr in ((keys, "STORE"), (keys, "STATE"), (permissions, "STORE"),
                          (enabled, "STORE"), (tools_mod, "IMAGE_CONFIG")):
            before = getattr(mod, attr)
            setattr(mod, attr, os.path.join(self.tmp.name, attr.lower() + ".json"))
            self.addCleanup(setattr, mod, attr, before)
        self.agent = Feiyu(Path(self.tmp.name))
        self.out = io.StringIO()

    def events(self, kind):
        got = [json.loads(l) for l in self.out.getvalue().splitlines() if l.strip()]
        return [e for e in got if e.get("kind") == kind]

    def test_拉到清单_原样带回编号(self):
        box = self.Box(self.agent, self.out, trusted=True)
        box.model_list({"ticket": 7, "kind": "openai", "base_url": self.base + "/v1", "key": "sk-a1"})
        reply = self.events("model_list")[-1]
        self.assertEqual(reply["ticket"], 7)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["total"], 3)
        self.assertEqual([r["id"] for r in reply["rows"]], ["甲/a", "乙/b", "丙"])
        self.assertNotIn("sk-a1", self.out.getvalue())

    def test_浏览器入口不替你出去问(self):
        box = self.Box(self.agent, self.out, trusted=False)
        box.model_list({"ticket": 1, "kind": "openai", "base_url": self.base + "/v1", "key": "sk"})
        self.assertFalse(self.events("model_list")[-1]["ok"])
        self.assertEqual(_Api.seen, [])

    def test_存着的_key_只发给原来那个地址(self):
        box = self.Box(self.agent, self.out, trusted=True)
        keys.add("甲", base_url=self.base + "/v1", model="甲/a", key="sk-stored", kind="openai")
        box.model_list({"ticket": 2, "kind": "openai", "base_url": self.base + "/v1", "name": "甲"})
        self.assertTrue(self.events("model_list")[-1]["ok"])
        self.assertEqual(_Api.seen[-1][1]["authorization"], "Bearer sk-stored")

        # 表单里把地址改去了别处：存着的 key 不带过去，一个请求都不发
        _Api.seen = []
        box.model_list({"ticket": 3, "kind": "openai", "base_url": "http://localhost:9/v1", "name": "甲"})
        reply = self.events("model_list")[-1]
        self.assertFalse(reply["ok"])
        self.assertIn("重新填", reply["error"])
        self.assertEqual(_Api.seen, [])

    def test_anthropic_没填_key_先说(self):
        box = self.Box(self.agent, self.out, trusted=True)
        box.model_list({"ticket": 4, "kind": "anthropic", "base_url": self.base + "/anth"})
        reply = self.events("model_list")[-1]
        self.assertFalse(reply["ok"])
        self.assertIn("key", reply["error"])
        self.assertEqual(_Api.seen, [])

    def test_saved_key_requires_same_origin_and_provider(self):
        box = self.Box(self.agent, self.out, trusted=True)
        old = {"kind": "openai", "base_url": "https://example.test/v1", "key": "fake-stored"}
        for base, kind in (("http://example.test/v1", "openai"),
                           ("https://example.test:8443/v1", "openai"),
                           ("https://other.test/v1", "openai"),
                           ("https://example.test/v1", "anthropic"),
                           ("https://example.test:bad/v1", "openai")):
            with self.subTest(base=base, kind=kind), mock.patch.object(keys, "load", return_value={"demo": old}), \
                    mock.patch.object(providers, "list_models") as request:
                box.model_list({"name": "demo", "base_url": base, "kind": kind})
                request.assert_not_called()
                self.assertFalse(self.events("model_list")[-1]["ok"])

    def test_same_origin_normalizes_default_port_and_hostname(self):
        box = self.Box(self.agent, self.out, trusted=True)
        old = {"kind": "openai", "base_url": "https://EXAMPLE.test/v1", "key": "fake-stored"}
        with mock.patch.object(keys, "load", return_value={"demo": old}), \
                mock.patch.object(providers, "list_models", return_value=[{"id": "demo"}]) as request:
            box.model_list({"name": "demo", "base_url": "https://example.test:443/v1", "kind": "openai"})
            request.assert_called_once_with("openai", "https://example.test:443/v1", "fake-stored")
            self.assertTrue(self.events("model_list")[-1]["ok"])

    def test_saved_default_endpoint_does_not_follow_changed_provider(self):
        old = {"kind": "anthropic", "base_url": None, "key": "fake-stored"}
        self.assertTrue(serve._can_reuse_key(old, "anthropic", "https://api.anthropic.com:443"))
        self.assertFalse(serve._can_reuse_key(old, "openai", ""))

    def test_save_cannot_rebind_saved_key_to_new_origin(self):
        box = self.Box(self.agent, self.out, trusted=True)
        old = {"kind": "openai", "base_url": "https://example.test/v1", "key": "fake-stored"}
        for base, kind in (("http://example.test/v1", "openai"),
                           ("https://other.test/v1", "openai"),
                           ("https://example.test/v1", "anthropic")):
            with self.subTest(base=base, kind=kind), mock.patch.object(keys, "load", return_value={"demo": old}), \
                    mock.patch.object(keys, "add") as save:
                box.key_write({"name": "demo", "model": "model", "kind": kind, "base_url": base, "key": ""})
                save.assert_not_called()
                self.assertEqual(self.events("note")[-1]["level"], "error")

    def test_price_edit_preserves_key_and_explicit_new_key_allows_new_origin(self):
        box = self.Box(self.agent, self.out, trusted=True)
        old = {"kind": "openai", "base_url": "https://example.test/v1", "key": "fake-stored"}
        for row, expected in (({"price": [0, 1, 2]}, "fake-stored"),
                              ({"base_url": "https://other.test/v1", "key": "fake-new"}, "fake-new")):
            with self.subTest(row=row), mock.patch.object(keys, "load", return_value={"demo": old}), \
                    mock.patch.object(keys, "add") as save, mock.patch.object(self.agent, "use_model"), \
                    mock.patch.object(box, "push_models"):
                box.key_write({"name": "demo", "model": "model", **row})
                self.assertEqual(save.call_args.kwargs["key"], expected)

    def test_畸形参数不抛(self):
        """没给地址的几种会落到官方默认地址 —— 指到本地，**用例不许出网**
        （第一版没指，每条都真去连 api.openai.com，被墙了就各等满 15 秒）"""
        for name in ("OPENAI_BASE", "ANTHROPIC_BASE"):
            patch = mock.patch.object(providers, name, self.base + "/nope/v1")
            patch.start()
            self.addCleanup(patch.stop)
        box = self.Box(self.agent, self.out, trusted=True)
        for msg in ({}, {"ticket": "x", "kind": ["openai"], "base_url": 3, "key": {"a": 1}},
                    {"ticket": None, "name": ["甲"]}, {"base_url": "javascript:alert(1)"}):
            with self.subTest(msg=msg):
                box.model_list(msg)
                self.assertFalse(self.events("model_list")[-1]["ok"])

    def test_没模型时桌面端指去设置_浏览器入口指去终端(self):
        self.assertFalse(self.agent.ready)
        self.Box(self.agent, self.out, trusted=True).handle_prompt("你好")
        note = self.events("note")[-1]
        self.assertEqual(note.get("act"), "models")
        self.assertIn("设置", note["text"])
        self.assertNotIn("终端", note["text"])
        self.Box(self.agent, self.out, trusted=False).handle_prompt("你好")
        note = self.events("note")[-1]
        self.assertNotIn("act", note)
        self.assertIn("终端", note["text"])


if __name__ == "__main__":
    unittest.main()
