"""测试用的假 MCP 服务器：streamable-http 只跑在 127.0.0.1 的临时端口上，不出网

两种回复形状都能出（`flavor="json"` / `"sse"`），因为客户端两种都要认 ——
Reactive Resume 开了 enableJsonResponse 回 JSON，多数服务器默认回 SSE
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {"name": "echo", "description": "原样返回 text", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "fail", "description": "总是报错", "inputSchema": {"type": "object", "properties": {}}},
]
SESSION = "sess-1"


class Fake:
    """起一个后台线程跑的服务器 `url` 拿去写 mcp.json，`seen` 是收到过的请求头 """

    def __init__(self, *, flavor="json", api_key=None, redirect_to=None):
        self.flavor = flavor
        self.api_key = api_key          # 不是 None 就要求 x-api-key 对得上
        self.redirect_to = redirect_to  # 不是 None 就一律 302 到这儿（试重定向）
        self.seen: list[dict] = []     # 收到过的请求头，键一律小写
        self.deleted = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass                     # 别往测试输出里刷访问日志

            def _send(self, code, body=b"", kind=None):
                self.send_response(code)
                if kind:
                    self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                if outer.state.get("session"):
                    self.send_header("mcp-session-id", outer.state["session"])
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_DELETE(self):
                outer.deleted = True
                self._send(200)

            def do_POST(self):
                if outer.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                # HTTP 头大小写不敏感，urllib 发出来是 X-api-key —— 记之前先抹平，
                # 否则断言按字面量取就会看不见一个明明送到了的头
                outer.seen.append({k.lower(): v for k, v in self.headers.items()})
                if outer.api_key is not None and self.headers.get("x-api-key") != outer.api_key:
                    self._send(401, b'{"error":"bad key"}', "application/json")
                    return
                size = int(self.headers.get("Content-Length") or 0)
                msg = json.loads(self.rfile.read(size) or b"{}")
                if "id" not in msg:
                    self._send(202)      # 通知：收下，没身子
                    return
                out = {"jsonrpc": "2.0", "id": msg["id"], "result": outer.result(msg)}
                blob = json.dumps(out).encode()
                if outer.flavor == "sse":
                    self._send(200, b"event: message\ndata: " + blob + b"\n\n", "text/event-stream")
                else:
                    self._send(200, blob, "application/json")

        self.state: dict = {}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def result(self, msg):
        method = msg.get("method")
        if method == "initialize":
            self.state["session"] = SESSION      # 从这条起带 session id
            return {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-http", "version": "0"}}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            if name == "fail":
                return {"content": [{"type": "text", "text": "炸了"}], "isError": True}
            text = ((msg.get("params") or {}).get("arguments") or {}).get("text", "")
            return {"content": [{"type": "text", "text": text}]}
        return {}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
