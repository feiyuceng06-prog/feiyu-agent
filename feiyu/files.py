"""记住 agent 看过的文件版本，写之前对一下，别盖掉它没看见的改动 """

import hashlib
from pathlib import Path


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
