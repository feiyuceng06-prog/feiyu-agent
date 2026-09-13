"""SWE-bench Verified：真实仓库上的 500 个 issue，官方判据

跟 `evals/tasks.py` 那 22 道自己出的题是两回事：那些测的是"代码对不对"，
这些测的是**在真实项目上解不解得掉一个真实的 issue** —— 有公开基线可比
（mini-swe-agent、OpenHands、SWE-agent 都在这套上报分），所以"比谁强"这句话
才有客观口径。

## 三条设计决定

**一、数据集走 HF 的 REST 接口，不引 `datasets` 包。**
`https://datasets-server.huggingface.co/rows?...&offset=N&length=M` 直接出 JSON，
`urllib` + `json` 就够 —— 项目的硬性约定是不引新依赖，而评测这条路也不该例外。
拉回来的 500 条缓存在 `evals/.cache/` 里（不进版本库，几 MB）。

**二、子集写死在 `evals/swebench_subset.txt`，不随机抽。**
随机抽的话这次 30% 下次 26% 分不清是改动的效果还是抽样的运气。
选法是「按 difficulty 分层 + 按 instance_id 排序后等距取」，
生成一次、进版本库、以后一直是这一批。

**三、agent 在宿主机上跑，命令送进容器。**
镜像里的仓库先 `docker cp` 出来到宿主机工作区，再把工作区挂回容器的 `/testbed` ——
于是 `read_file` / `write_file` / `search` 一个字都不用改（它们照旧在宿主机上操作），
只有跑测试要 `docker exec`。反过来（把 feiyu 装进 4503 个镜像）既慢又脏，
而且沙箱、LSP、jobs 全要在容器里重配一遍。
这也是 mini-swe-agent 那套做法，head-to-head 才对得起"同一个条件"。
"""

import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from evals.suites import Case

# **必须用新 org 那份。** `princeton-nlp/SWE-bench_Verified` 没有
# `image` / `eval_script` / `log_parser` 三个字段，而 swebench 5.x 的
# `make_test_spec()` 直接 `instance["image"]` —— 用旧的会 KeyError。
DATASET = "SWE-bench/SWE-bench_Verified"
ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE = 100                      # 接口单次上限
TOTAL = 500

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache" / "swebench_verified.json"
SUBSET = HERE / "swebench_subset.txt"
# 难题档：**全 Verified 里「1-4 小时」+「>4 小时」一共只有 45 道**，
# 小到不用抽样，全取 —— 于是"这把尺子是哪 45 道"由数据集本身定死，
# 连抽样规则都不用记，换谁来生成都是同一批
SUBSET_HARD = HERE / "swebench_hard.txt"
# A/B 对照用的 12 道（等距取自难题档）：跑两遍只翻 FEIYU_ASSIST 这一个开关，
# 拿来判断 0.68.0 那几样"帮忙的东西"到底有没有用 全量 45 道太贵
SUBSET_AB = HERE / "swebench_ab12.txt"
HARD_LEVELS = ("1-4 hours", ">4 hours")

# 一道题最多跑多久（秒） 真实仓库的测试本来就慢，卡死的那种要能自己停
CASE_TIMEOUT = 3600
# **出厂的 25 轮对这套题太小。** 实测最简单的 astropy-12907 就用了 18 轮，
# 而子集里 23 道是 django、还有「1-4 小时」难度的 —— 不调大的话，
# 量出来的是"撞上限了"而不是"做不出来"，这两件事必须分得开
MAX_ROUNDS = 120
# 单题花费上限（元） 放开轮数之后这是唯一的另一道闸：一道题卡在循环里
# 不该把整轮 50 题的预算烧光 实测单题 ¥0.17，给到 3 块是很宽的余量
BUDGET = 3.0
TEST_TIMEOUT = 900
# 拉镜像可以很慢（1.1 GB），但不能没有上限
PULL_TIMEOUT = 1800

# 官方的镜像命名：instance_id 里的 `__` 换成 `_1776_`，全小写
# （1776 是 SWE-bench 自己定的分隔符，没有别的含义，照抄就行）
IMAGE = "swebench/sweb.eval.x86_64.%s"


def image_for(instance_id: str) -> str:
    return IMAGE % instance_id.replace("__", "_1776_").lower()


# ---- 数据集 ----

def _get(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8", "replace"))


def fetch(force: bool = False) -> list[dict]:
    """把 500 条拉下来缓存住 只出我们要的那几个字段，别把 patch 原文也存着 """
    if CACHE.exists() and not force:
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except ValueError:
            pass        # 缓存写坏了就重拉，不值得为它报错
    rows = []
    for offset in range(0, TOTAL, PAGE):
        url = "%s?dataset=%s&config=default&split=test&offset=%d&length=%d" % (
            ROWS_API, urllib.parse.quote(DATASET, safe=""), offset, PAGE)
        got = _get(url)
        for item in got.get("rows") or []:
            rows.append(_slim(item.get("row") or {}))
        if not got.get("rows"):
            break
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def _slim(row: dict) -> dict:
    """只留跑分要用的字段 `patch`（官方答案）**特意不留**：
    它在缓存里躺着，哪天顺手喂进上下文就等于作弊 —— 要验判据本身时单独去拉 """
    return {
        "instance_id": row.get("instance_id") or "",
        "repo": row.get("repo") or "",
        "base_commit": row.get("base_commit") or "",
        "problem_statement": row.get("problem_statement") or "",
        "difficulty": row.get("difficulty") or "",
        "FAIL_TO_PASS": _tests(row.get("FAIL_TO_PASS")),
        "PASS_TO_PASS": _tests(row.get("PASS_TO_PASS")),
    }


def _tests(value) -> list[str]:
    """这两个字段在数据集里是**JSON 字符串**不是列表，直接当列表用会一个字符一个字符地迭代 """
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        got = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in got] if isinstance(got, list) else []


def pick(rows: list[dict], count: int) -> list[str]:
    """按 difficulty 分层 + 按 instance_id 排序后等距取，**确定性的**

    分层是为了别让子集全是简单题（那样分数虚高、也测不出难题上的差距）。
    等距取而不是随机取：同一份输入永远出同一批 id。
    """
    buckets: dict[str, list[str]] = {}
    for row in sorted(rows, key=lambda r: r["instance_id"]):
        buckets.setdefault(row["difficulty"] or "未标", []).append(row["instance_id"])
    out: list[str] = []
    for level in sorted(buckets):
        ids = buckets[level]
        # 这一档按它在总体里的占比分配名额，至少给一个
        want = max(1, round(count * len(ids) / max(1, len(rows))))
        step = max(1, len(ids) // want)
        out.extend(ids[::step][:want])
    return sorted(out)[:count]


def subset(path=None) -> list[str]:
    """钉死的那一批 id 没有这个文件就报错，**不许现场随机抽一批凑数** """
    path = path or SUBSET
    if not path.exists():
        raise SystemExit(
            "没有 %s —— 先跑一次 `python -m evals.swebench --pick 50`（或 --hard）生成，"
            "生成完提交进版本库（子集必须是固定的，否则前后两次分数不可比）" % path)
    ids = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    return [i for i in ids if i and not i.startswith("#")]


# ---- 判题 ----
#
# **判分不自己写，一律交给官方 harness**（`swebench.harness.run_evaluation`）。
#
# 自己写的话，光是把 FAIL_TO_PASS 从测试输出里认出来就要覆盖四套格式：
# pytest、django 自己的 runner、sympy 的 `bin/test`、sphinx 的 tox。差一点，
# 分数就不能跟公开榜（mini-swe-agent / OpenHands / SWE-agent 都在这套上报分）比 ——
# **而"能跟人比"正是接这套 benchmark 的全部理由**，自己写等于把它弄没了。
#
# 官方 harness 走 `uv run --with swebench==<钉死的版本>` 的一次性环境，
# **不进 pyproject，连 optional extra 都不进**：它拖 datasets → pyarrow/pandas，
# 一个 `pip install feiyu-agent[swebench]` 就毁掉"只有三个依赖"这块招牌。
#
# 我们这边只负责产出官方形状的 preds：
#     {"instance_id": …, "model_name_or_path": …, "model_patch": …}
# 判完从 report.json 里读 `resolved`。**报告路径不同版本变过**（logs/run_evaluation/
# 和 logs/evaluation/ 之间），所以要 glob 着找；两处都没有就返回"判不了"，
# **绝不能猜成没解决** —— 那是在说 agent 做错了，而其实是我们的链路坏了。


def preds_line(instance_id: str, patch: str, who: str) -> str:
    """官方 preds.jsonl 的一行 三家（feiyu / dsh / mini）都出这个形状，喂同一个 grader """
    return json.dumps({"instance_id": instance_id, "model_name_or_path": who,
                       "model_patch": patch or ""}, ensure_ascii=False)


def patch_of(root: Path) -> str:
    """把工作区里的改动提成一个 patch

    `add -A -N` 是为了让**新建的文件**也进 diff，同时不真的 staged。
    `.gitignore` 顺带挡掉 `__pycache__` / `.pytest_cache` / `*.egg-info` ——
    mini-swe-agent 那边就是拿 `git add -A` 被这些垃圾咬过。
    """
    def git(*argv):
        return subprocess.run(["git", "-C", str(root), *argv],
                              capture_output=True, text=True, timeout=120)
    git("add", "-A", "-N")
    done = git("diff", "HEAD", "--binary")
    return done.stdout if done.returncode == 0 else ""


PROMPT = """下面是这个仓库里的一个 issue 把它修好

%s

要求：
- 只改产品代码，**不要改测试文件** —— 判分用的是仓库自己的测试
- 改完自己跑一遍相关的测试确认
- 不要改跟这个 issue 无关的东西
"""


def cases(path=None) -> list[Case]:
    try:
        from evals import swedocker
    except ImportError:
        raise SystemExit(
            "容器那一层（evals/swedocker.py）还没做 —— 它被 docker 拉不动挡住了：\n"
            "  /etc/systemd/system/docker.service.d/proxy.conf 把 daemon 指向\n"
            "  socks5h://127.0.0.1:10808，而那个代理没在跑（直连 registry 是通的）。\n"
            "  要么把代理起起来，要么：\n"
            "    sudo mv /etc/systemd/system/docker.service.d/proxy.conf{,.off}\n"
            "    sudo systemctl daemon-reload && sudo systemctl restart docker") from None
    rows = {r["instance_id"]: r for r in fetch()}
    out = []
    for iid in subset(path):
        row = rows.get(iid)
        if row is None:
            continue        # 子集里有、数据集里没有：多半是数据集换了版本，跳过别炸
        out.append(swedocker.make_case(row, PROMPT % row["problem_statement"]))
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="evals.swebench")
    ap.add_argument("--pick", type=int, default=0, help="生成这么大的固定子集")
    ap.add_argument("--refresh", action="store_true", help="重新拉数据集")
    ap.add_argument("--hard", action="store_true",
                    help="生成难题档（「1-4 小时」+「>4 小时」全部，共 45 道）")
    args = ap.parse_args()
    rows = fetch(force=args.refresh)
    print("数据集 %d 条，缓存在 %s" % (len(rows), CACHE))
    if args.hard:
        ids = sorted(r["instance_id"] for r in rows if r["difficulty"] in HARD_LEVELS)
        SUBSET_HARD.write_text(
            "# SWE-bench Verified 的**难题档**：难度为 %s 的**全部** %d 道\n"
            "# 生成：python -m evals.swebench --hard\n"
            "# 为什么全取不抽样：这一档总共就这么多，全取之后「这把尺子是哪几道」\n"
            "# 由数据集本身定死，没有抽样规则要记，也就没得漂。\n"
            "# **50 题那把尺子在 qwen3.8-flash 上是 45/46，已经饱和、量不出改进**，\n"
            "# 这一档是用来接替它的。\n%s\n"
            % (" / ".join(HARD_LEVELS), len(ids), "\n".join(ids)), encoding="utf-8")
        print("写了 %d 条到 %s" % (len(ids), SUBSET_HARD))
        import collections
        for k, v in collections.Counter(i.split("__")[0] for i in ids).most_common():
            print("  %-16s %d" % (k, v))
        return 0
    if args.pick:
        ids = pick(rows, args.pick)
        SUBSET.write_text(
            "# SWE-bench Verified 的固定子集，%d 题 —— 按 difficulty 分层等距取\n"
            "# 生成：python -m evals.swebench --pick %d\n"
            "# **改这个文件等于换了一把尺子**，前后两次的分数就不能比了\n%s\n"
            % (len(ids), args.pick, "\n".join(ids)), encoding="utf-8")
        print("写了 %d 条到 %s" % (len(ids), SUBSET))
        by = {}
        for i in ids:
            by[rows_by(rows, i)] = by.get(rows_by(rows, i), 0) + 1
        for level, n in sorted(by.items()):
            print("  %-18s %d" % (level, n))
    return 0


def rows_by(rows, iid) -> str:
    for r in rows:
        if r["instance_id"] == iid:
            return r["difficulty"] or "未标"
    return "未标"


if __name__ == "__main__":
    raise SystemExit(main())
