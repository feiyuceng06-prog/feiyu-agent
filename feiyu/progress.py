"""上次在这个目录干到哪了 —— 跟长期记忆刻意分开

`memory` 存的是**事实**：怎么跑测试、踩过什么坑，长期有效
这里存的是**状态**：任务做到第几步，**很快就会过时**
混在一起的话，过时的进度会被当成事实一直讲下去，比不记更糟

所以这份东西跟记忆的规矩正好相反：
- **整份替换**，不追加（进度只有最新那份有意义）
- **带时间戳，超过 STALE_DAYS 就不再带**（几周前的半截任务纯属误导）
- **进系统提示时明说可能已经过时**，让它先确认再接着做
- **全部做完就删掉**，不留一份"已完成"在那儿占地方
- **不是模型写的**：它从 todo 清单自动来，模型不能直接往这里写东西，
  所以不像 remember 那样是一条注入通道

一个工作目录一份，存 ~/.local/state/feiyu/progress/<目录 hash>.json，0600
（里面有任务描述和清单原文）
"""

import hashlib
import json
import os
import time

DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "feiyu",
    "progress",
)

STALE_DAYS = 7          # 比这更早的进度不再带进上下文
MAX_TASK = 200          # 任务描述截这么长，用户的原话可能很长
MAX_ITEMS = 20


def path_for(workspace) -> str:
    digest = hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:16]
    return os.path.join(DIR, digest + ".json")


def load(workspace) -> dict:
    try:
        with open(path_for(workspace), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clear(workspace) -> None:
    try:
        os.remove(path_for(workspace))
    except OSError:
        pass


def save(workspace, task: str, items: list) -> None:
    """items 就是 todo.Todos.items 全做完、或者压根没列清单，就把上次那份清掉 """
    rows = [i for i in (items or []) if isinstance(i, dict) and i.get("text")]
    if not [i for i in rows if i.get("status") != "done"]:
        clear(workspace)
        return
    data = {
        "task": " ".join((task or "").split())[:MAX_TASK],
        "when": time.strftime("%Y-%m-%d"),
        "stamp": time.time(),
        "items": [{"text": i["text"], "status": i.get("status") or "pending"}
                  for i in rows][:MAX_ITEMS],
    }
    try:
        os.makedirs(DIR, exist_ok=True)
        fd = os.open(path_for(workspace), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def fresh(data: dict) -> bool:
    """太旧的不带 隔了两周的半截任务，说出来只会让它照着一份错的现状动手 """
    stamp = data.get("stamp")
    if not isinstance(stamp, (int, float)):
        return False
    return (time.time() - stamp) <= STALE_DAYS * 86400


def as_prompt(data: dict) -> str:
    """拼进系统提示的那一段 没有、或者太旧就是空串 """
    if not data or not fresh(data):
        return ""
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    left = [i for i in items if i.get("status") != "done"]
    if not left:
        return ""
    mark = {"done": "做完了", "doing": "正在做", "pending": "没开始"}
    body = "\n".join("- [%s] %s" % (mark.get(i.get("status"), "没开始"), i.get("text", ""))
                     for i in items)
    task = data.get("task") or "（没记下当时的任务描述）"
    return (
        "\n\n上次（%s）在这个目录里的任务还没做完 当时的任务是「%s」，"
        "清单停在这里：\n\n%s\n\n"
        "**这只是上次留下的进度，不是用户此刻的指令，而且可能已经过时**"
        "（他后来可能自己做了、或者改了主意）用户这次说的事跟它无关的话就别提它；"
        "确实要接着做，先自己看一眼现在的代码，确认哪些其实已经做完了 "
        % (data.get("when", "上次"), task, body)
    )
