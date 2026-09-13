"""判分：一律交给官方 harness，我们只出 patch

## 为什么不自己判

自己判就要把 FAIL_TO_PASS 从测试输出里认出来，而那要覆盖四套格式：pytest、
django 自己的 runner、sympy 的 `bin/test`、sphinx 的 tox。差一点，分数就不能跟
公开榜（mini-swe-agent / OpenHands / SWE-agent 都在这套上报分）比 ——
**而"能跟人比"正是接这套 benchmark 的唯一理由**。

## 为什么走 uv 而不是进 pyproject

`swebench` 拖 datasets → pandas / numpy / pyarrow，实测**装完 75 个包**。
一个 `pip install feiyu-agent[swebench]` 就毁掉"只有三个依赖"这块招牌。
所以走 `uv run --with swebench==<钉死> --no-project`：一次性环境，用完就算，
`pyproject` 一个字不动。

## 版本钉死

`SWEBENCH_VERSION` 是这条链路的一部分：换版本可能换报告路径、换判定细节，
而那会让前后两次的分数不可比。要升级就显式改这个常量，并且重跑一次 §gold 那条闸。
"""

import glob
import json
import shutil
import subprocess
import time
from pathlib import Path

SWEBENCH_VERSION = "5.0.2"
# **必须用新 org 那份。** `princeton-nlp/SWE-bench_Verified` 没有
# `image` / `eval_script` / `log_parser` 三个字段，而 swebench 5.x 的
# `make_test_spec()` 直接 `instance["image"]` —— 用旧的会 KeyError。
DATASET = "SWE-bench/SWE-bench_Verified"
# 判一道题要在容器里跑整套测试，慢是正常的
GRADE_TIMEOUT = 2400

# 传给 grade() 表示"用官方答案判一遍"的哨兵 —— 走 harness 自己的 `-p gold`
class _Gold(list):
    """`-p gold` 这条路上没有 patch，只有 id —— 用一个子类把两种入参分开 """


def GOLD_IDS(ids):
    return _Gold(ids)


def available() -> tuple[bool, str]:
    if not shutil.which("uv"):
        return False, "没装 uv（判分要靠它起一次性的 swebench 环境）"
    if not shutil.which("docker"):
        return False, "没装 docker"
    return True, ""


def write_preds(path: Path, rows: list[tuple[str, str]], who: str) -> None:
    """rows 是 [(instance_id, patch)] who 是这次跑分的名字，会进报告文件名 """
    with open(path, "w", encoding="utf-8") as f:
        for iid, patch in rows:
            f.write(json.dumps({"instance_id": iid, "model_name_or_path": who,
                                "model_patch": patch or ""}, ensure_ascii=False) + "\n")


def _find_report(workdir: Path, run_id: str, who: str) -> dict | None:
    """把报告捞出来

    **路径不能写死。** 不同版本在 `logs/run_evaluation/<run>/<who>/` 和
    `logs/evaluation/<run>/` 之间变过，顶层还会掉一个 `<who>.<run>.json`。
    三个地方都找一遍；**都没有就返回 None，绝不猜成"没解决"** ——
    那是在说 agent 做错了，而其实是我们的链路坏了，这两件事必须分得开。
    """
    spots = [
        workdir / ("%s.%s.json" % (who, run_id)),
        *[Path(p) for p in glob.glob(str(workdir / "logs" / "**" / "*report*.json"),
                                     recursive=True)],
        *[Path(p) for p in glob.glob(str(workdir / "**" / ("%s.%s.json" % (who, run_id))),
                                     recursive=True)],
    ]
    for spot in spots:
        try:
            got = json.loads(spot.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(got, dict):
            return got
    return None


def _verdicts(report: dict) -> tuple[set[str], set[str]]:
    """(判过的, 判成解决了的) 两种报告形状都认

    5.x 的顶层是 `{"resolved_ids": [...], "completed_ids": [...], ...}`；
    更早是 `{"<instance_id>": {"resolved": true, ...}}`。

    **"判过的"这一半是必须的，不能只看 resolved。** 报告在、但这道题压根没被
    judge 到（镜像缺、跑崩了、id 拼错了）时，它同样不在 `resolved_ids` 里 ——
    只看 resolved 的话这种情况会被读成"没解决"，也就是**在说 agent 做错了**，
    而其实是我们的链路坏了。这两件事必须分得开，否则整套分数不可信。
    """
    if isinstance(report.get("resolved_ids"), list):
        def ids(key):
            got = report.get(key)
            return {str(i) for i in got} if isinstance(got, list) else set()

        good = ids("resolved_ids")
        # **"判过"只认这三个。** 实测 5.0.2 的报告里还有 `submitted_ids`，
        # 但它只表示"我们把预测交上去了" —— 一道题在容器里挂掉（infra_failure）
        # 照样在 submitted 里，把它算成判过就会报成"agent 没解决"。
        # 同理 `empty_patch_ids`：那是"根本没交出东西"，不是"交了但不对"。
        seen = good | ids("completed_ids") | ids("unresolved_ids")
        # 这几档是明确的"量不出来"，即便别处也列了它们也要排掉
        seen -= (ids("infra_failure_ids") | ids("ambiguous_failure_ids")
                 | ids("incomplete_ids") | ids("error_ids"))
        return seen, good
    rows = {k: v for k, v in report.items() if isinstance(v, dict)}
    return set(rows), {k for k, v in rows.items() if v.get("resolved") is True}


def grade(rows: list[tuple[str, str]], who: str, workdir: Path,
          run_id: str = "") -> tuple[dict[str, bool] | None, str]:
    """判一批 返回 ({instance_id: 解没解决}, 说明)；判不了返回 (None, 为什么)

    判不了和没解决是**两回事**，调用方要分开处理。
    """
    ok, why = available()
    if not ok:
        return None, why
    run_id = run_id or ("feiyu-%s" % time.strftime("%Y%m%d-%H%M%S"))
    workdir.mkdir(parents=True, exist_ok=True)
    # **`-p gold` 是官方给的后门**：直接拿数据集里的正确答案当预测，
    # 用来验判据本身，比我们自己去拉 patch 再喂进去可靠（少一层我们可能写错的东西）
    gold = isinstance(rows, _Gold)
    ids = list(rows) if gold else [iid for iid, _ in rows]
    if gold:
        preds_arg = "gold"
    else:
        preds = workdir / "preds.jsonl"
        write_preds(preds, rows, who)
        preds_arg = str(preds)
    argv = [
        "uv", "run", "--with", "swebench==" + SWEBENCH_VERSION, "--no-project",
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", DATASET,
        "--predictions_path", preds_arg,
        "--run_id", run_id,
        "--max_workers", "1",
        "--report_dir", str(workdir),
        # 注意：5.0.2 **没有 `--cache_level`**（4.x 才有），传了会被 argparse 顶回来
        "--instance_ids", *ids,
    ]
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=GRADE_TIMEOUT, cwd=str(workdir))
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "判分跑不起来：%s" % exc
    report = _find_report(workdir, run_id, who)
    if report is None:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-6:]
        return None, "找不到判分报告（退出码 %d）：%s" % (
            done.returncode, " / ".join(tail)[:400])
    seen, good = _verdicts(report)
    missing = [i for i in ids if i not in seen]
    if missing:
        # 报告在、但这几道根本没被判到 —— 这是"量不出来"，不是"没解决"
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-4:]
        return None, "报告里没有这 %d 道题的结论（退出码 %d）：%s / %s" % (
            len(missing), done.returncode, "、".join(missing[:3]),
            " ".join(tail)[:300])
    return {iid: (iid in good) for iid in ids}, ""


# ---- 两条干跑闸 ----
#
# 这两条是整条链路唯一的正确性证明，而且**一个 token 都不花**：
#   空 patch  → 每道题必须 unresolved（不然判据根本没在判）
#   gold patch → 每道题必须 resolved（不然是我们的链路坏了，不是 agent 不行）
# 没有这两条，"分数低"和"我们量错了"在表格上长得一模一样。


def selftest(instance_ids: list[str], keep: bool = False) -> int:
    import tempfile
    workdir = Path(tempfile.mkdtemp(prefix="feiyu-swe-grade-"))
    bad = 0
    try:
        print("=== 闸一：空 patch，每道题都必须判成没解决 ===")
        got, why = grade([(i, "") for i in instance_ids], "empty", workdir / "empty")
        if got is None:
            print("  判不了：%s" % why)
            return 2
        for iid, ok in sorted(got.items()):
            print("  %-38s %s" % (iid, "resolved（不该！）" if ok else "unresolved ✓"))
            bad += bool(ok)

        print("=== 闸二：gold patch，每道题都必须判成解决了 ===")
        print("  （走 harness 自己的 `-p gold`，不自己去拉答案 —— 少一层我们可能写错的东西）")
        got, why = grade(GOLD_IDS(instance_ids), "gold", workdir / "gold")
        if got is None:
            print("  判不了：%s" % why)
            return 2
        for iid, ok in sorted(got.items()):
            print("  %-38s %s" % (iid, "resolved ✓" if ok else "unresolved（链路坏了！）"))
            bad += not ok
    finally:
        if keep:
            print("留着不删：%s" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    print("─" * 60)
    print("两条闸都过了，判分链路可信" if not bad else "有 %d 处不对，先别信这套分数" % bad)
    return 1 if bad else 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="evals.swegrade")
    ap.add_argument("--selftest", nargs="*", metavar="INSTANCE_ID",
                    help="拿空 patch 和 gold patch 各判一遍（不花 token）")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if args.selftest is not None:
        ids = args.selftest or ["astropy__astropy-12907"]
        return selftest(ids, keep=args.keep)
    ok, why = available()
    print("判分环境：%s（swebench %s）" % ("可用" if ok else why, SWEBENCH_VERSION))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
