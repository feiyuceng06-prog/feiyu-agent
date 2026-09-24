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
# 每份存盘旁边放一张只有摘要的小卡片，侧边栏只读它
# 后缀要以 .json 结尾，好让 _prune 顺手把孤儿卡片一起清掉
CARD_SUFFIX = ".card.json"
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


_last_id = ""


def new_id() -> str:
    """会话 id 用时间戳：**天生按时间排序**，列历史时不用再读一遍文件去比时间

    **同一秒里连开两段不能撞。** 时间戳只到秒，而"开一段新的"是一下点出来的
    （侧栏点两个机器人、连按两次新对话）—— 撞了的话第二段的存盘直接盖掉第一段，
    表现是"刚才那段对话没了"。撞上就往后缀加序号；`_ID_OK` 认这个形状，
    列表是按 `saved_at` 排的，多一截后缀不影响顺序。
    """
    global _last_id
    now = time.strftime("%Y%m%d-%H%M%S")
    if now == _last_id or _last_id.startswith(now + "-"):
        n = int(_last_id.rpartition("-")[2]) + 1 if _last_id.startswith(now + "-") else 2
        now = "%s-%d" % (now, n)
    _last_id = now
    return now


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
    """拿人说的第一段当标题 侧边栏上一行字，得让人认得出是哪次对话

    **只取第一段，遇空行就停。** 发出去的那条 user 消息后面常常挂着机器拼的东西
    —— 桌面端的「我上传了这些文件（相对工作区）：」、`@路径` 读好附上的整份文件
    正文，都是用空行隔开接在后面的。把整条拍平了截 60 个字，一列会话就全长成
    「看看这张图 我上传了这些文件（相对工作区）： -...」，彼此认不出来
    （实测侧边栏里连着十五条一模一样）。人自己打的那句话在第一段里。
    """
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            # anthropic 那边 user 消息里可能装的是 tool_result，那不是人说的话
            content = " ".join(b.get("text", "") for b in content
                               if isinstance(b, dict) and b.get("type") == "text")
        if not isinstance(content, str):
            continue        # 写坏的存盘里 content 可能是个 dict，别把它 str() 成标题
        para: list[str] = []
        for line in content.splitlines():
            if not line.strip():
                if para:
                    break
                continue        # 开头的空行不算一段的结束
            para.append(line)
        text = " ".join(" ".join(para).split())
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
        # 这段对话是哪个机器人开的 —— 不存的话切回来人格就没了，
        # 而前面说过的话全是按那个人格说的
        "bot": getattr(agent, "bot", "") or None,
        # 读过的第一份外部内容 恢复回来那道闸要照旧落着（见 agent._egress）
        "outside": getattr(agent.toolbox, "outside", "") or None,
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
        # **`legacy` 是个读旧位置的别名，不能变成写出去的文件名。**
        # 写成 folder/legacy.json 的话，listing 从目录里扫出来给它的 id 还是
        # `legacy`，而 drop 见到这个 id 会去删**旧位置**那份 —— 删不掉，
        # 于是那一行永远赖在侧栏上，点多少次「删掉」都只是回一句「删掉了」。
        path = (_legacy_path(workspace) if not sid or sid == LEGACY
                else _path(workspace, sid))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except (OSError, TypeError, ValueError):
        return
    _write_card(path, data)
    _prune(workspace)


def _prune(workspace) -> None:
    """只留最近 MAX_KEPT 份 一份就是一整段对话原文，不封顶磁盘会一直涨

    **数的时候要把小卡片排掉**：不排的话一份存盘算两个，实际只留 20 份。
    删存盘时把它的卡片一并带走，别留孤儿。
    """
    here = folder(workspace)
    try:
        names = sorted(n for n in os.listdir(here)
                       if n.endswith(".json") and not n.endswith(CARD_SUFFIX))
    except OSError:
        return
    for name in names[:-MAX_KEPT] if len(names) > MAX_KEPT else []:
        path = os.path.join(here, name)
        for one in (path, _card_path(path)):
            try:
                os.remove(one)
            except OSError:
                pass


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    # **messages 必须真的是个列表。** 只判真值的话 `{"messages": "坏了"}` 也算数，
    # restore 会把这个字符串直接装进 backend.messages，下一次请求就是 400 ——
    # 而那时候已经离这儿很远了，看着像"接口坏了"
    return (data if isinstance(data, dict) and isinstance(data.get("messages"), list)
            and data["messages"] else None)


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
        if not name.endswith(".json") or name.endswith(CARD_SUFFIX):
            continue
        path = os.path.join(folder(workspace), name)
        # 先看小卡片：拿一行标题不该把整份对话原文都解析一遍
        data = _card(path) or _read(path)
        if data:
            rows.append(_summary(name[:-5], data))
    old = _read(_legacy_path(workspace))
    if old:
        rows.append(_summary(LEGACY, old))
    rows.sort(key=lambda r: r["saved_at"], reverse=True)
    return rows


def _num(value, fallback: float = 0.0) -> float:
    """存盘里的数字一律过这道 —— 盘上的东西不保证是数

    一份写坏的存盘（写到一半被 kill、手改过、别的版本留下的）里 `saved_at`
    可能是字符串，而 `listing()` 会拿它去 `time.localtime()`，一个 TypeError
    **把整份侧边栏都带走** —— 一份坏文件，一条历史都列不出来。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    # 1e300 这种喂给 localtime 会抛 OverflowError，照样是一条都列不出来
    return value if -1e12 < value < 1e12 else fallback


def _summary(sid: str, data: dict) -> dict:
    saved = _num(data.get("saved_at"))
    title = data.get("title")
    if not isinstance(title, str) or not title:
        title = _first_words(data.get("messages")) or "（没有标题）"
    messages = data.get("messages")
    count = data.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        count = len(messages) if isinstance(messages, list) else 0
    model = data.get("model")
    return {
        "id": sid,
        "title": title,
        "model": model if isinstance(model, str) else "",
        "count": count,
        "saved_at": saved,
        "when": time.strftime("%m-%d %H:%M", time.localtime(saved)),
    }


def _card_path(path: str) -> str:
    return path[:-5] + CARD_SUFFIX


def _write_card(path: str, data: dict) -> None:
    """存一张只有摘要的小卡片，给侧边栏用

    **为什么要它：** `listing()` 原来要把每一份存盘整个 json.loads 一遍，
    只为了拿一行标题。实测 40 份长会话（96 MB）画一次侧边栏要 131 ms，
    而这个函数每开一段新会话、每切一次都要跑。卡片只有几百字节。

    卡片是**派生数据**，坏了 / 没有 / 过期都退回读整份（见 `_card`），
    所以它不是新的一份真相，只是缓存。
    """
    card = {"id": data.get("id"), "model": data.get("model", ""),
            "title": data.get("title") or _first_words(data.get("messages")) or "",
            "count": len(data.get("messages", [])), "saved_at": data.get("saved_at", 0)}
    try:
        fd = os.open(_card_path(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False)
    except (OSError, TypeError, ValueError):
        pass


def _card(path: str) -> dict | None:
    """读那张小卡片 **比存盘旧就当没有**——存盘才是真相，卡片只是缓存 """
    card_at = _card_path(path)
    try:
        if os.path.getmtime(card_at) < os.path.getmtime(path):
            return None
        with open(card_at, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("saved_at") else None


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
    if not _ID_OK.match(sid):
        return
    # legacy 两个位置都删：新位置那份是 0.55.0 以前的 save 留下的，
    # 只删旧位置的话侧栏上那一行删不掉（用户拿截图来问过）
    where = [_path(workspace, sid)]
    if sid == LEGACY:
        where.append(_legacy_path(workspace))
    for path in where:
        for one in (path, _card_path(path)):
            try:
                os.remove(one)
            except OSError:
                pass


def describe(data: dict) -> str:
    # **`saved_at` 也要过 `_num`。** 理由跟 `_summary` 那条一模一样，只是这条更疼：
    # 它在启动路径上（`__main__` 里 `--resume` / "这个目录有上次的对话" 那两句
    # 都不带兜底），一份 `saved_at` 是字符串的坏存盘 → `localtime` 抛 TypeError
    # → **feiyu 在这个目录里根本起不来**，连 /clear 都够不着
    when = time.strftime("%m-%d %H:%M", time.localtime(_num(data.get("saved_at"))))
    messages = data.get("messages")
    return "%s · %d 条消息 · %s" % (
        data.get("model") if isinstance(data.get("model"), str) else "?",
        len(messages) if isinstance(messages, list) else 0, when)
