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
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from evals import suites

CRASHES: list[str] = []
# --save 存到这里，--compare 从这里读 不进版本库：结果跟模型和当天的接口状态绑着
RESULTS = Path.home() / ".local" / "state" / "feiyu" / "evals"


def _isolate(box: str) -> None:
    """存盘 / 日志 / 记忆 / 配置全指到临时目录 —— 跑评测不该在真实状态里留痕

    **`XDG_CONFIG_HOME` 也要换，这条原来漏了。** `keys.STORE` / `keys.STATE` 在它
    下面，而每建一个 `Feiyu` 都会 `keys.set_last()`、撞了窗口还会 `keys.learn_limit()`
    —— 跑一次评测就把用户真正的 `state.json` 改了（`-m kimi-k2.7-code` 会被记成
    "上次用的"，下次在终端里敲 feiyu 就换了模型）。`permissions.load()` 见到
    `known` 跟出厂列表对不上时也会回写。

    模型清单是**只读要用的**（不然没 key 可用），所以先原样拷一份进来：
    读的是真配置，写的全落在这份拷贝上。

    **必须在 import feiyu 之前设**——那几个 `STORE` / `STATE` 都是模块 import
    时算好的常量，晚一步就已经指向真目录了。
    """
    real = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    here = os.path.join(box, "feiyu")
    os.makedirs(here, exist_ok=True)
    # **拷完必须收权限。** `shutil.copyfile` 只拷内容不拷权限位，落地是 umask 的 0644 ——
    # 而这份拷贝里是**明文 API key**，还躺在 /tmp 这种同机谁都列得到的地方。
    # 真配置那边 `keys._write()` 一直是 0600，这条拷贝不能把它降级了。
    os.chmod(here, 0o700)
    for name in ("models.json", "state.json"):
        target = os.path.join(here, name)
        try:
            shutil.copyfile(os.path.join(real, "feiyu", name), target)
            os.chmod(target, 0o600)
        except OSError:
            pass        # 没有就算了，keys.load() 自己会返回空，main() 会报"没有可用模型"
    os.environ["XDG_CONFIG_HOME"] = box
    os.environ["XDG_STATE_HOME"] = box
    # bash 工具靠一个临时文件跨调用带回 pwd（tools.py 里 mkstemp 那处）。
    # 它落在 TMPDIR 下，而 SWE-bench 那条路要把这个目录一起挂进容器 ——
    # 不指到 box 里的话，容器里看不见它，`cd` 就静默失效了。
    os.environ["TMPDIR"] = os.path.join(box, "tmp")
    os.makedirs(os.environ["TMPDIR"], exist_ok=True)
    os.environ["FEIYU_NO_LOG"] = "1"
    from feiyu import memory, session
    session.DIR = os.path.join(box, "sessions")
    memory.DIR = os.path.join(box, "memory")


def _jot(path: str, case, turn: int, got: dict, model: str, mode: str) -> None:
    """**边跑边追加，不要攒到最后一次性写。** 一轮 50 题的 benchmark 要跑几个钟头，
    中途 ctrl-c 或者机器睡过去的话，攒着的那份就全没了 —— 而每一条都是真花钱跑出来的。
    """
    if not path:
        return
    row = {"suite": case.name, "turn": turn, "model": model, "mode": mode,
           "when": time.strftime("%Y-%m-%d %H:%M:%S"), **got}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        print("写不进 %s：%s" % (path, exc), file=sys.stderr)


def _blank(name: str, why: str, spent: float = 0.0) -> dict:
    """一道题连跑都没跑起来时的那一行 形状要跟正常那条一模一样，
    否则汇总那几个 sum / median 会在缺字段上抛 """
    return {"ok": False, "why": why, "s": spent, "rounds": 0, "tools": 0,
            "tokens": 0, "cost": 0.0, "context": 0, "detail": {}}


def one(case, model, mode):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        began = time.perf_counter()
        # **铺现场也要接住。** SWE-bench 那条路要拉镜像、起容器，任何一步都可能挂；
        # 原来这里连 try 都没有，一道题铺不起来整轮带 traceback 崩掉
        try:
            handle = case.setup(root)
        except Exception as exc:
            CRASHES.append("【%s】铺现场失败\n%s: %s\n" % (case.name, type(exc).__name__, exc))
            return _blank(case.name, "铺不起来 %s" % str(exc)[:50],
                          time.perf_counter() - began)
        try:
            return _drive(case, root, handle, model, mode, began)
        finally:
            # 判完必收，**无论上面成没成、崩没崩** —— 泄漏一个容器比判错一道题贵
            if case.teardown is not None:
                try:
                    case.teardown(handle)
                except Exception:
                    pass


def _drive(case, root, handle, model, mode, began):
    from feiyu.agent import Feiyu
    agent = Feiyu(root, model=model)
    agent.set_mode(mode)
    # 每题自己的上限 内置题库不设（沿用出厂 25），benchmark 那边设得很大
    if case.max_rounds:
        agent.max_rounds = case.max_rounds
    if case.budget:
        agent.budget = case.budget
    answer, crash = "", ""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            answer = agent.send(case.prompt) or ""
    except Exception as exc:
        crash = "%s: %s" % (type(exc).__name__, exc)
    spent = time.perf_counter() - began
    detail = {}
    if crash:
        # 崩的原文写进文件 —— 有些错（比如接口 400）是偶发的，
        # 只在表格里留半句话的话事后没法查
        CRASHES.append("【%s】%s\n%s\n" % (case.name, case.prompt, crash))
        ok, why = False, "崩了 " + crash.split("\n")[0][:60]
    else:
        try:
            got = case.check(root, answer, handle)
            ok, why, detail = got.ok, got.why, got.detail
        except Exception as exc:
            ok, why = False, "判题崩了 %s" % exc
    rounds = sum(1 for m in agent.backend.messages if m.get("role") == "assistant")
    tools = sum(1 for m in agent.backend.messages if m.get("role") == "tool")
    agent.toolbox.jobs.kill_all()
    agent.toolbox.lsp.close()       # 用了 symbol 的话这儿会留一个语言服务器进程
    agent.toolbox.shell_reset()
    # context 是收尾时那次请求的规模：历史瘦身管不管用，看这个数随轮数涨多快
    return {"ok": ok, "why": why, "s": spent, "rounds": rounds,
            "tools": tools, "tokens": agent.tokens or 0, "cost": agent.cost or 0.0,
            "context": agent.context or 0, "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.run")
    ap.add_argument("-n", "--repeat", type=int, default=1, help="每题跑几遍")
    ap.add_argument("-m", "--model", default="", help="/key 清单里的名字，默认用上次那个")
    ap.add_argument("--mode", default="auto", help="权限模式，默认 auto（没人能回答确认）")
    ap.add_argument("--only", nargs="*", default=None, help="只跑这几题")
    ap.add_argument("--suite", default="builtin",
                    choices=sorted(suites.SUITES), help="跑哪套题（默认内置题库）")
    ap.add_argument("--out", default="",
                    help="每跑完一题往这个 JSONL 里追一行 —— 表格里的 why 会被截断，这里不会")
    ap.add_argument("--save", default="", help="把这次结果存成基线，起个名字")
    ap.add_argument("--compare", default="", help="跟之前存的哪份基线比")
    args = ap.parse_args()
    base = {}
    if args.compare:
        try:
            with open(RESULTS / (args.compare + ".json"), encoding="utf-8") as f:
                base = json.load(f)["tasks"]
        except (OSError, ValueError, KeyError) as exc:
            print("读不到基线 %s：%s" % (args.compare, exc), file=sys.stderr)
            return 2

    box = tempfile.mkdtemp(prefix="feiyu-evals-")
    _isolate(box)
    from feiyu import keys
    model = args.model or keys.pick_default()
    if not model:
        print("没有可用模型，先 /key add", file=sys.stderr)
        return 2

    chosen = suites.load(args.suite, args.only)
    if not chosen:
        print("没挑中任何题（suite=%s only=%s）" % (args.suite, args.only), file=sys.stderr)
        return 2
    print("模型 %s · %s · %d 题 × %d 遍 · 模式 %s\n" % (
        model, args.suite, len(chosen), args.repeat, args.mode))
    print("%-14s %-8s %6s %4s %5s %10s %8s" % (
        "题目", "结果", "耗时", "轮", "工具", "累计token", "上下文"))
    print("─" * 78)

    rows, wins = [], 0
    saved = {}
    worse = []
    for case in chosen:
        name = case.name
        marks, runs = [], []
        for turn in range(args.repeat):
            got = one(case, model, args.mode)
            _jot(args.out, case, turn, got, model, args.mode)
            runs.append(got)
            marks.append("✓" if got["ok"] else "✗")
            rows.append((name, got))
            wins += bool(got["ok"])
        bad = next((r["why"] for r in runs if not r["ok"]), "")
        passed = sum(bool(r["ok"]) for r in runs)
        tokens = statistics.median(r["tokens"] for r in runs)
        context = statistics.median(r["context"] for r in runs)
        saved[name] = {"passed": passed, "runs": len(runs), "tokens": tokens,
                       "rounds": statistics.median(r["rounds"] for r in runs),
                       "context": context,
                       # 墙钟和花费原来不存，于是 --compare 永远发现不了"变慢了"和"变贵了"
                       "s": round(statistics.median(r["s"] for r in runs), 1),
                       "cost": round(statistics.median(r["cost"] for r in runs), 6)}
        note = bad[:34]
        old = base.get(name)
        if old:
            # 通过率降了才算变差 token 涨跌有随机性，只标出来不判输赢
            was = old["passed"] / max(1, old["runs"])
            now = passed / max(1, len(runs))
            delta = tokens - old["tokens"]
            ctx_delta = context - old.get("context", context)
            flag = "↓" if now < was else ("↑" if now > was else " ")
            if now < was:
                worse.append(name)
            note = "%s 基线 %d/%d %+5d tok 上下文%+5d  %s" % (
                flag, old["passed"], old["runs"], delta, ctx_delta, note)
        print("%-14s %-8s %5.1fs %2d轮 %2d工具 %6d tok %6d ctx  %s" % (
            name, "".join(marks), statistics.median(r["s"] for r in runs),
            statistics.median(r["rounds"] for r in runs),
            statistics.median(r["tools"] for r in runs), tokens, context, note))

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
    if args.out:
        print("逐题明细写在 %s" % args.out)
    if args.save:
        RESULTS.mkdir(parents=True, exist_ok=True)
        with open(RESULTS / (args.save + ".json"), "w", encoding="utf-8") as f:
            json.dump({"model": model, "mode": args.mode, "repeat": args.repeat,
                       "suite": args.suite, "tasks": saved}, f,
                      ensure_ascii=False, indent=1)
        print("已存成基线 %s" % args.save)
    if base:
        print("变差的题：%s" % ("、".join(worse) if worse else "没有"))
    # **退出码按 suite 分两套。** 内置题库是"应该全过"的回归闸，一道不过就是红；
    # 而 benchmark 上三成解决率已经是好成绩，拿满分当及格线的话它永远是红的，
    # 那这个退出码就没人看了 —— benchmark 只在**比基线差了**的时候才算失败。
    if args.suite == "builtin":
        return 0 if wins == total and not worse else 1
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
