"""记住 agent 看过的文件版本，写之前对一下，别盖掉它没看见的改动

顺带放两样跟"文件系统本身"有关、别处都要用的东西：`private_dir()`（feiyu
自己那几个目录统一走它建，理由见那个函数）和 `IGNORE_DIRS`。

`IGNORE_DIRS` 放在这儿而不是 `tools.py`，是因为 `ui.py` 的 `@` 补全也要用它，
而 ui 不该为了一个集合把整个 tools 拉进来。
"""

import hashlib
import os
import stat as stat_mod
from pathlib import Path

# 谁都不想走进去的目录：列文件、搜内容、`@` 补全共用一份
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
               ".mypy_cache", "dist", "build"}


def private_dir(path) -> None:
    """建一个只有自己能进的目录，已经存在的也收紧

    里面的文件本来就是 0600，但**目录**默认跟着 umask 走，多半是 0755：
    同一台机器上别的用户虽然读不到内容，却能列出有哪些工作区、什么时候用过。
    密钥、完整对话原文、长期记忆都在这几个目录下面，没有理由让它们可列。

    `makedirs(mode=)` 只对新建的那一层生效、而且还要被 umask 削一道，
    所以建完再 chmod 一次 —— 老用户的目录是之前用 0755 建出来的。
    """
    os.makedirs(path, exist_ok=True)
    # 目标本身，加上路上那个叫 feiyu 的父目录 —— sessions / logs / memory 都在
    # `<base>/feiyu/` 下面，只收紧末一层的话，中间那层还是可列的。
    here = Path(path).resolve()
    targets = [here] + [p for p in here.parents if p.name == "feiyu"]
    for target in targets:
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass    # Windows 上是空操作，只读文件系统上也不该因此崩


def odd_kind(path) -> str | None:
    """这个路径不是一份普通文件时说清它是什么，否则 None

    **命名管道会把整个进程挂死。** 打开一个没有另一端的 FIFO 时，read 和 write
    都会一直阻塞：界面上一个字都没有，`STREAM_TIMEOUT` 也不会响（那条超时管的是
    模型的流，这儿根本还没发出去）。实测 `read_file` 和 `write_file` 各能这么挂住
    —— 跟 0.58.0 那次「为什么卡死了」是同一种毛病：**查不出来才是那个 bug**。
    设备节点和 socket 同理。

    `os.stat` 跟着软链走，所以指向 FIFO 的软链也认得出来。
    """
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None                 # 不在 / 权限不足，那是另一条判断管的事
    if stat_mod.S_ISDIR(mode):
        return "目录"
    if stat_mod.S_ISREG(mode):
        return None
    if stat_mod.S_ISFIFO(mode):
        return "命名管道"
    if stat_mod.S_ISSOCK(mode):
        return "socket"
    return "设备文件"


def _mark(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_mtime_ns, st.st_size


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class Watcher:
    def __init__(self) -> None:
        self._seen: dict[Path, tuple[int, int, str]] = {}

    def record(self, path: Path, raw: bytes | None = None) -> None:
        try:
            if raw is None:
                raw = path.read_bytes()
            self._seen[path] = (*_mark(path), _digest(raw))
        except OSError:
            self._seen.pop(path, None)

    def seen(self, path: Path) -> bool:
        return path in self._seen

    def stale(self, path: Path) -> bool:
        """看过、而且现在的内容跟看过的那份不一样 """
        known = self._seen.get(path)
        if known is None:
            return False
        try:
            if _mark(path) == known[:2]:
                return False
            # mtime 变了内容未必变：格式化工具、git checkout 回同一版本都会这样
            return _digest(path.read_bytes()) != known[2]
        except OSError:
            return False

    def forget(self, path: Path) -> None:
        self._seen.pop(path, None)
