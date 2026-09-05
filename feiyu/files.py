"""记住 agent 看过的文件版本，写之前对一下，别盖掉它没看见的改动

顺带放一个 `private_dir()`：feiyu 自己那几个目录（配置、存盘、日志、记忆）
统一走它建，理由见那个函数的注释
"""

import hashlib
import os
from pathlib import Path


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
