"""编辑器桥 重点是两件事：

1. **桥坏了不能影响 agent。** 每一种坏法（没 lock / 进程死了 / socket 连不上 /
   回的不是 JSON / 编辑器不答 / 中途断开）都必须让 `route()` 返回 None，
   调用方于是退回终端确认。这类退路只在真出事时才走，最容易写完就没人验过。
2. **「改完再接受」要落到工具的入参上。** 人在 diff 右边改了字，写进文件的
   必须是他看到的那份，不是模型原来那份。
"""

import itertools
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from feiyu import ide


_SEQ = itertools.count()


class Server:
    """一个假编辑器：在临时 socket 上应答，按脚本回复

    脚本是 method -> 回复 dict（或者一个 callable 拿 params 现算）。
    回复给 None 表示**收下但不答**，用来测超时那条路。
    """

    def __init__(self, folder, workspace, script, editor="fake", write_lock=True,
                 noise=False, delay=0.0):
        # 编号是为了「编辑器重启」那种用例：同一个测试里前后两个假编辑器，
        # 名字撞上的话第二个 bind 会报 EADDRINUSE（第一个的 socket 文件还在）
        self.path = os.path.join(folder, "s%d.sock" % next(_SEQ))
        self.script = script
        self.noise = noise      # 回复之前先推一条自己的消息
        self.delay = delay      # 隔这么久才回话（人在编辑器里读 diff 要时间）
        self.seen = []
        self.conn = None        # accept 出来的那条连接，stop() 要一起关（见下）
        self.workspace = workspace
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(1)
        self.lock = os.path.join(ide.lock_dir(), "%d.json" % os.getpid())
        if write_lock:
            os.makedirs(ide.lock_dir(), exist_ok=True)
            with open(self.lock, "w", encoding="utf-8") as f:
                json.dump({"socket": self.path, "workspace": workspace,
                           "editor": editor, "pid": os.getpid()}, f)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self.conn = conn
        # 客户端先关（正常收尾）时这边的 write 会撞 BrokenPipe；它跑在 daemon 线程里，
        # 不接的话 unittest 结束时会往屏幕上糊一条 traceback
        try:
            self._loop(conn)
        except OSError:
            pass

    def _loop(self, conn):
        with conn, conn.makefile("rwb") as f:
            while True:
                line = f.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line.decode())
                except ValueError:
                    return
                self.seen.append(msg)
                reply = self.script.get(msg.get("method"))
                if callable(reply):
                    reply = reply(msg.get("params") or {})
                if reply is None or msg.get("id") == 0:
                    continue          # 不答（超时那条路），或者对方说了别回
                # ping 不能慢：connect() 用的是 CONNECT(1.5s) 那道短超时，
                # 慢了就成了"连不上"，后面的断言全落在 bridge 为 None 上
                if self.delay and msg.get("method") != "ping":
                    time.sleep(self.delay)
                if self.noise:
                    f.write(b'{"id": 9999, "method": "heartbeat", "params": {}}\n')
                    f.write(b'{"id": 9998, "result": {"action": "reject"}}\n')
                f.write(json.dumps({"id": msg["id"], "result": reply}).encode() + b"\n")
                f.flush()

    def wait_for(self, method, timeout=3.0):
        """等服务端线程真的读到这条消息

        `close_diff` 是不等回复的通知，`route()` 返回时它可能还躺在 socket 缓冲里。
        直接断言 `seen` 是个竞态（第一版偶尔绿）。
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(m.get("method") == method for m in self.seen):
                return True
            time.sleep(0.02)
        return False

    def stop(self):
        """把这个假编辑器整个收掉

        ⚠️ 只关 listen 那个 socket 不算「编辑器关了」：已经 accept 出来的那条连接
        还在，服务线程照样答话，于是「编辑器重启」的用例会看到它若无其事地回 accept。
        真正让对面察觉的是把 accept 出来的那条关掉（recv 收到 0 字节）。
        而且要 `shutdown` 不能只 `close`：服务线程那边 `conn.makefile()` 另持一份
        fd，只 close 这个 socket 对象底下的 fd 还开着，对面什么都察觉不到。
        """
        try:
            self.sock.close()
        except OSError:
            pass
        if self.conn is not None:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.conn.close()
            except OSError:
                pass


class Base(unittest.TestCase):
    """lock 目录指到临时地方 别在用户真正的 XDG_RUNTIME_DIR 里留文件"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.ws)
        self.root = Path(self.ws)
        self._locks = os.path.join(self.tmp.name, "locks")
        os.makedirs(self._locks)
        self._saved_lock_dir = ide.lock_dir
        ide.lock_dir = lambda: self._locks
        self._saved_no_ide = os.environ.pop("FEIYU_NO_IDE", None)
        self.servers = []
        self.bridges = []

    def tearDown(self):
        for b in self.bridges:
            b.close()
        for s in self.servers:
            s.stop()
        ide.lock_dir = self._saved_lock_dir
        if self._saved_no_ide is not None:
            os.environ["FEIYU_NO_IDE"] = self._saved_no_ide
        else:
            os.environ.pop("FEIYU_NO_IDE", None)
        self.tmp.cleanup()

    def serve(self, script, workspace=None, **kw):
        s = Server(self.tmp.name, workspace or self.ws, script, **kw)
        self.servers.append(s)
        return s

    def link(self, workspace=None):
        """连上假编辑器，并登记下来让 tearDown 关掉（不关会刷一屏 ResourceWarning）"""
        b = ide.connect(workspace or self.ws)
        if b is not None:
            self.bridges.append(b)
        return b

    def file(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p


class TestDiscover(Base):
    def test_matches_same_workspace(self):
        self.serve({"ping": {"ok": True}})
        found = ide.discover(self.ws)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["editor"], "fake")

    def test_skips_other_workspace(self):
        self.serve({"ping": {"ok": True}}, workspace=self.ws + "-old")
        self.assertEqual(ide.discover(self.ws), [])

    def test_prefix_is_not_a_match(self):
        """~/proj 的编辑器不该接管 ~/proj-old 里的 agent """
        self.serve({"ping": {"ok": True}}, workspace=self.ws)
        self.assertEqual(ide.discover(self.ws + "-old"), [])

    def test_subdirectory_is_not_a_match(self):
        """朴素的前缀匹配会在这个方向上错：编辑器开在 ~/proj，agent 跑在 ~/proj/sub

        两者的 `@路径` 相对的根不一样，接管了就会拼出对方不认的路径。
        """
        self.serve({"ping": {"ok": True}}, workspace=self.ws)
        self.assertEqual(ide.discover(os.path.join(self.ws, "sub")), [])

    def test_symlinked_workspace_matches(self):
        link = os.path.join(self.tmp.name, "via-link")
        os.symlink(self.ws, link)
        self.serve({"ping": {"ok": True}}, workspace=link)
        self.assertEqual(len(ide.discover(self.ws)), 1)

    def test_dead_pid_lock_is_removed(self):
        lock = os.path.join(self._locks, "dead.json")
        with open(lock, "w", encoding="utf-8") as f:
            json.dump({"socket": "/nope.sock", "workspace": self.ws,
                       "editor": "gone", "pid": 2 ** 30}, f)
        self.assertEqual(ide.discover(self.ws), [])
        self.assertFalse(os.path.exists(lock), "死掉的 lock 应该被顺手清掉")

    def test_dead_pid_socket_file_is_removed_too(self):
        """编辑器被 kill 时 deactivate / VimLeavePre 都不跑，socket 文件会留下"""
        sock = os.path.join(self._locks, "gone.sock")
        open(sock, "w").close()
        lock = os.path.join(self._locks, "gone.json")
        with open(lock, "w", encoding="utf-8") as f:
            json.dump({"socket": sock, "workspace": self.ws,
                       "editor": "gone", "pid": 2 ** 30}, f)
        ide.discover(self.ws)
        self.assertFalse(os.path.exists(sock), "死掉的 socket 文件也该清掉")

    def test_only_removes_sockets_inside_the_lock_dir(self):
        """socket 路径是从文件里读出来的，别照着它去删别处的东西"""
        outside = os.path.join(self.tmp.name, "别动我")
        open(outside, "w").close()
        with open(os.path.join(self._locks, "evil.json"), "w", encoding="utf-8") as f:
            json.dump({"socket": outside, "workspace": self.ws,
                       "editor": "evil", "pid": 2 ** 30}, f)
        ide.discover(self.ws)
        self.assertTrue(os.path.exists(outside), "lock 目录之外的文件不许删")

    def test_garbage_lock_is_ignored(self):
        with open(os.path.join(self._locks, "bad.json"), "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        self.assertEqual(ide.discover(self.ws), [])

    def test_no_lock_dir_at_all(self):
        ide.lock_dir = lambda: os.path.join(self.tmp.name, "nothing-here")
        self.assertEqual(ide.discover(self.ws), [])


class TestConnect(Base):
    def test_connects_and_pings(self):
        s = self.serve({"ping": {"ok": True}})
        bridge = self.link()
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge.editor, "fake")
        self.assertEqual(s.seen[0]["method"], "ping")
        bridge.close()

    def test_no_editor_returns_none(self):
        self.assertIsNone(self.link())

    def test_env_switch_off(self):
        self.serve({"ping": {"ok": True}})
        os.environ["FEIYU_NO_IDE"] = "1"
        self.assertIsNone(self.link())

    def test_lock_without_listener(self):
        """lock 在、进程在，但 socket 没人 listen（编辑器重启、pid 被复用）"""
        with open(os.path.join(self._locks, "stale.json"), "w", encoding="utf-8") as f:
            json.dump({"socket": os.path.join(self.tmp.name, "nobody.sock"),
                       "workspace": self.ws, "editor": "stale", "pid": os.getpid()}, f)
        self.assertIsNone(self.link())

    def test_ping_not_answered(self):
        """接受了连接但不答 ping：不能当成连上了"""
        self.serve({"ping": None})
        saved, ide.CONNECT = ide.CONNECT, 0.3
        try:
            self.assertIsNone(self.link())
        finally:
            ide.CONNECT = saved


class TestRoute(Base):
    """route() 的返回值就是 _gate 要的那套答复"""

    def bridge(self, script):
        self.serve(dict(script, ping={"ok": True}))
        b = self.link()
        self.assertIsNotNone(b, "假编辑器没连上，后面的断言没意义")
        return b

    def test_write_file_accept(self):
        self.file("a.txt", "旧\n")
        b = self.bridge({"open_diff": {"action": "accept"}})
        args = {"path": "a.txt", "content": "新\n"}
        self.assertEqual(ide.route(b, "write_file", args, self.root), "yes")
        self.assertEqual(args["content"], "新\n", "没改过就不该动入参")

    def test_write_file_reject(self):
        self.file("a.txt", "旧\n")
        b = self.bridge({"open_diff": {"action": "reject"}})
        self.assertEqual(
            ide.route(b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root), "no")

    def test_accept_always(self):
        self.file("a.txt", "旧\n")
        b = self.bridge({"open_diff": {"action": "accept_always"}})
        self.assertEqual(
            ide.route(b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root),
            "always")

    def test_diff_payload_carries_both_sides(self):
        self.file("a.txt", "旧\n")
        seen = {}

        def grab(params):
            seen.update(params)
            return {"action": "accept"}

        b = self.bridge({"open_diff": grab})
        ide.route(b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root)
        self.assertEqual(seen["old"], "旧\n")
        self.assertEqual(seen["new"], "新\n")
        self.assertTrue(seen["path"].endswith("a.txt"))
        self.assertIn("a.txt", seen["label"])

    def test_append_diff_shows_whole_file(self):
        self.file("a.txt", "一\n")
        seen = {}
        b = self.bridge({"open_diff": lambda p: (seen.update(p), {"action": "accept"})[1]})
        ide.route(b, "write_file", {"path": "a.txt", "content": "二\n", "append": True},
                  self.root)
        self.assertEqual(seen["new"], "一\n二\n", "append 的 diff 右边要是拼接后的整份")

    def test_edit_file_accept(self):
        self.file("a.py", "def f():\n    return 1\n")
        seen = {}
        b = self.bridge({"open_diff": lambda p: (seen.update(p), {"action": "accept"})[1]})
        args = {"path": "a.py", "old_str": "return 1", "new_str": "return 2"}
        self.assertEqual(ide.route(b, "edit_file", args, self.root), "yes")
        self.assertEqual(seen["new"], "def f():\n    return 2\n")

    def test_edit_file_with_edits_list(self):
        self.file("a.py", "a = 1\nb = 2\n")
        seen = {}
        b = self.bridge({"open_diff": lambda p: (seen.update(p), {"action": "accept"})[1]})
        ide.route(b, "edit_file", {"path": "a.py", "edits": [
            {"old_str": "a = 1", "new_str": "a = 10"},
            {"old_str": "b = 2", "new_str": "b = 20"},
        ]}, self.root)
        self.assertEqual(seen["new"], "a = 10\nb = 20\n", "多处编辑要合成一份新文本")

    def test_unmatched_old_str_is_not_offered(self):
        """old_str 对不上：这次调用本来就会失败，不该开 diff 浪费一次确认"""
        self.file("a.py", "x = 1\n")
        b = self.bridge({"open_diff": {"action": "accept"}})
        self.assertIsNone(ide.route(
            b, "edit_file", {"path": "a.py", "old_str": "没有这一行", "new_str": "y"},
            self.root))

    def test_undecodable_file_is_not_offered(self):
        """解不出来的字节 read_text(errors="replace") 会换成 \ufffd

        原样接受没事（不回传内容），但人改过一个字就得整份写回去 ——
        那会把文件里所有这样的字节永久换掉。这层不该冒这个风险。
        """
        (self.root / "b.bin").write_bytes(b"\xff\xfe hello\n")
        b = self.bridge({"open_diff": {"action": "accept"}})
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "b.bin", "content": "干净的\n"}, self.root))

    def test_nul_byte_file_is_not_offered(self):
        (self.root / "z.bin").write_bytes(b"a\x00b\n")
        b = self.bridge({"open_diff": {"action": "accept"}})
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "z.bin", "content": "x\n"}, self.root))

    def test_no_op_is_not_offered(self):
        self.file("a.txt", "同\n")
        b = self.bridge({"open_diff": {"action": "accept"}})
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "a.txt", "content": "同\n"}, self.root))

    def test_other_tools_go_to_terminal(self):
        b = self.bridge({"open_diff": {"action": "accept"}})
        for name in ("bash", "git", "read_file"):
            self.assertIsNone(ide.route(b, name, {"command": "ls"}, self.root), name)

    def test_directory_target_is_not_offered(self):
        os.makedirs(self.root / "d")
        b = self.bridge({"open_diff": {"action": "accept"}})
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "d", "content": "x"}, self.root))

    def test_unsolicited_push_is_not_mistaken_for_the_answer(self):
        """编辑器可以在回复之前先推自己的消息（状态、心跳）

        不比对编号的话第一条读到的就被当成答案——那条里没有 action，
        于是本该通过的确认变成「桥没管这次」，退回终端又问一遍。
        """
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}},
                   noise=True)
        b = self.link()
        self.assertEqual(
            ide.route(b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root),
            "yes")

    def test_close_diff_is_sent(self):
        self.file("a.txt", "旧\n")
        s = self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}})
        b = self.link()
        ide.route(b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root)
        self.assertTrue(s.wait_for("close_diff"), "diff 收尾要通知编辑器把窗口关掉")
        b.close()
        self.assertEqual([m for m in s.seen if m.get("method") == "close_diff"][0]["id"], 0,
                         "通知类消息编号给 0，编辑器认这个就是别回")


class TestEdited(Base):
    """人在 diff 右边改过：写进去的必须是他看到的那份"""

    def bridge(self, content):
        self.serve({"ping": {"ok": True},
                    "open_diff": {"action": "accept", "content": content}})
        return self.link()

    def test_write_file_takes_edited(self):
        self.file("a.txt", "旧\n")
        b = self.bridge("我自己改的\n")
        args = {"path": "a.txt", "content": "模型写的\n", "append": True}
        self.assertEqual(ide.route(b, "write_file", args, self.root), "yes")
        self.assertEqual(args["content"], "我自己改的\n")
        self.assertNotIn("append", args, "换成整份内容之后 append 必须摘掉，否则会拼两遍")

    def test_edit_file_becomes_whole_file_edit(self):
        self.file("a.py", "x = 1\n")
        b = self.bridge("x = 99\n")
        args = {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"}
        self.assertEqual(ide.route(b, "edit_file", args, self.root), "yes")
        self.assertEqual(args["edits"], [{"old_str": "x = 1\n", "new_str": "x = 99\n"}])
        for gone in ("old_str", "new_str", "replace_all"):
            self.assertNotIn(gone, args, gone + " 没摘掉，工具会走老路径")

    def test_rewritten_edit_actually_applies(self):
        """改写出来的那条编辑必须真的能被 _apply_edit 吃下去 —— 这是它的全部意义"""
        from feiyu import tools
        self.file("a.py", "x = 1\n")
        b = self.bridge("x = 99\n")
        args = {"path": "a.py", "old_str": "x = 1", "new_str": "x = 2"}
        ide.route(b, "edit_file", args, self.root)
        edit = args["edits"][0]
        outcome = tools._apply_edit("x = 1\n", edit["old_str"], edit["new_str"], False)
        self.assertNotIsInstance(outcome, str, "改写后的编辑应用不上：" + str(outcome))
        self.assertEqual(outcome[0], "x = 99\n")

    def test_edited_same_as_proposed_leaves_args_alone(self):
        self.file("a.txt", "旧\n")
        b = self.bridge("新\n")
        args = {"path": "a.txt", "content": "新\n"}
        ide.route(b, "write_file", args, self.root)
        self.assertEqual(args, {"path": "a.txt", "content": "新\n"})

    def test_ask_diff_drops_content_on_reject(self):
        """不变量钉在执行它的那一层：拒绝了就不把编辑后的内容往上传

        `route` 里那个 `answer in ("yes","always")` 是冗余保险，
        真正执行这条的是 `ask_diff` —— 所以要在这儿断言，
        不然拆掉它的话没有任何用例会红。
        """
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True},
                    "open_diff": {"action": "reject", "content": "别写我\n"}})
        b = self.link()
        answer, edited = b.ask_diff("/x", "旧\n", "新\n", "label")
        self.assertEqual(answer, "no")
        self.assertIsNone(edited, "拒绝了还把内容传上去，写文件那一步会用它")

    def test_reject_ignores_content(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True},
                    "open_diff": {"action": "reject", "content": "别写我\n"}})
        b = self.link()
        args = {"path": "a.txt", "content": "新\n"}
        self.assertEqual(ide.route(b, "write_file", args, self.root), "no")
        self.assertEqual(args["content"], "新\n", "拒绝了就不该动入参")


class TestSlowEditor(Base):
    """人要读 diff，回话必然比任何轮询周期慢 —— 这条是这座桥最常走的那条路

    `_recv` 内部按秒分片等（好在编辑器被 kill 时不用等满超时）。头一版用
    `sock.makefile()` 的缓冲读配 `settimeout`，**第一次分片超时之后那个文件对象
    就废了**，再读直接抛 OSError → 判定成「编辑器断了」→ 每次都在 1.0 秒上
    返回 None，退回终端确认。假编辑器立刻回话，所以上面那些用例一条都没抓到；
    是 nvim 那边真按了一次 `ga` 才暴露的。
    """

    def test_reply_after_more_than_one_poll_tick(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}}, delay=2.4)
        b = self.link()
        self.assertEqual(
            ide.route(b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root),
            "yes", "回话慢于分片周期就当断线 —— 那是这座桥最常走的那条路")

    def test_slow_reply_with_edited_content(self):
        """慢 + 带内容：分片读不能把后面那段 JSON 丢掉"""
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True},
                    "open_diff": {"action": "accept", "content": "我改的\n"}}, delay=1.5)
        b = self.link()
        args = {"path": "a.txt", "content": "新\n"}
        self.assertEqual(ide.route(b, "write_file", args, self.root), "yes")
        self.assertEqual(args["content"], "我改的\n")


class TestDegrades(Base):
    """每一种坏法都要回 None，让调用方退回终端确认"""

    def test_unknown_action(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}, "open_diff": {"action": "随便什么"}})
        b = self.link()
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root))

    def test_editor_never_answers(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}, "open_diff": None})
        b = self.link()
        saved, ide.WAIT = ide.WAIT, 0.4
        try:
            self.assertIsNone(ide.route(
                b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root))
        finally:
            ide.WAIT = saved

    def test_editor_hangs_up_mid_request(self):
        self.file("a.txt", "旧\n")
        s = self.serve({"ping": {"ok": True}})   # open_diff 不在脚本里 -> 不答
        b = self.link()
        s.stop()
        saved, ide.WAIT = ide.WAIT, 0.6
        try:
            self.assertIsNone(ide.route(
                b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root))
        finally:
            ide.WAIT = saved

    def test_dead_bridge_short_circuits(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}})
        b = self.link()
        b.close()
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root))

    def test_none_bridge(self):
        self.assertIsNone(ide.route(
            None, "write_file", {"path": "a.txt", "content": "x"}, self.root))

    def test_error_reply(self):
        self.file("a.txt", "旧\n")
        folder = self.tmp.name
        path = os.path.join(folder, "err.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        self.addCleanup(srv.close)

        def serve():
            conn, _ = srv.accept()
            with conn, conn.makefile("rwb") as f:
                while True:
                    line = f.readline()
                    if not line:
                        return
                    msg = json.loads(line.decode())
                    # id 为 0 是通知（close_diff），协议说了别回。回了的话客户端
                    # 这时往往已经收摊，write 撞 BrokenPipe —— 跑在 daemon 线程里，
                    # 表现是测试全绿但屏幕上糊两条 traceback
                    if msg.get("id") == 0:
                        continue
                    if msg.get("method") == "ping":
                        f.write(b'{"id": %d, "result": {"ok": true}}\n' % msg["id"])
                    else:
                        f.write(json.dumps(
                            {"id": msg["id"], "error": "开不了"}).encode() + b"\n")
                    f.flush()

        threading.Thread(target=serve, daemon=True).start()
        with open(os.path.join(self._locks, "e.json"), "w", encoding="utf-8") as f:
            json.dump({"socket": path, "workspace": self.ws,
                       "editor": "err", "pid": os.getpid()}, f)
        b = self.link()
        self.assertIsNotNone(b)
        self.assertIsNone(ide.route(
            b, "write_file", {"path": "a.txt", "content": "新\n"}, self.root))

    def test_oversized_payload_is_refused(self):
        """超过 MAX_BYTES 的不往 socket 上写，也不能因此抛出来"""
        self.file("big.txt", "x")
        self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}})
        b = self.link()
        saved, ide.MAX_BYTES = ide.MAX_BYTES, 128
        try:
            self.assertIsNone(ide.route(
                b, "write_file", {"path": "big.txt", "content": "y" * 500}, self.root))
        finally:
            ide.MAX_BYTES = saved


class TestLink(Base):
    """编辑器不一定比 feiyu 先起来，也可能中途重启

    0.71.0 只在启动那一刻连一次，于是「先跑 feiyu、再开 nvim」整场都没有 diff，
    而且一声不吭。这一组盯的就是那条路。
    """

    def setUp(self):
        super().setUp()
        self.said = []      # on_change 收到的东西，连上给编辑器名、断了给 None

    def make(self, workspace=None):
        link = ide.Link(workspace or self.ws, on_change=self.said.append)
        if link.bridge is not None:
            self.bridges.append(link.bridge)
        return link

    def write(self, link, text="新\n"):
        return link.route("write_file", {"path": "a.txt", "content": text}, self.root)

    def test_editor_shows_up_after_feiyu_started(self):
        self.file("a.txt", "旧\n")
        link = self.make()                      # 这会儿还没有编辑器
        self.assertIsNone(link.bridge)
        self.assertIsNone(self.write(link))

        self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}})
        link._next = 0.0                        # 节流让开，模拟"过了 RETRY 秒"
        self.assertEqual(self.write(link), "yes")
        self.bridges.append(link.bridge)
        self.assertEqual(self.said, ["fake"])

    def test_retry_is_throttled(self):
        self.file("a.txt", "旧\n")
        tries = []
        saved = ide.connect
        ide.connect = lambda ws: tries.append(ws) or None
        try:
            link = self.make()                  # __init__ 里那一次
            self.assertEqual(len(tries), 1)
            self.write(link)
            self.write(link)
            self.assertEqual(len(tries), 1, "节流没拦住：每次确认都去连一遍")
            link._next = 0.0
            self.write(link)
            self.assertEqual(len(tries), 2)
        finally:
            ide.connect = saved

    def test_non_diffable_tool_does_not_retry(self):
        tries = []
        saved = ide.connect
        ide.connect = lambda ws: tries.append(ws) or None
        try:
            link = self.make()
            link._next = 0.0
            self.assertIsNone(link.route("bash", {"cmd": "ls"}, self.root))
            self.assertEqual(len(tries), 1, "bash 没有 diff 可开，不该为它去连编辑器")
        finally:
            ide.connect = saved

    def test_reconnects_after_the_editor_restarts(self):
        self.file("a.txt", "旧\n")
        first = self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}})
        link = self.make()
        self.assertEqual(self.write(link), "yes")
        self.assertEqual(self.said, ["fake"])

        first.stop()                            # 编辑器关了
        os.unlink(first.lock)
        saved, ide.WAIT = ide.WAIT, 0.6
        try:
            self.assertIsNone(self.write(link))
        finally:
            ide.WAIT = saved
        self.assertEqual(self.said, ["fake", None], "断开没说一声")
        self.assertIsNone(link.bridge)

        self.serve({"ping": {"ok": True}, "open_diff": {"action": "reject"}},
                   editor="nvim")               # 编辑器又起来了
        link._next = 0.0
        self.assertEqual(self.write(link), "no")
        self.bridges.append(link.bridge)
        self.assertEqual(self.said, ["fake", None, "nvim"])

    def test_failed_retry_stays_quiet(self):
        self.file("a.txt", "旧\n")
        link = self.make()
        for _ in range(3):
            link._next = 0.0
            self.write(link)
        self.assertEqual(self.said, [], "连不上不该每次都刷一句")

    def test_connect_blowing_up_does_not_escape(self):
        """铁律：桥的任何毛病都只意味着「这次没有编辑器」"""
        self.file("a.txt", "旧\n")
        saved = ide.connect
        ide.connect = lambda ws: (_ for _ in ()).throw(RuntimeError("炸了"))
        try:
            link = self.make()
            self.assertIsNone(link.bridge)
            link._next = 0.0
            self.assertIsNone(self.write(link))
        finally:
            ide.connect = saved

    def test_on_change_blowing_up_does_not_escape(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}})
        link = ide.Link(self.ws, on_change=lambda e: 1 / 0)
        self.bridges.append(link.bridge)
        self.assertEqual(self.write(link), "yes")

    def test_no_on_change_is_fine(self):
        self.file("a.txt", "旧\n")
        self.serve({"ping": {"ok": True}, "open_diff": {"action": "accept"}})
        link = ide.Link(self.ws)
        self.bridges.append(link.bridge)
        self.assertEqual(self.write(link), "yes")


class TestLockDir(unittest.TestCase):
    def test_prefers_runtime_dir(self):
        saved = os.environ.get("XDG_RUNTIME_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_RUNTIME_DIR"] = tmp
            try:
                self.assertEqual(ide.lock_dir(), os.path.join(tmp, "feiyu", "ide"))
            finally:
                if saved is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = saved

    def test_falls_back_when_runtime_dir_missing(self):
        saved_run = os.environ.pop("XDG_RUNTIME_DIR", None)
        saved_state = os.environ.get("XDG_STATE_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_STATE_HOME"] = tmp
            try:
                self.assertEqual(ide.lock_dir(),
                                 os.path.join(tmp, "feiyu", "ide"))
            finally:
                if saved_run is not None:
                    os.environ["XDG_RUNTIME_DIR"] = saved_run
                if saved_state is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = saved_state


if __name__ == "__main__":
    unittest.main()
