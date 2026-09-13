"""SWE-bench 的容器那一层：把仓库弄到宿主机上，把命令送进容器

## 为什么是这个形状（三个都验过，不是猜的）

**agent 跑在宿主机，不跑在容器里。** 实例镜像里的 python 常是 3.6~3.9（django、
sympy 的老版本都要），而 feiyu 要 3.10+；而且它的 `read_file` / `write_file` /
`search` 全是宿主机路径。把 feiyu 装进 4503 个镜像既慢又脏，沙箱、LSP、jobs
还要在容器里重配一遍。mini-swe-agent 也是这个架构，head-to-head 才算同构。

**仓库用 `docker cp` 弄出来，不 git clone。** 拿到的是精确的 base commit、
连同已经装好的依赖布局（`pip install -e .` 留下的 `.pth` / `egg-info` 都在），
而且不需要网络、不碰 GitHub 限流。

**两处挂同一个目录。** `/testbed` 是给镜像里那些指向它的 `.pth` / `egg-info` 用的；
再按**宿主机的绝对路径**挂一遍，这样 agent 在宿主机看到的路径在容器里是同一个路径 ——
它根本不用知道有容器这回事。

## 实测过的两条（原来只能靠猜）

- `docker cp 容器:路径 宿主目录` 拷出来的文件**归宿主用户**，不是 root。
- `docker run -u $(id -u):$(id -g)` 加 bind mount，容器里写出来的文件**也归宿主用户** ——
  所以临时目录删得掉、agent 也改得动。代价是容器里装不了新包（装不到
  `/opt/miniconda3` 去），但依赖本来就装好了，不需要。
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

from evals import swebench
from evals.suites import Case, Result

# 起容器、拷仓库这些都不该很久，久了就是出事了
DOCKER_TIMEOUT = 300
# 拉镜像可以很慢（1.1 GB 起），但不能没有上限
PULL_TIMEOUT = 1800


class DockerError(RuntimeError):
    pass


def _run(argv: list[str], timeout: int = DOCKER_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise DockerError("这台机器上没有 docker") from None
    except subprocess.TimeoutExpired:
        raise DockerError("docker %s 超过 %d 秒没结束" % (argv[1], timeout)) from None


def available() -> tuple[bool, str]:
    """docker 能用吗 返回 (能不能, 不能的话为什么)

    **要真问一次 daemon**，不能只看 `which docker`：客户端装着而 daemon 连不上
    （没起、没权限、代理配错）是最常见的情况，而那时候 `which` 是通的。
    """
    if not shutil.which("docker"):
        return False, "没装 docker"
    done = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
    if done.returncode != 0:
        return False, "docker daemon 连不上：%s" % (done.stderr or "").strip()[:200]
    return True, ""


def ensure_image(image: str) -> None:
    """本地没有就拉 拉不动要**说清是哪个镜像**，否则 50 题里挂了一个根本查不出是谁 """
    if _run(["docker", "image", "inspect", image], timeout=60).returncode == 0:
        return
    done = _run(["docker", "pull", image], timeout=PULL_TIMEOUT)
    if done.returncode != 0:
        raise DockerError("拉不下来 %s：%s" % (image, (done.stderr or "").strip()[:300]))


def _copy_out(image: str, root: Path) -> None:
    """把镜像里的 /testbed 弄到宿主机的工作区

    `docker cp 容器:/testbed/. 目标` —— 结尾那个 `/.` 是"把里面的东西拷出来"，
    不带的话会拷成 `目标/testbed`，多一层。
    """
    made = _run(["docker", "create", image, "sleep", "1"])
    if made.returncode != 0:
        raise DockerError("建不了临时容器：%s" % (made.stderr or "").strip()[:200])
    cid = made.stdout.strip()
    try:
        got = _run(["docker", "cp", "%s:/testbed/." % cid, str(root)])
        if got.returncode != 0:
            raise DockerError("拷不出仓库：%s" % (got.stderr or "").strip()[:200])
    finally:
        _run(["docker", "rm", "-f", cid], timeout=60)


def _scrub(root: Path) -> None:
    """**把答案从 .git 里清掉。**

    官方镜像是完整 clone 之后 `reset --hard base_commit` 做出来的 ——
    reflog 里还留着 reset 之前的那个尖端，顺着它能翻到真正的修复提交。
    不清的话，agent 一句 `git reflog` 就是作弊，而且它未必是故意的。
    远端也复查一遍（官方 setup 已经 remove 过 origin，但不能指望）。
    """
    logs = root / ".git" / "logs"
    if logs.exists():
        shutil.rmtree(logs, ignore_errors=True)
    _run(["git", "-C", str(root), "remote", "remove", "origin"], timeout=60)


def start(image: str, root: Path, box: Path, allow_net: bool = False) -> str:
    """起一个长驻容器，返回它的 id

    `--network none` 是默认：这些题不该联网，而断网也顺手挡掉"上网搜到这个 issue
    的真实补丁"这条路。少数 repo 的测试要装包，那种给 `--allow-net`。
    """
    argv = ["docker", "run", "-d", "--rm",
            "-u", "%d:%d" % (os.getuid(), os.getgid()),
            "-v", "%s:%s" % (root, root),
            "-v", "%s:/testbed" % root,
            # bash 靠 TMPDIR 下的临时文件跨调用带回 pwd 和 shell 状态（export /
            # source 进来的 venv / 函数），不挂进来这两样都静默失效
            "-v", "%s:%s" % (box, box),
            "-w", str(root)]
    if not allow_net:
        argv += ["--network", "none"]
    argv += [image, "sleep", "infinity"]
    done = _run(argv)
    if done.returncode != 0:
        raise DockerError("起不来容器：%s" % (done.stderr or "").strip()[:300])
    return done.stdout.strip()


def stop(cid: str) -> None:
    if cid:
        _run(["docker", "rm", "-f", cid], timeout=60)


# 送进容器执行的那层壳 `$1` 是 cwd，`$2` 是命令 —— 形状照 sandbox.wrap 的约定
#
# **`conda activate testbed` 这一句不能少。** 实例镜像里 `bash -lc` 进去的是
# conda 的 **base** 环境（`/opt/miniconda3/bin/python`），而这道题的依赖全装在
# 名叫 `testbed` 的 env 里。不激活的话 `import astropy` 是 ModuleNotFoundError、
# `pytest` 根本不存在 —— agent 会以为是自己把仓库改坏了，然后一路瞎修。
# 官方 eval_script 开头也是这一句。
WRAPPER = """#!/bin/sh
exec docker exec -i -w "$1" %s /bin/bash -lc '. /opt/miniconda3/bin/activate testbed >/dev/null 2>&1; '"$2"
"""


def write_wrapper(box: Path, cid: str) -> Path:
    """把执行器写成一个脚本，路径通过 FEIYU_EXEC_WRAPPER 交给 feiyu

    **写在 box 里不写在工作区里** —— 工作区里任何我们自己的文件都会进 patch，
    而 patch 是要交给官方 grader 判分的。
    """
    path = box / ("exec-%s.sh" % cid[:12])
    path.write_text(WRAPPER % cid, encoding="utf-8")
    path.chmod(0o700)
    return path


class Bench:
    """一道题的容器状态 setup 出它，check 和 teardown 都拿着它 """

    def __init__(self, row: dict, cid: str, box: Path, wrapper: Path):
        self.row = row
        self.cid = cid
        self.box = box
        self.wrapper = wrapper
        self.patch = ""


def make_case(row: dict, prompt: str) -> Case:
    image = swebench.image_for(row["instance_id"])

    def setup(root: Path) -> Bench:
        ok, why = available()
        if not ok:
            raise DockerError(why)
        ensure_image(image)
        _copy_out(image, root)
        _scrub(root)
        box = Path(os.environ.get("TMPDIR") or "/tmp")
        cid = start(image, root, box)
        wrapper = write_wrapper(box, cid)
        # **这一句才是把 agent 接上容器的地方。** feiyu 的 bash 走
        # `sandbox.wrap()`，它见到这个环境变量就整条交给这个脚本 ——
        # 文件工具照旧在宿主机上操作（工作区是 bind mount，容器里看到的是同一份）。
        os.environ["FEIYU_EXEC_WRAPPER"] = str(wrapper)
        return Bench(row, cid, box, wrapper)

    def check(root: Path, answer: str, bench: Bench) -> Result:
        from evals import swegrade
        bench.patch = swebench.patch_of(root)
        iid = row["instance_id"]
        base = {"instance_id": iid, "patch": bench.patch, "image": image,
                "patch_bytes": len(bench.patch)}
        if not bench.patch.strip():
            # 空 patch 不用去麻烦 harness：它一定判 unresolved，而那要几分钟
            return Result(ok=False, why="一个字都没改", detail={**base, "graded": True})
        # **判分一律交给官方 harness**（理由见 evals/swegrade.py 顶上）。
        # 它会自己另起容器跑整套测试，跟我们这个容器互不干扰。
        verdicts, why = swegrade.grade([(iid, bench.patch)], "feiyu", bench.box / "grade")
        if verdicts is None:
            # **判不了 ≠ 没解决。** 这里必须原样说出来，不能算它做错了 ——
            # 否则我们这边坏掉会显示成 agent 无能，而那正是要测的东西
            return Result(ok=False, why="判不了：" + why,
                          detail={**base, "graded": False, "why": why})
        ok = bool(verdicts.get(iid))
        return Result(ok=ok, why="" if ok else "没解决（官方判据）",
                      detail={**base, "graded": True, "resolved": ok})

    def teardown(bench) -> None:
        # **一定要把环境变量摘掉。** 它是进程级的，留着的话下一道题（乃至
        # 内置题库）的 bash 会被送进一个已经删掉的容器里，表现是命令全部失败
        os.environ.pop("FEIYU_EXEC_WRAPPER", None)
        if isinstance(bench, Bench):
            stop(bench.cid)
            try:
                bench.wrapper.unlink()
            except OSError:
                pass

    return Case(name=row["instance_id"], prompt=prompt, setup=setup,
                check=check, teardown=teardown,
                timeout=swebench.CASE_TIMEOUT,
                max_rounds=swebench.MAX_ROUNDS, budget=swebench.BUDGET)


def selftest(instance_id: str, keep: bool = False) -> int:
    """不带 agent 地把链路走一遍：拉镜像 → 拷仓库 → 起容器 → 在容器里跑一条命令

    **这一步不花一个 token**，但它证明的是「镜像名对、容器起得来、路径平行、
    命令送得进去」——而这四样坏掉的时候，表格上跟"agent 没做出来"长得一模一样。
    """
    import tempfile
    ok, why = available()
    if not ok:
        print("docker 不可用：%s" % why)
        return 2
    rows = {r["instance_id"]: r for r in swebench.fetch()}
    row = rows.get(instance_id)
    if row is None:
        print("数据集里没有 %s" % instance_id)
        return 2
    image = swebench.image_for(instance_id)
    print("镜像 %s" % image)
    began = time.monotonic()
    ensure_image(image)
    print("  镜像就绪 %.1fs" % (time.monotonic() - began))

    box = Path(tempfile.mkdtemp(prefix="feiyu-swe-box-"))
    root = Path(tempfile.mkdtemp(prefix="feiyu-swe-ws-"))
    cid = ""
    try:
        _copy_out(image, root)
        files = sum(1 for _ in root.rglob("*"))
        print("  仓库拷出来了：%d 个条目" % files)
        _scrub(root)
        print("  .git/logs 清掉了：%s" % (not (root / ".git" / "logs").exists()))
        cid = start(image, root, box)
        print("  容器 %s" % cid[:12])
        # 路径平行：容器里看到的绝对路径要跟宿主机一样
        probe = _run(["docker", "exec", "-w", str(root), cid, "/bin/bash", "-lc",
                      "pwd && git rev-parse HEAD && python -c 'import sys;print(sys.version.split()[0])'"])
        print("  容器里跑一条命令 → 退出码 %d" % probe.returncode)
        for line in (probe.stdout or "").strip().splitlines():
            print("    %s" % line)
        if probe.returncode != 0:
            print("    stderr: %s" % (probe.stderr or "").strip()[:300])
            return 1
        # **HEAD 不等于 base_commit，这是对的，别去 reset。**
        # 官方镜像在 base_commit 上面还压了一个自己的 "SWE-bench" 建置提交，
        # 而它只碰 `.github/` `.circleci/` `.gitattributes` 这类元数据（每个 0 行改动），
        # **源码树跟 base_commit 是一样的**。所以 `git diff HEAD` 出来的 patch
        # 拿到 base_commit 上照样能应用 —— 这正是官方 grader 期待的形状。
        # 要验的是「源码没差」，不是「HEAD 相等」。
        # **只看内容，不看权限位。** 那个建置提交实测是 1861 个 mode 644→755
        # 加 `pyproject.toml` 里 1 行（官方钉建置依赖）—— `--stat` 会把 1861 个
        # 纯 mode change 也列出来，看着像"源码全变了"，其实一行没动。
        # 用 `--numstat` 数增删行：mode change 那些是 0/0。
        diff = _run(["git", "-C", str(root), "diff", "--numstat",
                     row["base_commit"], "HEAD"])
        if diff.returncode != 0:
            print("  ！base_commit %s 不在历史里" % row["base_commit"][:12])
            return 1
        changed = []
        for line in (diff.stdout or "").splitlines():
            cols = line.split("\t")
            if len(cols) == 3 and (cols[0] not in ("0", "-") or cols[1] not in ("0", "-")):
                changed.append("%s (+%s/-%s)" % (cols[2], cols[0], cols[1]))
        source = [c for c in changed if c.endswith(".py") or ".py " in c]
        print("  源码树 vs base_commit：%d 个文件有真改动%s" % (
            len(changed), ("（%s）" % "、".join(changed[:3])) if changed else ""))
        if source:
            print("  ！有 .py 被建置提交改过，patch 的基准就不对了：%s" % source[:5])
            return 1
        # 工作区干净 = patch 是空的
        print("  空 patch：%d 字节（应该是 0）" % len(swebench.patch_of(root)))
        return 0
    finally:
        stop(cid)
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(box, ignore_errors=True)
        else:
            print("留着不删：%s" % root)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="evals.swedocker")
    ap.add_argument("--selftest", metavar="INSTANCE_ID", default="",
                    help="不带 agent 走一遍链路（不花 token）")
    ap.add_argument("--keep", action="store_true", help="跑完留着工作区别删")
    args = ap.parse_args()
    if args.selftest:
        return selftest(args.selftest, keep=args.keep)
    ok, why = available()
    print("docker：%s" % ("可用" if ok else why))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
