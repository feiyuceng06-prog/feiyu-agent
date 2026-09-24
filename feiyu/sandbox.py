"""bash 沙箱：把 bash 工具的写权限关进工作区

一直以来 `_resolve` 只管得住 read_file / write_file / edit_file，bash 想读写哪儿
就读写哪儿——权限规则和 `DENY_PATHS` 只是降低概率，不是隔离 这个模块补上隔离

用 bubblewrap（`bwrap`），无特权 user namespace，不需要 root，也**不引 Python 依赖** 
装不上或者内核不给开 user namespace 的机器上自动降级成不沙箱，并且明说 

## 三档

- `off`        —— 不沙箱，跟 0.18.0 以前一样
- `workspace`  —— 整个文件系统只读，**只有工作区和 /tmp 可写**；密钥目录挖空 联网照旧
- `strict`     —— 同上，另外**断网**

读过外部内容之后的 bash（`offline=True`）是逐条的 strict：断网，别的跟 workspace 一样

## 挡得住什么、挡不住什么

**写：真隔离** 工作区外一律 `Read-only file system`，靠的是内核的 mount namespace，
不是字符串匹配，绕不过去 

**读：只是挖空了几个目录** 整个文件系统仍然可读，只是 `~/.ssh` 这类被 tmpfs 盖成空的 
挖空清单是白名单式思维的反面，跟 `DENY_PATHS` 一样是权宜之计 —— 别把它说成
「读也隔离了」 真要读隔离得改成只挂载允许的目录，那会让一大半构建工具跑不起来 

**能指挥宿主机干活的 socket 要挡在外面**（0.79.0 才补上，0.19.0 起一直漏着）：
docker 组里的用户在沙箱里照样 `docker ps`，于是 `docker run -v /:/host` 一条命令
就能往任何地方写 —— 上面那句「写：真隔离」当场作废 workspace 档把 docker / podman
的 socket 盖成 /dev/null；断网的两档把整个 /run 盖掉：光 `--unshare-net` 不够，
glibc 的 nss-resolve 走的是 `/run/systemd/resolve` 下的 Unix socket，**断了网照样
解析得了域名**（实测 `getent hosts example.com` 照样出 IP），把数据编进子域名就送出去了
Unix socket 不归网络命名空间管，只能从文件系统这一侧挡

钩子和 `verify:` 的命令**不进沙箱**：那是用户自己写在配置里的，不是模型产出的 
"""

import json
import os
import shlex
import shutil
import subprocess

from . import files

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
# 容器引擎的 socket：碰得到它就等于能以 root 往宿主机任何地方写，沙箱形同虚设
# 也盖成 /dev/null —— socket 也是个文件，绑得上；连不上时 docker 报
# "Cannot connect to the Docker daemon"，`tools._sandbox_note` 认得这句
DAEMON_SOCKETS = ("/run/docker.sock", "/var/run/docker.sock", "/run/podman/podman.sock",
                  "$XDG_RUNTIME_DIR/docker.sock", "$XDG_RUNTIME_DIR/podman/podman.sock")

# 执行用的 shell **`/bin/sh` 在这台机器上就是 bash，但以 sh 的名字起来会进
# POSIX 模式**，而模型写的是 bash：`source 相对路径` 在 POSIX 模式下不搜当前目录
# （只搜 PATH），`export -p` 的输出格式也不一样 工具叫 bash、说明里写的是 shell 命令，
# 那就该真给它 bash 没装 bash 的系统退回 /bin/sh
SHELL = shutil.which("bash") or "/bin/sh"

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
            raw = json.load(f)
        # `.get` 直接接在 json.load 后面的话，一份内容是 `[]` 的配置会抛
        # AttributeError —— 而 except 只接了 OSError / ValueError
        saved = raw.get("level") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        saved = None
    if saved in LEVELS:
        return saved
    return DEFAULT if available() else "off"


def set_level(name: str) -> None:
    try:
        files.private_dir(_DIR)
        fd = os.open(STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"level": name}, f)
    except OSError:
        pass


def effective() -> tuple[str, str]:
    """(实际生效的档位, 为什么) 想开但开不了的时候要说清楚，不能默默不沙箱 """
    # 外挂执行器接管的时候要照实说 —— 那时候命令跑在别的地方（SWE-bench 的实例
    # 容器里），「写被关进工作区」这句话不成立，欢迎框和 /sandbox 不能还那么显示
    if os.environ.get("FEIYU_EXEC_WRAPPER", "").strip():
        return "off", "命令交给外部执行器跑（FEIYU_EXEC_WRAPPER），本机不沙箱"
    want = level()
    if want == "off":
        return "off", "已关闭"
    if not available():
        return "off", ("装了 bubblewrap 才能沙箱（pacman -S bubblewrap）"
                       if not shutil.which("bwrap")
                       else "bwrap 跑不起来，多半是内核没开无特权 user namespace")
    return want, ""


def can_cut_net() -> bool:
    """这一刻能不能让一条命令断网跑 读过外部内容之后 bash 默认断网，靠的就是它
    （`agent._egress`）；断不了的话那道闸退成「能联网的命令都要人点头」

    外挂执行器（`FEIYU_EXEC_WRAPPER`）接管时 effective() 报 off，这里跟着是 False
    """
    return effective()[0] in ("workspace", "strict")


def wrap(command: str, *, workspace, cwd, writable=(), offline=False) -> list[str] | None:
    """把一条 shell 命令包进沙箱 返回 argv；不该沙箱时返回 None

    writable 是额外要放行写入的路径（bash 工具那个记 pwd 的临时文件要靠它，
    否则它落在沙箱内的 tmpfs 上，外面读不到，cd 就跨不了调用）

    offline 是「这一条断网跑」，跟 strict 那档一个效果，只管这一次调用

    **`FEIYU_EXEC_WRAPPER` 是把命令送到别处执行的钩子，不是安全机制。**
    给了它就整条走它、不再叠 bwrap —— SWE-bench 那条路靠它把命令送进实例容器
    （容器里 python 是 3.6~3.9，agent 本身跑不进去，只有命令要进去）。
    开着的时候「写被关进工作区」这句话不成立，`effective()` 会照实说。
    接缝选在这儿是因为 `_tool_bash` 和 `_start_job` 是仅有的两个执行入口，
    两个都从这里拿 argv，改一处就够。
    """
    hook = os.environ.get("FEIYU_EXEC_WRAPPER", "").strip()
    if hook:
        return shlex.split(hook) + [str(cwd), command]
    mode, _ = effective()
    if mode == "off":
        return None

    argv = [
        "bwrap",
        "--ro-bind", "/", "/",          # 整个文件系统只读打底
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",              # 沙箱里的 /tmp 是自己的，出去就没了
    ]
    cut = mode == "strict" or offline
    if cut:
        # 宿主机的服务全在 /run 下面听 Unix socket（DNS 解析、docker、dbus），
        # 断网的时候连它们一起断 **要排在工作区那条 bind 前面**：工作区在 /run/media
        # 下面（U 盘）的话，后盖上来的 tmpfs 会把它整个遮住
        argv += ["--tmpfs", "/run"]
    argv += [
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
    if not cut:
        seen = set()
        for path in DAEMON_SOCKETS:
            real = os.path.realpath(os.path.expandvars(path))
            if "$" not in real and real not in seen and os.path.exists(real):
                seen.add(real)
                argv += ["--ro-bind", "/dev/null", real]
    for path in writable:
        argv += ["--bind", str(path), str(path)]
    if cut:
        argv += ["--unshare-net"]
    argv += ["--chdir", str(cwd), "--", SHELL, "-c", command]
    return argv
