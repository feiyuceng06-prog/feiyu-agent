"""模型清单：接口地址、密钥、价格，全由用户自己用 /key 添加

存在 ~/.config/feiyu/models.json，权限 600 因为里面是明文密钥 这个文件不会进对话
上下文，也不会被 agent 的工具读到（工具只能动工作区，它在 ~/.config 下）

上次用的是哪个存在同目录的 state.json，跟密钥分开放
"""

import json
import os

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
STORE = os.path.join(_DIR, "models.json")
STATE = os.path.join(_DIR, "state.json")

FIELDS = ("kind", "base_url", "model", "key", "price")


def _write(path: str, data) -> None:
    os.makedirs(_DIR, exist_ok=True)
    # 先按 600 建好再写，别让密钥有一瞬间是 644
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)


def load() -> dict[str, dict]:
    try:
        with open(STORE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for name, entry in (raw or {}).items():
        if isinstance(entry, dict) and entry.get("key") and entry.get("model"):
            row = {k: entry.get(k) for k in FIELDS}
            row["kind"] = row.get("kind") or "openai"
            out[name] = row
    return out


def save(entries: dict[str, dict]) -> None:
    _write(STORE, entries)


def add(name, *, base_url, model, key, kind="openai", price=None) -> None:
    entries = load()
    entries[name] = {
        "kind": kind,
        "base_url": base_url or None,
        "model": model,
        "key": key,
        "price": price,
    }
    save(entries)


def remove(name: str) -> bool:
    entries = load()
    if name not in entries:
        return False
    del entries[name]
    save(entries)
    if last() == name:
        set_last("")
    return True


def last() -> str:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f).get("last") or ""
    except (OSError, ValueError, AttributeError):
        return ""


def set_last(name: str) -> None:
    try:
        _write(STATE, {"last": name})
    except OSError:
        pass


def pick_default() -> str:
    """启动时用哪个：上次那个，没有就清单里第一个，都没有就空 """
    entries = load()
    chosen = last()
    if chosen in entries:
        return chosen
    return next(iter(entries), "")


def mask(key: str) -> str:
    if len(key) <= 10:
        return "…" * 4
    return key[:6] + "…" + key[-4:]
