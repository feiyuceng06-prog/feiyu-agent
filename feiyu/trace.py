"""结构化日志与花费追踪

每件事一行 JSONL，写在 ~/.local/state/feiyu/logs/<日期>.jsonl
按天分文件、每行带 run id：查"刚才那次它为什么干了怪事"就按 run 过滤，
算"这周花了多少"就按天汇总，一份文件两用

**日志里有命令原文和文件路径**，跟会话存盘一样按 0600 存 参数会截断，
不记文件内容——排查要的是"调了什么"，不是把整个工作区抄一遍

写日志永远不能弄崩主流程：所有出错都咽掉
"""

import json
import os
import time
import uuid

DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "feiyu",
    "logs",
)

# 参数值记多长 够认出调的是哪个文件、哪条命令就行
MAX_VALUE = 300


def _short(value) -> object:
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE else value[:MAX_VALUE] + "…"
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_short(v) for v in value[:10]]
    return value


class Trace:
    """一次运行的日志器 关掉（enabled=False）时所有方法都是空转 """

    def __init__(self, workspace, *, enabled: bool = True):
        self.run = uuid.uuid4().hex[:8]
        self.workspace = str(workspace)
        self.enabled = enabled and _wanted()
        self.started = time.time()

    def write(self, kind: str, **fields) -> None:
        if not self.enabled:
            return
        row = {
            "ts": round(time.time(), 3),
            "run": self.run,
            "workspace": self.workspace,
            "kind": kind,
        }
        row.update({k: _short(v) for k, v in fields.items() if v is not None})
        try:
            os.makedirs(DIR, exist_ok=True)
            path = today_path()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass      # 日志坏了也不能影响干活


def _wanted() -> bool:
    return os.environ.get("FEIYU_NO_LOG", "").strip() in ("", "0", "false", "no")


def today_path() -> str:
    return os.path.join(DIR, time.strftime("%Y-%m-%d") + ".jsonl")


def read(days: int = 7, workspace: str | None = None) -> list[dict]:
    """读最近几天的日志 workspace 给了就只要那个目录的 """
    rows = []
    for offset in range(days):
        stamp = time.strftime("%Y-%m-%d", time.localtime(time.time() - offset * 86400))
        path = os.path.join(DIR, stamp + ".jsonl")
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if workspace is None or row.get("workspace") == workspace:
                        rows.append(row)
        except OSError:
            continue
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def summarize(rows: list[dict]) -> dict:
    """按 run 汇总 一次运行就是一个任务，这是能对上"这次干活花了多少"的粒度 """
    runs: dict[str, dict] = {}
    for row in rows:
        run = runs.setdefault(row.get("run", "?"), {
            "run": row.get("run", "?"), "start": row.get("ts", 0), "end": row.get("ts", 0),
            "model": "", "tokens": 0, "cost": 0.0, "priced": False,
            "turns": 0, "tools": 0, "denied": 0, "task": "",
            "input": 0, "hit": 0,
        })
        run["end"] = max(run["end"], row.get("ts", 0))
        kind = row.get("kind")
        if kind == "run_start":
            run["model"] = row.get("model", "")
            run["task"] = row.get("task", "")
        elif kind == "turn":
            run["turns"] += 1
            run["tokens"] += row.get("tokens", 0) or 0
            run["input"] += row.get("input", 0) or 0
            run["hit"] += row.get("hit", 0) or 0
            if row.get("cost") is not None:
                run["cost"] += row["cost"]
                run["priced"] = True
        elif kind == "tool":
            run["tools"] += 1
            if not row.get("ok", True):
                run["denied"] += 1
    return runs
