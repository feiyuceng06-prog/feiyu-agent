"""把 Harbor 的结果目录读成 `evals.report` 认得的 JSONL，再按同一套口径汇总

    python -m evals.tbench_report <结果目录>              # 直接看汇总
    python -m evals.tbench_report <结果目录> --out a.jsonl # 顺便存一份，之后能 --ab

## 为什么要有这个

0.70.1 那十道的「解出来 5 / 真跑完但错 1 / 墙钟没跑完 2 / 判不了 2」是**手工数的**。
手工数有两个问题：数一遍要翻十个目录，而且「判不了」到底算不算进分母全凭当时记不记得。
`report.py` 早就把这套口径写死了（判不了 / 铺不起来 / 崩了**不进分母**），
这里只做一件事：把 Harbor 的 `result.json` 翻译成它认得的行，分类规则写死在 `_row()`。

## 分类依据（全部翻过 harbor 源码，不是猜的）

`result.json` 就是 `harbor.models.trial.result.TrialResult`：

- `verifier_result.rewards.reward` 是分数；**没跑到判分那一步时整个
  `verifier_result` 是 None**（`trial.py::run` 把异常记进 `exception_info` 就收摊了）。
- `exception_info.exception_type` 是**类名**（`ExceptionInfo.from_exception` 取
  `type(e).__name__`），所以 `NotUsable`（这个适配器自己抛的）和 `AgentTimeoutError`
  都是确定的字符串，不用去 match 报错文本。
- **墙钟超时不阻止判分。** `single_step.py::_run_agent` 抓到 `AgentTimeoutError`
  只是 `_record_exception`，后面照样跑 verifier —— 所以一条记录可以**同时**有超时
  异常和一个 reward。判定顺序因此是「先看分数，再看为什么没得分」。
- `agent_execution` 那对时间戳才是 agent 真正跑了多久；`started_at`/`finished_at`
  含拉镜像和判分，`fix-git` 那道 nop 实测 67 秒里有 35 秒是起容器。

feiyu 自己那几个数（轮数 / token / 花费 / 为什么停）在 `agent_result.metadata` 里，
`tbench.py::_fill_usage` 填的。
"""

import argparse
import json
import re
import sys
import pathlib
from datetime import datetime
from pathlib import Path

from evals import report

BENCH = "Terminal-Bench 2.0 子集"


def _stamp(text) -> float:
    """ISO8601 → epoch 秒 **`Z` 结尾要自己换掉**：`fromisoformat` 认 `Z`
    是 3.11 才有的事，而这个项目的下限是 3.10（本机 3.14，不换照样全绿）
    """
    if not isinstance(text, str) or not text:
        return 0.0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _span(info) -> float:
    if not isinstance(info, dict):
        return 0.0
    began, ended = _stamp(info.get("started_at")), _stamp(info.get("finished_at"))
    return max(0.0, ended - began) if began and ended else 0.0


def _reward(result) -> float | None:
    """拿这次的分数 拿不到返回 None（= 没判过，跟「判了但 0 分」不是一回事）

    `rewards` 是个 dict，题可以报多项 主项叫 `reward`；只有一项时就用那一项
    （别默默取 max —— 那等于自己给自己挑一个好看的数）。
    """
    if not isinstance(result, dict):
        return None
    rewards = result.get("rewards")
    if not isinstance(rewards, dict) or not rewards:
        return None
    if "reward" in rewards:
        got = rewards["reward"]
    elif len(rewards) == 1:
        got = next(iter(rewards.values()))
    else:
        return None
    return float(got) if isinstance(got, (int, float)) else None


# 这几个是「环境就没铺起来」，跟 agent 做没做出来无关
SETUP_FAILS = ("NotUsable", "EnvironmentStartTimeoutError", "AgentSetupTimeoutError")
# `DockerEnvironment._communicate` 里那句 —— exec 自己的 timeout_sec 到点时抛的
# 是个**光秃秃的 RuntimeError**，只能认报错文本（类名 `RuntimeError` 太泛，
# 拿它当判据会把别的 RuntimeError 也吞进来）
_EXEC_TIMEOUT = re.compile(r"Command timed out after \d+ seconds")
# agent 那侧的日志：`run()` 把 feiyu 的 stdout 落在 trial 的 `agent/` 里
AGENT_LOG = "feiyu-stdout.txt"
# **接口层面的报错是「我们这边坏了」，不是 agent 做不出来。**
# 401/403 是 key 不对，402 是余额不够，429 是被限流，5xx 是上游挂了 ——
# 没有一条是 agent 的能力问题，全都不该进分母。
# 这已经是第二次栽在这上面：0.70.1 那次是 `filter-js-from-html` 撞上游 429，
# 当时是人看出来的；这次是 24 道基线跑到一半 openrouter 余额见底，
# **11 次 trial 全被判成「没解决」**，直接把解决率压下去了。
_PROVIDER_ERR = re.compile(r"Error code: (40[123]|429|5\d\d)")


def _provider_error(text: str) -> str:
    """stdout 里有没有接口报错 有就返回那一句

    **先把空白压平再找。** feiyu 的输出是按终端宽度折行的，
    `requires more credits` 会被换行拆成两段 —— 我自己查这批数据时就先用
    `grep` 搜了一次原文，得到 0 命中，差点据此得出"首轮没被污染"的结论。
    """
    flat = " ".join((text or "").split())
    got = _PROVIDER_ERR.search(flat)
    if not got:
        return ""
    # 把报错那一段带出来，光说"402"事后查不出是哪种
    return flat[got.start():got.start() + 120]


def _usage_tokens(meta: dict):
    """这条记录里的 token 是不是真的「用量」 不是就返回 None

    **老记录要追溯修正。** 0.72.0 之前的适配器在跑飞时退回拿 `turn` 行，
    而那一行的 `tokens` 是**上下文大小**不是用量，却照样填进了
    `feiyu_total_tokens` —— 0.72.0 的基线就是这么跑出来的。
    好在 `feiyu_partial` 当时就记了：它为真就说明这个数来自 `turn`，
    据此能把已有的基线一起修对，不用重跑。
    """
    if meta.get("feiyu_context_tokens") is not None:
        return None                     # 新适配器：分得清清楚楚，用量那栏是空的
    return None if meta.get("feiyu_partial") else meta.get("feiyu_total_tokens")


def _row(data: dict, turn: int = 1, stdout: str = "") -> dict:
    """一份 result.json → 一行 `report.py` 认得的记录

    `why` 里的那几个词是 `report.classify()` 的判据（"铺不起来" / "崩了" /
    "判不了" / "上限"），别改字面 —— 改了这边的分类会静默掉进「没解决」。
    """
    meta = ((data.get("agent_result") or {}).get("metadata")) or {}
    exc = data.get("exception_info") or {}
    kind = str(exc.get("exception_type") or "")
    note = " ".join(str(exc.get("exception_message") or "").split())[:80]
    reward = _reward(data.get("verifier_result"))
    took = _span(data.get("agent_execution")) or _span(data)

    # **判定顺序就是这一段的全部内容。** 先看分数（超时了但做出来了照样算解出来），
    # 再看墙钟（**哪怕没判成也算撞上限**：那是 agent 用完了时间，得进分母 ——
    # 丢进「判不了」等于把它从分母里抹掉，分数会虚高），最后才轮到「我们这边坏了」。
    ours_broke = False
    failed = _provider_error(stdout)
    if reward is not None and reward >= 1.0:
        ok, why = True, ""
    elif failed:
        # **排在墙钟前面**：接口一挂 feiyu 当场退出，后面那些超时都是它的后果
        ours_broke = True
        ok, why = False, "判不了 接口报错：%s" % failed
    elif kind == "AgentTimeoutError" or _EXEC_TIMEOUT.search(note):
        # 两种墙钟：Harbor 那道闸抛 `AgentTimeoutError`；`environment.exec` 自己的
        # `timeout_sec` 到点抛的是 `RuntimeError: Command timed out after N seconds`。
        # **后者也得算「撞上限」。** 首轮基线 48 次里有 7 次是它，当成「判不了」
        # 的话那 7 条退出分母，解决率凭空变高 —— 而它明明是 agent 用完了时间。
        # 闸本身已经在 `tbench.py` 里改成只留 Harbor 那一道，这里是第二层保险。
        ok, why = False, "撞上限 墙钟 %.0f 秒，%s" % (took, note or "被 Harbor 掐了")
    elif reward is None:
        ours_broke = True
        if kind in SETUP_FAILS:
            why = "铺不起来 %s：%s" % (kind, note)
        elif kind:
            why = "判不了 %s：%s" % (kind, note)
        else:
            why = "判不了 没有 verifier_result，也没记异常"
        ok = False
    elif meta.get("feiyu_stop") == "budget":
        ok, why = False, "撞上限 预算到顶（¥%s）" % (meta.get("feiyu_cost_cny") or "?")
    elif meta.get("feiyu_stop") == "rounds":
        ok, why = False, "撞上限 轮数到顶（%s 轮）" % (meta.get("feiyu_rounds") or "?")
    else:
        ok, why = False, "没解决 reward=%g" % reward

    return {
        "bench": BENCH,
        "suite": str(data.get("task_name") or "?"),
        "turn": turn,
        "model": (((data.get("agent_info") or {}).get("model_info")) or {}).get("name")
                 or meta.get("feiyu_model") or "?",
        "ok": ok,
        "why": why,
        "s": took,
        "rounds": meta.get("feiyu_rounds") or 0,
        "tools": 0,
        # **跑飞那次没有用量可报，就报 None 不报 0。** `report.py` 的 `_median`
        # 会跳过 None、却会把 0 算进去 —— 一个 0 混进中位数，量纲没错但数是假的。
        # （原来这里是 `or 0`，而 `feiyu_total_tokens` 那时还混着"上下文大小"，
        #  两个错凑在一起得出过「没解出来的反而更省 token」这种结论。）
        "tokens": _usage_tokens(meta),
        # **只用 feiyu 自己记的人民币。** harbor 那个 `cost_usd` 是另一个币种，
        # 混进同一列会得到一个既不是美元也不是人民币的数
        "cost": meta.get("feiyu_cost_cny") or 0.0,
        "context": 0,
        # `graded` 是 `report.classify()` 的判据，它问的是**我们这边坏没坏**
        # （判不了 / 铺不起来不进分母），不是"有没有拿到分数" —— 墙钟超时没判成
        # 那种是 agent 的事，`graded` 得是 True，`reward` 那项才是原始事实
        "detail": {"graded": not ours_broke, "reward": reward,
                   "exception": kind or None, "provider_error": failed or None,
                   "stop": meta.get("feiyu_stop") or None,
                   "partial": meta.get("feiyu_partial"),
                   "context_tokens": meta.get("feiyu_context_tokens")
                   or (meta.get("feiyu_total_tokens") if meta.get("feiyu_partial") else None),
                   "trial": data.get("trial_name")},
    }


def trials(root: Path) -> list[tuple]:
    """结果目录里的每一次 trial，连它那份 stdout 一起

    **顶层那份 `result.json` 是 JobResult，不是 trial**（它没有 `task_name`），
    拿它当 trial 解析会多出一行全空的记录。
    """
    out = []
    for path in sorted(root.rglob("result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (isinstance(data, dict) and data.get("task_name")):
            continue
        log = path.parent / "agent" / AGENT_LOG
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""        # 被 SIGKILL 的那次可能一个字都没落地
        out.append((data, text))
    return out


def rows(root: Path) -> list[dict]:
    """同一道题跑了几遍就编几号 `-k 2` 的两次是两个 trial 目录，按开始时间排 """
    seen: dict[str, int] = {}
    out = []
    for data, text in sorted(trials(root),
                             key=lambda pair: str(pair[0].get("started_at") or "")):
        name = str(data.get("task_name") or "?")
        seen[name] = seen.get(name, 0) + 1
        out.append(_row(data, seen[name], text))
    return out


def stability(got: list[dict]) -> str:
    """每题跑了不止一遍时，把逐题的 ✓✗ 串打出来

    **跑两遍的全部意义就在这几行。** 两遍都过 / 两遍都不过是结论，
    一过一不过说明这道题在噪音里 —— 那种题上的「改进」多半也是噪音。
    """
    by: dict[str, list[dict]] = {}
    for row in got:
        by.setdefault(row["suite"], []).append(row)
    if not by or max(len(v) for v in by.values()) < 2:
        return ""
    out = ["", "─" * 66, "稳定性（每题多遍）："]
    flaky = 0
    for name in sorted(by):
        marks = "".join("✓" if r["ok"] else "✗" for r in by[name])
        if "✓" in marks and "✗" in marks:
            flaky += 1
            marks += "  ← 一过一不过，这道题在噪音里"
        out.append("  %-34s %s" % (name, marks))
    out.append("%d 道里 %d 道每遍结果不一样" % (len(by), flaky))
    return "\n".join(out)


def merge(roots: list) -> list[dict]:
    """几个结果目录合成一份 **同一道题以最后一个目录里的为准**

    为什么要有这个：一轮跑完发现其中几道是坏在我们自己身上（余额、配错的闸），
    补跑的是那几道。补跑的结果要顶掉原来那几道，而不是跟它们并列 ——
    并列的话同一道题会有四条记录，稳定性那段直接失去意义。

    **整道顶掉，不按「遍」拼。** 一道题两遍里只有一遍坏了时，也用新的那两遍，
    别把「旧的第一遍 + 新的第二遍」凑一对：那两遍跑在不同的配置下，
    凑出来的稳定性是假的。
    """
    out: dict = {}
    for root in roots:
        for row in rows(pathlib.Path(root)):
            out.setdefault(str(root), {}).setdefault(row["suite"], []).append(row)
    merged: dict = {}
    for root in roots:                      # 后面的目录覆盖前面的
        for suite, got in out.get(str(root), {}).items():
            merged[suite] = got
    return [row for suite in sorted(merged) for row in merged[suite]]


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.tbench_report")
    ap.add_argument("root", nargs="+",
                    help="harbor 的结果目录 给多个就合并，同名题以最后一个为准")
    ap.add_argument("--out", default="", help="把翻译好的 JSONL 存一份（之后能 --ab 比）")
    args = ap.parse_args()

    for one in args.root:
        if not Path(one).is_dir():
            print("不是目录：%s" % one, file=sys.stderr)
            return 2
    got = merge(args.root) if len(args.root) > 1 else rows(Path(args.root[0]))
    if not got:
        print("%s 下面一个 trial 的 result.json 都没有" % "、".join(args.root),
              file=sys.stderr)
        return 2
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for row in got:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        print("写了 %d 行到 %s\n" % (len(got), args.out))
    print(report.render(got))
    print(stability(got))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
