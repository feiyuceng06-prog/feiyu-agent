"""哪些技能 / 工具 / MCP 服务器是开着的

一份小得不能再小的开关表 存 `~/.config/feiyu/enabled.json`，0600。

## 只存关掉的那些

默认全开，文件里记的是**关掉的名字**。反过来（记开着的）有两个毛病：
新装一份技能得先想起来去开它；升级加了新工具，老用户的表里没有它，于是拿不到。
跟 0.33.0 那条「老用户拿不到新出厂规则」是同一个形状，这次一开始就避开。

所以文件通常是不存在的，或者只有一两行 —— **常态不占位置**。

## 谁读它

- `agent.tool_schemas()` —— 关掉的工具不挂进请求
- `skills_mod.catalog()` / `skill` 工具 —— 关掉的技能不进系统提示，也读不出来
- 三处都是"每次要用的时候现读"，**不缓存**：桌面端在面板里一按开关就写盘，
  而正在跑的那个 agent 得当场跟上（0.47.5 那条「配置写盘了，活着的实例没跟上」）。
  文件才几十字节，省这一次读没有意义。

终端那条路一样吃这份表 —— 它不是桌面端的私有状态，是这台机器上的配置。
"""

import json
import os

from . import files

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
STORE = os.path.join(_DIR, "enabled.json")

KINDS = ("skills", "tools", "mcp")

# 关不掉的工具 `exit_plan` 不是一项能力，是计划模式**唯一的出口** ——
# 关掉之后 `_gate` 还会指名让模型调它，而它不在工具表里，那一轮就转不出来了。
LOCKED = {"exit_plan"}


def _read() -> dict:
    try:
        with open(STORE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def disabled(kind: str) -> set[str]:
    """这一类里被关掉的名字 认不出的键一律当空 """
    rows = _read().get(kind)
    return {str(n) for n in rows} if isinstance(rows, list) else set()


def on(kind: str, name: str) -> bool:
    return name in LOCKED or name not in disabled(kind)


def _write(data: dict) -> None:
    try:
        files.private_dir(_DIR)
        fd = os.open(STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def set_on(kind: str, name: str, value: bool) -> bool:
    """开或关一项 返回它现在是不是开着的（锁住的那些永远是开着的）"""
    if kind not in KINDS or not name:
        return True
    if name in LOCKED:
        return True
    data = _read()
    off = {str(n) for n in data.get(kind) or [] if isinstance(n, str)}
    if value:
        off.discard(name)
    else:
        off.add(name)
    if off:
        data[kind] = sorted(off)
    else:
        data.pop(kind, None)     # 一项都没关就把这条整个去掉，别留个空列表
    _write(data)
    return value
