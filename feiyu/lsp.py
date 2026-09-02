"""最小的 LSP 客户端：只做「定义在哪」和「谁引用了它」

search 是文本匹配，「谁调用了这个函数」会把同名的变量、注释、字符串全算上，
跨文件改名靠它就是漏一处多一处 语言服务器是按语义找的 这里只实现
definition / references 两个请求，stdio JSON-RPC，不引依赖
"""

import json
import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path

# 按扩展名选服务器 这是工具表不是模型表：这几个的命令行多年没变过
SERVERS = {
    ".py": ["pyright-langserver", "--stdio"],
    ".pyi": ["pyright-langserver", "--stdio"],
    ".c": ["clangd"], ".h": ["clangd"], ".cc": ["clangd"], ".cpp": ["clangd"], ".hpp": ["clangd"],
    ".rs": ["rust-analyzer"],
    ".lua": ["lua-language-server"],
    ".ts": ["typescript-language-server", "--stdio"],
    ".tsx": ["typescript-language-server", "--stdio"],
    ".js": ["typescript-language-server", "--stdio"],
    ".go": ["gopls"],
}
# nvim 的 mason 把服务器装在这里，不在 PATH 上
EXTRA_BIN = [Path.home() / ".local" / "share" / "nvim" / "mason" / "bin"]
REQUEST_TIMEOUT = 20
# 刚起来的服务器要先分析一遍项目，第一次请求给它多等一会儿
FIRST_TIMEOUT = 45
# 刚起来的服务器还没把整个目录分析完，这时候问 references 只会报当前文件里的
# 实测 pyright 0 秒时 1 处、0.5 秒后 3 处 等一下再问，漏引用比慢一秒糟得多
WARMUP = 1.0
MAX_RESULTS = 100


def which(command: str) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    for base in EXTRA_BIN:
        candidate = base / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def server_for(path: Path) -> list[str] | None:
    """这个文件该用哪个服务器 装了才算，没装返回 None """
    argv = SERVERS.get(path.suffix.lower())
    if not argv:
        return None
    exe = which(argv[0])
    return [exe] + argv[1:] if exe else None


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse
    return Path(unquote(urlparse(uri).path))


class Client:
    """一个语言服务器进程 请求是同步的，中间收到服务器发来的请求一律回空 """

    def __init__(self, argv: list[str], root: Path):
        self.argv = argv
        self.root = root
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=root, start_new_session=True)
        self.lock = threading.Lock()
        self.buf = b""
        self.seq = 0
        self.opened: dict[str, int] = {}     # uri -> version
        self.warm = False
        self._initialize()

    # ---- 传输 ----
    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def _read(self, deadline: float) -> dict | None:
        """自己维护缓冲、直接 os.read 不能用 BufferedReader：它会把 fd 上的数据
        整段吸进自己的缓冲区，之后 select 看 fd 是空的就一直等，明明消息已经到了 """
        while b"\r\n\r\n" not in self.buf:
            if not self._fill(deadline):
                return None
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        while len(self.buf) < length:
            if not self._fill(deadline):
                return None
        body, self.buf = self.buf[:length], self.buf[length:]
        try:
            return json.loads(body)
        except ValueError:
            return {}

    def _fill(self, deadline: float) -> bool:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        fd = self.proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            return False
        chunk = os.read(fd, 65536)
        if not chunk:
            return False
        self.buf += chunk
        return True

    def request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT):
        """发一个请求等它的回复 返回 result；超时或出错抛 RuntimeError """
        with self.lock:
            self.seq += 1
            mine = self.seq
            self._send({"jsonrpc": "2.0", "id": mine, "method": method, "params": params})
            deadline = time.monotonic() + timeout
            while True:
                msg = self._read(deadline)
                if msg is None:
                    raise RuntimeError("语言服务器 %d 秒没回复" % timeout)
                if "id" in msg and "method" in msg:
                    # 服务器反过来问我们（注册能力、要配置）：一律回空，别让它等
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
                elif msg.get("id") == mine:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"].get("message") or msg["error"]))
                    return msg.get("result")

    def notify(self, method: str, params: dict) -> None:
        with self.lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ---- 生命周期 ----
    def _initialize(self) -> None:
        self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": _uri(self.root),
            "workspaceFolders": [{"uri": _uri(self.root), "name": self.root.name}],
            "capabilities": {"textDocument": {
                "definition": {"linkSupport": False},
                "references": {},
            }},
        }, timeout=FIRST_TIMEOUT)
        self.notify("initialized", {})

    def open(self, path: Path, text: str) -> None:
        """每次请求前把文件当前内容喂过去 版本号递增，服务器只信最新那份 """
        uri = _uri(path)
        version = self.opened.get(uri, 0) + 1
        language = {".py": "python", ".pyi": "python", ".rs": "rust", ".lua": "lua",
                    ".ts": "typescript", ".tsx": "typescriptreact", ".js": "javascript",
                    ".go": "go"}.get(path.suffix.lower(), "cpp")
        if uri in self.opened:
            self.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}]})
        else:
            self.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": language, "version": version, "text": text}})
        self.opened[uri] = version

    def locate(self, kind: str, path: Path, line: int, column: int) -> list[dict]:
        """kind 是 definition / references，行列都是 0 起始 """
        params = {"textDocument": {"uri": _uri(path)},
                  "position": {"line": line, "character": column}}
        if kind == "references":
            params["context"] = {"includeDeclaration": True}
        timeout = REQUEST_TIMEOUT if self.warm else FIRST_TIMEOUT
        if not self.warm and kind == "references":
            time.sleep(WARMUP)
        result = self.request("textDocument/" + kind, params, timeout=timeout)
        self.warm = True
        if result is None:
            return []
        if isinstance(result, dict):
            result = [result]
        spots = []
        for item in result:
            uri = item.get("uri") or item.get("targetUri")
            rng = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
            if uri and rng:
                spots.append({"path": _path(uri), "line": rng["start"]["line"],
                              "column": rng["start"]["character"]})
        return spots

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class Pool:
    """按服务器命令缓存 Client 一个 Toolbox 一份，退出时全部杀掉 """

    def __init__(self, root: Path):
        self.root = root
        self.clients: dict[str, Client] = {}
        self.lock = threading.Lock()

    def get(self, path: Path) -> Client | None:
        argv = server_for(path)
        if not argv:
            return None
        key = argv[0]
        with self.lock:
            client = self.clients.get(key)
            if client is not None and client.proc.poll() is not None:
                client = None      # 死了就重起一个
            if client is None:
                client = Client(argv, self.root)
                self.clients[key] = client
            return client

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()


def find_position(text: str, name: str, line: int | None) -> tuple[int, int] | None:
    """在文件里找 name 出现的位置（0 起始的行列） 给了行号就只看那一行，
    否则取第一个整词匹配 —— 模型报得出名字和大概行号，报不准列号 """
    import re
    pattern = re.compile(r"(?<![\w])%s(?![\w])" % re.escape(name))
    lines = text.splitlines()
    rows = [line - 1] if line else range(len(lines))
    for i in rows:
        if 0 <= i < len(lines):
            hit = pattern.search(lines[i])
            if hit:
                return i, hit.start()
    return None
