"""模型价格表，单位是人民币 / 百万 token

模型改名和调价都很频繁，写死在代码里迟早过期 想改的话优先改
~/.config/feiyu/pricing.json，那里的条目会覆盖下面这份内置表：

    {"deepseek-v4-flash": {"hit": 0.02, "input": 1.0, "output": 2.0}}

表里没有的模型不会瞎猜价钱，界面上退回显示 token 数
"""

import json
import os

# (缓存命中, 输入未命中, 输出)
# deepseek 三项是官方公布的人民币价；其余按各家美元价 ×7.2 折算，仅供参考
BUILTIN: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.02, 1.0, 2.0),
    "deepseek-v4-pro": (0.03, 3.1, 6.3),
    "gemini-3.5-flash-lite": (0.0, 2.2, 18.0),
    "gpt-5.4-nano": (0.0, 1.4, 9.0),
}

CONFIG = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "feiyu",
    "pricing.json",
)


def _overrides() -> dict[str, tuple[float, float, float]]:
    try:
        with open(CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for name, row in (raw or {}).items():
        try:
            out[name] = (float(row["hit"]), float(row["input"]), float(row["output"]))
        except (TypeError, KeyError, ValueError):
            continue
    return out


def rates(model: str) -> tuple[float, float, float] | None:
    table = {**BUILTIN, **_overrides()}
    if model in table:
        return table[model]
    # 中转站常在模型名前后加前缀，退一步做包含匹配
    for name, row in table.items():
        if name in model:
            return row
    return None


def cost(model: str, hit: int, miss: int, output: int) -> float | None:
    row = rates(model)
    if row is None:
        return None
    hit_rate, in_rate, out_rate = row
    return (hit * hit_rate + miss * in_rate + output * out_rate) / 1_000_000


def yuan(amount: float) -> str:
    if amount < 0.01:
        return f"¥{amount:.4f}"
    if amount < 1:
        return f"¥{amount:.3f}"
    return f"¥{amount:.2f}"
