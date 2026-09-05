"""会话存盘 一个工作目录可以有多份，网页端的侧边栏靠它列历史

存在 `~/.local/state/feiyu/sessions/<工作目录 hash>/<会话 id>.json`。
注意这里面有完整对话原文，包括 agent 读过的文件内容，所以跟着密钥一样按 600 存，
目录走 `files.private_dir()`。

## 从「一个目录一份」改成「一个目录多份」（0.51.0）

0.50.x 之前是 `sessions/<hash>.json`，一个工作目录只有一份，`--resume` 就是捞它。
网页端要列历史对话，那个形状装不下。现在每份一个 id（时间戳），
`listing()` 按时间倒序列出来。

**老的那份文件要照样能读**（`_legacy_path`）：升级之后 `feiyu --resume` 不能突然
说没有上次的对话。`listing()` 把它当成一条 id 为 `legacy` 的记录，
`load(sid="legacy")` 读得到，写的时候只往新目录写、不再动它。
"""

import hashlib
import json
import os
import re
import time

from . import files

from .providers import without_images

DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "feiyu",
    "sessions",
)

# 一个工作目录最多留这么多份，超了从最早的删 存的是完整对话原文，不封顶会一直涨
MAX_KEPT = 40
# 侧边栏上那个标题从第一句话里取，截这么长
TITLE_CAP = 60
LEGACY = "legacy"
_ID_OK = re.compile(r"^[0-9a-zA-Z_-]{1,40}$")


def _hash(workspace) -> str:
    return hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:16]


def folder(workspace) -> str:
    return os.path.join(DIR, _hash(workspace))


def _legacy_path(workspace) -> str:
    """0.51.0 之前的位置 只读不写 """
    return os.path.join(DIR, _hash(workspace) + ".json")


def _path(workspace, sid: str) -> str:
    return os.path.join(folder(workspace), sid + ".json")


def new_id() -> str:
    """会话 id 用时间戳：**天生按时间排序**，列历史时不用再读一遍文件去比时间 """
    return time.strftime("%Y%m%d-%H%M%S")


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


def _first_words(messages) -> str:
    """拿人说的第一句当标题 侧边栏上一行字，得让人认得出是哪次对话 """
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            # anthropic 那边 user 消息里可能装的是 tool_result，那不是人说的话
            content = " ".join(b.get("text", "") for b in content
                               if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(str(content or "").split())
        if text:
            return text[:TITLE_CAP]
    return ""


def save(workspace, agent, sid: str | None = None) -> None:
    if not agent.ready or len(agent.backend.messages) <= 1:
        return
    if sid is not None and not _ID_OK.match(sid):
        return
    b = agent.backend
    messages = without_images(_plain(b.messages))
    data = {
        "id": sid,
        "model": agent.name,
        "style": agent.style,
        "mode": agent.mode,
        "title": _first_words(messages),
        # 图片不进存盘：一张就是几十万字，恢复回来也未必是当初那张
        "messages": messages,
        "todos": agent.toolbox.todos.items,
        "tokens_total": b.tokens_total,
        "tokens_context": b.tokens_context,
        "tokens_in": getattr(b, "tokens_in", 0),
        "tokens_hit": getattr(b, "tokens_hit", 0),
        "cost": b.cost,
        "priced": b.priced,
        "saved_at": time.time(),
    }
    try:
        here = folder(workspace)
        files.private_dir(here)
        path = _path(workspace, sid) if sid else _legacy_path(workspace)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except (OSError, TypeError, ValueError):
        return
    _prune(workspace)


def _prune(workspace) -> None:
    """只留最近 MAX_KEPT 份 一份就是一整段对话原文，不封顶磁盘会一直涨 """
    try:
        names = sorted(n for n in os.listdir(folder(workspace)) if n.endswith(".json"))
    except OSError:
        return
    for name in names[:-MAX_KEPT] if len(names) > MAX_KEPT else []:
        try:
            os.remove(os.path.join(folder(workspace), name))
        except OSError:
            pass


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("messages") else None


def listing(workspace) -> list[dict]:
    """这个工作目录下的历史对话，**新的在前** 只出摘要，不出正文

    正文动辄几十万字，侧边栏用不上；点开某一条的时候再 `load()`。
    """
    rows = []
    try:
        names = os.listdir(folder(workspace))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        data = _read(os.path.join(folder(workspace), name))
        if data:
            rows.append(_summary(name[:-5], data))
    old = _read(_legacy_path(workspace))
    if old:
        rows.append(_summary(LEGACY, old))
    rows.sort(key=lambda r: r["saved_at"], reverse=True)
    return rows


def _summary(sid: str, data: dict) -> dict:
    return {
        "id": sid,
        "title": data.get("title") or _first_words(data.get("messages")) or "（没有标题）",
        "model": data.get("model", ""),
        "count": len(data.get("messages", [])),
        "saved_at": data.get("saved_at", 0),
        "when": time.strftime("%m-%d %H:%M", time.localtime(data.get("saved_at", 0))),
    }


def load(workspace, sid: str | None = None) -> dict | None:
    """不给 sid 就读最近的一份 """
    if sid == LEGACY:
        return _read(_legacy_path(workspace))
    if sid is not None:
        if not _ID_OK.match(sid):
            return None
        return _read(_path(workspace, sid))
    rows = listing(workspace)
    return load(workspace, rows[0]["id"]) if rows else None


def latest_id(workspace) -> str | None:
    rows = listing(workspace)
    return rows[0]["id"] if rows else None


def drop(workspace, sid: str | None = None) -> None:
    """删一份 不给 sid 就删最近的那份（`/clear` 走的是这条）"""
    if sid is None:
        sid = latest_id(workspace)
        if sid is None:
            return
    path = _legacy_path(workspace) if sid == LEGACY else (
        _path(workspace, sid) if _ID_OK.match(sid) else None)
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def describe(data: dict) -> str:
    when = time.strftime("%m-%d %H:%M", time.localtime(data.get("saved_at", 0)))
    return "%s · %d 条消息 · %s" % (data.get("model", "?"), len(data.get("messages", [])), when)
