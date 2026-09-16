"""把一次跑分的 JSONL 读成人看得懂的结论

## 为什么"解决率"这一个数不够

一道题没过有三种完全不同的原因，混在一起那个百分比就没有意义了：

- **没解决** —— agent 真的做不出来。这是我们要改进的东西。
- **撞上限** —— 轮数或预算到顶了。量出来的是上限不是能力，**调大再跑一次**。
- **判不了** —— 我们这边坏了（镜像拉不到、容器起不来、harness 报告解析不了）。
  它既不算 agent 的错，也不该算进分母，**必须先修**。

第三类尤其危险：整条链路坏掉的时候，表格上跟"agent 全都没做出来"长得一模一样。
这个模块的全部意义就是把这三类分开。
"""

import json
import statistics
import sys
from pathlib import Path


def load(path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue        # 写到一半被 ctrl-c 掐断的那行，跳过就好
    return rows


def classify(row: dict) -> str:
    """一行 → resolved / 没解决 / 撞上限 / 判不了 / 铺不起来 / 崩了 """
    detail = row.get("detail") or {}
    why = row.get("why") or ""
    if row.get("ok"):
        return "resolved"
    if "铺不起来" in why:
        return "铺不起来"
    if why.startswith("崩了"):
        return "崩了"
    if detail.get("graded") is False or why.startswith("判不了"):
        return "判不了"
    # 轮数/预算到顶：agent 自己会把这句话作为最后的回答带出来
    if "上限" in why or "停下来" in why:
        return "撞上限"
    return "没解决"


def summarize(rows: list[dict]) -> dict:
    kinds: dict[str, list[str]] = {}
    for row in rows:
        kinds.setdefault(classify(row), []).append(row.get("suite") or "?")
    good = len(kinds.get("resolved", []))
    # **分母只算真判过的。** 判不了 / 铺不起来 / 崩了 是我们这边的问题，
    # 把它们算进去等于让自己的 bug 拉低 agent 的分数，那个数没法跟别人比。
    judged = good + len(kinds.get("没解决", [])) + len(kinds.get("撞上限", []))
    return {"kinds": kinds, "resolved": good, "judged": judged, "total": len(rows)}


def _median(rows, key, default=0):
    got = [r.get(key) or 0 for r in rows if r.get(key) is not None]
    return statistics.median(got) if got else default


def render(rows: list[dict]) -> str:
    if not rows:
        return "一行都没有"
    got = summarize(rows)
    out = []
    out.append("═" * 66)
    model = rows[0].get("model") or "?"
    # 题库名字跟着记录走 —— 这套口径现在也给 Terminal-Bench 用（`tbench_report.py`），
    # 写死"SWE-bench"的话表头会说谎，而表头正是事后翻日志时唯一能认出这是哪批数的东西
    bench = rows[0].get("bench") or "SWE-bench Verified 子集"
    out.append("%s · %s · %d 题" % (bench, model, got["total"]))
    out.append("═" * 66)
    if got["judged"]:
        out.append("**解决率 %d/%d = %.1f%%**（分母只算真判过的）" % (
            got["resolved"], got["judged"], 100 * got["resolved"] / got["judged"]))
    else:
        out.append("**一道都没判成** —— 先去看「判不了」那几条，是链路坏了")
    order = ["resolved", "没解决", "撞上限", "判不了", "铺不起来", "崩了"]
    for kind in order:
        ids = got["kinds"].get(kind)
        if not ids:
            continue
        note = {
            "撞上限": "  ← 量出来的是上限不是能力，调大 MAX_ROUNDS/BUDGET 重跑",
            "判不了": "  ← **我们这边坏了**，不算 agent 的错，先修再谈分数",
            "铺不起来": "  ← 镜像/容器的问题，同上",
            "崩了": "  ← agent 抛异常，看 crashes",
        }.get(kind, "")
        out.append("")
        out.append("%-8s %2d%s" % (kind, len(ids), note))
        for i in range(0, len(ids), 2):
            out.append("    " + "  ".join("%-34s" % x for x in ids[i:i + 2]))
    done = [r for r in rows if classify(r) in ("resolved", "没解决", "撞上限")]
    if done:
        out.append("")
        out.append("─" * 66)
        out.append("中位：%d 轮 · %.0f 秒 · %s token　|　合计 %.1f 分钟 · ¥%.2f" % (
            _median(done, "rounds"), _median(done, "s"),
            "%.0fk" % (_median(done, "tokens") / 1000),
            sum(r.get("s") or 0 for r in rows) / 60,
            sum(r.get("cost") or 0 for r in rows)))
        win = [r for r in rows if r.get("ok")]
        lose = [r for r in done if not r.get("ok")]
        if win and lose:
            out.append("解出来的那些中位 %d 轮，没解出来的中位 %d 轮" % (
                _median(win, "rounds"), _median(lose, "rounds")))
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--ab":
        return main_compare()
    if len(sys.argv) < 2:
        print("用法：python -m evals.report <跑分的 jsonl>", file=sys.stderr)
        return 2
    rows = load(sys.argv[1])
    print(render(rows))
    return 0


# ---- A/B 对比 ----
#
# 12 道样本太小，**解决率差一两道全是噪音**。所以对比的重点不是那个百分比，
# 而是几个"机制指标"——它们直接对应 0.68.0 那几样东西该起的作用：
#
#   白跑轮次  工具结果里 is_error 的占比 —— 诊断该把它压下去
#   轮数      复现优先该让它少走弯路
#   token     省下来的往返就是省下来的钱
#
# 这几个指标每题都有一个数，12 道就是 12 个配对样本，比"解决/没解决"这种
# 0-1 的信号灵敏得多。


def _pair(a: list[dict], b: list[dict]) -> list[tuple[dict, dict]]:
    """按题名配对 只留两边都跑过的 —— 一边崩了的那道不能参与比较 """
    ai = {r.get("suite"): r for r in a}
    bi = {r.get("suite"): r for r in b}
    out = []
    for name in sorted(set(ai) & set(bi)):
        x, y = ai[name], bi[name]
        if classify(x) in ("崩了", "判不了", "铺不起来"):
            continue
        if classify(y) in ("崩了", "判不了", "铺不起来"):
            continue
        out.append((x, y))
    return out


def _num(rows, key):
    got = [r.get(key) or 0 for r in rows]
    return statistics.median(got) if got else 0


def compare(old: list[dict], new: list[dict], names=("对照", "实验")) -> str:
    pairs = _pair(old, new)
    if not pairs:
        return "两边没有共同跑成的题，没法比"
    a = [x for x, _ in pairs]
    b = [y for _, y in pairs]
    out = ["═" * 62,
           "A/B 对比：%s vs %s · 共同跑成 %d 道" % (names[0], names[1], len(pairs)),
           "═" * 62]
    wa = sum(1 for r in a if r.get("ok"))
    wb = sum(1 for r in b if r.get("ok"))
    out.append("解决率      %s %d/%d   →   %s %d/%d" % (
        names[0], wa, len(a), names[1], wb, len(b)))
    out.append("            （%d 道样本，差一两道都在噪音里，别当结论）" % len(pairs))
    out.append("")
    out.append("%-12s %10s %10s %10s" % ("中位", names[0], names[1], "变化"))
    for label, key in (("轮数", "rounds"), ("耗时(秒)", "s"),
                       ("token", "tokens"), ("花费(元)", "cost")):
        va, vb = _num(a, key), _num(b, key)
        delta = ("%+.1f%%" % (100 * (vb - va) / va)) if va else "—"
        out.append("%-12s %10.0f %10.0f %10s" % (label, va, vb, delta))
    out.append("")
    out.append("逐题（轮数 / 是否解决）：")
    for x, y in pairs:
        mark = lambda r: "✓" if r.get("ok") else "✗"
        flip = ""
        if x.get("ok") != y.get("ok"):
            flip = "  ← 翻盘（%s→%s）" % (mark(x), mark(y))
        out.append("  %-34s %3d%s → %3d%s%s" % (
            x.get("suite", "?"), x.get("rounds", 0), mark(x),
            y.get("rounds", 0), mark(y), flip))
    return "\n".join(out)


def main_compare() -> int:
    if len(sys.argv) < 4:
        print("用法：python -m evals.report --ab <对照.jsonl> <实验.jsonl>", file=sys.stderr)
        return 2
    print(compare(load(sys.argv[2]), load(sys.argv[3])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
