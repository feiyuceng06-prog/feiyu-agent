"""题库跑起来不许在真实状态里留痕

`evals/run.py` 的 `_isolate()` 是这条规矩唯一的落点。原来它只换了
`XDG_STATE_HOME`，**漏了 `XDG_CONFIG_HOME`** —— 而 `keys.STORE` / `keys.STATE`
在它下面，每建一个 `Feiyu` 都会 `keys.set_last()`。于是 `python -m evals.run -m 甲`
跑完，用户在终端里敲 `feiyu` 就发现模型被换成甲了。`permissions.load()` 见到
`known` 跟出厂列表对不上时也会回写。

**这条只能在子进程里测。** 那几个 `STORE` / `STATE` 是模块 import 时算好的常量，
真实场景里 `_isolate()` 跑在任何 `import feiyu` 之前才管用；而测试进程里 feiyu
早就导入完了，直接调 `_isolate()` 再断言 `keys.STORE` 会得到一个假的绿灯。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 子进程里跑的那段：照真实顺序来 —— 先 _isolate，再 import feiyu，再建 agent
PROBE = """
import json, os, pathlib, sys, tempfile
sys.path.insert(0, %(repo)r)
from evals.run import _isolate

box = tempfile.mkdtemp(prefix="feiyu-evals-test-")
_isolate(box)

from feiyu import keys
from feiyu.agent import Feiyu

out = {"store": keys.STORE, "entries": len(keys.load()), "picked": keys.pick_default()}
# 这一下就是原来污染真配置的那一步：use_model 会 keys.set_last()
Feiyu(pathlib.Path(box), model=%(model)r)
out["box_last"] = keys.last()
out["box"] = box
out["tmpdir"] = os.environ.get("TMPDIR", "")
print("@@" + json.dumps(out))
"""


class TestIsolate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "cfg"
        (self.cfg / "feiyu").mkdir(parents=True)
        self.models = self.cfg / "feiyu" / "models.json"
        self.state = self.cfg / "feiyu" / "state.json"
        self.models.write_text(json.dumps({
            "甲": {"kind": "openai", "base_url": "https://例子/v1",
                   "model": "m/甲", "key": "sk-假的"},
            "乙": {"kind": "openai", "base_url": "https://例子/v1",
                   "model": "m/乙", "key": "sk-也是假的"},
        }, ensure_ascii=False), encoding="utf-8")
        self.state.write_text(json.dumps({"last": "甲"}, ensure_ascii=False),
                              encoding="utf-8")

    def probe(self, model: str) -> dict:
        env = dict(os.environ, XDG_CONFIG_HOME=str(self.cfg),
                   XDG_STATE_HOME=str(Path(self.tmp.name) / "state"))
        done = subprocess.run(
            [sys.executable, "-c", PROBE % {"repo": str(REPO), "model": model}],
            capture_output=True, text=True, env=env, timeout=120, cwd=str(REPO))
        line = next((l for l in done.stdout.splitlines() if l.startswith("@@")), "")
        self.assertTrue(line, "子进程没跑成：\n%s\n%s" % (done.stdout[-800:], done.stderr[-800:]))
        return json.loads(line[2:])

    def test_模型清单读得到_写落在临时目录(self):
        got = self.probe("乙")
        self.assertNotIn(str(self.cfg), got["store"], "keys.STORE 还指着真配置")
        self.assertEqual(got["entries"], 2, "拷进去的模型清单读不出来")
        self.assertEqual(got["picked"], "甲", "state.json 里的 last 没带过去")
        self.assertEqual(got["box_last"], "乙", "换模型这一下没写进临时目录")

    def test_真的_state_json_一个字节都不变(self):
        """这条是这个用例的全部意义 """
        before = self.state.read_bytes()
        self.probe("乙")
        self.assertEqual(self.state.read_bytes(), before,
                         "跑一遍题库把用户真正的 state.json 改了")

    def test_真的_models_json_也不变(self):
        before = self.models.read_bytes()
        self.probe("乙")
        self.assertEqual(self.models.read_bytes(), before)

    def test_没有配置也不炸(self):
        """一台没配过模型的机器上，_isolate 自己不该抛 —— 该由 main() 去报"没有可用模型" """
        self.models.unlink()
        self.state.unlink()
        got = self.probe("")
        self.assertEqual(got["entries"], 0)
        self.assertEqual(got["picked"], "")


    def test_拷过去的模型清单是_0600(self):
        """**拷贝里是明文 key。** `shutil.copyfile` 只拷内容不拷权限，落地是 umask 的
        0644，而它躺在 /tmp 这种同机谁都列得到的地方 —— 真配置那边一直是 0600，
        这条拷贝不能把它降级了。
        """
        import stat
        got = self.probe("乙")
        box = got["box"]
        here = os.path.join(box, "feiyu")
        self.assertEqual(stat.S_IMODE(os.stat(here).st_mode), 0o700, "临时配置目录可列")
        for name in ("models.json", "state.json"):
            mode = stat.S_IMODE(os.stat(os.path.join(here, name)).st_mode)
            self.assertEqual(mode, 0o600, "%s 是 %o，明文 key 谁都读得到" % (name, mode))

    def test_TMPDIR_指进临时目录(self):
        """bash 靠 TMPDIR 下一个临时文件带回 pwd；SWE-bench 那条路要把它挂进容器 """
        got = self.probe("乙")
        self.assertTrue(got["tmpdir"].startswith(got["box"]),
                        "TMPDIR=%s 没指进 box" % got["tmpdir"])


class TestSuites(unittest.TestCase):
    """内置那 22 道题一个字没改，得原样从新接缝里出来 """

    def test_内置题原样适配成_case(self):
        from evals import suites
        from evals.tasks import TASKS
        cases = suites.load("builtin")
        self.assertEqual(len(cases), len(TASKS))
        self.assertEqual([c.name for c in cases], [t[0] for t in TASKS])
        self.assertEqual([c.prompt for c in cases], [t[2] for t in TASKS])

    def test_only_的匹配规则没变(self):
        """照旧是对题名做子串匹配、先去掉空格 —— 所以「改名」会同时挑中「跨文件改名」"""
        from evals import suites
        got = [c.name for c in suites.load("builtin", ["改名"])]
        self.assertEqual(got, ["改名", "跨文件改名"])
        self.assertEqual([c.name for c in suites.load("builtin", ["修bug"])], ["修 bug"])

    def test_铺现场和判题接得上(self):
        """不碰 agent，只验适配层：铺完能判、套上参考答案能判过 """
        from evals import suites
        case = next(c for c in suites.load("builtin") if c.name == "改名")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handle = case.setup(root)
            self.assertIsNone(handle, "内置题不出句柄")
            self.assertFalse(case.check(root, "", handle).ok)
            calc = root / "calc.py"
            calc.write_text(calc.read_text().replace("def sub(", "def minus("))
            self.assertTrue(case.check(root, "", handle).ok)

    def test_不认识的_suite_直接报错(self):
        from evals import suites
        with self.assertRaises(SystemExit):
            suites.load("没这套")


class TestOneRobust(unittest.TestCase):
    """`one()` 的兜底：铺现场会失败、收尾必须做

    原来 `setup()` 连 try 都没有 —— SWE-bench 要拉镜像起容器，一道题铺不起来
    就会带着 traceback 把整轮几个钟头的跑分全带走。
    """

    def setUp(self):
        from evals import run
        self.run = run
        self.addCleanup(run.CRASHES.clear)

    def case(self, **over):
        from evals.suites import Case, Result
        spec = {"name": "试", "prompt": "干活",
                "setup": lambda root: "句柄",
                "check": lambda root, answer, handle: Result(ok=True)}
        spec.update(over)
        return Case(**spec)

    def test_铺不起来算这题没过_不往外抛(self):
        def boom(root):
            raise RuntimeError("镜像拉不下来")
        got = self.run.one(self.case(setup=boom), "随便", "auto")
        self.assertFalse(got["ok"])
        self.assertIn("铺不起来", got["why"])
        # 形状要跟正常那行一样，否则收尾的 sum / median 会在缺字段上抛
        for key in ("s", "rounds", "tools", "tokens", "cost", "context", "detail"):
            self.assertIn(key, got)
        self.assertTrue(any("镜像拉不下来" in c for c in self.run.CRASHES))

    def test_铺不起来就不该去收尾(self):
        """句柄根本没拿到，teardown 收的会是个 None —— 不如不收 """
        collected = []
        def boom(root):
            raise RuntimeError("挂了")
        self.run.one(self.case(setup=boom, teardown=collected.append), "随便", "auto")
        self.assertEqual(collected, [])

    def test_跑崩了也要收尾(self):
        """容器泄漏比判错一道题贵 """
        collected = []
        def explode(*a, **kw):
            raise RuntimeError("agent 整个炸了")
        # **先登记恢复再替换。** 反过来的话 addCleanup 存的是已经被替换掉的那个，
        # 收尾等于把假的 _drive 留给后面所有用例（顺序一变就一片红）
        self.addCleanup(setattr, self.run, "_drive", self.run._drive)
        self.run._drive = explode
        case = self.case(teardown=collected.append)
        with self.assertRaises(RuntimeError):
            self.run.one(case, "随便", "auto")
        self.assertEqual(collected, ["句柄"], "跑崩了没收尾")

    def test_收尾自己崩了不许盖掉结果(self):
        def bad(handle):
            raise RuntimeError("收尾也挂了")
        def boom(root):
            raise RuntimeError("铺现场挂了")
        got = self.run.one(self.case(setup=boom, teardown=bad), "随便", "auto")
        self.assertIn("铺不起来", got["why"])


class TestJot(unittest.TestCase):
    def test_每题一行_边跑边追加(self):
        from evals import run
        from evals.suites import Case, Result
        case = Case(name="某题", prompt="p", setup=lambda r: None,
                    check=lambda r, a, h: Result(ok=True))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.jsonl")
            for turn in range(3):
                run._jot(path, case, turn, {"ok": True, "why": "", "detail": {"a": 1}},
                         "某模型", "auto")
            rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["turn"] for r in rows], [0, 1, 2])
        self.assertEqual(rows[0]["suite"], "某题")
        self.assertEqual(rows[0]["model"], "某模型")
        self.assertEqual(rows[0]["detail"], {"a": 1})

    def test_没给_out_就一个字节都不写(self):
        from evals import run
        from evals.suites import Case, Result
        case = Case(name="x", prompt="p", setup=lambda r: None,
                    check=lambda r, a, h: Result(ok=True))
        run._jot("", case, 0, {"ok": True}, "m", "auto")     # 不该抛

    def test_写不进去也不该把跑分带走(self):
        from evals import run
        from evals.suites import Case, Result
        case = Case(name="x", prompt="p", setup=lambda r: None,
                    check=lambda r, a, h: Result(ok=True))
        run._jot("/根本不存在的目录/out.jsonl", case, 0, {"ok": True}, "m", "auto")


if __name__ == "__main__":
    # `feiyu` 是 pip install -e 装过的，随便在哪都 import 得到；**`evals` 不是**
    # （`pyproject` 的 packages 只列了 feiyu），只有仓库根目录在 path 上才找得着。
    # 不补这一句的话，单独跑这个文件是一片 ModuleNotFoundError。
    sys.path.insert(0, str(REPO))
    unittest.main()


class TestGradeVerdicts(unittest.TestCase):
    """**"判不了"和"没解决"必须分得开。**

    这是整套分数可信与否的那一条。报告在、但这道题压根没被 judge 到
    （镜像缺、跑崩了、instance_id 拼错了）时，它同样不出现在 `resolved_ids` 里 ——
    只看 resolved 的话，这种情况会被读成"没解决"，也就是**在说 agent 做错了**。
    实测踩过：换数据集之后 harness 在 `make_test_spec` 上 KeyError 整批没判，
    而第一版代码把那一批全报成了漂亮的 `unresolved ✓`。
    """

    def verdicts(self, report):
        from evals.swegrade import _verdicts
        return _verdicts(report)

    def test_5x_的形状(self):
        """a 解决了、b 判过但没解决、c 是 harness 自己报错 ——
        **c 不算判过**：它是"量不出来"，不是"agent 没做出来"。
        """
        seen, good = self.verdicts({
            "resolved_ids": ["a"], "unresolved_ids": ["b"], "error_ids": ["c"]})
        self.assertEqual(good, {"a"})
        self.assertEqual(seen, {"a", "b"})

    def test_老形状(self):
        seen, good = self.verdicts({"a": {"resolved": True}, "b": {"resolved": False}})
        self.assertEqual(good, {"a"})
        self.assertEqual(seen, {"a", "b"})

    def test_判过但没解决_跟_压根没判过_不是一回事(self):
        seen, good = self.verdicts({"resolved_ids": [], "unresolved_ids": ["b"]})
        self.assertIn("b", seen, "b 判过了，只是没解决")
        self.assertNotIn("b", good)
        # 而 c 根本不在报告里 —— 这时候不能说 c "没解决"
        self.assertNotIn("c", seen)

    def test_空报告不该让任何题变成没解决(self):
        seen, good = self.verdicts({"resolved_ids": []})
        self.assertEqual(seen, set())
        self.assertEqual(good, set())

    def test_resolved_也要算进判过的(self):
        """有的版本 completed_ids 不含已解决的那些，漏了会把 ✓ 误报成"判不了" """
        seen, _ = self.verdicts({"resolved_ids": ["a"], "completed_ids": []})
        self.assertIn("a", seen)

    def test_基础设施挂了不算判过(self):
        """**这条最容易写错。** 实测 5.0.2 的报告里 infra_failure 那道题**照样在
        `submitted_ids` 里** —— 把 submitted 当成"判过"，容器挂掉就会报成
        "agent 没解决"，而那是我们这边坏了。
        """
        seen, good = self.verdicts({
            "resolved_ids": [], "submitted_ids": ["a"], "infra_failure_ids": ["a"]})
        self.assertNotIn("a", seen, "基础设施挂了被当成判过了")
        self.assertNotIn("a", good)

    def test_交了空_patch_不算判过(self):
        """那是"根本没交出东西"，不是"交了但不对" """
        seen, _ = self.verdicts({
            "resolved_ids": [], "submitted_ids": ["a"], "empty_patch_ids": ["a"]})
        self.assertNotIn("a", seen)

    def test_模棱两可和跑崩的都排掉(self):
        for key in ("ambiguous_failure_ids", "incomplete_ids", "error_ids"):
            with self.subTest(key=key):
                seen, _ = self.verdicts({
                    "resolved_ids": [], "completed_ids": ["a"], key: ["a"]})
                self.assertNotIn("a", seen)

    def test_真实报告的形状(self):
        """从一次真的判分里抄下来的顶层键 —— 换 swebench 版本时这条会先红 """
        seen, good = self.verdicts({
            "total_instances": 1, "submitted_instances": 1, "completed_instances": 1,
            "resolved_instances": 1, "unresolved_instances": 0,
            "infra_failure_instances": 0, "ambiguous_failure_instances": 0,
            "empty_patch_instances": 0, "error_instances": 0,
            "completed_ids": ["astropy__astropy-12907"], "incomplete_ids": [],
            "empty_patch_ids": [], "submitted_ids": ["astropy__astropy-12907"],
            "resolved_ids": ["astropy__astropy-12907"], "unresolved_ids": [],
            "infra_failure_ids": [], "ambiguous_failure_ids": [], "failure_reasons": {}})
        self.assertEqual(good, {"astropy__astropy-12907"})
        self.assertEqual(seen, {"astropy__astropy-12907"})


class TestReport(unittest.TestCase):
    """一道题没过有三种原因，混在一起那个解决率就没意义了

    最要紧的是**「判不了」不能进分母** —— 那是我们这边坏了（镜像拉不到、
    容器起不来、harness 报告解析不了），把它算成 agent 没做出来，
    等于让自己的 bug 拉低分数，而那个数还要拿去跟公开榜比。
    """

    def row(self, **over):
        spec = {"suite": "x__x-1", "ok": False, "why": "", "rounds": 5, "s": 10.0,
                "tokens": 1000, "cost": 0.1, "model": "m", "detail": {"graded": True}}
        spec.update(over)
        return spec

    def kind(self, **over):
        from evals.report import classify
        return classify(self.row(**over))

    def test_六种归类(self):
        self.assertEqual(self.kind(ok=True), "resolved")
        self.assertEqual(self.kind(why="没解决（官方判据）"), "没解决")
        self.assertEqual(self.kind(why="判不了：找不到判分报告",
                                   detail={"graded": False}), "判不了")
        self.assertEqual(self.kind(why="铺不起来 拉不下来镜像"), "铺不起来")
        self.assertEqual(self.kind(why="崩了 RuntimeError: x"), "崩了")
        self.assertEqual(
            self.kind(why="连续调用了 120 轮工具还没做完，我先停下来 "), "撞上限")

    def test_花超了也算撞上限(self):
        self.assertEqual(
            self.kind(why="这次任务已经花掉 ¥3.01，到了你设的上限 ¥3.00 我先停下 "),
            "撞上限")

    def test_判不了不进分母(self):
        from evals.report import summarize
        got = summarize([
            self.row(ok=True),
            self.row(why="没解决（官方判据）"),
            self.row(why="判不了：镜像拉不到", detail={"graded": False}),
            self.row(why="铺不起来 起不来容器"),
        ])
        self.assertEqual(got["resolved"], 1)
        self.assertEqual(got["judged"], 2, "判不了 / 铺不起来 混进分母了")
        self.assertEqual(got["total"], 4)

    def test_一道都没判成时不许除以零(self):
        from evals.report import render
        text = render([self.row(why="判不了：x", detail={"graded": False})])
        self.assertIn("一道都没判成", text)

    def test_读得了写到一半被掐断的文件(self):
        """跑几十小时的东西中途 ctrl-c 很正常，最后半行不该让报告读不出来 """
        from evals.report import load
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jsonl"
            path.write_text(json.dumps(self.row()) + "\n" + '{"suite": "半行',
                            encoding="utf-8")
            self.assertEqual(len(load(path)), 1)

    def test_空文件不炸(self):
        from evals.report import render
        self.assertIn("一行都没有", render([]))


class TestHardSubset(unittest.TestCase):
    """难题档：50 题那把尺子在 qwen3.8-flash 上是 45/46，饱和了，量不出改进

    这一档取的是「1-4 小时」+「>4 小时」的**全部** 45 道 —— 全取而不抽样，
    于是"这把尺子是哪几道"由数据集本身定死，没有抽样规则要记，也就没得漂。
    """

    def ids(self, name):
        from evals import swebench
        path = getattr(swebench, name)
        return [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]

    def test_难题档就是那一档的全部(self):
        from evals import swebench
        rows = json.loads(swebench.CACHE.read_text(encoding="utf-8"))
        want = sorted(r["instance_id"] for r in rows
                      if r["difficulty"] in swebench.HARD_LEVELS)
        self.assertEqual(self.ids("SUBSET_HARD"), want,
                         "难题档跟数据集对不上了 —— 重跑 python -m evals.swebench --hard")

    def test_里面一道简单题都没有(self):
        from evals import swebench
        rows = {r["instance_id"]: r["difficulty"]
                for r in json.loads(swebench.CACHE.read_text(encoding="utf-8"))}
        for iid in self.ids("SUBSET_HARD"):
            self.assertIn(rows.get(iid), swebench.HARD_LEVELS, iid)

    def test_几把尺子都注册了(self):
        from evals import suites
        self.assertEqual(sorted(suites.SUITES),
                         ["builtin", "swebench", "swebench-ab", "swebench-hard"])

    def test_ab_档取自难题档(self):
        """A/B 对照必须全是难题 —— 拿简单题做对照，改进再有效也看不出来
        （50 题那把尺子 45/46 就是这么废掉的）"""
        hard = set(self.ids("SUBSET_HARD"))
        ab = self.ids("SUBSET_AB")
        self.assertEqual(len(ab), 12)
        self.assertTrue(set(ab) <= hard, "A/B 档里混进了难题档以外的题")

    def test_子集文件是排过序的_读两次一样(self):
        """顺序也要稳 —— 不然 diff 里全是噪音，也看不出是真换了题还是只是重排 """
        got = self.ids("SUBSET_HARD")
        self.assertEqual(got, sorted(got))
        self.assertEqual(len(got), len(set(got)), "有重复的 id")

    def test_旧的_50_题档没被动过(self):
        """换尺子不能把旧基线作废 —— swe50-baseline 还要留着做对照 """
        self.assertEqual(len(self.ids("SUBSET")), 50)


class TestCompare(unittest.TestCase):
    """A/B 对比：只比两边都跑成的题

    **一边崩了的那道不能参与比较。** 接口 429、连接中断这些是链路噪音，
    而且只砸在其中一轮上 —— 拿它去比等于把噪音算成"改动的效果"。
    50 题那一轮里 8% 的题是这么崩的，这个比例足以翻转一个 12 道的对比。
    """

    def row(self, name, ok=True, rounds=20, why="", **over):
        spec = {"suite": name, "ok": ok, "why": why or ("" if ok else "没解决（官方判据）"),
                "rounds": rounds, "s": rounds * 10, "tokens": rounds * 1000,
                "cost": rounds * 0.01, "detail": {"graded": True}}
        spec.update(over)
        return spec

    def test_只比共同跑成的(self):
        from evals.report import _pair
        old = [self.row("a"), self.row("b"), self.row("c")]
        new = [self.row("a"), self.row("b")]
        self.assertEqual([x["suite"] for x, _ in _pair(old, new)], ["a", "b"])

    def test_任意一边崩了就整道排掉(self):
        from evals.report import _pair
        for bad_side in ("old", "new"):
            with self.subTest(side=bad_side):
                crashed = self.row("a", ok=False, why="崩了 RateLimitError: 429",
                                   detail={})
                old = [crashed if bad_side == "old" else self.row("a"), self.row("b")]
                new = [crashed if bad_side == "new" else self.row("a"), self.row("b")]
                got = [x["suite"] for x, _ in _pair(old, new)]
                self.assertEqual(got, ["b"], "崩了的题混进对比了")

    def test_判不了的也排掉(self):
        from evals.report import _pair
        old = [self.row("a", ok=False, why="判不了：找不到报告", detail={"graded": False})]
        new = [self.row("a")]
        self.assertEqual(_pair(old, new), [])

    def test_翻盘会被标出来(self):
        from evals.report import compare
        text = compare([self.row("a", ok=False, rounds=60)],
                       [self.row("a", ok=True, rounds=30)])
        self.assertIn("翻盘", text)
        self.assertIn("✗→✓", text)

    def test_中位变化算得对(self):
        from evals.report import compare
        old = [self.row("a", rounds=40), self.row("b", rounds=60)]
        new = [self.row("a", rounds=20), self.row("b", rounds=30)]
        text = compare(old, new)
        self.assertIn("-50.0%", text)      # 中位 50 → 25

    def test_没有共同题时说清楚而不是报零(self):
        from evals.report import compare
        self.assertIn("没法比", compare([self.row("a")], [self.row("z")]))

    def test_样本小要自己说出来(self):
        """12 道差一两道全是噪音 —— 这句提醒必须跟着数字一起出现 """
        from evals.report import compare
        self.assertIn("噪音", compare([self.row("a")], [self.row("a")]))
