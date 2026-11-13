"""最小的 LSP 客户端：只做「定义在哪」和「谁引用了它」

search 是文本匹配，「谁调用了这个函数」会把同名的变量、注释、字符串全算上，
跨文件改名靠它就是漏一处多一处 语言服务器是按语义找的 这里只实现
definition / references 两个请求，stdio JSON-RPC，不引依赖
"""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from .pipe import PipeReader

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
# 推送式诊断最多等这么久 服务器分析要一点时间，但这是**卡在写文件之后**的，
# 等太久每次改文件都要顿一下；等太短就收不到 实测 pyright 半秒够
# 一个文件最多报几条 前几条通常就够定位了，全报会把工具结果撑爆
# 骨架最多列几项 大文件（本仓库 tools.py 是 147 项）全列也就几百行，
# 但几万行的文件要封顶，否则骨架自己就成了那个撑爆上下文的东西
MAX_OUTLINE = 200


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
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    return Path(url2pathname(urlparse(uri).path))


class Client:
    """一个语言服务器进程 请求是同步的，中间收到服务器发来的请求一律回空 """

    def __init__(self, argv: list[str], root: Path):
        self.argv = argv
        self.root = root
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=root, start_new_session=True)
        self.reader = PipeReader(self.proc.stdout)
        self.lock = threading.Lock()
        self.buf = b""
        self.seq = 0
        self.opened: dict[str, int] = {}     # uri -> version
        # 服务器推过来的诊断 uri -> [诊断]
        # **原来这些是被丢掉的**：`request()` 的循环只认「服务器的请求」和
        # 「自己那条回复」，而 publishDiagnostics 有 method 没有 id，两个分支
        # 都不match，被 while 吃掉。改完代码有没有语法错、用没用错名字，
        # 本来这一刻就知道，却要等模型自己去跑一遍测试才发现
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
        chunk = self.reader.read(deadline)
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
                elif "method" in msg:
                    # 通知（有 method 没有 id）：诊断、进度、日志 收下**丢掉**。
                    # 要紧的是把它从管道里读走 —— 不读的话它一直卡在这个 while 里，
                    # 自己那条回复永远等不到（0.68.0 之前就是这样，两个分支都不 match，
                    # 被 while True 吃掉）
                    pass
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
                # 声明了才会推给我们；`diagnostic` 那条是 3.17 的拉取式
                "publishDiagnostics": {},
                "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                "diagnostic": {"dynamicRegistration": False},
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

    SYMBOL_KIND = {5: "class", 6: "method", 12: "function", 13: "变量", 14: "常量",
                   11: "interface", 23: "struct", 10: "enum", 8: "field", 9: "构造"}
    # 骨架里只留"结构"。**参数和局部变量一律不要** —— pyright 把它们也报成
    # symbol（kind 13），一个几千行的文件里那是几百条噪音，正好淹掉要找的东西
    OUTLINE_KINDS = frozenset({5, 6, 12, 11, 23, 10, 9})
    # 模块层的常量留着（配置、上限这类是找代码时的路标），嵌套里的不留
    OUTLINE_TOP_ONLY = frozenset({13, 14, 8})

    def outline(self, path: Path, text: str) -> list[dict]:
        """文件的骨架：类、函数、方法 + 它们在第几行

        **一个几千行的文件，整份读进来是上万 token，而且每一轮都要重发。**
        骨架只有几十行，看完再用 read_file 的 offset/limit 读那一段 ——
        这正是系统提示里"大文件先定位再读"那条一直缺的工具。

        两种返回形状都认：新的 `DocumentSymbol`（带 children 的树）和
        老的 `SymbolInformation`（拍平的、位置在 location 里）。
        """
        self.open(path, text)
        got = self.request("textDocument/documentSymbol",
                           {"textDocument": {"uri": _uri(path)}})
        if not isinstance(got, list):
            return []
        out: list[dict] = []

        def walk(items, depth):
            for item in items:
                if not isinstance(item, dict):
                    continue
                rng = item.get("selectionRange") or item.get("range")
                if rng is None:
                    rng = ((item.get("location") or {}).get("range") or {})
                line = ((rng or {}).get("start") or {}).get("line")
                if line is None:
                    continue
                kind = item.get("kind")
                keep = kind in self.OUTLINE_KINDS or (
                    depth == 0 and kind in self.OUTLINE_TOP_ONLY)
                if keep:
                    out.append({
                        "name": str(item.get("name") or ""),
                        "kind": self.SYMBOL_KIND.get(kind, ""),
                        "line": int(line) + 1,      # 给人和模型看的行号从 1 起
                        "depth": depth,
                    })
                kids = item.get("children")
                if isinstance(kids, list):
                    # 层级按**留下来的**那些算，不然过滤完缩进会是错的
                    walk(kids, depth + 1 if keep else depth)

        walk(got, 0)
        out.sort(key=lambda x: x["line"])
        return out

    def close(self) -> None:
        if hasattr(self, "reader"):
            self.reader.close()
        try:
            self.proc.kill()
        except OSError:
            pass
        # **光 kill 不够，还得收尸、关管道。** 不 wait 的话它挂成僵尸，stdin / stdout
        # 两根管道一直开着 —— 退出时无所谓，可 `--serve` 一跑一整天，语言服务器
        # 死一次重起一次（`Pool.get`），每次漏一对 fd
        try:
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for pipe in (self.proc.stdin, self.proc.stdout):
            try:
                if pipe:
                    pipe.close()
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
                client.close()     # 死了就重起一个，旧的那对管道先关掉
                client = None
            if client is None:
                client = Client(argv, self.root)
                self.clients[key] = client
            return client

    SYMBOL_KIND = {5: "class", 6: "method", 12: "function", 13: "变量", 14: "常量",
                   11: "interface", 23: "struct", 10: "enum", 8: "field", 9: "构造"}
    # 骨架里只留"结构"。**参数和局部变量一律不要** —— pyright 把它们也报成
    # symbol（kind 13），一个几千行的文件里那是几百条噪音，正好淹掉要找的东西
    OUTLINE_KINDS = frozenset({5, 6, 12, 11, 23, 10, 9})
    # 模块层的常量留着（配置、上限这类是找代码时的路标），嵌套里的不留
    OUTLINE_TOP_ONLY = frozenset({13, 14, 8})

    def outline(self, path: Path, text: str) -> list[dict]:
        """文件的骨架：类、函数、方法 + 它们在第几行

        **一个几千行的文件，整份读进来是上万 token，而且每一轮都要重发。**
        骨架只有几十行，看完再用 read_file 的 offset/limit 读那一段 ——
        这正是系统提示里"大文件先定位再读"那条一直缺的工具。

        两种返回形状都认：新的 `DocumentSymbol`（带 children 的树）和
        老的 `SymbolInformation`（拍平的、位置在 location 里）。
        """
        self.open(path, text)
        got = self.request("textDocument/documentSymbol",
                           {"textDocument": {"uri": _uri(path)}})
        if not isinstance(got, list):
            return []
        out: list[dict] = []

        def walk(items, depth):
            for item in items:
                if not isinstance(item, dict):
                    continue
                rng = item.get("selectionRange") or item.get("range")
                if rng is None:
                    rng = ((item.get("location") or {}).get("range") or {})
                line = ((rng or {}).get("start") or {}).get("line")
                if line is None:
                    continue
                kind = item.get("kind")
                keep = kind in self.OUTLINE_KINDS or (
                    depth == 0 and kind in self.OUTLINE_TOP_ONLY)
                if keep:
                    out.append({
                        "name": str(item.get("name") or ""),
                        "kind": self.SYMBOL_KIND.get(kind, ""),
                        "line": int(line) + 1,      # 给人和模型看的行号从 1 起
                        "depth": depth,
                    })
                kids = item.get("children")
                if isinstance(kids, list):
                    # 层级按**留下来的**那些算，不然过滤完缩进会是错的
                    walk(kids, depth + 1 if keep else depth)

        walk(got, 0)
        out.sort(key=lambda x: x["line"])
        return out

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
