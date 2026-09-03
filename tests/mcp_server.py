"""测试用的假 MCP 服务器：stdio、一行一个 JSON 四个工具够覆盖正常 / 出错 / 超时 / 只读 """
import json
import sys
import time

TOOLS = [
    {"name": "echo", "description": "原样返回 text", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "write_note", "description": "假装写了点东西",
     "inputSchema": {"type": "object", "properties": {"body": {"type": "string"}}}},
    {"name": "fail", "description": "总是报错", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "slow", "description": "睡两秒", "inputSchema": {"type": "object", "properties": {}}},
]


def reply(msg, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg["id"]}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    asked_roots = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if "id" not in msg:
            continue
        if method == "initialize":
            reply(msg, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "0"}})
        elif method == "tools/list":
            reply(msg, {"tools": TOOLS})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments") or {}
            if name == "echo":
                if not asked_roots:
                    # 反过来问一次客户端，验证它会回答而不是卡死
                    asked_roots = True
                    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": "srv1", "method": "roots/list"}) + "\n")
                    sys.stdout.flush()
                reply(msg, {"content": [{"type": "text", "text": "echo: " + str(args.get("text", ""))}]})
            elif name == "write_note":
                reply(msg, {"content": [{"type": "text", "text": "写好了"}]})
            elif name == "fail":
                reply(msg, {"content": [{"type": "text", "text": "坏了"}], "isError": True})
            elif name == "slow":
                time.sleep(2)
                reply(msg, {"content": [{"type": "text", "text": "醒了"}]})
            else:
                reply(msg, error={"code": -32602, "message": "没有这个工具"})
        else:
            reply(msg, error={"code": -32601, "message": "不支持"})


if __name__ == "__main__":
    main()
