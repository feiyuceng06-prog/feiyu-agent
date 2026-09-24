"""从基线存档里挑一批**能分辨出好坏**的题，给墙钟 / 收敛类的改动做配对 A/B

    python -m evals.tbench_ab                # 打印挑出来的题和给每道题的软闸
    python -m evals.tbench_ab --write        # 写进 evals/tbench_ab.txt
    python -m evals.tbench_ab --check        # 复验文件跟现在算出来的一致，不一致退 1
    python -m evals.tbench_ab --cmd          # 打印能直接粘的两档跑法

## 为什么不能拿基线那 24 道来做 A/B

0.72.0 的基线是量**总体水平**的尺子：24 道分层子集、每题两遍、69.6%。
**它量不出小改动** —— 一道题就是 4 个百分点，而 24 道里有 4 道本来就一过一不过。

0.73.0 更吃过一次亏：挑去做墙钟 A/B 的三道题里有两道**两档都撞满 1800 秒**。
主指标是墙钟而所有样本都被上限截断时，这个指标没有分辨力 —— 不管改动有没有用，
量出来都是 1800。那一节末尾写着「下次做墙钟相关的 A/B 要挑能跑完的题」，
这个文件就是那句话。

## 挑的规矩（全部从基线存档里算，不是手挑）

1. **两遍都判过**：判不了的题连分母都进不去，更别说做对照。
2. **两遍都解出来**：要的是一个「它本来做得到」的参照。本来就做不出的题，
   两档都是 0 分，量不出任何东西。
3. **两遍都没撞墙钟**：撞了的那些墙钟被截断在上限上，没有分辨力（0.73.0 的坑）。
4. **墙钟中位 ≥ FLOOR**：几十秒就跑完的题，改动的影响淹在噪声里。

## 每道题的软闸是怎么来的

`--time-budget` 给的是**这道题基线中位墙钟的 SQUEEZE 倍**（默认 0.7）。

**必须一题一个值。** 挑出来的题自然耗时差着好几倍（两分钟到二十分钟都有），
一个统一的秒数对一半的题太松（永远轮不到它说话）、对另一半太紧（还没开始干活
就到点）。按各自的中位掐，才是同一种「掐掉三成时间」的处理。

**两档的闸完全一样**，差别只有 `FEIYU_DEADLINE_HINT`：一档提前说「该收口了」，
一档不说。量的就是「提前知道到底有没有用」——
**注意它不是在量能力**：0.70.1 已经证伪过"给更多资源"那条路（预算 ×3、轮数 120、
超时 ×3 重跑三道 0 分题，一道没翻盘），所以别指望它把做不出的题变成做得出。
它量的是：同样被掐掉三成时间，提前收口的那一档能不能多留下点能用的东西。
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBSET = HERE / "tbench_ab.txt"
# 基线存档：0.72.0 那轮 24 道 × 2 遍的翻译结果（`tbench_report --out` 出来的）
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
BASELINE = STATE / "feiyu" / "evals" / "tbench-0.72.0-baseline.jsonl"
# 墙钟中位低于这个数的题不要：量程太小，改动的影响淹在噪声里
FLOOR = 300.0
# 软闸 = 中位墙钟 × 这个数 掐掉三成：够它察觉到"来不及了"，又不至于什么都没开始
SQUEEZE = 0.7


def _rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def picked(rows: list[dict], floor: float = FLOOR,
           squeeze: float = SQUEEZE) -> list[tuple[str, int]]:
    """(题名, 软闸秒数) 按题名排序，同一份存档算出来永远是同一批 """
    by: dict[str, list[dict]] = {}
    for row in rows:
        by.setdefault(row["suite"], []).append(row)
    out = []
    for name, runs in sorted(by.items()):
        if len(runs) < 2:
            continue
        if not all((r.get("detail") or {}).get("graded") for r in runs):
            continue
        if not all(r.get("ok") for r in runs):
            continue
        if any((r.get("detail") or {}).get("exception") == "AgentTimeoutError"
               for r in runs):
            continue
        mid = statistics.median(float(r["s"]) for r in runs)
        if mid < floor:
            continue
        out.append((name, int(round(mid * squeeze))))
    return out


def load() -> list[tuple[str, int]]:
    """读已经定死的那份 一行一道：`题名 <空格> 秒数` """
    out = []
    for line in SUBSET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, secs = line.partition(" ")
        secs = secs.strip()
        # **单位必须写着。** 这个数会一路传到 `--time-budget`，而那儿的裸数按
        # **分钟**算 —— 0.78.0 那轮 A/B 就是这么白跑的：文件里 445，闸变成
        # 445 分钟，60 倍，两档一次都没咬住，看着像"这个改动没影响"。
        # 所以宁可在这儿读不出来报错，也不要让一个没单位的数飘出去。
        if not secs.endswith("s"):
            raise ValueError("%s 里 %r 没写单位：该是 `%s 445s` 这种形状"
                             % (SUBSET.name, line, name))
        out.append((name, int(secs[:-1])))
    return out


def _render(picks: list[tuple[str, int]]) -> str:
    out = ["%d 道，软闸 = 基线中位墙钟 × %g" % (len(picks), SQUEEZE)]
    for name, secs in picks:
        out.append("  %-34s --time-budget %ds" % (name, secs))
    out.append("一档总墙钟上界 %d 分钟（两档两遍就是它的四倍）"
               % (sum(s for _, s in picks) // 60))
    return "\n".join(out)


def _cmd(picks: list[tuple[str, int]]) -> str:
    """两档的跑法 **一题一条命令**：软闸是一题一个值，而 `harbor run` 的环境
    变量对这一次调用里的所有题都一样 —— 想一次跑完就只能所有题共用一个秒数，
    那正是这个文件要避免的事 一题一跑也顺带让中途挂掉只丢一道题
    """
    lines = [
        "# 两档只差 FEIYU_DEADLINE_HINT，别顺手改别的 —— 改了就不是配对 A/B 了",
        "# 跑完两档各自存一份，再 python -m evals.report --ab off.jsonl on.jsonl",
        "for HINT in on off; do",
        "  while read -r TASK SECS; do",
        "    FEIYU_BENCH_ROUNDS=120 FEIYU_BENCH_BUDGET=1.0 FEIYU_BENCH_MODEL=<模型> \\",
        "    FEIYU_BENCH_TIME=$SECS FEIYU_DEADLINE_HINT=$HINT \\",
        "    PYTHONPATH=%s harbor run -d terminal-bench@2.0 -a evals.tbench:FeiyuAgent \\" % HERE.parent,
        "      -k 2 -n 2 --agent-timeout-multiplier 2.0 -o <结果目录>/$HINT/$TASK \\",
        "      -i \"$TASK\"",
        "  done < <(grep -v '^#' %s | grep -v '^$')" % SUBSET,
        "done",
        "",
        "# --agent-timeout-multiplier 照旧放宽 Harbor 那道**硬**闸：这次量的是软闸，",
        "# 硬闸先响的话量到的还是「被外面杀掉」，跟改动没关系",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.tbench_ab")
    ap.add_argument("--write", action="store_true", help="写进 %s" % SUBSET.name)
    ap.add_argument("--check", action="store_true", help="复验文件跟算出来的一致")
    ap.add_argument("--cmd", action="store_true", help="打印能直接粘的两档跑法")
    ap.add_argument("--baseline", default=str(BASELINE), help="基线存档（JSONL）")
    args = ap.parse_args()

    if args.cmd and not (args.write or args.check):
        print(_cmd(load()))
        return 0

    path = Path(args.baseline)
    if not path.exists():
        print("找不到基线存档 %s\n"
              "它是 0.72.0 那轮 24 道 × 2 遍的翻译结果，用\n"
              "    python -m evals.tbench_report <结果目录> --out %s\n"
              "生成 没有它就没有「本来要跑多久」这个参照，挑不出题 " % (path, path),
              file=sys.stderr)
        return 2
    picks = picked(_rows(path))

    if args.check:
        try:
            have = load()
        except OSError as exc:
            print("读不到 %s：%s" % (SUBSET, exc), file=sys.stderr)
            return 1
        if have != picks:
            print("A/B 子集跟现在算出来的不一致：\n  文件里：%s\n  算出来：%s"
                  % (have, picks), file=sys.stderr)
            return 1
        print("一致，%d 道" % len(have))
        return 0

    if args.write:
        body = [
            "# 墙钟 / 收敛类改动的配对 A/B 子集（evals/tbench_ab.py 从基线存档里算的）",
            "# 一行一道：题名 + 给它的软闸（= 基线中位墙钟 × %g，**秒，单位必须写**）" % SQUEEZE,
            "# 挑的规矩：两遍都判过、两遍都解出来、两遍都没撞墙钟、中位 ≥ %g 秒 " % FLOOR,
            "# **改这个文件等于换了尺子**，之前跑出来的数就不能跟之后的比了 ",
            # **带着单位写**，理由见 `load()`：这个数要一路传到 `--time-budget`
            *["%s %ds" % (name, secs) for name, secs in picks],
        ]
        SUBSET.write_text("\n".join(body) + "\n", encoding="utf-8")
        print("写好了 %s" % SUBSET)
    print(_render(picks))
    if args.cmd:
        print()
        print(_cmd(picks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
