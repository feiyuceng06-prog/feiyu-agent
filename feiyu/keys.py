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

FIELDS = ("kind", "base_url", "model", "key", "price", "vision", "max_tokens", "limit")


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


def add(name, *, base_url, model, key, kind="openai", price=None, vision=False,
        max_tokens=None, limit=None) -> None:
    entries = load()
    entries[name] = {
        "kind": kind,
        "base_url": base_url or None,
        "model": model,
        "key": key,
        "price": price,
        "vision": bool(vision),
        "max_tokens": int(max_tokens) if max_tokens else None,
        "limit": int(limit) if limit else None,
    }
    save(entries)
    forget_limit(name)      # 重新填过的配置，之前学到的上限不再作数


def remove(name: str) -> bool:
    entries = load()
    if name not in entries:
        return False
    del entries[name]
    save(entries)
    if last() == name:
        set_last("")
    if _state().get("helper") == name:
        set_helper("")
    forget_limit(name)
    return True


def _state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _set_state(**fields) -> None:
    state = _state()
    state.update(fields)
    try:
        _write(STATE, state)
    except OSError:
        pass


def last() -> str:
    return _state().get("last") or ""


def set_last(name: str) -> None:
    _set_state(last=name)


def helper() -> str:
    """做旁活（摘要、子 agent、网页提炼）用哪条配置 空串就是跟主模型一样 """
    name = _state().get("helper") or ""
    return name if name in load() else ""


def set_helper(name: str) -> None:
    _set_state(helper=name)


def theme() -> str:
    """界面主题 空串就是默认 """
    return _state().get("theme") or ""


def set_theme(name: str) -> None:
    _set_state(theme=name)


def learned_limit(name: str) -> int:
    """撞过墙之后记下来的上下文上限 0 表示没学过 """
    limits = _state().get("limits") or {}
    value = limits.get(name) if isinstance(limits, dict) else None
    return int(value) if isinstance(value, int) and value > 0 else 0


def learn_limit(name: str, tokens: int) -> None:
    limits = dict(_state().get("limits") or {})
    limits[name] = int(tokens)
    _set_state(limits=limits)


def forget_limit(name: str) -> None:
    limits = dict(_state().get("limits") or {})
    if limits.pop(name, None) is not None:
        _set_state(limits=limits)


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
