"""题从哪来 —— 内置题库和外部 benchmark 走同一个形状

原来 `run.py` 顶上硬 `from evals.tasks import TASKS`，题是扁平四元组
`(名字, 铺现场, 题面, 判题)`，`run.py` 里直接位置解包。那个形状装不下外部 benchmark：

- **铺现场会失败**。SWE-bench 要拉镜像、起容器、把仓库挂进去，任何一步都可能挂；
  而四元组那条路上 `setup()` 连 try 都没有，一道题铺不起来整轮带 traceback 崩掉。
- **铺现场要留下东西给后面用**（容器 id），判完还要收拾掉。四元组没有这个位置。
- **判题结果不止"过没过"**。SWE-bench 要 FAIL_TO_PASS / PASS_TO_PASS 的明细、
  提出来的 patch、日志在哪 —— 而四元组的 `why` 在表格里被截到 34 个字符。
- **命令要送进容器跑**。内置题在宿主机上跑，SWE-bench 的测试得在实例镜像里跑。

`Case` 是这两者的最小公共形状。内置那 22 道**一个字都没改**，
由 `_from_tuple()` 原样适配过来 —— 它们的 `setup` 不返回句柄、`check` 不看句柄、
判题结果没有明细，三处都退化成默认值。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Result:
    """判题的结果 `ok` 和 `why` 是内置题库一直在用的那两个，其余是 benchmark 才有的"""
    ok: bool
    why: str = ""
    # 机器可读的明细，原样进 --out 的 JSONL：SWE-bench 往里放 FAIL_TO_PASS 的逐条结果、
    # 提出来的 patch、日志路径。**表格里那个 why 会被截断，这里不会。**
    detail: dict = field(default_factory=dict)


@dataclass
class Case:
    """一道题

    `setup` 返回的东西原样传给 `check` 和 `teardown`（内置题返回 None）——
    容器 id 这类"铺现场时才知道、判题和收尾都要用"的东西只能这么带。
    """
    name: str
    prompt: str
    setup: Callable[[Path], object]
    check: Callable[[Path, str, object], Result]
    # 判完必收，**无论判题成没成、崩没崩**（容器泄漏比判错贵得多）
    teardown: Callable[[object], None] | None = None
    # bash 要不要套一层前缀送进别处跑 空的就是照旧在宿主机上跑
    # 形状跟 sandbox.wrap 一样：一个 argv 列表，命令接在后面
    exec_prefix: list[str] = field(default_factory=list)
    # 这道题最多跑多久（秒） None 就是不限
    timeout: float | None = None
    # 连续调多少轮工具之后停 0 就是用 agent 的出厂值（25）
    # **benchmark 一定要调大**：出厂那个是给交互用的，真实仓库的题
    # 十几到几十轮很常见，不调的话量出来的是上限不是能力
    max_rounds: int = 0
    # 单题花费上限（元），0 不限 放开轮数之后这是唯一的另一道闸
    budget: float = 0.0


def _from_tuple(row) -> Case:
    """内置的四元组 → Case 三处退化：不出句柄、不看句柄、判题结果没有明细 """
    name, setup, prompt, check = row

    def prepare(root: Path):
        setup(root)
        return None

    def judge(root: Path, answer: str, _handle) -> Result:
        ok, why = check(root, answer)
        return Result(ok=bool(ok), why=why or "")

    return Case(name=name, prompt=prompt, setup=prepare, check=judge)


def builtin() -> list[Case]:
    from evals.tasks import TASKS
    return [_from_tuple(row) for row in TASKS]


def swebench() -> list[Case]:
    from evals.swebench import cases
    return cases()


def swebench_hard() -> list[Case]:
    """难题档 —— 50 题那把尺子在 qwen3.8-flash 上是 45/46，已经饱和 """
    from evals import swebench as mod
    return mod.cases(mod.SUBSET_HARD)


def swebench_ab() -> list[Case]:
    """A/B 对照的 12 道 跑两遍、只翻 FEIYU_ASSIST 一个开关 """
    from evals import swebench as mod
    return mod.cases(mod.SUBSET_AB)


SUITES = {"builtin": builtin, "swebench": swebench,
          "swebench-hard": swebench_hard, "swebench-ab": swebench_ab}


def load(suite: str, only=None) -> list[Case]:
    """挑出要跑的那几道 `only` 照旧是对题名做子串匹配（空格先去掉）"""
    make = SUITES.get(suite)
    if make is None:
        raise SystemExit("没有 %r 这个 suite，只认：%s" % (suite, " / ".join(SUITES)))
    cases = make()
    if not only:
        return cases
    return [c for c in cases if any(k in c.name.replace(" ", "") for k in only)]
