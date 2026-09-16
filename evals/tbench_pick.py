"""挑一批固定的 Terminal-Bench 题，生成 / 复验基线子集

    python -m evals.tbench_pick                  # 打印这批题，带难度和它自己声明的超时
    python -m evals.tbench_pick --write          # 写进 evals/tbench_base.txt
    python -m evals.tbench_pick --check          # 复验文件跟现在算出来的一致，不一致退 1
    python -m evals.tbench_pick --cmd            # 打印可以直接粘的 harbor 命令

## 为什么要有这个

0.70.1 的十道是「89 道按题名排序等距取」——**没有分层**，抽出来 5 medium / 4 hard / 1
判不了，跑一遍拿到 5/8。那个数框住了区间，但它不是基线：换一批题、换一次跑就变。

这里按 **difficulty 分层、按比例分配名额、层内按题名排序等距取**，跟
`swebench.py::pick` 是同一个函数（直接 import，不另写一份）。分层是为了让子集的
难度构成跟全量 89 道一致 —— 榜单上那些 60-83% 是全量的数，构成不一样就没法比。

**难度读的是 harbor 下载到本地的 `task.toml`**，先下一次（只下题面，不拉镜像）：

    harbor download terminal-bench@2.0 --cache

## 为什么不用 tomllib

`tomllib` 是 3.11 才有的，而 `pyproject` / `install.sh` / README 三处都写着支持 3.10。
本机是 3.14，用了照样全绿 —— `list_files` 的 `full_match` 就是这么坏了一年多的。
要的只有两个标量字段，`_meta()` 十几行按 section 扫一遍就够，还能进主用例集。
"""

import argparse
import os
import re
import sys
from pathlib import Path

from evals.swebench import pick as _pick

HERE = Path(__file__).resolve().parent
SUBSET = HERE / "tbench_base.txt"
DATASET = "terminal-bench@2.0"
# harbor `--cache` 落地的位置：<cache>/<内容哈希>/<题名>/task.toml
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "harbor" / "tasks"
# 子集多大 24 道 × 2 遍在这台机器上是一夜的量级；再大就得砍遍数，
# 而「每题只跑一遍」正是 0.70.1 那个数不能引用的原因
COUNT = 24

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_ENTRY = re.compile(r'^\s*([A-Za-z_][\w-]*)\s*=\s*(".*?"|[-\d.]+)\s*$')


def _meta(text: str) -> dict:
    """把 task.toml 里要的几个标量读出来 —— 按 section 限定，别全文乱抓

    只认 `key = "字符串"` 和 `key = 数字` 这两种最朴素的行：要的四个字段
    （difficulty / category / agent.timeout_sec / expert_time_estimate_min）全长这样。
    数组、多行、嵌套表一律跳过 —— 认不了的行原样忽略，不要猜。
    """
    out: dict = {}
    section = ""
    for line in text.splitlines():
        got = _SECTION.match(line)
        if got:
            section = got.group(1).strip()
            continue
        got = _ENTRY.match(line)
        if not got:
            continue
        key, raw = got.group(1), got.group(2)
        if raw.startswith('"'):
            value = raw[1:-1]
        else:
            try:
                value = float(raw)
            except ValueError:
                continue
        out["%s.%s" % (section, key) if section else key] = value
    return out


def tasks(cache: Path = CACHE) -> list[dict]:
    """本地缓存里的全部题 没下过就返回空，调用方负责说清楚怎么下 """
    out = []
    for path in sorted(cache.glob("*/*/task.toml")):
        try:
            meta = _meta(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append({
            # 字段名照 `swebench._pick` 要的来 —— 那个函数按 `instance_id` 排序、
            # 按 `difficulty` 分层，这里复用它而不是再写一份等距取
            "instance_id": path.parent.name,
            "difficulty": str(meta.get("metadata.difficulty") or "未标"),
            "category": str(meta.get("metadata.category") or ""),
            "timeout": meta.get("agent.timeout_sec"),
            "expert_min": meta.get("metadata.expert_time_estimate_min"),
        })
    return out


def chosen(rows: list[dict], count: int = COUNT) -> list[str]:
    return _pick(rows, count)


def load() -> list[str]:
    """读已经定死的那份 注释行和空行跳掉 """
    out = []
    for line in SUBSET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _render(rows: list[dict], names: list[str]) -> str:
    by = {r["instance_id"]: r for r in rows}
    out = []
    levels: dict[str, int] = {}
    for name in names:
        row = by.get(name, {})
        levels[row.get("difficulty", "?")] = levels.get(row.get("difficulty", "?"), 0) + 1
        # 它自己声明的 agent 超时：`make-doom-for-mips` 是 900 秒而专家估 480 分钟，
        # 那种题「墙钟没跑完」量的是这个数，不是 agent 的能力
        out.append("%-34s %-7s %-22s to=%-6s expert=%s" % (
            name, row.get("difficulty", "?"), row.get("category", ""),
            "%g" % row["timeout"] if row.get("timeout") else "?",
            "%gmin" % row["expert_min"] if row.get("expert_min") else "?"))
    total = len(rows)
    head = ["%d 道（全量 %d 道）" % (len(names), total)]
    for level in sorted(levels):
        share = sum(1 for r in rows if r["difficulty"] == level)
        head.append("  %-7s %2d 道（全量 %d，占比 %.0f%% → 子集 %.0f%%）" % (
            level, levels[level], share,
            100 * share / max(1, total), 100 * levels[level] / max(1, len(names))))
    return "\n".join(head + [""] + out)


def _cmd(names: list[str]) -> str:
    """能直接粘的跑法 参数写死在这儿，别每次手敲 —— 敲错一个就不是同一把尺子了

    `-k 2` 是每题两遍：一遍出来的数没有稳定性可言（0.70.1 那十道就是一遍）。
    `--agent-timeout-multiplier` 放宽题里自带的那道闸（十道冒烟里四道栽在它上面）；
    **放宽不是取消** —— 放到 2.0，再跑不完就是真跑不完，记成「撞上限」。
    **`-n` 别照着「题里声明的内存 × 并发数」算。** 题 toml 里普遍写着 1 cpu / 2G，
    28 核 31G 的机器上 `-n 6` 看着只占 12G —— 实测跑到一半被系统按内存杀掉
    （harbor 进程整个没了，26 次 trial 停在 17 次）。声明的是任务自己要的，
    不含镜像里那些构建、测试、qemu 之类真正吃内存的东西。3 稳当。
    """
    flags = " ".join("-i %s" % n for n in names)
    return (
        "FEIYU_BENCH_ROUNDS=120 FEIYU_BENCH_BUDGET=1.0 FEIYU_BENCH_MODEL=<模型> \\\n"
        "PYTHONPATH=%s harbor run -d %s -a evals.tbench:FeiyuAgent \\\n"
        "  -k 2 -n 3 --agent-timeout-multiplier 2.0 -o <结果目录> \\\n"
        "  %s" % (HERE.parent, DATASET, flags))


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.tbench_pick")
    ap.add_argument("--write", action="store_true", help="写进 %s" % SUBSET.name)
    ap.add_argument("--check", action="store_true", help="复验文件跟算出来的一致")
    ap.add_argument("--cmd", action="store_true", help="打印可以直接粘的 harbor 命令")
    ap.add_argument("-c", "--count", type=int, default=COUNT)
    args = ap.parse_args()

    if args.cmd and not (args.write or args.check):
        print(_cmd(load()))
        return 0

    rows = tasks()
    if not rows:
        print("本地没有题面缓存（%s）。先下一次（只下题面，不拉镜像）：\n"
              "    harbor download %s --cache" % (CACHE, DATASET), file=sys.stderr)
        return 2
    names = chosen(rows, args.count)

    if args.check:
        try:
            have = load()
        except OSError as exc:
            print("读不到 %s：%s" % (SUBSET, exc), file=sys.stderr)
            return 1
        if have != names:
            print("子集文件跟现在算出来的不一致：\n  文件里多：%s\n  算出来多：%s"
                  % (sorted(set(have) - set(names)), sorted(set(names) - set(have))),
                  file=sys.stderr)
            return 1
        print("一致，%d 道" % len(have))
        return 0

    text = _render(rows, names)
    if args.write:
        body = ["# Terminal-Bench 2.0 的基线子集，%d 道" % len(names),
                "# 按 difficulty 分层 + 层内按题名排序等距取（evals/tbench_pick.py），",
                "# **不随机**：换台机器、换个时间都是这一批 改这个文件等于换了尺子，",
                "# 之前跑出来的数就不能跟之后的比了 ",
                *names]
        SUBSET.write_text("\n".join(body) + "\n", encoding="utf-8")
        print("写好了 %s" % SUBSET)
    print(text)
    if args.cmd:
        print()
        print(_cmd(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
