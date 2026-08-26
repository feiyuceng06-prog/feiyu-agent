"""花费计算

价格只有一个来源：`/key add` 时填的那三项，跟着模型配置一起存在
~/.config/feiyu/models.json 里 没填就不算钱，界面上退回显示 token 数

刻意不做内置价格表——模型改名和调价都很频繁，写死的表迟早变成错的报价，
而报错价比不报价更糟
"""


def cost(rates, hit: int, miss: int, output: int) -> float | None:
    """rates 是 (缓存命中, 输入, 输出)，单位人民币每百万 token """
    if rates is None:
        return None
    hit_rate, in_rate, out_rate = rates
    return (hit * hit_rate + miss * in_rate + output * out_rate) / 1_000_000


def yuan(amount: float) -> str:
    if amount < 0.01:
        return f"¥{amount:.4f}"
    if amount < 1:
        return f"¥{amount:.3f}"
    return f"¥{amount:.2f}"
