"""并行工具调用

只读工具整批一起跑，掺进一个写类或 bash 就整批串行 判断放在批一级：
「读三个文件、然后按内容写一个」这种批次里，写那步依赖前面读的结果，
挑着并行等于乱序执行
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from feiyu import agent as agent_mod
from feiyu.agent import Feiyu
from feiyu.tools import ToolResult
from tests.fakes import FakeBackend, call, unpaired
from tests.test_agent import quiet


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.mode = "auto"
        self.started = []
        self.lock = threading.Lock()

    def tearDown(self):
        self.tmp.cleanup()

    def use(self, calls):
        self.agent.backend = FakeBackend([("", calls), ("好了", [])])
        return self.agent.backend

    def slow_tools(self, delay=0.2):
        """记下每个工具的起止时刻，用来判断到底有没有同时在跑 """
        def run(name, args):
            with self.lock:
                self.started.append((name, time.monotonic()))
            time.sleep(delay)
            return ToolResult("内容 " + name, summary="读了 " + name)
        self.agent.toolbox.run = run

    def results(self, backend):
        return [m["content"] for m in backend.messages if m["role"] == "tool"]


class TestGoesParallel(Base):
    def test_整批只读会同时跑(self):
        reads = [call("c%d" % i, "read_file", path="%d.py" % i) for i in range(4)]
        self.use(reads)
        self.slow_tools(0.2)
        began = time.monotonic()
        quiet(self.agent.send, "读四个文件")
        spent = time.monotonic() - began
        # 串行要 0.8 秒，并行应该接近 0.2
        self.assertLess(spent, 0.5, "看起来还是串行的：%.2fs" % spent)

    def test_四个是同时开始的(self):
        reads = [call("c%d" % i, "read_file", path="%d.py" % i) for i in range(4)]
        self.use(reads)
        self.slow_tools(0.2)
        quiet(self.agent.send, "读四个文件")
        stamps = [t for _, t in self.started]
        self.assertLess(max(stamps) - min(stamps), 0.15)

    def test_只读工具的名单(self):
        # task 也在里面：子 agent 的工具集限死在只读三件，本身不改任何东西
        self.assertEqual(
            agent_mod.PARALLEL_SAFE,
            {"read_file", "list_files", "search", "web_fetch", "web_search", "task", "symbol",
         "skill"})

    def test_结果按调用顺序排(self):
        # 谁先跑完是不确定的，但回给模型的顺序必须是定的
        reads = [call("c%d" % i, "read_file", path="%d.py" % i) for i in range(4)]
        b = self.use(reads)
        def run(name, args):
            # 让后面的先跑完，故意打乱完成顺序
            time.sleep(0.05 * (4 - int(args["path"][0])))
            return ToolResult("内容 " + args["path"], summary="读了")
        self.agent.toolbox.run = run
        quiet(self.agent.send, "读四个文件")
        ids = [m["tool_call_id"] for m in b.messages if m["role"] == "tool"]
        self.assertEqual(ids, ["c0", "c1", "c2", "c3"])

    def test_每个结果配到自己的调用(self):
        reads = [call("c%d" % i, "read_file", path="%d.py" % i) for i in range(3)]
        b = self.use(reads)
        self.agent.toolbox.run = lambda n, a: ToolResult("我是 " + a["path"], summary="ok")
        quiet(self.agent.send, "读三个文件")
        got = {m["tool_call_id"]: m["content"] for m in b.messages if m["role"] == "tool"}
        self.assertEqual(got["c0"], "我是 0.py")
        self.assertEqual(got["c2"], "我是 2.py")
        self.assertEqual(unpaired(b.messages), set())


class TestStaysSerial(Base):
    def test_掺了_bash_就整批串行(self):
        # bash 共享 self.cwd，两条并发的 cd 会互相盖掉
        mixed = [call("c1", "read_file", path="a.py"),
                 call("c2", "bash", command="ls"),
                 call("c3", "read_file", path="b.py")]
        self.use(mixed)
        self.slow_tools(0.15)
        began = time.monotonic()
        quiet(self.agent.send, "混着来")
        self.assertGreater(time.monotonic() - began, 0.4)

    def test_掺了写就整批串行(self):
        mixed = [call("c1", "read_file", path="a.py"),
                 call("c2", "write_file", path="b.py", content="x")]
        self.use(mixed)
        self.slow_tools(0.2)
        began = time.monotonic()
        quiet(self.agent.send, "读了再写")
        self.assertGreater(time.monotonic() - began, 0.35)

    def test_单个调用不走并行(self):
        self.use([call("c1", "read_file", path="a.py")])
        self.slow_tools(0.05)
        quiet(self.agent.send, "读一个")
        self.assertEqual(len(self.started), 1)

    def test_参数解析失败的批次串行(self):
        bad = call("c1", "read_file", path="a.py")
        bad.error = "参数不是完整的 JSON"
        self.use([bad, call("c2", "read_file", path="b.py")])
        self.agent.toolbox.run = lambda n, a: ToolResult("ok", summary="ok")
        b = self.agent.backend
        quiet(self.agent.send, "两个读")
        self.assertIn("JSON", self.results(b)[0])

    def test_写类批次照旧回报改动(self):
        # 串行那条路要继续返回 (工具名, 主参数) 给 PostToolUse 匹配
        self.use([call("c1", "write_file", path="a.py", content="x")])
        self.agent.toolbox.run = lambda n, a: ToolResult("ok", summary="ok")
        outputs = []
        changed = quiet(self.agent._run_tools,
                        [call("c1", "write_file", path="a.py", content="x")], outputs)
        self.assertEqual(changed[0], "write_file")


class TestGating(Base):
    def test_被拒的也有结果不破坏配对(self):
        self.agent.mode = "readonly"
        reads = [call("c1", "read_file", path="a.py"),
                 call("c2", "web_fetch", url="https://x.com")]
        b = self.use(reads)
        self.agent.toolbox.run = lambda n, a: ToolResult("ok", summary="ok")
        quiet(self.agent.send, "读和抓")
        self.assertEqual(unpaired(b.messages), set())

    def test_确认只在主线程问一次一个(self):
        # ui.confirm 要读 stdin，两个线程同时问就乱了
        self.agent.mode = "default"
        asking = []
        def approve(name, args):
            with self.lock:
                asking.append(1)
                self.assertEqual(len(asking), 1)   # 同一时刻只能有一个在问
                asking.pop()
            return "yes"
        self.agent.approve = approve
        fetches = [call("c%d" % i, "web_fetch", url="https://x%d.com" % i) for i in range(3)]
        b = self.use(fetches)
        self.agent.toolbox.run = lambda n, a: ToolResult("ok", summary="ok")
        quiet(self.agent.send, "抓三个")
        self.assertEqual(unpaired(b.messages), set())

    def test_钩子能拦下并行批次里的一个(self):
        from feiyu import hooks
        self.agent.hooks = [hooks.Hook(
            "PreToolUse", {"matcher": "Fetch", "command": "echo 不许抓 >&2; exit 1"}, "测试")]
        mixed = [call("c1", "read_file", path="a.py"),
                 call("c2", "web_fetch", url="https://x.com")]
        b = self.use(mixed)
        self.agent.toolbox.run = lambda n, a: ToolResult("ok", summary="ok")
        quiet(self.agent.send, "读和抓")
        got = {m["tool_call_id"]: m["content"] for m in b.messages if m["role"] == "tool"}
        self.assertIn("不许抓", got["c2"])
        self.assertEqual(got["c1"], "ok")


class TestInterrupt(Base):
    def test_并行途中被打断历史仍然配对(self):
        # 这是整个项目最要紧的不变量：少一条配对结果，会话就废了
        reads = [call("c%d" % i, "read_file", path="%d.py" % i) for i in range(4)]
        b = self.use(reads)
        def run(name, args):
            if args["path"].startswith("2"):
                raise KeyboardInterrupt()
            time.sleep(0.05)
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "读四个文件")
        self.assertEqual(unpaired(b.messages), set())

    def test_打断后每个调用都有结果(self):
        reads = [call("c%d" % i, "read_file", path="%d.py" % i) for i in range(3)]
        b = self.use(reads)
        self.agent.toolbox.run = lambda n, a: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "读三个文件")
        ids = {m["tool_call_id"] for m in b.messages if m["role"] == "tool"}
        self.assertEqual(ids, {"c0", "c1", "c2"})


if __name__ == "__main__":
    unittest.main()


class TestAllowAll(Base):
    """「本轮都允许」在并行那一批里也得算数

    原来并行走的是 `_deny_reason`，那里 allow_all 写死是 False：四条 web_fetch
    你在第一条上按了「都允许」，后三条照样问、照样被拒。用户拿截图来问过 ——
    一屏红字「没有执行」，而他明明点了允许。
    """

    def setUp(self):
        super().setUp()
        self.agent.mode = "default"
        self.asked = []
        self.agent.toolbox.run = lambda n, a: ToolResult("ok", summary="ok")

    def fetches(self, n=4):
        return [call("c%d" % i, "web_fetch", url="https://x%d.com" % i) for i in range(n)]

    def answer(self, said):
        def approve(name, args):
            self.asked.append(args.get("url"))
            return said if len(self.asked) == 1 else "no"   # 只在第一条上答应
        self.agent.approve = approve

    def ran(self, backend):
        return [m["content"] for m in backend.messages if m["role"] == "tool"]

    def test_第一条答都允许_后面几条不再问也不被拒(self):
        self.answer("always")
        b = self.use(self.fetches())
        quiet(self.agent.send, "抓四个")
        self.assertEqual(len(self.asked), 1, self.asked)      # 只问了一次
        self.assertEqual(self.ran(b), ["ok"] * 4)
        self.assertEqual(unpaired(b.messages), set())

    def test_第一条答只这次_后面几条照旧要问(self):
        self.answer("yes")
        b = self.use(self.fetches())
        quiet(self.agent.send, "抓四个")
        self.assertEqual(len(self.asked), 4)
        # 第一条跑了，后三条被拒
        self.assertEqual(self.ran(b)[0], "ok")
        self.assertTrue(all("拒绝" in r for r in self.ran(b)[1:]), self.ran(b))

    def test_记住那条会写进规则(self):
        from feiyu import permissions
        import os
        permissions.STORE = os.path.join(self.tmp.name, "permissions.json")
        self.answer("remember")
        b = self.use(self.fetches(2))
        quiet(self.agent.send, "抓两个")
        self.assertIn("Fetch(*)", permissions.load()["allow"])
        self.assertEqual(self.ran(b), ["ok", "ok"])


class TestImages(Base):
    """并行那条路不能把图弄丢

    `read_file` 在 PARALLEL_SAFE 里，所以"读一张图 + 读一个文件"就走并行 ——
    而图是挂在 `ToolResult.image` 上、由后端另起一条消息送出去的。
    原来 `_run_parallel` 拼 `ToolOutput` 时没抄 `image`：模型收到的只有
    「（下面是 a.png，…）」这句话，图根本没发出去。同一次 read_file，
    看不看得见图取决于它旁边有没有别的调用 —— 而这个**没有任何报错**。
    """

    def images(self, backend):
        """后端为了送图另起的那几条消息 """
        return [m for m in backend.messages
                if m["role"] == "user" and isinstance(m.get("content"), list)]

    def one_image(self, name, args):
        if args.get("path", "").endswith(".png"):
            return ToolResult("（下面是 %s）" % args["path"], summary="看图",
                              image=("image/png", "AAAA"))
        return ToolResult("文本", summary="读了")

    def test_并行批里的图要送到模型手上(self):
        b = self.use([call("c1", "read_file", path="a.png"),
                      call("c2", "read_file", path="b.txt")])
        self.agent.toolbox.run = self.one_image
        quiet(self.agent.send, "看这两个")
        self.assertEqual(len(self.images(b)), 1, "并行那一批把图丢了")
        self.assertEqual(unpaired(b.messages), set())

    def test_串行和并行送出去的图一样多(self):
        """同一次读图，旁边多一个只读调用就看不见图了 —— 那是最难查的一种 """
        solo = self.use([call("c1", "read_file", path="a.png")])
        self.agent.toolbox.run = self.one_image
        quiet(self.agent.send, "看一张")
        alone = len(self.images(solo))
        self.setUp()
        pair = self.use([call("c1", "read_file", path="a.png"),
                         call("c2", "read_file", path="b.txt")])
        self.agent.toolbox.run = self.one_image
        quiet(self.agent.send, "看一张加一个文件")
        self.assertEqual(len(self.images(pair)), alone)

    def test_后台命令结束的通知不该把图顶掉(self):
        """`_note_jobs` / `_after_tools` 会重建最后那条结果，字段要抄全 """
        from feiyu.providers import ToolOutput

        outputs = [ToolOutput(id="c1", content="（下面是 a.png）",
                              image=("image/png", "AAAA"))]

        class Done:
            id, command, code = 7, "true", 0

        self.agent.toolbox.jobs.finished_since = lambda: [Done()]
        self.agent._note_jobs(outputs)
        self.assertIn("后台命令已经结束", outputs[-1].content)
        self.assertEqual(outputs[-1].image, ("image/png", "AAAA"))
