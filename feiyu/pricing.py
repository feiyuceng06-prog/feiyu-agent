"""花费计算

价格只有一个来源：`/key add` 时填的那三项，跟着模型配置一起存在
~/.config/feiyu/models.json 里 没填就不算钱，界面上退回显示 token 数

刻意不做内置价格表——模型改名和调价都很频繁，写死的表迟早变成错的报价，
而报错价比不报价更糟
"""


def cost(rates, hit: int, miss: int, output: int, write: int = 0) -> float | None:
    """rates 是 (缓存命中, 输入, 输出[, 缓存写入])，单位人民币每百万 token

    write 是这次新写进缓存的那部分 anthropic 直连按输入价的 1.25 倍收，
    没填第四项就按输入价算——填不填由用户定，不内置任何一家的倍率
    """
    if rates is None:
        return None
    hit_rate, in_rate, out_rate = rates[:3]
    write_rate = rates[3] if len(rates) > 3 else in_rate
    return (hit * hit_rate + miss * in_rate + output * out_rate + write * write_rate) / 1_000_000


def yuan(amount: float) -> str:
    if amount < 0.01:
        return f"¥{amount:.4f}"
    if amount < 1:
        return f"¥{amount:.3f}"
    return f"¥{amount:.2f}"
