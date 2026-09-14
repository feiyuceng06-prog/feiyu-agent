"""编辑器桥：改动先在编辑器里开成 diff，等人接受或拒绝，再决定写不写

为什么要有这一层：在编辑器里跑 agent 时，`preview()` 那十四行 +/- 是「看一眼」，
不是「看清楚」——一处跨三个函数的改动，靠终端面板里截断的 diff 判断不了该不该收。
装上桥之后同一次确认变成编辑器原生的左右对照，接受/拒绝在那边按。

## 为什么是 unix socket + 行 JSON，而不是 WebSocket

Claude Code 那套用 WebSocket（编辑器起服务、写 lock 文件、CLI 去连）。方向是对的，
协议不必照抄：本项目硬规矩是不引新依赖（只有 anthropic / pillow / prompt_toolkit），
手搓 RFC 6455 的帧和掩码换不来任何东西，而 unix socket 两边都是原生的
（nvim 有 `vim.uv`，VS Code 有 node 的 `net`）。行分隔 JSON 同理：
每行一个对象，`makefile()` 读一行就是一条消息，不用自己数长度。

## 方向：编辑器是 server，feiyu 是 client

编辑器活得比 agent 长，而且一个编辑器可能同时挂着好几个 agent 会话。
反过来（agent 起服务）的话编辑器得去轮询有没有新 agent，还要处理 agent 退出后
留下的死 socket。

## 发现

编辑器在 `$XDG_RUNTIME_DIR/feiyu/ide/` 下放一个 `<pid>.json`：

    {"socket": "/run/user/1000/feiyu/ide/1234.sock",
     "workspace": "/home/x/proj", "editor": "nvim", "pid": 1234}

feiyu 启动时扫这个目录，挑 **workspace 完全相同**（不是前缀，见 `_match`）
且进程还活着的那一个。多个匹配时取 mtime 最新的——那是你正在看的那个窗口。

## 一条铁律：桥坏了不能影响 agent

连不上、超时、编辑器崩了、回的不是 JSON —— 一律当「这次没有编辑器」，
退回终端里原来的确认。`ask()` 因此永远只返回 `str | None`，
`None` 的意思是「我没管这次，你按老路走」。编辑器那边卡住不答也不能把 agent
吊死：`WAIT` 到点就放手（默认十分钟，`FEIYU_IDE_TIMEOUT` 可调）。

`FEIYU_NO_IDE=1` 整个关掉这层。
"""

import json
import os
import select
import socket
import time

# 等人在编辑器里点的时间上限。人要读 diff，给得比一般的网络超时宽得多；
# 到点之后不是报错，是退回终端确认——真正的死法是这里等到天荒地老。
WAIT = float(os.environ.get("FEIYU_IDE_TIMEOUT") or 600)
# 连接和握手用的短超时：编辑器在就是本机 socket，毫秒级
CONNECT = 1.5
# 一条消息的字节上限。整份文件的新旧内容都要过这条线，放宽到 8 MB；
# 再大的文件在编辑器里做 diff 本身就没意义了
MAX_BYTES = 8 * 1024 * 1024


def lock_dir() -> str:
    """lock 文件放哪儿

    优先 `$XDG_RUNTIME_DIR`：它是 tmpfs、按用户隔离、登出自动清空，
    正好是「进程活着才有意义」的东西该待的地方。没有这个变量（一些容器、
    cron）才退回 `~/.local/state`，那边要自己处理陈旧文件。
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        base = os.path.join(
            os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
            "feiyu")
        return os.path.join(base, "ide")
    return os.path.join(base, "feiyu", "ide")


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _match(a: str, b: str) -> bool:
    """两个工作区算不算同一个

    必须是**同一个目录**，不是前缀包含：`~/proj` 的编辑器不该接管
    `~/proj-old` 里跑的 agent，那是另一个项目。realpath 让符号链接、
    尾斜杠、`.` 这些写法归一。
    """
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


def discover(workspace: str) -> list[dict]:
    """这个工作区里能用的编辑器，新的排前面 顺手清掉进程已经没了的 lock """
    folder = os.path.abspath(lock_dir())
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
            mtime = os.path.getmtime(path)
        except (OSError, ValueError):
            continue
        if not _alive(info.get("pid")):
            # 编辑器没了，socket 也不会有人 accept。留着只会让下一次白等一个超时。
            # socket 文件一起清掉：编辑器被 kill 时（VS Code 的 deactivate 和
            # nvim 的 VimLeavePre 都不会跑）它会留在这儿，一个进程一个，
            # 攒着虽然只占 inode 但看起来像还有人在听。
            sock = info.get("socket")
            if isinstance(sock, str) and os.path.dirname(os.path.abspath(sock)) == folder:
                # 只删自己这个目录里的 —— 路径是从文件里读出来的，别照着它删别处
                try:
                    os.unlink(sock)
                except OSError:
                    pass
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        if not _match(str(info.get("workspace") or ""), workspace):
            continue
        info["_lock"] = path
        info["_mtime"] = mtime
        found.append(info)
    found.sort(key=lambda x: x["_mtime"], reverse=True)
    return found


class Bridge:
    """一个连上的编辑器 所有方法出错都只是让这座桥失效，不往上抛"""

    def __init__(self, sock: socket.socket, info: dict):
        self.sock = sock
        self.info = info
        self.editor = str(info.get("editor") or "editor")
        self._buf = b""
        self._id = 0
        self.dead = False

    # ---------- 底层 ----------
    # ⚠️ 这里刻意**不用** `sock.makefile()`。它的缓冲读撞上 `settimeout` 会把文件
    # 对象留在不一致状态：第一次超时之后再 readline 直接抛 OSError，于是
    # 「等人在编辑器里点」这条路必然在第一个轮询周期就判定成「编辑器断了」。
    # 单测里假编辑器是立刻回话的，所以这个坑一条用例都抓不到 —— 是 nvim 那边
    # 真按了一次键才暴露的（每次都在 1.0 秒上返回 None）。
    # 自己按 select + recv 攒行，超时只是「这一轮没数据」，缓冲区一个字节不丢。
    def _send(self, obj: dict) -> bool:
        if self.dead:
            return False
        try:
            line = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(line) > MAX_BYTES:
                return False
            self.sock.sendall(line)
            return True
        except (OSError, ValueError):
            self.close()
            return False

    def _line(self, deadline: float):
        """攒出一整行 返回 bytes；None = 超时或者对端关了（后者会把桥标死）"""
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line, self._buf = self._buf[:nl], self._buf[nl + 1:]
                return line
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            # 分片等：一次挂十分钟的话，编辑器中途被 kill 也要等满十分钟才发现。
            # 每秒回来看一眼对端还在不在。
            try:
                ready, _, _ = select.select([self.sock], [], [], min(1.0, left))
            except (OSError, ValueError):
                self.close()
                return None
            if not ready:
                continue
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                self.close()
                return None
            if not chunk:          # 对端关了
                self.close()
                return None
            if len(self._buf) + len(chunk) > MAX_BYTES:
                # 对面在灌一条没完没了的消息，别把内存吃光
                self.close()
                return None
            self._buf += chunk

    def _recv(self, want_id: int, deadline: float):
        """等编号为 want_id 的回复

        编号要对：编辑器可能在回复之前先推几条自己的消息（状态、心跳），
        不比对的话会把它们当成答案。对不上的一律丢掉——这座桥是半双工的，
        feiyu 只问不听，没有需要留着的推送。
        """
        while not self.dead:
            line = self._line(deadline)
            if line is None:
                return None
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        return None

    def request(self, method: str, params: dict, wait: float = CONNECT):
        self._id += 1
        mine = self._id
        if not self._send({"id": mine, "method": method, "params": params}):
            return None
        msg = self._recv(mine, time.monotonic() + wait)
        if not msg:
            return None
        if msg.get("error"):
            return None
        result = msg.get("result")
        return result if isinstance(result, dict) else None

    def notify(self, method: str, params: dict) -> None:
        """不要回复的那种 编号给 0，编辑器认这个就是「别回」"""
        self._send({"id": 0, "method": method, "params": params})

    def close(self) -> None:
        self.dead = True
        try:
            self.sock.close()
        except OSError:
            pass

    # ---------- 上层：一次确认 ----------
    def ask_diff(self, path: str, old: str, new: str, label: str):
        """在编辑器里开 diff 等人决定

        返回 (答复, 新内容 或 None)：
          答复    "yes" / "no" / "always"，跟 `ui.confirm` 一套词，直接给 `_gate`
          新内容  人在 diff 里改过右边才有；`None` 表示原样接受
        整座桥出任何问题都返回 (None, None)，调用方退回终端确认。
        """
        got = self.request("open_diff", {
            "path": path, "old": old, "new": new, "label": label,
        }, wait=WAIT)
        if not got:
            return None, None
        action = str(got.get("action") or "")
        content = got.get("content")
        if not isinstance(content, str):
            content = None
        if action == "accept":
            return "yes", content
        if action == "accept_always":
            return "always", content
        if action == "reject":
            return "no", None
        return None, None

    def close_diff(self, path: str) -> None:
        self.notify("close_diff", {"path": path})


# ---------- 一次确认该不该交给编辑器 ----------

# 只有这两个工具有「整份新旧文本」可比。bash 改的东西、git commit 这些
# 没有 diff 可开，照旧在终端里确认。
DIFFABLE = ("write_file", "edit_file")


def _proposed(tools, name: str, args: dict, old: str):
    """模型这次想把文件变成什么样 算不出来就返回 None"""
    if name == "write_file":
        content = str(args.get("content") or "")
        return (old + content) if args.get("append") else content
    edits = args.get("edits")
    if not (isinstance(edits, list) and edits):
        edits = [{"old_str": args.get("old_str", ""),
                  "new_str": args.get("new_str", ""),
                  "replace_all": args.get("replace_all")}]
    updated = old
    for edit in edits:
        outcome = tools._apply_edit(updated, str(edit.get("old_str", "")),
                                   str(edit.get("new_str", "")),
                                   bool(edit.get("replace_all")))
        if isinstance(outcome, str):
            return None      # old_str 对不上之类 —— 这次调用本来就会失败，别开 diff
        updated = outcome[0]
    return updated


def _rewrite(name: str, args: dict, old: str, edited: str) -> None:
    """人在 diff 右边改过：让工具写下他看到的那份，而不是模型原来那份

    `write_file` 直接换 content。`edit_file` 换成一处「整份换整份」的编辑——
    old_str 就是当前全文，必然唯一匹配，`_apply_edit` 一次替换就完事，
    不用为此在工具里另开一条路径。这中间盘上的内容被别人改过的话 old_str 对不上，
    `edit_file` 会照常报「找不到 old_str」——那正是该有的行为，别绕过去。
    """
    if name == "write_file":
        args["content"] = edited
        args.pop("append", None)
        return
    args.pop("old_str", None)
    args.pop("new_str", None)
    args.pop("replace_all", None)
    args["edits"] = [{"old_str": old, "new_str": edited}]


def route(bridge, name: str, args: dict, root):
    """把一次确认交给编辑器

    返回 `ui.confirm` 那套答复（"yes" / "no" / "always"），
    `None` = 这次没交出去（不是可 diff 的工具、算不出新内容、桥出问题），
    调用方退回终端确认。
    """
    if bridge is None or bridge.dead or name not in DIFFABLE:
        return None
    from . import tools      # 用到时才 import：ide 在启动路径上，tools 是最大的一个模块
    try:
        target = (root / str(args.get("path") or "")).resolve()
    except (OSError, ValueError):
        return None
    old = ""
    if target.is_file():
        try:
            old = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # errors="replace" 会把解不出来的字节换成 \ufffd。原样接受没事（那时不回传
        # 内容），但人要是在 diff 里改过一个字就得整份写回去 —— 那会把文件里所有
        # 解不出来的字节永久换成替换字符。不是这层该冒的风险，退回终端确认。
        if "\x00" in old or "\ufffd" in old:
            return None
    elif target.exists():
        return None          # 目录 / FIFO 这些，交给工具自己去报错
    new = _proposed(tools, name, args, old)
    if new is None or new == old:
        return None

    try:
        shown = str(target.relative_to(root))
    except ValueError:
        shown = str(target)
    answer, edited = bridge.ask_diff(str(target), old, new,
                                     "%s · %s" % (name, shown))
    bridge.close_diff(str(target))
    if answer is None:
        return None
    # `ask_diff` 已经保证拒绝时 edited 是 None，这里再判一次是写文件路径上的
    # 冗余保险：那一层将来改坏了，不该由「照着人没同意的内容写盘」来兑现。
    if answer in ("yes", "always") and edited is not None and edited != new:
        if name == "write_file" or old:
            _rewrite(name, args, old, edited)
    return answer


def connect(workspace: str):
    """挑一个编辑器连上 没有 / 连不上就返回 None，调用方照旧走终端"""
    if os.environ.get("FEIYU_NO_IDE"):
        return None
    for info in discover(workspace):
        target = info.get("socket")
        if not target or not isinstance(target, str):
            continue
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(CONNECT)
            sock.connect(target)
        except OSError:
            continue
        bridge = Bridge(sock, info)
        # 握一次手再用：lock 文件在、进程也在，但 socket 可能是上一次会话留下的
        # （编辑器重启而 pid 复用）。ping 通了才算真的连上。
        if bridge.request("ping", {}) is None:
            bridge.close()
            continue
        return bridge
    return None
