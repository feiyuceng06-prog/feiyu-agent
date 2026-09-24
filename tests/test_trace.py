"""结构化日志与花费汇总 日志坏了永远不能弄崩主流程，所以这里也测"写不进去会怎样" """

import json
import os
import stat
import tempfile
import unittest

from feiyu import trace


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = trace.DIR
        trace.DIR = os.path.join(self.tmp.name, "logs")

    def tearDown(self):
        trace.DIR = self._saved
        self.tmp.cleanup()

    def rows(self):
        with open(trace.today_path(), encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class TestWrite(Base):
    def test_一件事一行(self):
        t = trace.Trace("/ws")
        t.write("tool", name="bash", ok=True)
        t.write("tool", name="read_file", ok=True)
        self.assertEqual(len(self.rows()), 2)

    def test_每行带_run_和工作区(self):
        t = trace.Trace("/ws")
        t.write("tool", name="bash")
        row = self.rows()[0]
        self.assertEqual(row["run"], t.run)
        self.assertEqual(row["workspace"], "/ws")
        self.assertEqual(row["kind"], "tool")
        self.assertIn("ts", row)

    def test_同一次运行共用一个_run(self):
        t = trace.Trace("/ws")
        t.write("a")
        t.write("b")
        self.assertEqual(len({r["run"] for r in self.rows()}), 1)

    def test_两次运行的_run_不同(self):
        self.assertNotEqual(trace.Trace("/ws").run, trace.Trace("/ws").run)

    def test_文件是_600(self):
        # 日志里有命令原文和文件路径
        trace.Trace("/ws").write("tool", name="bash")
        self.assertEqual(stat.S_IMODE(os.stat(trace.today_path()).st_mode), 0o600)

    def test_长参数截断(self):
        trace.Trace("/ws").write("tool", args={"content": "x" * 5000})
        self.assertLess(len(self.rows()[0]["args"]["content"]), 400)

    def test_None_字段不写进去(self):
        trace.Trace("/ws").write("tool", name="bash", why=None)
        self.assertNotIn("why", self.rows()[0])

    def test_关掉之后什么都不写(self):
        trace.Trace("/ws", enabled=False).write("tool", name="bash")
        self.assertFalse(os.path.exists(trace.today_path()))

    def test_环境变量能关掉(self):
        os.environ["FEIYU_NO_LOG"] = "1"
        try:
            trace.Trace("/ws").write("tool", name="bash")
            self.assertFalse(os.path.exists(trace.today_path()))
        finally:
            del os.environ["FEIYU_NO_LOG"]

    def test_写不进去也不抛异常(self):
        # 目录建不出来时必须安静地放弃，不能把正在干的活带崩
        trace.DIR = "/proc/绝对建不出来的目录"
        trace.Trace("/ws").write("tool", name="bash")

    def test_塞不进_json_的值也不崩(self):
        trace.Trace("/ws").write("tool", args={"x": object()})


class TestRead(Base):
    def test_只要指定工作区的(self):
        trace.Trace("/a").write("tool")
        trace.Trace("/b").write("tool")
        self.assertEqual(len(trace.read(workspace="/a")), 1)
        self.assertEqual(len(trace.read()), 2)

    def test_没有日志就返回空(self):
        self.assertEqual(trace.read(), [])

    def test_坏行跳过不影响别的(self):
        trace.Trace("/a").write("tool")
        with open(trace.today_path(), "a", encoding="utf-8") as f:
            f.write("这不是 json\n")
        self.assertEqual(len(trace.read()), 1)


class TestSummarize(Base):
    def make(self, run, *, cost=None, tools=0, denied=0):
        rows = [{"run": run, "ts": 100, "kind": "run_start", "model": "m", "task": "干活"}]
        rows.append({"run": run, "ts": 101, "kind": "turn", "tokens": 500, "cost": cost})
        for i in range(tools):
            rows.append({"run": run, "ts": 102, "kind": "tool", "name": "bash",
                         "ok": i >= denied})
        return rows

    def test_按_run_分组(self):
        out = trace.summarize(self.make("a") + self.make("b"))
        self.assertEqual(set(out), {"a", "b"})

    def test_累加_token_和花费(self):
        out = trace.summarize(self.make("a", cost=0.5) + self.make("a", cost=0.25))
        self.assertEqual(out["a"]["tokens"], 1000)
        self.assertAlmostEqual(out["a"]["cost"], 0.75)
        self.assertTrue(out["a"]["priced"])

    def test_没配价格就不标记为已计价(self):
        self.assertFalse(trace.summarize(self.make("a"))["a"]["priced"])

    def test_数工具和被拒的(self):
        out = trace.summarize(self.make("a", tools=4, denied=2))
        self.assertEqual(out["a"]["tools"], 4)
        self.assertEqual(out["a"]["denied"], 2)

    def test_带上任务描述(self):
        self.assertEqual(trace.summarize(self.make("a"))["a"]["task"], "干活")

    def test_空输入(self):
        self.assertEqual(trace.summarize([]), {})


if __name__ == "__main__":
    unittest.main()


class TestSlowestTurn(unittest.TestCase):
    """一个任务里九轮五秒、一轮十分钟，只看平均那十分钟就被摊没了 ——
    而要查的正是它。所以 summarize 单独记最慢那一轮。"""

    def rows(self, *ms):
        out = [{"run": "r", "ts": 0, "kind": "run_start", "model": "m", "task": "干活"}]
        for n, one in enumerate(ms):
            out.append({"run": "r", "ts": n, "kind": "turn", "round": n, "ms": one,
                        "tokens": 100, "input": 90, "hit": 80, "out": 10, "cost": 0.1})
        return out

    def test_记下最慢那一轮和总时长(self):
        run = trace.summarize(self.rows(5000, 5000, 622000, 5000))["r"]
        self.assertEqual(run["slowest"], 622000)
        self.assertEqual(run["ms"], 637000)
        self.assertEqual(run["out"], 40)

    def test_没有_ms_的老记录不崩(self):
        rows = self.rows(5000)
        del rows[1]["ms"]
        del rows[1]["out"]
        run = trace.summarize(rows)["r"]
        self.assertEqual(run["slowest"], 0)
        self.assertEqual(run["out"], 0)
