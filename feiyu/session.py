"""会话存盘 每个工作目录一份，下次 feiyu --resume 接着聊

存在 ~/.local/state/feiyu/sessions/ 里 注意这里面有完整对话原文，
包括 agent 读过的文件内容，所以跟着密钥一样按 600 存
"""

import hashlib
import json
import os
import time

from .providers import without_images

DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "feiyu",
    "sessions",
)


def _path(workspace) -> str:
    digest = hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:16]
    return os.path.join(DIR, digest + ".json")


def _plain(obj):
    """anthropic 的 messages 里是 SDK 对象，先摊平成能 json 序列化的结构 """
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return _plain(dump())
    return obj


def save(workspace, agent) -> None:
    if not agent.ready or len(agent.backend.messages) <= 1:
        return
    b = agent.backend
    data = {
        "model": agent.name,
        "style": agent.style,
        "mode": agent.mode,
        # 图片不进存盘：一张就是几十万字，恢复回来也未必是当初那张
        "messages": without_images(_plain(b.messages)),
        "todos": agent.toolbox.todos.items,
        "tokens_total": b.tokens_total,
        "tokens_context": b.tokens_context,
        "cost": b.cost,
        "priced": b.priced,
        "saved_at": time.time(),
    }
    try:
        os.makedirs(DIR, exist_ok=True)
        path = _path(workspace)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except (OSError, TypeError, ValueError):
        pass


def load(workspace) -> dict | None:
    try:
        with open(_path(workspace), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("messages") else None


def drop(workspace) -> None:
    try:
        os.remove(_path(workspace))
    except OSError:
        pass


def describe(data: dict) -> str:
    when = time.strftime("%m-%d %H:%M", time.localtime(data.get("saved_at", 0)))
    return "%s · %d 条消息 · %s" % (data.get("model", "?"), len(data.get("messages", [])), when)
