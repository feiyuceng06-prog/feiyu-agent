"""把 feiyu 接进 Harbor（Terminal-Bench 2.x 的跑分框架）

## 为什么是「装进容器」，而不是「把命令送进容器」

SWE-bench 那条路（`swedocker.py`）是 agent 跑在宿主机、**只把 bash 送进容器**
（`FEIYU_EXEC_WRAPPER`）。那条路成立的前提是 `swedocker.start()` 把工作区
bind-mount 进去，宿主机和容器**看的是同一份文件**，所以 `read_file` /
`write_file` / `search` 留在宿主机上照样是对的。

**Harbor 不挂任何 volume**（compose 模板里一个 `volumes:` 都没有），容器文件系统
跟宿主机完全隔开。照搬那条路的话，文件类工具在宿主机上对着一个**空目录**操作，
而 bash 在容器里改真文件——两边不是一个世界，错得还很安静。

所以这里反过来：把 feiyu 装进容器，一切都在容器内本地发生。这也正是 Harbor
自己的模型——`claude-code` / `codex` 那些官方适配器都是在 `setup()` 里装进去的。

## 代价，说在前面

1. **要网。** 容器里得 `pip install`。Harbor 的默认策略是 `NetworkMode.PUBLIC`，
   但单个任务可以声明 `no_network`——那种任务这个适配器跑不了，`setup()` 会
   当场说清楚，而不是留一堆看不懂的 ImportError。
2. **API key 要进容器。** 写成 0600，但容器里的 bash 就是模型自己在用，
   它读得到。**拿一把额度有限的 key 来跑**，别用主力 key。
3. **容器里没有 bwrap**，feiyu 自己的沙箱在里面是关着的。这没关系——
   容器本身就是沙箱，而且跑完就删。
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import NetworkMode

# 装在容器里的位置 放 /opt 而不是工作目录：工作目录是任务自己的地盘，
# 往里塞东西会被任务的测试看见，也可能被任务自己的构建清掉
REMOTE_ROOT = "/opt/feiyu"
REMOTE_CFG = "/opt/feiyu-cfg"          # XDG_CONFIG_HOME，models.json 在这下面
REMOTE_STATE = "/opt/feiyu-state"      # XDG_STATE_HOME，trace 日志在这下面
MODEL_NAME = "bench"                   # 容器里那条配置叫什么，`-m` 用它
REMOTE_OUT = "/tmp/feiyu-out.txt"      # feiyu 的整份输出（stdout+stderr）先落在容器里

SETUP_TIMEOUT = 900
# 我们这边的 exec 不再设墙钟闸（0 = 不设，`DockerEnvironment` 里是 `if timeout_sec:`）。
#
# **两道墙钟闸，而且互相不知道对方的值 —— 这就是坑。** 原来这里写死 1800 秒，
# 而 Harbor 自己那道闸是「题里声明的 agent.timeout_sec × --agent-timeout-multiplier」。
# 24 道基线那轮把 multiplier 提到 2.0，声明 1800s 的题于是有 3600s 的额度，
# **却在 1800 秒上被我们自己掐掉**，而且掐出来的是 `RuntimeError: Command timed
# out after 1800 seconds` —— 不是 `AgentTimeoutError`，于是被归进「判不了」、
# **退出分母**。48 次 trial 里 7 次这样，解决率是在一个少了 7 条的分母上算的。
#
# 适配器看不见 Harbor 解析出来的那个值（`BaseAgent` 拿不到），所以也没法"设得比它松一点"——
# 那就干脆只留一道闸：Harbor 那道。它到点抛 `AgentTimeoutError`，分类是对的。
# 真想要一道更紧的网就显式给 `FEIYU_BENCH_TIMEOUT`，**但记得它会先响**。
DEFAULT_TIMEOUT = 0
DEFAULT_MAX_ROUNDS = 60
# 每道题最多花这么多钱（元）feiyu 自己的 `--budget` 闸
#
# **比墙钟超时管用。** 十道冒烟里有五道撞了 900 秒超时，钱照烧但一分记录没有
# （超时是被 SIGKILL，trace 的 run_end 根本没写）。按钱收口的话，跑飞的那道
# 自己会停下来、而且会把停下来这件事记进历史。
DEFAULT_BUDGET = 0.5
# 最低 Python 跟 pyproject 一致 比这个老的镜像直接说不行，别让它跑一半炸
MIN_PY = (3, 10)
# 容器里可能是哪家的包管理器，以及同一样东西在各家叫什么名字
PKG_MANAGERS = (
    ("apt-get", "apt-get update -qq && apt-get install -y -qq %s"),
    ("apk", "apk add --no-cache %s"),
    ("dnf", "dnf install -y -q %s"),
    ("yum", "yum install -y -q %s"),
)
PY_PKGS = {"apt-get": "python3 python3-pip", "apk": "python3 py3-pip",
           "dnf": "python3 python3-pip", "yum": "python3 python3-pip"}
PIP_PKGS = {"apt-get": "python3-pip", "apk": "py3-pip",
            "dnf": "python3-pip", "yum": "python3-pip"}


class NotUsable(RuntimeError):
    """这道题的环境跑不了 feiyu 要跟「agent 没做出来」区分开 """


def _feiyu_src() -> Path:
    return Path(__file__).resolve().parent.parent / "feiyu"


class FeiyuAgent(BaseAgent):
    """在容器里跑 feiyu

    `harbor run --agent-import-path evals.tbench:FeiyuAgent` 就能用它。
    """

    @staticmethod
    def name() -> str:
        return "feiyu"

    def version(self) -> str | None:
        """**从源码里抠，不 import feiyu。** 这个适配器跑在 Harbor 自己的 venv 里，
        那里不一定有 feiyu 的依赖（openai / prompt_toolkit）—— import 一下就炸，
        而炸的地方离原因很远
        """
        import re
        try:
            text = (_feiyu_src() / "__init__.py").read_text(encoding="utf-8")
        except OSError:
            return None
        got = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return got.group(1) if got else None

    # ---- 装进去 ----

    async def _sh(self, environment, command: str, *, timeout: int = 120,
                  env: dict | None = None):
        return await environment.exec(command, timeout_sec=timeout, env=env)

    async def setup(self, environment) -> None:
        # **走公开的 `network_policy`。** `_network_disabled` 那几个是带下划线的私有
        # 属性，第一版我直接猜了个 `no_network`，结果 AttributeError ——
        # 名字要么查出来，要么用公开的那条路，不能猜
        if environment.network_policy.network_mode == NetworkMode.NO_NETWORK:
            raise NotUsable(
                "这道题的容器断网，而 feiyu 要在里面 pip install 才能跑 "
                "（装不进去跟「没解决」是两回事，别把它算进解决率的分母）")

        got = await self._probe_python(environment)
        if got.return_code != 0:
            # 镜像里压根没有解释器 —— feiyu 是个 Python 程序，装运行时跟
            # claude-code 要 node 是一回事，Harbor 的官方适配器也都在 setup() 里装
            if not await self._install_python(environment):
                raise NotUsable("容器里没有 python3，也装不上：%s"
                                % (got.stderr or "").strip()[:200])
            got = await self._probe_python(environment)
            if got.return_code != 0:
                raise NotUsable("装完 python3 还是跑不起来")
        try:
            ver = tuple(int(x) for x in (got.stdout or "").strip().split("."))
        except ValueError:
            raise NotUsable("认不出容器里的 python 版本：%r" % (got.stdout or "")[:80])
        if ver < MIN_PY:
            raise NotUsable("容器里的 python 是 %d.%d，feiyu 要 %d.%d 以上"
                            % (*ver, *MIN_PY))

        # **版本够了不等于装得上**：有 python3 没 pip 的镜像占了 24 道里的两道
        if not await self._ensure_pip(environment):
            raise NotUsable("容器里的 python3 没有 pip，ensurepip 和包管理器都没装上")

        await environment.upload_dir(_feiyu_src(), REMOTE_ROOT + "/feiyu")

        got = await self._pip_install(environment, "openai prompt_toolkit")
        if got.return_code != 0:
            raise NotUsable("容器里装依赖失败：%s"
                            % ((got.stderr or got.stdout or "").strip()[-400:]))

        await self._write_model_config(environment)

    async def _probe_python(self, environment):
        return await self._sh(
            environment,
            "python3 -c 'import sys;print(\"%d.%d\"%sys.version_info[:2])'")

    async def _system_install(self, environment, names: dict) -> bool:
        """用容器自己的包管理器装东西 `names` 是 {包管理器: 该叫什么包名} """
        for probe, template in PKG_MANAGERS:
            if (await self._sh(environment, "command -v %s" % probe)).return_code != 0:
                continue
            got = await self._sh(environment, template % names[probe],
                                 timeout=SETUP_TIMEOUT)
            if got.return_code == 0:
                return True
            self.logger.warning("用 %s 装 %s 没成：%s", probe, names[probe],
                                (got.stderr or "")[-200:])
        return False

    async def _install_python(self, environment) -> bool:
        """镜像里没有 python3 就用它自己的包管理器装一个

        十道冒烟题里有两道（`adaptive-rejection-sampler` / `sqlite-with-gcov`）
        是这种，占两成——不处理的话两成的题根本够不着。
        """
        return await self._system_install(environment, PY_PKGS)

    async def _ensure_pip(self, environment) -> bool:
        """有 python3 不等于有 pip

        **这条是 24 道基线预检抓出来的**：`path-tracing` / `build-pmars` 两道镜像里
        `python3` 好好的，`python3 -m pip` 却是 `No module named pip` —— 而原来只在
        「压根没有 python3」时才去装东西，于是这两道直接 `NotUsable`。
        24 道里占两道，**而 NotUsable 是不进分母的**：不修的话它们不声不响地
        从尺子上消失，剩下 22 道还显得一切正常。

        先试 `ensurepip`（标准库自带，**不用网**，多数镜像只是没 bootstrap 过），
        再退回包管理器。两条都走完还是没有就返回 False，让 `setup()` 说清楚。
        """
        if (await self._sh(environment, "python3 -m pip --version")).return_code == 0:
            return True
        await self._sh(environment, "python3 -m ensurepip --default-pip",
                       timeout=SETUP_TIMEOUT)
        if (await self._sh(environment, "python3 -m pip --version")).return_code == 0:
            return True
        await self._system_install(environment, PIP_PKGS)
        return (await self._sh(environment, "python3 -m pip --version")).return_code == 0

    async def _pip_install(self, environment, packages: str):
        """**PEP 668 那道闸要绕。** Debian 12+ 的镜像上 `pip install` 直接拒绝
        （externally-managed-environment），十道里有两道栽在这儿。
        `--break-system-packages` 是官方给的出口，但老 pip 不认这个参数，
        所以认不认都试一遍。容器跑完就删，弄坏系统 python 也无所谓。

        **第二档是 `--ignore-installed`，24 道基线预检抓出来的。** `build-pmars`
        那个镜像里 `typing_extensions` 是 apt 装的、没有 RECORD 文件，于是 pip
        卸不掉它，`openai` 的依赖升级就整条失败（`The package's contents are
        unknown`）。`--ignore-installed` 是直接盖上去不卸旧的 —— 在一个跑完就删的
        容器里这没有代价。**报错还特别会骗人**：第三档（不带 break）接着撞 PEP 668，
        于是最后带出来的是 "externally-managed-environment"，指着一个根本不是
        病因的地方。
        """
        base = "python3 -m pip install --quiet --no-input "
        for extra in ("--break-system-packages ",
                      "--break-system-packages --ignore-installed ",
                      ""):
            got = await self._sh(environment, base + extra + packages,
                                 timeout=SETUP_TIMEOUT)
            if got.return_code == 0:
                return got
        return got

    async def _write_model_config(self, environment) -> None:
        """把一条模型配置写进容器

        **key 会落到容器里。** 没有别的办法——feiyu 在容器里跑就得能连接口。
        写成 0600 只挡得住别的用户，挡不住容器里的 bash（那就是模型自己）。
        所以文档里写清楚：**拿额度有限的 key 跑**。
        """
        entry = self._bench_entry()
        payload = json.dumps({MODEL_NAME: entry}, ensure_ascii=False)
        await self._sh(environment, "mkdir -p %s/feiyu %s && chmod 700 %s"
                       % (REMOTE_CFG, REMOTE_STATE, REMOTE_CFG))
        # **heredoc，不是命令行参数。** `exec()` 没有 stdin 这个口子（签名只有
        # command/cwd/env/timeout/user），而把 key 拼进命令行会进 ps 和 Harbor 的日志。
        # 引号 `FEIYU_EOF` 关掉变量展开，json 里的 $ 和反引号才不会被 shell 咬。
        got = await self._sh(
            environment,
            "umask 077 && cat > %s/feiyu/models.json <<'FEIYU_EOF'\n%s\nFEIYU_EOF"
            % (REMOTE_CFG, payload))
        if got.return_code != 0:
            raise NotUsable("写不进模型配置：%s" % (got.stderr or "").strip()[:200])

    def _bench_entry(self) -> dict:
        """从宿主机的 models.json 里挑一条，原样搬进容器

        用 `--model` 指的那条；没指就看 `state.json` 里上次用的那条。
        **直接读文件，不 import feiyu.keys** —— 理由同 `version()`。
        这两个文件的格式就是 `keys.py` 写出来的那个，改了那边这里要跟着改。
        """
        import os
        cfg = Path(os.environ.get("XDG_CONFIG_HOME")
                   or os.path.expanduser("~/.config")) / "feiyu"
        try:
            entries = json.loads((cfg / "models.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NotUsable("读不到宿主机的 models.json：%s" % exc)
        if not isinstance(entries, dict) or not entries:
            raise NotUsable("宿主机的 models.json 是空的或者格式不对")
        want = self.model_name or os.environ.get("FEIYU_BENCH_MODEL")
        if not want:
            try:
                want = json.loads((cfg / "state.json").read_text(encoding="utf-8")).get("last")
            except (OSError, ValueError, AttributeError):
                want = None
        entry = entries.get(want) if want else None
        if not isinstance(entry, dict):
            raise NotUsable("models.json 里没有 %r 这条（有的是：%s）"
                            % (want, "、".join(sorted(entries))[:200]))
        return dict(entry)

    # ---- 跑 ----

    async def run(self, instruction: str, environment, context: AgentContext) -> None:
        import os
        rounds = int(os.environ.get("FEIYU_BENCH_ROUNDS") or DEFAULT_MAX_ROUNDS)
        timeout = int(os.environ.get("FEIYU_BENCH_TIMEOUT") or DEFAULT_TIMEOUT)
        budget = float(os.environ.get("FEIYU_BENCH_BUDGET") or DEFAULT_BUDGET)

        # 题面走文件不走命令行：里头有换行、引号、反引号，拼进命令行迟早出事
        await self._sh(environment,
                       "cat > /tmp/feiyu-task.txt <<'FEIYU_EOF'\n%s\nFEIYU_EOF"
                       % instruction)

        env = {
            "PYTHONPATH": REMOTE_ROOT,
            "XDG_CONFIG_HOME": REMOTE_CFG,
            "XDG_STATE_HOME": REMOTE_STATE,
            "FEIYU_SANDBOX": "off",      # 容器里没有 bwrap，容器本身就是沙箱
            "NO_COLOR": "1",
        }
        # 题面是**位置参数**，没有 -p 这个开关（`feiyu [选项] [prompt ...]`）
        #
        # **输出重定向到容器里的文件，不靠 `got.stdout`。** 超时那条路上 `exec` 直接抛，
        # 那个 ExecResult 根本没回来过 —— 于是**最值得看的那几次跑一个字都没留下**
        # （`adaptive-rejection-sampler` 撞超时那遍，18 轮、1379 秒，`agent/` 是空目录，
        # 时间花在哪无从查起）。写进文件再从 `finally` 里捞，超时照样有。
        #
        # **`-u` 不能省**：输出到文件是块缓冲的，进程被杀时攒着的那一段会跟着没掉，
        # 而那一段恰好是最后在干什么 —— 正是要查的东西。
        cmd = ("python3 -u -m feiyu --mode auto -m %s --max-rounds %d --budget %s "
               "-- \"$(cat /tmp/feiyu-task.txt)\" > %s 2>&1"
               % (shlex.quote(MODEL_NAME), rounds, shlex.quote("%g" % budget), REMOTE_OUT))
        # **收尾一律在 finally 里。** 超时那条路上 `exec` 会抛，原来写在后面的
        # `_fill_usage` 根本轮不到 —— 十道冒烟里有四道就是这么丢的记录：
        # 钱在账面上，feiyu 这边一分没记，于是「记的」和「扣的」覆盖的不是同一批题，
        # 对账怎么算都对不上（这个坑连着踩了三次才找对地方）。
        # 捞日志是 0.72.0 补的**同一个形状的第二处**：当时只把取用量挪了进来。
        try:
            # `timeout or None` —— 0 就是不设，让 Harbor 那道闸做唯一的墙钟闸
            got = await environment.exec(cmd, timeout_sec=timeout or None, env=env)
            self.logger.info("feiyu 退出码 %s", got.return_code)
        finally:
            for what, take in (("收日志", self._collect), ("取用量", self._fill_usage)):
                try:
                    await take(environment, context)
                except Exception as exc:  # 收尾失败不该盖掉真正的超时异常
                    self.logger.warning("%s失败：%s", what, exc)

    async def _collect(self, environment, context=None) -> None:
        """把容器里的输出和 trace 捞回宿主机 —— **超时那次也要捞得到**

        trace 里每条 `turn` 都带 `ms`（这一轮花了多久）和 `calls`（调了哪些工具），
        那正是"18 轮 1379 秒到底耗在哪"唯一的答案。它一直在容器里写着，
        原来跑完就跟容器一起删了。

        容器这会儿还活着：`_fill_usage` 在同一个 `finally` 里也是这么取数的，
        超时的那几次它确实取到了（有 `feiyu_rounds` / `feiyu_cost_cny` 为证）。
        """
        for name, command in (
                ("feiyu-stdout.txt", "cat %s 2>/dev/null" % REMOTE_OUT),
                ("feiyu-trace.jsonl",
                 "cat %s/feiyu/logs/*.jsonl 2>/dev/null" % REMOTE_STATE)):
            got = await self._sh(environment, command)
            if got.stdout:
                (self.logs_dir / name).write_text(got.stdout, encoding="utf-8")

    async def _fill_usage(self, environment, context=None) -> None:
        """把 token 和花费填进 context 取不到就留空，别编

        `cost` 每行都是**累计值**，所以要的是最后一条，不是把所有行加起来
        （自己在 0.69.1 的体检里刚踩过这个）。

        **不能只认 `run_end`。** 被 SIGKILL 的那次根本写不到 `run_end`，
        但它每一轮都写了 `turn`、每条都带累计 `cost` —— 退回去拿最后一条 `turn`，
        跑飞的题照样有账可查。

        **但 `tokens` 这个字段名在两种行里是两个量，别跟着一起退回去。**
        0.70.1 写过一句"trace 每行都是累计值"，那话对 `cost` 成立、对 `tokens` 不成立：

            run_end 的 tokens = `agent.tokens`   —— 累计计费量
            turn    的 tokens = `self.context`   —— 当时的上下文大小

        混在同一列里的后果是一张看着合理、其实没有意义的表：0.72.0 的基线上算出
        「解出来 292k token 花 ¥0.13，没解出来 68k 却花 ¥0.41」—— 后一个 68k 根本
        不是用量，是上下文。所以退回 `turn` 时**不填 `feiyu_total_tokens`**，
        另记一个 `feiyu_context_tokens`，让它没法被当成用量误用。
        """
        got = await self._sh(environment,
                             "cat %s/feiyu/logs/*.jsonl 2>/dev/null | tail -800" % REMOTE_STATE)
        last = partial = stop = rounds = None
        for line in (got.stdout or "").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "run_end":
                last = row
            elif row.get("kind") == "turn" and row.get("cost") is not None:
                partial = row
                rounds = row.get("round")
            # **为什么停下来，只有它自己知道。** 没有这两行的话，「预算到顶」
            # 和「真的做不出来」在结果里长得一模一样，只能靠人翻 stdout 分 ——
            # 0.70.1 那十道就是这么手工数的
            elif row.get("kind") == "budget_stop":
                stop = "budget"
            elif row.get("kind") == "round_limit":
                stop = "rounds"
        last = last or partial
        if not last:
            return
        # **token 只往 metadata 放。** feiyu 记的是「累计总量」，而 AgentContext
        # 那几个字段分 input / cache / output —— 把总量填进 n_input_tokens 是
        # 报一个看着对其实错的数，比留空更糟
        # 字段名照 `__main__._finish` 里 `tracer.write("run_end", ...)` 写的那几个抄
        # —— `round` 不在里面，第一版我又猜了一个，回来是 null
        done = last.get("kind") != "turn"
        context.metadata = {"feiyu_version": self.version(),
                            # 只有 run_end 那条的 tokens 才是用量（见 docstring）
                            "feiyu_total_tokens": last.get("tokens") if done else None,
                            "feiyu_context_tokens": None if done else last.get("tokens"),
                            "feiyu_cost_cny": last.get("cost"),
                            "feiyu_exit_code": last.get("code"),
                            "feiyu_verified": last.get("verified"),
                            # 为什么停：budget / rounds / None（自己收敛的）。
                            # `tbench_report.py` 靠它把"撞上限"跟"没解决"分开
                            "feiyu_stop": stop,
                            "feiyu_rounds": rounds,
                            "feiyu_model": self.model_name or None,
                            # run_end 没写（被杀了）就是拿 turn 兜的，标出来
                            "feiyu_partial": not done}


# ---- 怎么跑 ----
#
# Harbor 不进 pyproject（跟 swebench 5.0.2 一个待遇），用一次性 venv：
#
#     uv venv --python 3.12 /tmp/hb && uv pip install --python /tmp/hb/bin/python harbor
#
# 先验判分链路（**不花钱**，两条都要跑）：
#
#     harbor run -d terminal-bench@2.0 -a oracle -i <题名>   # 参考答案 → 要 1.0
#     harbor run -d terminal-bench@2.0 -a nop    -i <题名>   # 什么都不做 → 要 0.0
#
# 再跑 feiyu。**基线一律跑定死的那 24 道，参数别手敲** —— 敲错一个就不是同一把尺子了，
# `tbench_pick.py --cmd` 会把带全部 `-i` 的整条命令打出来：
#
#     python -m evals.tbench_pick --cmd      # 粘它
#
# 单独试一道照旧：
#
#     PYTHONPATH=<仓库根> FEIYU_BENCH_MODEL=qwen3.8-flash FEIYU_BENCH_ROUNDS=40 \
#     harbor run -d terminal-bench@2.0 -a evals.tbench:FeiyuAgent -i <题名>
#
# 跑完看结果（**别手工数**，0.70.1 那十道就是手工数的）：
#
#     python -m evals.tbench_report <结果目录> --out 这次.jsonl
#     python -m evals.report --ab 上次.jsonl 这次.jsonl
#
# 结果在 `<job>/<题名>__<id>/result.json`：`verifier_result.rewards.reward` 是分数，
# `agent_result.metadata` 是这边回填的 token / 花费。**不是 `agent_context`**。
#
# 机器上要有 `docker compose`（Arch 上是 `docker-compose` 包，Docker 本体不带它）。
#
# **`FEIYU_BENCH_TIMEOUT` 管不住 Harbor 自己那道闸。** Harbor 对 agent 另有一个
# 执行超时（题里带的，默认 900 秒），到点直接 `AgentTimeoutError`，
# 跟这边 `exec` 的 timeout 是两回事。十道冒烟里有四道栽在它上面，
# 要放宽得在命令行上给：
#
#     harbor run ... --agent-timeout-multiplier 2.0
#
# **超时的题算「撞上限」，不算「没解决」**（跟 `evals/report.py` 里那套分类一个道理）
# —— 量出来的是我们给的上限，不是 agent 的能力。
