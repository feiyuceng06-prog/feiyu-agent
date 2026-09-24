"""还剩多少额度

`/cost` 算的是**我们自己按你填的价格估的**花费；这里问的是**接口那边的账**——
充了多少、用掉多少、还剩多少。两个数不是一回事，也不该互相替代：
前者按 token 乘价格得来，后者是人家的余额。

## 只对认得出来的接口查，认不出就明说

各家的余额接口长得都不一样，而且**多数厂商根本没有**（OpenAI 和 anthropic
的普通 key 都查不到余额，那是账号后台的事）。所以这儿是一张按 host 认的表，
认不出来就返回一句"这个接口没有查额度的地方"——**绝不猜，也绝不显示一个编的数**。

这跟"不要写内置价格表"那条不冲突：那条禁的是**会过期的事实**（模型 id、单价），
而这张表记的是**接口形状**，跟 `lsp.SERVERS` 是一类东西。真变了也是整条失效、
显示"查不到"，不会像过期的价格那样给出一个看着对其实错的数。

## 密钥只发给你自己填的那个 host

余额地址一律从配置里的 `base_url` 推出来，**不允许指向别的 host**
（`_same_host`）。这条是硬的：查个余额把 key 发到第三方去，比不查糟糕得多。

## 绝不能拖慢一轮对话

`Feiyu` 那边是丢到后台线程里查的，查不到就当没有。超时压得很短。
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT = 8
# 同一条配置多久之内不重复查 每轮都问一次纯属给人家添乱，而余额本来就是慢变量
TTL = 60.0
# **失败要另算，而且短得多。** 按 TTL 缓存失败的话，一次网络抖动就让余额
# 消失一分钟 —— 实测第一次就撞上了：两条配置共用一个缓存位，头一条抖了，
# 第二条直接吃到那份失败。
FAIL_TTL = 8.0

_cache: dict[str, tuple[float, "Balance | None", str]] = {}
_lock = threading.Lock()


@dataclass
class Balance:
    left: float | None          # 还剩多少，None 表示这家只给用量不给余额
    used: float | None          # 已经用掉多少
    total: float | None         # 一共充了多少
    unit: str                   # "$" / "¥" / "CNY"…… 原样显示，不做汇率换算

    def shown(self) -> str:
        """给人看的一行 有余额就显余额，只有用量就显用量 """
        if self.left is not None:
            return "%s%s" % (self.unit, _num(self.left))
        if self.used is not None:
            return "已用 %s%s" % (self.unit, _num(self.used))
        return ""


def _num(value: float) -> str:
    if abs(value) >= 100:
        return "%.0f" % value
    if abs(value) >= 1:
        return "%.2f" % value
    return "%.4f" % value


def _same_host(base: str, url: str) -> bool:
    """余额地址必须跟配置里的 base_url 同一个 host —— 查余额不能把 key 发去别处 """
    try:
        a = urllib.parse.urlparse(base).netloc.lower()
        b = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return False
    return bool(a) and a == b


def _get(url: str, key: str, base: str) -> dict | None:
    """**key 只发给配置里那个 host。** 查个余额把密钥发到第三方去，
    比查不到余额糟糕得多 —— 所以这条闸放在真正发请求的地方，
    而不是指望上面每个查法自己记得。 """
    if not _same_host(base, url):
        return None
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": "feiyu",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
            raw = json.loads(reply.read(200_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        exc.close()          # HTTPError 也是个响应体（401 / 404 在这儿是常态），不关就漏着
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _openrouter(base: str, key: str) -> Balance | None:
    got = _get(base.rstrip("/") + "/credits", key, base)
    data = (got or {}).get("data") or {}
    total, used = data.get("total_credits"), data.get("total_usage")
    if not isinstance(total, (int, float)) or not isinstance(used, (int, float)):
        return None
    return Balance(left=total - used, used=used, total=total, unit="$")


def _deepseek(base: str, key: str) -> Balance | None:
    got = _get(base.rstrip("/").removesuffix("/v1") + "/user/balance", key, base)
    rows = (got or {}).get("balance_infos") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    one = rows[0]
    try:
        left = float(one.get("total_balance"))
    except (TypeError, ValueError):
        return None
    unit = "¥" if str(one.get("currency", "")).upper() == "CNY" else "$"
    return Balance(left=left, used=None, total=None, unit=unit)


def _moonshot(base: str, key: str) -> Balance | None:
    got = _get(base.rstrip("/") + "/users/me/balance", key, base)
    data = (got or {}).get("data") or {}
    try:
        left = float(data.get("available_balance"))
    except (TypeError, ValueError):
        return None
    return Balance(left=left, used=None, total=None, unit="¥")


def _siliconflow(base: str, key: str) -> Balance | None:
    got = _get(base.rstrip("/") + "/user/info", key, base)
    data = (got or {}).get("data") or {}
    try:
        left = float(data.get("totalBalance"))
    except (TypeError, ValueError):
        return None
    return Balance(left=left, used=None, total=None, unit="¥")


def _oneapi(base: str, key: str) -> Balance | None:
    """一堆中转站是 one-api / new-api 那套，抄的是 OpenAI 早年的计费接口 """
    root = base.rstrip("/")
    sub = _get(root + "/dashboard/billing/subscription", key, base)
    if not sub:
        return None
    try:
        total = float(sub.get("hard_limit_usd"))
    except (TypeError, ValueError):
        return None
    used = None
    spent = _get(root + "/dashboard/billing/usage", key, base)
    if spent and isinstance(spent.get("total_usage"), (int, float)):
        used = spent["total_usage"] / 100.0        # 这个接口的单位是"分"
    return Balance(left=total - used if used is not None else total,
                   used=used, total=total, unit="$")


# host 里带这个片段 → 用这个查法。**照 host 认，不照模型 id 认**
_BY_HOST = (
    ("openrouter.ai", _openrouter),
    ("api.deepseek.com", _deepseek),
    ("api.moonshot.", _moonshot),
    ("api.siliconflow.", _siliconflow),
)

# 认不出来时说的话 —— 一句能照着做的，别只说"不支持"
UNKNOWN = ("这个接口没有查额度的地方（或者我不认识它的查法）"
           "OpenAI 和 anthropic 的普通 key 本来就查不到余额，要去各自的账号后台看 ")


def _pick(base: str):
    host = urllib.parse.urlparse(base or "").netloc.lower()
    for probe, fn in _BY_HOST:
        if probe in host:
            return fn
    return None


def look(entry: dict, *, fresh: bool = False, cached: bool = False) -> tuple["Balance | None", str]:
    """查一条模型配置的额度 返回 (余额, 说明)，说明非空就是查不到

    结果按 (host, key 的指纹) 缓存 TTL 秒 —— 同一个账号下的六条配置共用一份，
    不该问六次。

    `cached=True` **只看缓存，一个字节都不发**：给那些不能等的地方用
    （`/cost` 是同步的，实测这条路走代理偶尔要八秒 —— 让终端卡八秒去显示
    一个锦上添花的数字，是本末倒置）。
    """
    base = str(entry.get("base_url") or "")
    key = str(entry.get("key") or "")
    if not base or not key:
        return None, "这条配置没填接口地址，查不了 "
    fn = _pick(base)
    if fn is None:
        # 中转站多半是 one-api 那套，试一次；不通就照实说
        fn = _oneapi
    spot = "%s|%s" % (urllib.parse.urlparse(base).netloc.lower(), hash(key))
    now = time.monotonic()
    with _lock:
        seen = _cache.get(spot)
        if seen and not fresh:
            age = now - seen[0]
            if age < (TTL if seen[1] else FAIL_TTL):
                return seen[1], seen[2]
    if cached:
        return None, "还没查到（正在后台问）"
    got = fn(base, key)
    note = "" if got else UNKNOWN
    with _lock:
        _cache[spot] = (now, got, note)
    return got, note


def forget() -> None:
    with _lock:
        _cache.clear()
