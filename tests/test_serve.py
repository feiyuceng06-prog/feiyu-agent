"""网页端那半边的 Python 侧：事件 sink 和 stdio 协议

**这里测的是协议，不是画面。** 画面归 `web/dev/shoot.mjs`（headless Chrome）管，
真链路归 `web/dev/live.mjs` 管。这份只保证：ui 报的事件形状是对的、
一条要回话的请求会阻塞并能被回话唤醒、对面断了不会把 agent 挂死。
"""

import contextlib
import io
import json
import os
import pathlib
import queue
import re
import tempfile
import threading
import types
import unittest

from feiyu import credits as credits_mod
from feiyu import enabled, events, keys, permissions, serve, ui
from feiyu import skills as skills_mod, tools as tools_mod
from feiyu.agent import Feiyu
from feiyu.providers import _as_rates
from pathlib import Path


class Sink:
    def __init__(self):
        self.seen = []
        events.install(lambda kind, payload: self.seen.append((kind, payload)))

    def kinds(self):
        return [k for k, _ in self.seen]

    def find(self, kind):
        return [p for k, p in self.seen if k == kind]


class Base(unittest.TestCase):
    def setUp(self):
        self.sink = Sink()

    def tearDown(self):
        events.clear()


class TestEvents(Base):
    def test_没装_sink_时不报也不崩(self):
        events.clear()
        self.assertFalse(events.active())
        ui.notice("没人听")            # 不该抛
        self.assertEqual(self.sink.seen, [])

    def test_sink_自己抛异常不能把_agent_带崩(self):
        events.install(lambda *a: 1 / 0)
        ui.notice("炸一个")            # 咽掉，理由同 trace.write

    def test_paused_期间不报(self):
        with events.paused():
            ui.notice("这条不该出去")
        self.assertEqual(self.sink.seen, [])
        ui.notice("这条要出去")
        self.assertEqual(self.sink.kinds(), ["note"])

    def test_paused_里抛异常也要把_sink_装回去(self):
        with self.assertRaises(ValueError):
            with events.paused():
                raise ValueError("x")
        ui.notice("装回来了")
        self.assertEqual(self.sink.kinds(), ["note"])


class TestUiEmits(Base):
    def test_三档提示带上等级(self):
        ui.notice("a"), ui.warn("b"), ui.error("c")
        self.assertEqual([p["level"] for p in self.sink.find("note")],
                         ["notice", "warn", "error"])

    def test_工具调用报的是原始参数不是画好的字符串(self):
        ui.tool_call("edit_file", {"path": "a.py"})
        got = self.sink.find("tool_start")[0]
        self.assertEqual(got["name"], "edit_file")
        self.assertEqual(got["args"], {"path": "a.py"})
        self.assertEqual(got["target"], "a.py")
        self.assertEqual(got["label"], "Update")

    def test_结果的_detail_是结构不是_ANSI(self):
        ui.tool_result("已修改 1 处", [("-", "旧"), ("+", "新")], is_error=False)
        got = self.sink.find("tool_result")[0]
        self.assertEqual(got["detail"], [{"kind": "-", "text": "旧"},
                                         {"kind": "+", "text": "新"}])
        self.assertFalse(got["is_error"])

    def test_正文报的是原始_markdown(self):
        # 非终端时 Stream.write 会直接往 stdout 写一份，测试里挡掉
        stream = ui.Stream()
        with contextlib.redirect_stdout(io.StringIO()):
            stream.write("**粗**")
        self.assertEqual(self.sink.find("text"), [{"delta": "**粗**"}])

    def test_参数进度按步长报不是每个_delta_都报(self):
        """一个长文件几千个 delta，全推给浏览器会把它卡住 """
        stream = ui.Stream()
        for n in range(0, ui.PROGRESS_STEP * 3, 50):
            stream.progress("write_file", "x" * n)
        reported = len(self.sink.find("tool_progress"))
        self.assertLessEqual(reported, 4)
        self.assertGreaterEqual(reported, 2)


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self.proto = serve.Protocol(self.out)

    def lines(self):
        return [json.loads(l) for l in self.out.getvalue().splitlines() if l.strip()]

    def test_一行一个_json(self):
        self.proto.send("note", level="notice", text="一\n二")
        raw = self.out.getvalue()
        self.assertEqual(raw.count("\n"), 1)      # 正文里的换行要被转义掉
        self.assertEqual(self.lines()[0]["text"], "一\n二")

    def test_中文不转成_unicode_转义(self):
        self.proto.send("note", text="中文")
        self.assertIn("中文", self.out.getvalue())

    def test_请求会阻塞到有人回话(self):
        done = []

        def ask():
            done.append(self.proto.request("ask", type="approve"))

        worker = threading.Thread(target=ask)
        worker.start()
        for _ in range(200):                       # 等它把请求发出去
            if self.lines():
                break
            threading.Event().wait(0.01)
        rid = self.lines()[0]["id"]
        self.proto.reply(rid, "yes")
        worker.join(timeout=5)
        self.assertEqual(done, ["yes"])

    def test_回话的_id_对不上就不该唤醒(self):
        self.proto.reply(999, "yes")               # 不该抛

    def test_对面断了要把等着的都放掉(self):
        """不放的话 agent 会卡到 REPLY_TIMEOUT，网页早关了 """
        done = []
        worker = threading.Thread(target=lambda: done.append(self.proto.request("ask")))
        worker.start()
        for _ in range(200):
            if self.lines():
                break
            threading.Event().wait(0.01)
        self.proto.fail_all()
        worker.join(timeout=5)
        self.assertEqual(done, [None])

    def test_写不出去不能抛(self):
        self.out.close()
        self.proto.send("note", text="对面没了")    # 不该抛


class TestReaderHardening(unittest.TestCase):
    """一条坏消息不能把读 stdin 的线程弄死

    死了之后 stdin 再也没人读，页面上看起来是「不响应了」，
    而 stderr 上那条 traceback 谁也看不到。0.50.1 之前真的是这样。
    """

    class Box(serve.Server):
        def __init__(self):
            self.inbox = __import__("queue").Queue()
            self.busy = False
            self.pushed = 0
            self.proto = serve.Protocol(io.StringIO())

        def push_status(self):
            self.pushed += 1

    def setUp(self):
        self.server = self.Box()

    def feed(self, line):
        return self.server._dispatch_in(line)

    def test_畸形的_reply_id_不抛(self):
        for bad in ('{"op":"reply","id":"不是数字"}', '{"op":"reply"}',
                    '{"op":"reply","id":null}', '{"op":"reply","id":[1]}'):
            self.assertTrue(self.feed(bad), bad)

    def test_不是_json_不抛(self):
        for bad in ("これはJSONではない", "[1,2,3]", '"字符串"', "null", "{"):
            self.assertTrue(self.feed(bad), bad)

    def test_不认识的_op_忽略掉(self):
        self.assertTrue(self.feed('{"op":"没这个"}'))
        self.assertTrue(self.feed("{}"))

    def test_坏消息之后照样收得下正常消息(self):
        self.feed('{"op":"reply","id":"不是数字"}')
        self.feed('{"op":"command","text":"/mode"}')
        self.assertEqual(self.server.inbox.get_nowait(), ("command", "/mode"))

    def test_超长的正文要截(self):
        self.feed(json.dumps({"op": "prompt", "text": "x" * (serve.MAX_TEXT + 5000)}))
        op, text = self.server.inbox.get_nowait()
        self.assertEqual(op, "prompt")
        self.assertEqual(len(text), serve.MAX_TEXT)

    def test_stop_要让读线程停下来(self):
        self.assertFalse(self.feed('{"op":"stop"}'))

    def test_不忙的时候不该扔中断(self):
        # busy 是 False，扔了会落在没人接的地方，把整个服务带走
        self.server.busy = False
        self.assertTrue(self.feed('{"op":"interrupt"}'))


class TestDeleteSession(unittest.TestCase):
    """删对话 **删的是正在看的那段时，agent 也得归零** ——
    盘上没了内存里还在的话，下一轮存盘会照着旧 id 写回去，等于没删。
    """

    class Agent:
        def __init__(self, sid):
            self.session_id = sid
            self.reset_called = False
            self.ready = True

        def reset(self):
            self.reset_called = True

    def setUp(self):
        import queue
        import tempfile

        from feiyu import session as session_mod

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = session_mod.DIR
        self.addCleanup(setattr, session_mod, "DIR", self._saved)
        session_mod.DIR = str(Path(self.tmp.name) / "sessions")
        self.ws = Path(self.tmp.name) / "ws"
        self.dropped = []
        # **换完要还回去。** 不还的话它跟着模块活到别的测试文件里，
        # test_session 那几条删除用例会莫名其妙地挂 —— 真踩了一次。
        self.addCleanup(setattr, session_mod, "drop", session_mod.drop)
        session_mod.drop = lambda ws, sid: self.dropped.append(sid)

        self.out = io.StringIO()
        self.server = serve.Server.__new__(serve.Server)
        self.server.workspace = self.ws
        self.server.proto = serve.Protocol(self.out)
        self.server.inbox = queue.Queue()
        self.server.busy = False
        # 状态那条要一整个 agent 才拼得出来，这儿测的是删的逻辑，换掉
        self.server.push_status = lambda: None

    def kinds(self):
        return [json.loads(l)["kind"] for l in self.out.getvalue().splitlines() if l.strip()]

    def test_删别的那段_不动_agent(self):
        agent = self.Agent("20260101-000000")
        self.server.agent = agent
        self.server.delete_session("20251231-000000")
        self.assertEqual(self.dropped, ["20251231-000000"])
        self.assertFalse(agent.reset_called)
        self.assertNotIn("cleared", self.kinds())
        self.assertIn("sessions", self.kinds())

    def test_删正在看的那段_要把_agent_归零并换新_id(self):
        agent = self.Agent("20260101-000000")
        self.server.agent = agent
        self.server.delete_session("20260101-000000")
        self.assertTrue(agent.reset_called)
        self.assertNotEqual(agent.session_id, "20260101-000000")
        self.assertIn("cleared", self.kinds())

    def test_空_id_什么都不做(self):
        self.server.agent = self.Agent("x")
        self.server.delete_session("")
        self.assertEqual(self.dropped, [])
        self.assertEqual(self.kinds(), [])


class TestPrivateDirs(unittest.TestCase):
    """密钥、对话原文、长期记忆都在这几个目录下，别让同机的别人列得到 """

    def test_目录建出来是_700(self):
        import os
        import tempfile

        from feiyu import files

        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "feiyu", "sessions")
            files.private_dir(target)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o700)
            # 中间那层叫 feiyu 的也要收紧，不然只收末一层等于没收
            self.assertEqual(os.stat(os.path.join(tmp, "feiyu")).st_mode & 0o777, 0o700)

    def test_已经存在的老目录也收紧(self):
        import os
        import tempfile

        from feiyu import files

        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "feiyu", "logs")
            os.makedirs(target)
            os.chmod(target, 0o755)
            files.private_dir(target)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o700)

    def test_输入历史是_600(self):
        """里面是用户打过的每一行原文，跟对话存盘同一性质 """
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_STATE_HOME"] = tmp
            try:
                path = ui._history_file()
            finally:
                del os.environ["XDG_STATE_HOME"]
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class TestPlain(unittest.TestCase):
    def test_剥掉_ANSI(self):
        painted = ui.paint("red", "危险") if ui._COLOR else "\033[31m危险\033[0m"
        self.assertEqual(serve._plain(painted), "危险")

    def test_空的也认(self):
        self.assertEqual(serve._plain(None), "")


class TestHistoryView(unittest.TestCase):
    class FakeBackend:
        def __init__(self, messages):
            self.messages = messages

    class FakeAgent:
        def __init__(self, messages):
            self.backend = TestHistoryView.FakeBackend(messages)

    def view(self, messages):
        return serve.history_view(self.FakeAgent(messages))

    def test_openai_那种形状(self):
        rows = self.view([
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "帮我改一下"},
            {"role": "assistant", "content": "改完了"},
            {"role": "tool", "content": "读取 3 行"},
        ])
        self.assertEqual(rows, [{"role": "user", "text": "帮我改一下"},
                                {"role": "assistant", "text": "改完了"}])

    def test_anthropic_那种形状_跳过工具块(self):
        rows = self.view([
            {"role": "user", "content": "帮我改一下"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "在改了"},
                {"type": "tool_use", "name": "edit_file", "input": {}},
            ]},
            # 工具结果是挂在 user 消息里的，那不是人说的话
            {"role": "user", "content": [{"type": "tool_result", "content": "已修改"}]},
        ])
        self.assertEqual(rows, [{"role": "user", "text": "帮我改一下"},
                                {"role": "assistant", "text": "在改了"}])

    def test_空历史(self):
        self.assertEqual(self.view([]), [])


if __name__ == "__main__":
    unittest.main()


class TestCatalog(unittest.TestCase):
    """技能与工具那个面板要的三份清单，和开关的往返

    **正文不在 catalog 里**：五十份技能各上万字，一次全推过去几百 KB，
    而面板一次只看得见一份。这条跟 `skills.catalog()` 不放正文是同一个道理，
    只是省的东西不一样 —— 那边省的是每轮的 token，这边省的是一次推送的体积。
    """

    class Box(serve.Server):
        def __init__(self, agent, out):
            self.agent = agent
            self.inbox = queue.Queue()
            self.busy = False
            self.commands = {}
            self.proto = serve.Protocol(out)

        def push_status(self):
            pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for mod, attr in ((permissions, "STORE"), (keys, "STORE"), (keys, "STATE"),
                          (enabled, "STORE"), (tools_mod, "IMAGE_CONFIG")):
            before = getattr(mod, attr)
            setattr(mod, attr, os.path.join(self.tmp.name, attr.lower() + ".json"))
            self.addCleanup(setattr, mod, attr, before)
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.toolbox.skills = {
            "甲": skills_mod.Skill("甲", "甲的用途", "甲的正文很长" * 100, "全局", "/x/甲.md"),
        }
        self.out = io.StringIO()
        self.server = self.Box(self.agent, self.out)

    def events(self, kind):
        rows = [json.loads(l) for l in self.out.getvalue().splitlines() if l.strip()]
        return [r for r in rows if r.get("kind") == kind]

    def test_三份清单齐全(self):
        self.server.push_catalog()
        got = self.events("catalog")[-1]
        self.assertEqual([s["name"] for s in got["skills"]], ["甲"])
        self.assertTrue(got["skills"][0]["on"])
        self.assertIn("bash", [t["name"] for t in got["tools"]])
        self.assertEqual(got["mcp"], [])

    def test_清单里没有技能正文(self):
        self.server.push_catalog()
        got = self.events("catalog")[-1]
        self.assertNotIn("body", got["skills"][0])
        self.assertNotIn("甲的正文", self.out.getvalue())
        self.assertEqual(got["skills"][0]["chars"], len("甲的正文很长" * 100))

    def test_正文点开才给(self):
        self.server.push_detail("skill", "甲")
        got = self.events("detail")[-1]
        # **载荷里不能有 kind**：send() 是 {"kind": 事件名, **载荷}，
        # 重名的话事件名会被盖掉，前端那条 case 永远不匹配（真踩过）
        self.assertEqual(got["kind"], "detail")
        self.assertEqual(got["what"], "skill")
        self.assertIn("甲的正文", got["body"])

    def test_没有的技能回空正文而不是报错(self):
        self.server.push_detail("skill", "没这个")
        self.assertEqual(self.events("detail")[-1]["body"], "")

    def test_开关往返(self):
        self.server.toggle("tool", "web_fetch", False)
        got = self.events("catalog")[-1]
        off = [t["name"] for t in got["tools"] if not t["on"]]
        self.assertEqual(off, ["web_fetch"])
        self.server.toggle("tool", "web_fetch", True)
        self.assertEqual([t["name"] for t in self.events("catalog")[-1]["tools"] if not t["on"]], [])

    def test_锁住的那个开关标出来(self):
        self.server.push_catalog()
        row = next(t for t in self.events("catalog")[-1]["tools"] if t["name"] == "exit_plan")
        self.assertTrue(row["locked"])
        self.server.toggle("tool", "exit_plan", False)
        row = next(t for t in self.events("catalog")[-1]["tools"] if t["name"] == "exit_plan")
        self.assertTrue(row["on"])

    def test_认不出的类别不动任何东西(self):
        self.server.toggle("插件", "x", False)
        self.assertEqual(self.events("catalog"), [])

    def test_没配出图接口时清单里没有那一项(self):
        """摆一个永远打不开的开关只会让人以为坏了 """
        self.server.push_catalog()
        self.assertNotIn("image_generate", [t["name"] for t in self.events("catalog")[-1]["tools"]])
        with open(tools_mod.IMAGE_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"base_url": "https://x/v1", "key": "k"}, f)
        self.server.push_catalog()
        self.assertIn("image_generate", [t["name"] for t in self.events("catalog")[-1]["tools"]])


class TestApproveWords(unittest.TestCase):
    """界面上按的那几个键要翻译成 agent 认的词

    `_gate` 只认 yes / always / remember。桌面端原来把按钮值 `all` / `save`
    直接交上去，两个都落到「用户拒绝」那句 —— 按了「都允许」反而全被拒。
    用户拿截图来问过：一批 web_fetch 全是「没有执行」。
    """

    class Box(serve.Server):
        def __init__(self, answer):
            self.answer = answer
            self.proto = self

        def request(self, kind, **payload):
            return self.answer

    def answered(self, said):
        return self.Box(said).approve("web_fetch", {"url": "https://x"})

    def test_四个按钮各自翻译对(self):
        self.assertEqual(self.answered("yes"), "yes")
        self.assertEqual(self.answered("no"), "no")
        self.assertEqual(self.answered("all"), "always")
        self.assertEqual(self.answered("save"), "remember")

    def test_翻译出来的词_gate_全认得(self):
        """这条是真正的不变量：翻译表的值必须落在 _gate 的分支里 """
        known = {"yes", "no", "always", "remember"}
        self.assertTrue(set(serve.Server._ANSWERS.values()) <= known,
                        serve.Server._ANSWERS)

    def test_没回话按拒绝(self):
        for bad in (None, "", "什么", 1, ["yes"]):
            self.assertEqual(self.Box(bad).approve("bash", {"command": "ls"}), "no")


class TestThinking(Base):
    """想的过程要报出去

    思考型模型可以想十分钟一个字都不说。**数据一直在流，所以超时不会响**，
    界面上却什么都没有 —— 跟死了长得一模一样。实测过一轮 622 秒全花在这儿，
    用户拿这个来问「为什么卡死了」。
    """

    def test_按步长报_不是每个_delta_都报(self):
        stream = ui.Stream()
        for n in range(0, ui.PROGRESS_STEP * 3, 50):
            stream.thinking(n)
        got = self.sink.find("thinking")
        self.assertLessEqual(len(got), 4, got)
        self.assertGreaterEqual(len(got), 2, got)
        # 报的是"到这一档为止"，不是最后那个数 —— 只在跨档时才报
        self.assertLessEqual(got[-1]["size"], ui.PROGRESS_STEP * 3 - 50)
        self.assertGreater(got[-1]["size"], ui.PROGRESS_STEP * 2 - 50 - ui.PROGRESS_STEP)

    def test_想的原文跟字数一起报出去(self):
        stream = ui.Stream()
        for n in range(1, ui.PROGRESS_STEP * 2 + 1):
            stream.thinking(n, "想")
        got = self.sink.find("thinking")
        # 原文按同一个步长攒着一起发：**每条的 delta 长度要跟字数的增量对得上**，
        # 两条节流各走各的话这两个数会漂开，前端拿哪个都不对
        self.assertEqual(sum(len(p["delta"]) for p in got), got[-1]["size"])

    def test_想完最后一截也要出来(self):
        """只在跨档时报的话，不足一个步长的尾巴永远留在缓冲里 ——
        而尾巴恰好是「它决定要干什么」那一段。"""
        stream = ui.Stream()
        stream.thinking(ui.PROGRESS_STEP + 10, "甲" * (ui.PROGRESS_STEP + 10))
        stream.thinking(ui.PROGRESS_STEP + 13, "尾巴")
        self.assertEqual(len(self.sink.find("thinking")), 1)
        stream.finish()                      # 非 tty 也要冲
        got = self.sink.find("thinking")
        self.assertEqual(got[-1]["delta"], "尾巴")
        self.assertEqual(got[-1]["size"], ui.PROGRESS_STEP + 13)

    def test_开口之前先把想的冲出去(self):
        """顺序反了的话前端会先收到正文、再收到属于它前面那段的思考。"""
        stream = ui.Stream()
        stream.thinking(5, "先想")
        stream.write("再说")
        self.assertEqual(self.sink.kinds(), ["thinking", "text"])

    def test_没想过就不报空事件(self):
        stream = ui.Stream()
        stream.finish()
        self.assertEqual(self.sink.find("thinking"), [])

    def test_想的字数会到状态行上(self):
        ui._COLOR = False
        stream = ui.Stream()
        stream._tty = True
        stream.thinking(3200)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            stream._draw_status(0, None)
        self.assertIn("已经想了 3200 字", out.getvalue())

    def test_还没想的时候不占地方(self):
        ui._COLOR = False
        stream = ui.Stream()
        stream._tty = True
        with contextlib.redirect_stdout(io.StringIO()) as out:
            stream._draw_status(0, None)
        self.assertIn("思考中", out.getvalue())
        self.assertNotIn("已经想了", out.getvalue())


class TestNoTextOps(unittest.TestCase):
    """不带正文的 op 不能被主循环那道"空的就跳过"的闸吃掉

    那道闸是拦空 prompt 的。新加的 op 忘了登记就会**静默丢掉** ——
    shift+tab 的 cycle_mode 就这么白做了一版：前端发得好好的，服务端一句话没说，
    而两边的单测各自都是绿的（前端只验"发出去了"，后端根本没被调到）。
    """

    class Box(serve.Server):
        def __init__(self):
            self.inbox = queue.Queue()
            self.busy = False
            self.proto = serve.Protocol(io.StringIO())

    def test_每个不带正文的_op_都登记了(self):
        """反过来查：`_dispatch_in` 里凡是往 inbox 塞空正文的，都得在表里 """
        box = self.Box()
        for op in ("new", "cycle_mode"):
            box.inbox = queue.Queue()
            box._dispatch_in(json.dumps({"op": op}))
            got_op, text = box.inbox.get_nowait()
            self.assertEqual(got_op, op)
            if not text.strip():
                self.assertIn(op, serve.NO_TEXT_OPS, "%s 会被主循环静默丢掉" % op)

    def test_带正文的_op_不在表里(self):
        # 表里多塞东西会让空 prompt 也走下去
        self.assertNotIn("prompt", serve.NO_TEXT_OPS)
        self.assertNotIn("command", serve.NO_TEXT_OPS)


class TestCommandBusy(unittest.TestCase):
    """斜杠命令跑的时候要报 busy

    `/review` 和 `/compact` 会真的调一次模型（十几秒起）。不报 busy 的话
    桌面端秒表不转、停止键不出现，而那句"正在压缩…"只走 stderr ——
    用户看到的就是点了一下什么都没发生。
    """

    class Box(serve.Server):
        def __init__(self, out, boom=None):
            self.busy = False
            self.seen = []
            self.commands = {}
            self.boom = boom
            self.proto = serve.Protocol(out)
            self.agent = None

        def push_status(self):
            self.seen.append(self.busy)

        def _dispatch(self, cli, head, rest):
            self.seen.append("跑了:" + str(self.busy))
            if self.boom:
                raise self.boom

    def test_跑的时候_busy_跑完清掉(self):
        box = self.Box(io.StringIO())
        box.run_command("/mode auto")
        self.assertEqual(box.seen, [True, "跑了:True", False])

    def test_命令抛异常也要把_busy_清掉(self):
        """不清的话界面会永远停在"干活中"，而且下一次中断会落到没人接的地方 """
        box = self.Box(io.StringIO(), boom=RuntimeError("炸了"))
        with self.assertRaises(RuntimeError):
            box.run_command("/review")
        self.assertFalse(box.busy)


class TestCommandPushes(unittest.TestCase):
    """改了什么就推哪份清单

    `/profile` 原来推的是 push_catalog（技能/工具/MCP），档案那一页照旧显示
    切换前的状态。这类错编译器和单测都看不出来——两个方法名长得差不多，
    推错了界面只是"没反应"。
    """

    class Box(serve.Server):
        def __init__(self):
            self.busy = False
            self.commands = {}
            self.pushed = []
            self._level = None
            self.proto = serve.Protocol(io.StringIO())
            self.agent = None

        def push_status(self): pass
        def push_catalog(self): self.pushed.append("catalog")
        def push_profiles(self): self.pushed.append("profiles")
        def push_help(self): self.pushed.append("help")

    def pushed_by(self, head, rest="", **fake):
        import feiyu.__main__ as cli
        box = self.Box()
        before = {k: getattr(cli, k) for k in fake}
        for k, v in fake.items():
            setattr(cli, k, v)
        try:
            box._dispatch(cli, head, rest)
        finally:
            for k, v in before.items():
                setattr(cli, k, v)
        return box.pushed

    def test_profile_推档案那份(self):
        got = self.pushed_by("/profile", "甲", _profile=lambda a, r: "换好了")
        self.assertIn("profiles", got)
        self.assertNotIn("catalog", got)

    def test_skills_推技能那份(self):
        got = self.pushed_by("/skills", "甲", _skills=lambda a, c, r="": "列表")
        self.assertIn("catalog", got)
        self.assertNotIn("profiles", got)


class TestSettingsCommands(unittest.TestCase):
    """设置那一页按一下 = 发一条斜杠命令，**那条命令服务端得真认**

    这是跨两种语言的契约：前端拼字符串，后端 `_dispatch` 认前缀。
    两边各自的测试都看不见对方 —— `/memory rm 1` 就这么混过去了
    （真语法是 `forget`），按钮按下去只回一句"未知用法"，界面上毫无反应。

    所以这条用例直接**从 app.js 里把命令扒出来**，一条条真喂给服务端。
    加新设置项时不用改这里，扒出来的自动就多一条。
    """

    APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "desktop", "ui", "app.js")
    # 每个前缀配一个合法参数，好把命令拼完整
    ARGS = {
        "/mode ": "auto", "/style ": "concise", "/model ": "某模型",
        "/helper ": "off", "/sandbox ": "off", "/verify ": "off",
        "/allow ": "Bash(ls:*)", "/memory forget ": "1",
    }

    def commands(self):
        with open(self.APP, encoding="utf-8") as f:
            found = re.findall(r'tune\("(/[^"]*)"', f.read())
        self.assertTrue(found, "app.js 里一条 tune() 都没扒到，正则该改了")
        return sorted(set(found))

    def test_每条命令服务端都认(self):
        import feiyu.__main__ as cli

        said = []

        class Box(serve.Server):
            def __init__(self):
                self.busy = False
                self.commands = {}
                self.agent = None
                self._level = None
                self.proto = self

            def send(self, kind, **payload):
                if kind == "note":
                    said.append(payload.get("text", ""))

            def push_settings(self): pass
            def push_status(self): pass
            def push_catalog(self): pass
            def push_profiles(self): pass

        box = Box()
        for prefix in self.commands():
            self.assertIn(prefix, self.ARGS,
                          "app.js 里多了一条没登记的命令 %r，补进 ARGS 再跑" % prefix)
            said.clear()
            head, _, rest = (prefix + self.ARGS[prefix]).partition(" ")
            # 只要没落到"未知命令 / 未知用法"就算认得；真跑的效果各自的用例管
            for fn in ("_mode", "_style", "_model", "_helper", "_sandbox",
                       "_verify", "_allow", "_memory", "_profile", "_skills"):
                setattr(cli, fn, getattr(cli, fn))
            try:
                box._dispatch(cli, head, rest.strip())
            except Exception as exc:
                # agent 是 None，真跑会炸——炸在哪儿不重要，只要不是"没这条命令"
                self.assertNotIsInstance(exc, KeyError, prefix)
            joined = " ".join(said)
            self.assertNotIn("未知命令", joined, prefix)
            self.assertNotIn("未知用法", joined, "%s：%s" % (prefix, joined))


class TestCommandLevel(unittest.TestCase):
    """斜杠命令报错，桌面端也得看着像报错

    `run_command` 原来把 ui 的事件整个 `paused` 掉，再一律按 notice 发出去 ——
    于是 `/mode 没这个模式` 跟切换成功长得一模一样。等级本来就报过，只是被扔了。
    """

    class Box(serve.Server):
        def __init__(self, out):
            self.busy = False
            self.commands = {}
            self.agent = None
            self._level = None
            self.said = []
            self.proto = serve.Protocol(out)

        def push_status(self): pass
        def push_settings(self): pass

        def _dispatch(self, cli, head, rest):
            # 装成一条会报错的命令：跟 __main__ 里那些一样，内部拿 ui.error 拼
            self._say(ui.error("没有 %s 这个模式" % rest) if rest == "坏的"
                      else ui.notice("已切到「%s」" % rest))

    def levels(self, out):
        return [json.loads(l)["level"] for l in out.getvalue().splitlines()
                if l.strip() and json.loads(l).get("kind") == "note"]

    def test_报错发_error_成功发_notice(self):
        out = io.StringIO()
        box = self.Box(out)
        box.run_command("/mode 坏的")
        box.run_command("/mode auto")
        self.assertEqual(self.levels(out), ["error", "notice"])

    def test_一趟里报过错就按最高那一档(self):
        """命令内部先 notice 再 error 的话，整条要按 error 发 """
        out = io.StringIO()
        box = self.Box(out)
        box._dispatch = lambda cli, head, rest: box._say(
            ui.notice("先说一句") + ui.error("然后出错") + ui.notice("再说一句"))
        box.run_command("/x")
        self.assertEqual(self.levels(out), ["error"])

    def test_跑完要把捕获器摘掉(self):
        """不摘的话下一轮的 ui 输出会被这个已经过期的收集器接走 """
        out = io.StringIO()
        box = self.Box(out)
        box.run_command("/mode auto")
        self.assertIsNone(box._level)
        self.assertFalse(events.active())


class TestKeys(unittest.TestCase):
    """模型配置能在桌面端改，但**密钥只进不出**

    0.50.4 定过"密钥的增删只在终端做"，理由是网页端**听在回环上**——
    同机任何进程都能连（0.50.1 那三道门就是为它设的）。桌面端不监听端口，
    走进程内通道，那条理由对它不成立，所以对它放开。

    放开之后要守住的是另一条：**服务端从来不把已经存下的 key 发给前端**，
    前端就算被人拿下也读不走。
    """

    class Box(serve.Server):
        def __init__(self, agent, out, *, trusted):
            self.agent = agent
            self.trusted = trusted
            self.busy = False
            self.commands = {}
            self._level = None
            self.workspace = agent.toolbox.root      # status() 要它
            self.proto = serve.Protocol(out)

        def push_status(self): pass
        def push_settings(self): pass
        def push_help(self): pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for mod, attr in ((keys, "STORE"), (keys, "STATE"), (permissions, "STORE"),
                          (enabled, "STORE"), (tools_mod, "IMAGE_CONFIG")):
            before = getattr(mod, attr)
            setattr(mod, attr, os.path.join(self.tmp.name, attr.lower() + ".json"))
            self.addCleanup(setattr, mod, attr, before)
        self.agent = Feiyu(Path(self.tmp.name))
        self.out = io.StringIO()

    def box(self, trusted=True):
        return self.Box(self.agent, self.out, trusted=trusted)

    def rows(self):
        got = [json.loads(l) for l in self.out.getvalue().splitlines() if l.strip()]
        last = [r for r in got if r.get("kind") == "models"]
        return last[-1]["rows"] if last else []

    def add(self, box, **over):
        row = {"name": "甲", "model": "x/y", "base_url": "https://a/v1",
               "key": "sk-绝密", "vision": True, "price": ["0.1", "1", "2"]}
        row.update(over)
        box.key_write(row)

    def test_加一条然后列出来(self):
        box = self.box()
        self.add(box)
        box.push_models()
        got = self.rows()
        self.assertEqual([r["name"] for r in got], ["甲"])
        self.assertTrue(got[0]["has_key"])
        self.assertEqual(got[0]["price"], [0.1, 1.0, 2.0])

    def test_密钥绝不出现在发出去的任何一个字节里(self):
        """这是这块唯一要守死的东西 """
        box = self.box()
        self.add(box)
        box.push_models()
        box.push_settings = serve.Server.push_settings          # 真跑一遍设置那条
        self.assertNotIn("sk-绝密", self.out.getvalue())
        self.assertNotIn("key", {k for r in self.rows() for k in r})

    def test_key_留空就是不动原来那条(self):
        box = self.box()
        self.add(box)
        self.add(box, key="", model="x/y2")
        self.assertEqual(keys.load()["甲"]["key"], "sk-绝密")
        self.assertEqual(keys.load()["甲"]["model"], "x/y2")

    def test_没带的字段不动_带了空才清(self):
        """只想改模型 id 的请求不该把价格和上限一起抹掉 """
        box = self.box()
        self.add(box, limit="64000")
        box.key_write({"name": "甲", "model": "x/y2"})
        self.assertEqual(keys.load()["甲"]["price"], {"hit": 0.1, "input": 1.0, "output": 2.0})
        self.assertEqual(keys.load()["甲"]["limit"], 64000)
        box.key_write({"name": "甲", "model": "x/y2", "price": [], "limit": ""})
        self.assertIsNone(keys.load()["甲"]["price"])
        self.assertIsNone(keys.load()["甲"]["limit"])

    def test_面板填的价要真能算出钱(self):
        """**存进去的形状得是 `_as_rates` 认的那个。**

        原来面板存的是列表、`/key add` 存的是 dict，而 `_as_rates` 只认后者 ——
        于是面板上填了价照样 `priced=False`、一分钱不显示（界面上那句提示说的是
        "不填就只显示 token 数"，填了也一样，看着像坏了）。这条断言从存盘一路
        走到 `rates`，形状再漂开就会红。
        """
        box = self.box()
        self.add(box)
        self.assertEqual(_as_rates(keys.load()["甲"]["price"]), (0.1, 1.0, 2.0))

    def test_改别的字段不会把面板读不到的写入价冲掉(self):
        """面板上没有「缓存写入价」这一格，它不该因为存了一次就消失 """
        box = self.box()
        self.add(box)
        keys.add("甲", base_url="https://a/v1", model="x/y", key="sk-绝密",
                 price={"hit": 0.1, "input": 1.0, "output": 2.0, "write": 1.25})
        box.key_write({"name": "甲", "model": "x/y", "price": ["0.1", "1", "2"]})
        self.assertEqual(_as_rates(keys.load()["甲"]["price"]), (0.1, 1.0, 2.0, 1.25))

    def test_回一趟面板再存回来_价格不丢(self):
        """push_models 出去的形状，原样送回来必须还是同一个价

        两边形状不一样的时候这条就是**抹掉价格**：表单读不出 dict 里的值，
        回来是三个空格子，`merged()` 那段注释要防的事故照样发生。
        """
        box = self.box()
        self.add(box)
        box.push_models()
        shown = self.rows()[0]["price"]
        box.key_write({"name": "甲", "model": "x/y", "price": shown})
        self.assertEqual(_as_rates(keys.load()["甲"]["price"]), (0.1, 1.0, 2.0))

    def test_没填价格时状态里的_cost_是_None(self):
        """**`round(None)` 会把整个 --serve 带走。**

        `status()` 在每一处 push_status 上，连主循环那道兜底 except 自己都要调它，
        所以它一抛异常就不只是"这条没处理成"——是进程没了，桌面端表现为窗口不响应。
        没配价格（`priced=False`）是常态，不是异常输入。
        """
        box = self.box()
        self.add(box, price=[])
        self.agent.backend.priced = False
        self.assertIsNone(self.agent.cost)
        self.assertIsNone(box.status()["cost"])

    def test_新配置没有_key_就不给存(self):
        box = self.box()
        self.add(box, key="")
        self.assertEqual(keys.load(), {})

    def test_名字或模型_id_空的不给存(self):
        box = self.box()
        self.add(box, name="")
        self.add(box, model="")
        self.assertEqual(keys.load(), {})

    def test_加完第一条就直接用上(self):
        """不然加完还要再去 /model 选一次 """
        box = self.box()
        self.add(box)
        self.assertEqual(self.agent.name, "甲")

    def test_删掉在用的那条要换一条还在的(self):
        box = self.box()
        self.add(box)
        self.add(box, name="乙")
        self.agent.use_model("甲")
        box.key_drop("甲")
        self.assertEqual(list(keys.load()), ["乙"])
        self.assertEqual(self.agent.name, "乙")

    def test_删光了就不再是_ready(self):
        box = self.box()
        self.add(box)
        box.key_drop("甲")
        self.assertFalse(self.agent.ready)

    def test_听在端口上的那条路一律拒(self):
        """devserver 听在回环上，同机任何进程都能连 —— 它不该改得了密钥 """
        box = self.box(trusted=False)
        self.add(box)
        self.assertEqual(keys.load(), {})
        self.add(self.box())            # 先加一条，好验删除
        box.key_drop("甲")
        self.assertIn("甲", keys.load())

    def test_删一条不存在的要说一声(self):
        box = self.box()
        box.key_drop("没这条")
        self.assertIn("清单里没有", self.out.getvalue())


class TestCredits(unittest.TestCase):
    """接口那边的余额

    **跟 `/cost` 不是一回事**：那个是按你填的价格估的，这个是人家账上的数。
    查不到就整个不显示 —— 摆一个编的数比不显示糟得多。
    """

    def setUp(self):
        credits_mod.forget()
        self.addCleanup(credits_mod.forget)

    def fake(self, body, *, seen=None):
        def get(url, key, base):
            if seen is not None:
                seen.append((url, key, base))
            return body
        before = credits_mod._get
        credits_mod._get = get
        self.addCleanup(setattr, credits_mod, "_get", before)

    def test_openrouter_算的是充值减用量(self):
        self.fake({"data": {"total_credits": 10, "total_usage": 0.25}})
        got, why = credits_mod.look({"base_url": "https://openrouter.ai/api/v1", "key": "k"})
        self.assertEqual(why, "")
        self.assertAlmostEqual(got.left, 9.75)
        self.assertEqual(got.shown(), "$9.75")

    def test_认不出的接口照实说_不猜(self):
        got, why = credits_mod.look({"base_url": "https://api.openai.com/v1", "key": "k"})
        self.assertIsNone(got)
        self.assertIn("没有查额度的地方", why)

    def test_没填接口地址就不查(self):
        got, why = credits_mod.look({"key": "k"})
        self.assertIsNone(got)
        self.assertIn("没填接口地址", why)

    def test_密钥只发给配置里那个_host(self):
        """查个余额把 key 发到第三方去，比查不到余额糟糕得多 """
        self.assertIsNone(credits_mod._get(
            "https://evil.example/credits", "k", "https://openrouter.ai/api/v1"))
        self.assertIsNotNone(credits_mod._get.__doc__)

    def test_同一个账号只问一次(self):
        seen = []
        self.fake({"data": {"total_credits": 10, "total_usage": 1}}, seen=seen)
        one = {"base_url": "https://openrouter.ai/api/v1", "key": "k"}
        for _ in range(4):
            credits_mod.look(one)
        self.assertEqual(len(seen), 1, seen)

    def test_失败缓存得比成功短得多(self):
        """一次网络抖动不该让余额消失一分钟 —— 实测撞过 """
        self.assertLess(credits_mod.FAIL_TTL, credits_mod.TTL / 4)

    def test_fresh_能绕过缓存(self):
        seen = []
        self.fake({"data": {"total_credits": 10, "total_usage": 1}}, seen=seen)
        one = {"base_url": "https://openrouter.ai/api/v1", "key": "k"}
        credits_mod.look(one)
        credits_mod.look(one, fresh=True)
        self.assertEqual(len(seen), 2)

    def test_形状对不上就当查不到(self):
        for bad in ({}, {"data": {}}, {"data": {"total_credits": "十块"}}, None):
            credits_mod.forget()
            self.fake(bad)
            got, why = credits_mod.look({"base_url": "https://openrouter.ai/api/v1", "key": "k"})
            self.assertIsNone(got, bad)
            self.assertTrue(why)

    def test_只给用量不给余额的也显示得出来(self):
        one = credits_mod.Balance(left=None, used=3.5, total=None, unit="¥")
        self.assertEqual(one.shown(), "已用 ¥3.50")

    def test_状态里带上余额_查不到就只带理由(self):
        class Fake:
            ready = True
            mode = style = profile = "x"
            bot = ""
            model = name = "m"
            tokens = 1
            cost = 0.0
            context = 1
            context_limit = 100
            credits = credits_mod.Balance(left=9.75, used=0.25, total=10, unit="$")
            credits_note = ""

        class Box(serve.Server):
            def __init__(self, agent):
                self.agent = agent
                self.workspace = "/tmp"
                self.busy = False
        box = Box(Fake())
        self.assertEqual(box.status()["credits"]["shown"], "$9.75")
        Fake.credits, Fake.credits_note = None, "查不到"
        self.assertEqual(box.status()["credits"], {"why": "查不到"})

    def test_cached_不发任何请求(self):
        """`/cost` 是同步的，走代理偶尔要八秒 —— 那条路只准读缓存 """
        seen = []
        self.fake({"data": {"total_credits": 10, "total_usage": 1}}, seen=seen)
        one = {"base_url": "https://openrouter.ai/api/v1", "key": "k"}
        got, why = credits_mod.look(one, cached=True)
        self.assertIsNone(got)
        self.assertIn("还没查到", why)
        self.assertEqual(seen, [])
        credits_mod.look(one)                 # 后台那次填上缓存
        self.assertIsNotNone(credits_mod.look(one, cached=True)[0])


class TestLoopSurvivesBadPayload(unittest.TestCase):
    """**一条畸形消息不能带走整个进程。**

    reader 线程 0.50.1 就整个包起来了，但 `run()` 那个循环没有 —— 而它跑的是
    真正干活的那几支。实测 `{"op":"bot","do":"save","row":[1,2,3]}` 一条就让
    整个 agent 连同后台命令、语言服务器一起没掉，用户那边只看到窗口不响应了。
    """

    def box(self, out, boom=None):
        class Box(serve.Server):
            def __init__(self):
                self.busy = True
                self.proto = serve.Protocol(out)
                self.inbox = queue.Queue()
                self.agent = None
                self.commands = {}

            def push_status(self): pass
            def push_sessions(self): pass
            def status(self): return {}
            def reader(self): pass          # 别真去读 stdin
            def new_session(self):
                if boom:
                    raise boom
        box = Box()
        box.agent = types.SimpleNamespace(refresh_credits=lambda **k: None)
        return box

    def kinds(self, out):
        return [json.loads(l)["kind"] for l in out.getvalue().splitlines() if l.strip()]

    def test_处理器抛异常时循环还活着(self):
        out = io.StringIO()
        box = self.box(out, boom=TypeError("'list' object is not a mapping"))
        box.inbox.put(("new", "x"))
        box.inbox.put(("stop", ""))
        self.assertEqual(box.run(), 0)          # 正常退出，不是抛出去
        said = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        errs = [e for e in said if e.get("kind") == "note" and e.get("level") == "error"]
        self.assertTrue(errs, said)             # 而且要说一声，不能默默吞掉
        self.assertIn("TypeError", errs[0]["text"])
        self.assertFalse(box.busy)              # 忙标志要清，否则界面一直转

    def test_畸形的_bot_save_不再抛(self):
        """`row` 是列表时 `{**row}` 会 TypeError —— 这条是真撞出来的 """
        out = io.StringIO()
        box = self.box(out)
        got = []
        box.bot_write = got.append
        box.inbox.put(("bot", json.dumps({"do": "save", "name": "甲", "row": [1, 2, 3]})))
        box.inbox.put(("stop", ""))
        self.assertEqual(box.run(), 0)
        self.assertEqual(got, [{"name": "甲"}])   # 认不出的 row 当空的，不带崩


class TestApiHints(unittest.TestCase):
    """接口报错要翻成人话，而且**别翻错** """

    def explain(self, text, code=None):
        from feiyu import __main__ as cli
        exc = Exception(text)
        if code is not None:
            exc.status_code = code
        return cli._explain(exc)

    def test_request_id_里恰好有_401_不能说成_key_过期(self):
        said = self.explain(
            "Error code: 400 - {'message': 'Provider returned error "
            "(request id: 20260828401547313053226)'}")
        self.assertNotIn("key", said)

    def test_按状态码认(self):
        self.assertIn("key", self.explain("Error code: 401 - bad key"))
        self.assertIn("限流", self.explain("Error code: 429 - slow down"))

    def test_sdk_自己带的状态码优先(self):
        self.assertIn("模型 id", self.explain("Not Found", code=404))

    def test_余额不足和超上下文按措辞认(self):
        self.assertIn("余额", self.explain(
            "Error code: 400 - {'message': 'credit insufficient balance: balance=1'}"))
        self.assertIn("上下文", self.explain(
            "Error code: 400 - {'message': 'Input token exceed the limit'}"))

    def test_认不出就一个字不加(self):
        self.assertEqual(self.explain("Error code: 400 - something new"), "")

    def test_桌面端也吃这份提示(self):
        """0.26.0 给终端加过，桌面端这条路一直在原样甩 JSON """
        src = pathlib.Path("feiyu/serve.py").read_text(encoding="utf-8")
        self.assertIn("cli._explain(exc)", src)


class TestPaths(unittest.TestCase):
    """`@` 补全的候选走 reader 线程，不进 inbox

    进 inbox 的话它会排在正在跑的那一轮后面 —— 补全菜单成了"打完一句才弹出来"，
    等于没有。这条路只读一层目录、不碰 agent 的任何状态，所以可以就地答。
    """

    class Box(serve.Server):
        def __init__(self, workspace, out):
            self.workspace = workspace
            self.inbox = queue.Queue()
            self.busy = False
            self.proto = serve.Protocol(out)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "feiyu").mkdir()
        (self.root / "README.md").write_text("x\n", encoding="utf-8")
        self.out = io.StringIO()
        self.box = self.Box(self.root, self.out)

    def sent(self):
        return [json.loads(l) for l in self.out.getvalue().splitlines() if l.strip()]

    def test_就地答不排队(self):
        self.box._dispatch_in(json.dumps({"op": "paths", "prefix": ""}))
        self.assertTrue(self.box.inbox.empty())
        row = self.sent()[-1]
        self.assertEqual(row["kind"], "paths")
        self.assertEqual(row["prefix"], "")
        self.assertIn("feiyu/", row["rows"])

    def test_带上问的是哪一截(self):
        """前端拿 prefix 对一下才知道这份候选还算不算数（打字比往返快）"""
        self.box._dispatch_in(json.dumps({"op": "paths", "prefix": "READ"}))
        row = self.sent()[-1]
        self.assertEqual(row["prefix"], "READ")
        self.assertEqual(row["rows"], ["README.md"])

    def test_越界的问法答空(self):
        self.box._dispatch_in(json.dumps({"op": "paths", "prefix": "../"}))
        self.assertEqual(self.sent()[-1]["rows"], [])

    def test_prefix_不是字符串也不崩(self):
        self.box._dispatch_in(json.dumps({"op": "paths", "prefix": {"a": 1}}))
        self.assertEqual(self.sent()[-1]["rows"], [])
