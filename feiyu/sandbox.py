"""bash 沙箱：把 bash 工具的写权限关进工作区

一直以来 `_resolve` 只管得住 read_file / write_file / edit_file，bash 想读写哪儿
就读写哪儿——权限规则和 `DENY_PATHS` 只是降低概率，不是隔离 这个模块补上隔离

用 bubblewrap（`bwrap`），无特权 user namespace，不需要 root，也**不引 Python 依赖** 
装不上或者内核不给开 user namespace 的机器上自动降级成不沙箱，并且明说 

## 三档

- `off`        —— 不沙箱，跟 0.18.0 以前一样
- `workspace`  —— 整个文件系统只读，**只有工作区和 /tmp 可写**；密钥目录挖空 联网照旧
- `strict`     —— 同上，另外**断网**

## 挡得住什么、挡不住什么

**写：真隔离** 工作区外一律 `Read-only file system`，靠的是内核的 mount namespace，
不是字符串匹配，绕不过去 

**读：只是挖空了几个目录** 整个文件系统仍然可读，只是 `~/.ssh` 这类被 tmpfs 盖成空的 
挖空清单是白名单式思维的反面，跟 `DENY_PATHS` 一样是权宜之计 —— 别把它说成
「读也隔离了」 真要读隔离得改成只挂载允许的目录，那会让一大半构建工具跑不起来 

钩子和 `verify:` 的命令**不进沙箱**：那是用户自己写在配置里的，不是模型产出的 
"""

import json
import os
import shutil
import subprocess

LEVELS = ("off", "workspace", "strict")
DEFAULT = "workspace"

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
STORE = os.path.join(_DIR, "sandbox.json")

# 盖成空 tmpfs 的目录 前四个是别人的密钥，后两个是 feiyu 自己的——
# 模型配置里有 API key，state 里有完整对话原文、日志和长期记忆
SHADOW_DIRS = (
    "~/.ssh", "~/.gnupg", "~/.aws", "~/.docker",
    "~/.config/feiyu", "~/.local/state/feiyu",
)
# 盖成 /dev/null 的单个文件（tmpfs 只能盖目录）
SHADOW_FILES = ("~/.netrc", "~/.pgpass", "~/.git-credentials")

_probe: bool | None = None


def available() -> bool:
    """bwrap 在不在，以及**真的跑得起来**

    光看文件在不在不够：有些发行版把无特权 user namespace 关了，
    bwrap 装着但一跑就失败 探一次，结果记下来
    """
    global _probe
    if _probe is None:
        _probe = _try_probe()
    return _probe


def _try_probe() -> bool:
    if not shutil.which("bwrap"):
        return False
    try:
        done = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--", "true"],
            capture_output=True, timeout=10,
        )
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def level() -> str:
    """当前档位 环境变量优先于配置文件，方便临时关掉 """
    from_env = os.environ.get("FEIYU_SANDBOX", "").strip().lower()
    if from_env in LEVELS:
        return from_env
    try:
        with open(STORE, encoding="utf-8") as f:
            saved = json.load(f).get("level")
    except (OSError, ValueError):
        saved = None
    if saved in LEVELS:
        return saved
    return DEFAULT if available() else "off"


def set_level(name: str) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        fd = os.open(STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"level": name}, f)
    except OSError:
        pass


def effective() -> tuple[str, str]:
    """(实际生效的档位, 为什么) 想开但开不了的时候要说清楚，不能默默不沙箱 """
    want = level()
    if want == "off":
        return "off", "已关闭"
    if not available():
        return "off", ("装了 bubblewrap 才能沙箱（pacman -S bubblewrap）"
                       if not shutil.which("bwrap")
                       else "bwrap 跑不起来，多半是内核没开无特权 user namespace")
    return want, ""


def wrap(command: str, *, workspace, cwd, writable=()) -> list[str] | None:
    """把一条 shell 命令包进沙箱 返回 argv；不该沙箱时返回 None

    writable 是额外要放行写入的路径（bash 工具那个记 pwd 的临时文件要靠它，
    否则它落在沙箱内的 tmpfs 上，外面读不到，cd 就跨不了调用）
    """
    mode, _ = effective()
    if mode == "off":
        return None

    argv = [
        "bwrap",
        "--ro-bind", "/", "/",          # 整个文件系统只读打底
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",              # 沙箱里的 /tmp 是自己的，出去就没了
        "--bind", str(workspace), str(workspace),      # 唯一敞开的写入口
        "--die-with-parent",
        "--unshare-pid",
    ]
    for path in SHADOW_DIRS:
        real = os.path.expanduser(path)
        if os.path.isdir(real):
            argv += ["--tmpfs", real]
    for path in SHADOW_FILES:
        real = os.path.expanduser(path)
        if os.path.exists(real):
            argv += ["--ro-bind", "/dev/null", real]
    for path in writable:
        argv += ["--bind", str(path), str(path)]
    if mode == "strict":
        argv += ["--unshare-net"]
    argv += ["--chdir", str(cwd), "--", "/bin/sh", "-c", command]
    return argv
