"""跑一遍题库 每题在自己的临时目录里从头开始

    python -m evals.run                    # 全部，跑一遍
    python -m evals.run -n 3               # 每题跑三遍看稳定性
    python -m evals.run -m kimi-k2.6       # 换模型
    python -m evals.run --only 改名 修bug   # 只跑其中几题

**要花钱**：打真实接口 一轮全套大约几分钱
存盘和日志都指到临时目录，不碰 ~/.local/state/feiyu
"""

import argparse
import contextlib
import io
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from evals.tasks import TASKS

CRASHES: list[str] = []


def _isolate(box: str) -> None:
    """存盘 / 日志 / 记忆全指到临时目录 —— 跑评测不该在真实状态里留痕 """
    os.environ["XDG_STATE_HOME"] = box
    os.environ["FEIYU_NO_LOG"] = "1"
    from feiyu import memory, session
    session.DIR = os.path.join(box, "sessions")
    memory.DIR = os.path.join(box, "memory")


def one(name, setup, prompt, check, model, mode):
    from feiyu.agent import Feiyu
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        setup(root)
        agent = Feiyu(root, model=model)
        agent.set_mode(mode)
        began = time.perf_counter()
        answer, crash = "", ""
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                answer = agent.send(prompt) or ""
        except Exception as exc:
            crash = "%s: %s" % (type(exc).__name__, exc)
        spent = time.perf_counter() - began
        if crash:
            # 崩的原文写进文件 —— 有些错（比如接口 400）是偶发的，
            # 只在表格里留半句话的话事后没法查
            CRASHES.append("【%s】%s\n%s\n" % (name, prompt, crash))
            ok, why = False, "崩了 " + crash.split("\n")[0][:60]
        else:
            try:
                ok, why = check(root, answer)
            except Exception as exc:
                ok, why = False, "判题崩了 %s" % exc
        rounds = sum(1 for m in agent.backend.messages if m.get("role") == "assistant")
        tools = sum(1 for m in agent.backend.messages if m.get("role") == "tool")
        agent.toolbox.jobs.kill_all()
        return {"ok": ok, "why": why, "s": spent, "rounds": rounds,
                "tools": tools, "tokens": agent.tokens or 0, "cost": agent.cost or 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.run")
    ap.add_argument("-n", "--repeat", type=int, default=1, help="每题跑几遍")
    ap.add_argument("-m", "--model", default="", help="/key 清单里的名字，默认用上次那个")
    ap.add_argument("--mode", default="auto", help="权限模式，默认 auto（没人能回答确认）")
    ap.add_argument("--only", nargs="*", default=None, help="只跑这几题")
    args = ap.parse_args()

    box = tempfile.mkdtemp(prefix="feiyu-evals-")
    _isolate(box)
    from feiyu import keys
    model = args.model or keys.pick_default()
    if not model:
        print("没有可用模型，先 /key add", file=sys.stderr)
        return 2

    chosen = [t for t in TASKS if not args.only
              or any(k in t[0].replace(" ", "") for k in args.only)]
    print("模型 %s · %d 题 × %d 遍 · 模式 %s\n" % (model, len(chosen), args.repeat, args.mode))
    print("%-14s %s" % ("题目", "结果"))
    print("─" * 78)

    rows, wins = [], 0
    for name, setup, prompt, check in chosen:
        marks, runs = [], []
        for _ in range(args.repeat):
            got = one(name, setup, prompt, check, model, args.mode)
            runs.append(got)
            marks.append("✓" if got["ok"] else "✗")
            rows.append((name, got))
            wins += bool(got["ok"])
        bad = next((r["why"] for r in runs if not r["ok"]), "")
        print("%-14s %-8s %5.1fs %2d轮 %2d工具 %6d tok  %s" % (
            name, "".join(marks), statistics.median(r["s"] for r in runs),
            statistics.median(r["rounds"] for r in runs),
            statistics.median(r["tools"] for r in runs),
            statistics.median(r["tokens"] for r in runs), bad[:34]))

    total = len(rows)
    print("─" * 78)
    if CRASHES:
        where = os.path.join(box, "crashes.txt")
        with open(where, "w", encoding="utf-8") as f:
            f.write("\n".join(CRASHES))
        print("崩了 %d 次，原文在 %s" % (len(CRASHES), where))
    print("通过 %d/%d（%.0f%%） · 合计 %.0f 秒 · %d token · %s" % (
        wins, total, 100 * wins / max(1, total),
        sum(r["s"] for _, r in rows), sum(r["tokens"] for _, r in rows),
        ("¥%.3f" % sum(r["cost"] for _, r in rows)) if any(r["cost"] for _, r in rows)
        else "没配价格"))
    return 0 if wins == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
