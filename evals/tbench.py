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

SETUP_TIMEOUT = 900
DEFAULT_TIMEOUT = 1800
DEFAULT_MAX_ROUNDS = 60
# 最低 Python 跟 pyproject 一致 比这个老的镜像直接说不行，别让它跑一半炸
MIN_PY = (3, 10)


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

        got = await self._sh(environment, "python3 -c "
                             "'import sys;print(\"%d.%d\"%sys.version_info[:2])'")
        if got.return_code != 0:
            raise NotUsable("容器里没有 python3，feiyu 装不进去：%s"
                            % (got.stderr or "").strip()[:200])
        try:
            ver = tuple(int(x) for x in (got.stdout or "").strip().split("."))
        except ValueError:
            raise NotUsable("认不出容器里的 python 版本：%r" % (got.stdout or "")[:80])
        if ver < MIN_PY:
            raise NotUsable("容器里的 python 是 %d.%d，feiyu 要 %d.%d 以上"
                            % (*ver, *MIN_PY))

        await environment.upload_dir(_feiyu_src(), REMOTE_ROOT + "/feiyu")

        # **要 openai 和 prompt_toolkit。** 这两个是硬的：providers 里
        # `from openai import OpenAI`，ui 里 `get_cwidth` 没有兜底
        got = await self._sh(environment,
                             "python3 -m pip install --quiet --no-input "
                             "openai prompt_toolkit", timeout=SETUP_TIMEOUT)
        if got.return_code != 0:
            raise NotUsable("容器里装依赖失败：%s"
                            % ((got.stderr or got.stdout or "").strip()[-400:]))

        await self._write_model_config(environment)

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
        cmd = ("python3 -m feiyu --mode auto -m %s --max-rounds %d -- \"$(cat /tmp/feiyu-task.txt)\""
               % (shlex.quote(MODEL_NAME), rounds))
        got = await environment.exec(cmd, timeout_sec=timeout, env=env)
        self.logger.info("feiyu 退出码 %s", got.return_code)
        if got.stdout:
            (self.logs_dir / "feiyu-stdout.txt").write_text(got.stdout, encoding="utf-8")
        if got.stderr:
            (self.logs_dir / "feiyu-stderr.txt").write_text(got.stderr, encoding="utf-8")

        await self._fill_usage(environment, context)

    async def _fill_usage(self, environment, context: AgentContext) -> None:
        """把 token 和花费填进 context 取不到就留空，别编

        feiyu 的 trace 每行都是**累计值**，所以要的是最后一条 `run_end`，
        不是把所有行加起来（自己在 0.69.1 的体检里刚踩过这个）。
        """
        got = await self._sh(environment,
                             "cat %s/feiyu/logs/*.jsonl 2>/dev/null | tail -400" % REMOTE_STATE)
        last = None
        for line in (got.stdout or "").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "run_end":
                last = row
        if not last:
            return
        # **token 只往 metadata 放。** feiyu 记的是「累计总量」，而 AgentContext
        # 那几个字段分 input / cache / output —— 把总量填进 n_input_tokens 是
        # 报一个看着对其实错的数，比留空更糟
        # 字段名照 `__main__._finish` 里 `tracer.write("run_end", ...)` 写的那几个抄
        # —— `round` 不在里面，第一版我又猜了一个，回来是 null
        context.metadata = {"feiyu_version": self.version(),
                            "feiyu_total_tokens": last.get("tokens"),
                            "feiyu_cost_cny": last.get("cost"),
                            "feiyu_exit_code": last.get("code"),
                            "feiyu_verified": last.get("verified")}


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
# 再跑 feiyu：
#
#     PYTHONPATH=<仓库根> FEIYU_BENCH_MODEL=qwen3.8-flash FEIYU_BENCH_ROUNDS=40 \
#     harbor run -d terminal-bench@2.0 -a evals.tbench:FeiyuAgent -i <题名>
#
# 结果在 `<job>/<题名>__<id>/result.json`：`verifier_result.rewards.reward` 是分数，
# `agent_result.metadata` 是这边回填的 token / 花费。**不是 `agent_context`**。
#
# 机器上要有 `docker compose`（Arch 上是 `docker-compose` 包，Docker 本体不带它）。
