"""Terminal-Bench 那把尺子：选题定得死不死，结果分得清不清

这两件事都没法靠跑一遍评测来验（跑一遍要几个钟头、要钱、还得有 docker），
所以全在这儿用夹具验：

- `tbench_pick`：同一份输入永远出同一批题，难度构成跟全量一致
- `tbench_ab`：从基线存档里挑出**有分辨力**的那几道（挑错了的代价见 0.73.0）
- `tbench_report`：一份 `result.json` 落进哪个格子

**分类那几条是这套东西的承重墙。** 判错一条的代价不是表格难看：
「判不了」不进分母，把 agent 自己的超时丢进去，解决率会凭空变高。

这两个模块都**不 import harbor**（harbor 不是本项目依赖），所以能进主用例集；
`tbench.py` 那个适配器不行，它 `import harbor`。
"""

import json
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

from evals import report, tbench_ab, tbench_pick, tbench_report

TOML = """\
version = "1.0"

[metadata]
author_name = "某人"
difficulty = "hard"
category = "mathematics"
tags = [ "a", "b",]
expert_time_estimate_min = 480.0

[verifier]
timeout_sec = 1800.0

[agent]
timeout_sec = 3600.0
difficulty = "不该被当成题目难度"
"""


class TestMeta(unittest.TestCase):
    def test_按_section_限定(self):
        got = tbench_pick._meta(TOML)
        self.assertEqual(got["metadata.difficulty"], "hard")
        # 同名 key 落在别的 section 里，不能串台 —— 全文抓一个 `difficulty = `
        # 的话这里就会拿到下面那条
        self.assertEqual(got["agent.difficulty"], "不该被当成题目难度")
        self.assertEqual(got["agent.timeout_sec"], 3600.0)
        self.assertEqual(got["metadata.expert_time_estimate_min"], 480.0)

    def test_认不了的行原样跳过(self):
        got = tbench_pick._meta(TOML)
        self.assertNotIn("metadata.tags", got)      # 数组不认，也不能抛

    def test_不用_tomllib(self):
        """3.11 才有 tomllib，而这个项目的下限是 3.10 本机是 3.14，
        真用了这里也不会红 —— 所以直接查源码里有没有这个词
        """
        src = Path(tbench_pick.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import tomllib", src)


def _rows(easy=4, medium=55, hard=30):
    out = []
    for level, n in (("easy", easy), ("medium", medium), ("hard", hard)):
        out += [{"instance_id": "%s-%02d" % (level, i), "difficulty": level}
                for i in range(n)]
    return out


class TestPick(unittest.TestCase):
    def test_同一份输入永远同一批(self):
        rows = _rows()
        self.assertEqual(tbench_pick.chosen(rows, 24), tbench_pick.chosen(rows, 24))
        # 顺序换了也一样 —— `pick` 自己先按题名排过
        self.assertEqual(tbench_pick.chosen(rows, 24),
                         tbench_pick.chosen(list(reversed(rows)), 24))

    def test_难度构成跟全量一致(self):
        rows = _rows()
        got = tbench_pick.chosen(rows, 24)
        self.assertEqual(len(got), 24)
        share = {}
        for name in got:
            share[name.split("-")[0]] = share.get(name.split("-")[0], 0) + 1
        self.assertEqual(share, {"easy": 1, "medium": 15, "hard": 8})

    def test_子集文件跟算出来的一致(self):
        """本机有题面缓存时才跑 改了 `COUNT` 或者换了数据集版本却忘了
        `--write`，这条会红
        """
        rows = tbench_pick.tasks()
        if not rows:
            self.skipTest("本地没有 harbor 题面缓存")
        self.assertEqual(tbench_pick.load(), tbench_pick.chosen(rows))


class _fake_subset:
    """当一个 `SUBSET` 用：只要 `.read_text()` 和 `.name` """
    def __init__(self, text):
        self.text, self.name = text, "tbench_ab.txt"

    def read_text(self, encoding=None):
        return self.text


def _ab_row(name, ok=True, s=600.0, graded=True, exc=None, turn=1):
    return {"suite": name, "turn": turn, "ok": ok, "s": s,
            "detail": {"graded": graded, "exception": exc}}


class TestAbPick(unittest.TestCase):
    """挑 A/B 题的规矩 0.73.0 挑错过一次：三道里两道两档都撞满 1800 秒墙钟，
    主指标被上限截断，改动有用没用量出来都一样
    """

    def test_两遍都跑完又都做出来的才要(self):
        rows = [_ab_row("甲", turn=1), _ab_row("甲", turn=2),
                _ab_row("乙", turn=1), _ab_row("乙", ok=False, turn=2)]
        self.assertEqual([n for n, _ in tbench_ab.picked(rows)], ["甲"])

    def test_撞过墙钟的不要(self):
        """撞了的那遍墙钟被截断在上限上，两档都是那个数，没有分辨力 """
        rows = [_ab_row("甲", turn=1),
                _ab_row("甲", turn=2, exc="AgentTimeoutError")]
        self.assertEqual(tbench_ab.picked(rows), [])

    def test_判不了的不要(self):
        rows = [_ab_row("甲", turn=1), _ab_row("甲", turn=2, graded=False)]
        self.assertEqual(tbench_ab.picked(rows), [])

    def test_太短的不要(self):
        """几十秒跑完的题，改动的影响淹在噪声里 """
        rows = [_ab_row("甲", s=30.0, turn=1), _ab_row("甲", s=40.0, turn=2)]
        self.assertEqual(tbench_ab.picked(rows), [])

    def test_只跑过一遍的不要(self):
        self.assertEqual(tbench_ab.picked([_ab_row("甲")]), [])

    def test_软闸按各自的中位算(self):
        """**必须一题一个值**：挑出来的题自然耗时差着好几倍，统一一个秒数
        对一半太松（轮不到它说话）、对另一半太紧（还没干活就到点）
        """
        rows = [_ab_row("alpha", s=400.0, turn=1), _ab_row("alpha", s=600.0, turn=2),
                _ab_row("beta", s=1000.0, turn=1), _ab_row("beta", s=2000.0, turn=2)]
        self.assertEqual(tbench_ab.picked(rows, squeeze=0.5),
                         [("alpha", 250), ("beta", 750)])

    def test_同一份存档永远同一批(self):
        rows = [_ab_row("beta", turn=1), _ab_row("beta", turn=2),
                _ab_row("alpha", turn=1), _ab_row("alpha", turn=2)]
        self.assertEqual(tbench_ab.picked(rows), tbench_ab.picked(list(reversed(rows))))
        self.assertEqual([n for n, _ in tbench_ab.picked(rows)], ["alpha", "beta"])

    def test_写出去的软闸必须带单位(self):
        """**0.78.0 那轮 A/B 白跑就死在这儿**：文件里存的是秒，而
        `--time-budget` 的裸数按分钟算，60 倍，两档一次都没咬住 ——
        跟 0.73.0「开关到不了容器」一个形状：设了、命令也对，就是不起作用
        """
        for line in tbench_ab.SUBSET.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertRegex(line, r"^\S+ \d+s$", "软闸没带单位：%r" % line)

    def test_读到没单位的数要当场抛(self):
        """静默当成分钟是最糟的：它照样跑得下去，只是闸永远轮不到响 """
        with unittest.mock.patch.object(
                tbench_ab, "SUBSET", _fake_subset("甲 445\n")):
            with self.assertRaises(ValueError):
                tbench_ab.load()
        with unittest.mock.patch.object(
                tbench_ab, "SUBSET", _fake_subset("甲 445s\n")):
            self.assertEqual(tbench_ab.load(), [("甲", 445)])

    def test_子集文件跟基线算出来的一致(self):
        """本机有基线存档时才跑 改了 FLOOR / SQUEEZE 却忘了 `--write`，这条会红 """
        if not tbench_ab.BASELINE.exists():
            self.skipTest("本地没有 0.72.0 的基线存档")
        self.assertEqual(tbench_ab.load(),
                         tbench_ab.picked(tbench_ab._rows(tbench_ab.BASELINE)))


def _result(**kw):
    """一份 result.json 形状照真跑出来的那份抄（harbor 5.x 的 TrialResult）"""
    meta = kw.pop("meta", None)
    reward = kw.pop("reward", None)
    exc = kw.pop("exc", None)
    note = kw.pop("exc_note", "说明")
    got = {
        "task_name": kw.pop("task_name", "某题"),
        "trial_name": "某题__ABC1234",
        "agent_info": {"name": "feiyu", "version": "0.72.0",
                       "model_info": {"name": "某模型", "provider": None}},
        "agent_result": {"n_input_tokens": None, "cost_usd": None, "metadata": meta},
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
        "exception_info": ({"exception_type": exc, "exception_message": note,
                            "exception_traceback": "…", "occurred_at": "2026-09-15T14:00:00Z"}
                           if exc else None),
        "started_at": "2026-09-15T14:30:31.910376Z",
        "finished_at": "2026-09-15T14:41:39.653567Z",
        "agent_execution": {"started_at": "2026-09-15T14:31:06.856927Z",
                            "finished_at": "2026-09-15T14:41:06.857088Z"},
    }
    got.update(kw)
    return got


class _像三点十的(datetime):
    """3.10 的 `fromisoformat`：`Z` 结尾直接 ValueError """

    @classmethod
    def fromisoformat(cls, text):
        if text.endswith("Z"):
            raise ValueError("Invalid isoformat string: %r" % text)
        return datetime.fromisoformat(text)


class TestStamp(unittest.TestCase):
    def test_Z_结尾要认(self):
        """**这条必须把解释器降级了测。** `fromisoformat` 认 `Z` 是 3.11 才有的事，
        而项目下限是 3.10、本机是 3.14 —— 直接断言那个值的话，把 `_stamp` 里
        换 `Z` 那两行删掉它照样是绿的（第一版就是这样，回退验证当场抓到）。
        `list_files` 的 `full_match` 坏了一年多也是同一个形状。
        """
        span = {"started_at": "2026-09-15T14:31:06.000000Z",
                "finished_at": "2026-09-15T14:41:06.000000Z"}
        with unittest.mock.patch.object(tbench_report, "datetime", _像三点十的):
            self.assertEqual(tbench_report._span(span), 600.0)

    def test_坏时间戳返回零不抛(self):
        self.assertEqual(tbench_report._stamp("不是时间"), 0.0)
        self.assertEqual(tbench_report._span({"started_at": "x"}), 0.0)


class TestReward(unittest.TestCase):
    def test_只认_reward_那一项(self):
        self.assertEqual(tbench_report._reward({"rewards": {"reward": 1.0}}), 1.0)
        # 只有一项时就用那一项；多项又没有 reward 就是拿不到（**不许取 max**，
        # 那等于自己给自己挑一个好看的数）
        self.assertEqual(tbench_report._reward({"rewards": {"别的": 0.5}}), 0.5)
        self.assertIsNone(tbench_report._reward({"rewards": {"甲": 0.1, "乙": 0.9}}))
        self.assertIsNone(tbench_report._reward(None))
        self.assertIsNone(tbench_report._reward({"rewards": {}}))


class TestClassify(unittest.TestCase):
    """一份 result.json 落进哪个格子 —— 走的是 `report.classify()` 真正那条路 """

    def kind(self, **kw):
        return report.classify(tbench_report._row(_result(**kw)))

    def test_解出来(self):
        self.assertEqual(self.kind(reward=1.0), "resolved")

    def test_真跑完但错(self):
        self.assertEqual(self.kind(reward=0.0), "没解决")

    def test_墙钟超时算撞上限(self):
        self.assertEqual(self.kind(reward=0.0, exc="AgentTimeoutError"), "撞上限")

    def test_超时了但做出来了照样算解出来(self):
        """harbor 抓到 `AgentTimeoutError` 之后**照样跑 verifier**，
        所以一条记录可以同时有超时和满分 —— 先看分数
        """
        self.assertEqual(self.kind(reward=1.0, exc="AgentTimeoutError"), "resolved")

    def test_超时且没判成也算撞上限不算判不了(self):
        """**这条最要紧。** 丢进「判不了」就等于把它从分母里抹掉，解决率虚高 """
        row = tbench_report._row(_result(exc="AgentTimeoutError"))
        self.assertEqual(report.classify(row), "撞上限")
        self.assertTrue(row["detail"]["graded"])
        self.assertIsNone(row["detail"]["reward"])   # 原始事实照实记：没拿到分数

    def test_预算和轮数到顶(self):
        self.assertEqual(self.kind(reward=0.0, meta={"feiyu_stop": "budget"}), "撞上限")
        self.assertEqual(self.kind(reward=0.0, meta={"feiyu_stop": "rounds"}), "撞上限")

    def test_自己收敛的不算撞上限(self):
        self.assertEqual(self.kind(reward=0.0, meta={"feiyu_stop": None}), "没解决")

    def test_环境铺不起来(self):
        for exc in ("NotUsable", "EnvironmentStartTimeoutError", "AgentSetupTimeoutError"):
            self.assertEqual(self.kind(exc=exc), "铺不起来", exc)

    def test_别的异常是判不了(self):
        self.assertEqual(self.kind(exc="VerifierTimeoutError"), "判不了")
        self.assertEqual(self.kind(), "判不了")      # 没判成又没记异常

    def test_判不了和铺不起来不进分母(self):
        got = report.summarize([
            tbench_report._row(_result(reward=1.0, task_name="甲")),
            tbench_report._row(_result(reward=0.0, task_name="乙")),
            tbench_report._row(_result(exc="NotUsable", task_name="丙")),
            tbench_report._row(_result(exc="VerifierTimeoutError", task_name="丁")),
        ])
        self.assertEqual((got["resolved"], got["judged"], got["total"]), (1, 2, 4))


class TestRow(unittest.TestCase):
    def test_耗时取_agent_execution(self):
        """`started_at`/`finished_at` 含拉镜像和判分 实测一道 nop 的 67 秒里
        35 秒是起容器，拿它当"agent 跑了多久"会把中位数整个抬高
        """
        self.assertAlmostEqual(tbench_report._row(_result(reward=1.0))["s"], 600.0, places=3)

    def test_没有_agent_execution_时退回整场(self):
        got = tbench_report._row(_result(reward=1.0, agent_execution=None))
        self.assertAlmostEqual(got["s"], 667.743191, places=3)

    def test_花费只认人民币那一项(self):
        got = tbench_report._row(_result(reward=1.0, meta={
            "feiyu_cost_cny": 0.25, "feiyu_total_tokens": 1234, "feiyu_rounds": 7}))
        self.assertEqual((got["cost"], got["tokens"], got["rounds"]), (0.25, 1234, 7))

    def test_元数据缺失时不抛(self):
        got = tbench_report._row({"task_name": "光秃秃"})
        self.assertEqual((got["suite"], got["ok"], got["cost"]), ("光秃秃", False, 0.0))


class TestScan(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, data: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_顶层那份_JobResult_不算一次_trial(self):
        """`<job>/result.json` 是整场的汇总，没有 `task_name` ——
        当成 trial 解析会多出一行全空的记录，而那一行会进分母
        """
        self.write("job/result.json", {"stats": {"n_completed_trials": 1}})
        self.write("job/某题__A/result.json", _result(reward=1.0))
        self.assertEqual(len(tbench_report.trials(self.root)), 1)

    def test_坏文件跳过(self):
        (self.root / "job").mkdir()
        (self.root / "job" / "result.json").write_text("{半条", encoding="utf-8")
        self.write("job/某题__A/result.json", _result(reward=1.0))
        self.assertEqual(len(tbench_report.trials(self.root)), 1)

    def test_同题多遍按开始时间编号(self):
        self.write("job/某题__A/result.json",
                   _result(reward=1.0, started_at="2026-09-15T20:00:00Z"))
        self.write("job/某题__B/result.json",
                   _result(reward=0.0, started_at="2026-09-15T10:00:00Z"))
        got = tbench_report.rows(self.root)
        self.assertEqual([(r["turn"], r["ok"]) for r in got], [(1, False), (2, True)])


class TestStability(unittest.TestCase):
    def test_一遍的时候不打印(self):
        self.assertEqual(tbench_report.stability(
            [tbench_report._row(_result(reward=1.0))]), "")

    def test_一过一不过要点出来(self):
        got = tbench_report.stability([
            tbench_report._row(_result(reward=1.0, task_name="甲"), 1),
            tbench_report._row(_result(reward=0.0, task_name="甲"), 2),
            tbench_report._row(_result(reward=1.0, task_name="乙"), 1),
            tbench_report._row(_result(reward=1.0, task_name="乙"), 2),
        ])
        self.assertIn("噪音", got)
        self.assertIn("2 道里 1 道", got)


class TestRenderHeader(unittest.TestCase):
    def test_表头跟着记录走(self):
        got = report.render([tbench_report._row(_result(reward=1.0))])
        self.assertIn(tbench_report.BENCH, got)
        self.assertNotIn("SWE-bench", got)

    def test_没写_bench_的老记录照旧(self):
        self.assertIn("SWE-bench Verified 子集",
                      report.render([{"ok": True, "why": "", "model": "某模型"}]))


if __name__ == "__main__":
    unittest.main()


class TestExecTimeout(unittest.TestCase):
    """`environment.exec` 自己的 timeout_sec 到点，抛的是光秃秃的 RuntimeError

    首轮 24 道基线 48 次 trial 里有 7 次是这个：我们这边写死 1800 秒，而 Harbor
    那道闸是「题里声明的 × multiplier」= 3600 秒 —— 紧的那道先响，报出来却是
    另一种异常类型，于是全被归进「判不了」**退出了分母**。
    闸已经改成只留 Harbor 那一道，这几条是第二层保险。
    """

    def test_算撞上限不算判不了(self):
        row = tbench_report._row(_result(
            exc="RuntimeError", exc_note="Command timed out after 1800 seconds"))
        self.assertEqual(report.classify(row), "撞上限")
        self.assertTrue(row["detail"]["graded"])

    def test_别的_RuntimeError_照旧是判不了(self):
        """类名 `RuntimeError` 太泛，不能拿它当判据 """
        self.assertEqual(report.classify(tbench_report._row(
            _result(exc="RuntimeError", exc_note="Docker compose 起不来"))), "判不了")


class TestProviderError(unittest.TestCase):
    """接口报错是「我们这边坏了」，不是 agent 做不出来

    24 道基线跑到一半 openrouter 余额见底，**11 次 trial 全被判成「没解决」**——
    余额不够跟"这道题它做不出来"是两件事，混在一起那个百分比就没有意义了。
    """

    WRAPPED = (
        "  出错了：Error code: 402 - {'error': {'message': 'This request requires more\n"
        "  credits, or fewer max_tokens. You requested up to 131072 tokens, but can\n"
        "  only afford 59912.\n")

    def test_折行的报错也要认出来(self):
        """**这条是我自己踩的。** feiyu 的输出按终端宽度折行，
        `requires more credits` 被换行拆开 —— 直接 grep 原文得到 0 命中，
        差点据此得出"这批数据没被污染"的结论
        """
        self.assertIn("\n", self.WRAPPED)          # 确实是折了行的
        self.assertTrue(tbench_report._provider_error(self.WRAPPED))

    def test_余额不够算判不了(self):
        row = tbench_report._row(_result(reward=0.0), 1, self.WRAPPED)
        self.assertEqual(report.classify(row), "判不了")
        self.assertFalse(row["detail"]["graded"])
        self.assertIn("402", row["detail"]["provider_error"])

    def test_跑了一半才断的也算(self):
        """0 轮就死和跑了十几轮才死是同一回事 —— 都不是它做不出来 """
        row = tbench_report._row(
            _result(reward=0.0, meta={"feiyu_rounds": 14, "feiyu_cost_cny": 0.4}),
            1, self.WRAPPED)
        self.assertEqual(report.classify(row), "判不了")

    def test_限流和上游挂了也算(self):
        for code in ("401", "402", "403", "429", "500", "502", "529"):
            row = tbench_report._row(_result(reward=0.0), 1,
                                     "出错了：Error code: %s - 随便什么" % code)
            self.assertEqual(report.classify(row), "判不了", code)

    def test_做出来了就不管它(self):
        """分数优先：真做出来了，后面死没死无所谓 """
        self.assertEqual(report.classify(
            tbench_report._row(_result(reward=1.0), 1, self.WRAPPED)), "resolved")

    def test_别的报错码不算(self):
        """400 是我们自己把请求拼坏了，404 是模型名写错 —— 那些要露出来，
        不能跟"余额不够"一样被挡在分母外
        """
        self.assertEqual(report.classify(tbench_report._row(
            _result(reward=0.0), 1, "出错了：Error code: 400 - 请求拼坏了")), "没解决")

    def test_正常输出不会误判(self):
        self.assertEqual(tbench_report._provider_error(
            "跑了 12 轮，用了 402 个 token，一切正常"), "")


class TestMerge(unittest.TestCase):
    """补跑的结果要顶掉原来那道，不是跟它并列

    一轮跑完发现几道坏在我们自己身上（余额、配错的闸），补跑的是那几道。
    并列的话同一道题会有四条记录，稳定性那段直接失去意义。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, data: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_同名题以最后一个目录为准(self):
        self.write("旧/job/甲__A/result.json", _result(task_name="甲", reward=0.0))
        self.write("旧/job/乙__A/result.json", _result(task_name="乙", reward=1.0))
        self.write("新/job/甲__B/result.json", _result(task_name="甲", reward=1.0))
        got = tbench_report.merge([self.root / "旧", self.root / "新"])
        self.assertEqual({(r["suite"], r["ok"]) for r in got}, {("甲", True), ("乙", True)})

    def test_整道顶掉不按遍拼(self):
        """旧的两遍里只有一遍坏了，也整道用新的 —— 两遍跑在不同配置下，
        凑出来的稳定性是假的
        """
        self.write("旧/job/甲__A/result.json",
                   _result(task_name="甲", reward=1.0, started_at="2026-09-15T01:00:00Z"))
        self.write("旧/job/甲__B/result.json",
                   _result(task_name="甲", exc="NotUsable", started_at="2026-09-15T02:00:00Z"))
        self.write("新/job/甲__C/result.json",
                   _result(task_name="甲", reward=0.0, started_at="2026-09-16T01:00:00Z"))
        self.write("新/job/甲__D/result.json",
                   _result(task_name="甲", reward=0.0, started_at="2026-09-16T02:00:00Z"))
        got = tbench_report.merge([self.root / "旧", self.root / "新"])
        self.assertEqual(len(got), 2)                       # 不是 3 也不是 4
        self.assertEqual([r["ok"] for r in got], [False, False])


class TestTokenColumn(unittest.TestCase):
    """跑飞那次没有用量可报，就报 None 不报 0

    起因是基线上算出「解出来 292k token 花 ¥0.13，没解出来 68k 却花 ¥0.41」。
    两个错凑在一起：`turn` 行的 `tokens` 其实是**上下文大小**不是用量
    （适配器那侧已修），而这边又把缺失当成 0 —— `report._median` 跳过 None
    却会把 0 算进去，于是中位数被一堆 0 拉下来，量纲没错但数是假的。
    """

    def test_没有用量就是_None(self):
        got = tbench_report._row(_result(reward=0.0, meta={
            "feiyu_context_tokens": 68000, "feiyu_partial": True}))
        self.assertIsNone(got["tokens"])
        self.assertEqual(got["detail"]["context_tokens"], 68000)

    def test_有用量照常填(self):
        got = tbench_report._row(_result(reward=1.0, meta={"feiyu_total_tokens": 292000}))
        self.assertEqual(got["tokens"], 292000)

    def test_中位数不被缺失的那几条拉下来(self):
        rows = [tbench_report._row(_result(reward=1.0, task_name="甲",
                                           meta={"feiyu_total_tokens": 300000})),
                tbench_report._row(_result(reward=1.0, task_name="乙",
                                           meta={"feiyu_total_tokens": 300000})),
                tbench_report._row(_result(reward=0.0, task_name="丙",
                                           meta={"feiyu_partial": True}))]
        self.assertIn("300k token", report.render(rows))

    def test_老记录靠_partial_追溯修正(self):
        """0.72.0 之前的适配器把"上下文大小"填进了 `feiyu_total_tokens`，
        但 `feiyu_partial` 当时就记了 —— 据此能把已有基线一起修对，不用重跑
        """
        老 = tbench_report._row(_result(reward=0.0, meta={
            "feiyu_total_tokens": 68000, "feiyu_partial": True}))
        self.assertIsNone(老["tokens"])
        self.assertEqual(老["detail"]["context_tokens"], 68000)
        # partial 为假的老记录是真用量，照常用
        好 = tbench_report._row(_result(reward=1.0, meta={
            "feiyu_total_tokens": 292000, "feiyu_partial": False}))
        self.assertEqual(好["tokens"], 292000)


class TestCompare表(unittest.TestCase):
    def test_花费要留小数位(self):
        """整张表原来都是 `%10.0f`，一道题几毛钱时 ¥0.365 和 ¥0.912 双双显示成 0 ——
        那一行看着像"两边都没花钱"，而"变化"那列还写着 -18.3%，自相矛盾得很隐蔽
        """
        甲 = [{"suite": "题", "ok": False, "why": "", "cost": 0.365, "rounds": 3}]
        乙 = [{"suite": "题", "ok": False, "why": "", "cost": 0.912, "rounds": 3}]
        got = report.compare(甲, 乙)
        self.assertIn("0.365", got)
        self.assertIn("0.912", got)
