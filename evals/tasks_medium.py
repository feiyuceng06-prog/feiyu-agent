"""中等规模的题：几个模块、上百行、带测试 每题铺一个小项目，判据照旧是确定性的

前十一题几轮就完，测不出「在一堆代码里找对地方、改完不把别处弄坏」这种能力
这里每题都有两条共同的暗线：**修 bug 的题测试文件不许改**（改测试凑绿是最常见的作弊；
加功能的题可以加测试，但原有的行不能动），**没让动的模块不许动**
"""

import subprocess
import sys
from pathlib import Path


def _run(cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True,
                          text=True, timeout=90)


def _ran(done, label: str):
    if done.returncode == 0:
        return True, ""
    tail = (done.stderr or done.stdout or "").strip().splitlines()
    return False, "%s：%s" % (label, tail[-1][:70] if tail else "退出码 %d" % done.returncode)


def _text(root: Path, name: str) -> str:
    try:
        return (root / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _lay(root: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _kept(root: Path, files: dict[str, str], name: str) -> bool:
    """原有的每一行都还在（可以加新测试，不能改旧的） 加功能的题用这个，修 bug 的题用 _unchanged """
    now = _text(root, name).splitlines()
    return all(line in now for line in files[name].splitlines() if line.strip())


def _unchanged(root: Path, files: dict[str, str], names) -> str:
    """这些文件必须原样 返回第一个被改的名字，没有就空串 """
    for name in names:
        if _text(root, name) != files[name]:
            return name
    return ""


# ---- 12. 小工具加 --json：三个模块，输出要走 formatter 那一层 ----

STATS = {
    "stats/__init__.py": "",
    "stats/reader.py": '''"""读一列数字 一行一个，空行和 # 开头的跳过 """


def read_numbers(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(float(line))
    return out
''',
    "stats/calc.py": '''def summary(nums):
    if not nums:
        return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "count": len(nums),
        "mean": sum(nums) / len(nums),
        "min": min(nums),
        "max": max(nums),
    }
''',
    "stats/formatter.py": '''def as_text(result):
    return "\\n".join("%s: %s" % (k, _fmt(v)) for k, v in result.items())


def _fmt(v):
    if isinstance(v, float):
        return "%.2f" % v
    return str(v)
''',
    "cli.py": '''import argparse

from stats.calc import summary
from stats.formatter import as_text
from stats.reader import read_numbers


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stats")
    ap.add_argument("file")
    args = ap.parse_args(argv)
    print(as_text(summary(read_numbers(args.file))))


if __name__ == "__main__":
    main()
''',
    "data.txt": "# 三个数\n1\n2\n\n6\n",
    "test_stats.py": '''import unittest

from stats.calc import summary
from stats.formatter import as_text


class T(unittest.TestCase):
    def test_summary(self):
        self.assertEqual(summary([1, 2, 6])["mean"], 3)

    def test_text(self):
        self.assertIn("mean: 3.00", as_text(summary([1, 2, 6])))
''',
}


def setup_json_flag(root):
    _lay(root, STATS)


def check_json_flag(root, answer):
    import json
    done = _run(root, "cli.py", "--json", "data.txt")
    ok, why = _ran(done, "带 --json 跑不起来")
    if not ok:
        return False, why
    try:
        got = json.loads(done.stdout)
    except ValueError:
        return False, "--json 的输出不是 JSON：%r" % done.stdout[:50]
    if got.get("count") != 3 or got.get("mean") != 3:
        return False, "JSON 里的数不对：%s" % got
    plain = _run(root, "cli.py", "data.txt")
    if "mean: 3.00" not in plain.stdout:
        return False, "不带 --json 的原有输出坏了"
    if "json" not in _text(root, "stats/formatter.py").lower():
        return False, "JSON 没放在 formatter 层（现有的文本输出就在那儿）"
    if not _kept(root, STATS, "test_stats.py"):
        return False, "改了原有的测试"
    return _ran(_run(root, "-m", "unittest", "-q", "test_stats"), "原有测试挂了")


# ---- 13. 两个模块各藏一个 bug，九条测试挂三条 不许改测试 ----

SHOP = {
    "shop/__init__.py": "",
    "shop/cart.py": '''class Cart:
    def __init__(self):
        self.items = {}     # sku -> (unit_price, qty)

    def add(self, sku, price, qty=1):
        if sku in self.items:
            old_price, old_qty = self.items[sku]
            self.items[sku] = (old_price, old_qty + qty)
        else:
            self.items[sku] = (price, qty)

    def remove(self, sku, qty=1):
        if sku not in self.items:
            return
        price, have = self.items[sku]
        if have - qty <= 0:
            del self.items[sku]
        else:
            self.items[sku] = (price, have - qty)

    def subtotal(self):
        return sum(price * qty for price, qty in self.items.values())

    def count(self):
        return len(self.items)
''',
    "shop/discount.py": '''"""满减：subtotal 达到门槛就减 门槛按从高到低只用一条 """

RULES = [(100, 20), (50, 8), (20, 2)]


def discount_for(subtotal):
    for threshold, off in RULES:
        if subtotal > threshold:
            return off
    return 0


def final_price(subtotal):
    return max(0, subtotal - discount_for(subtotal))
''',
    "shop/receipt.py": '''from shop.discount import final_price


def render(cart):
    lines = []
    for sku, (price, qty) in sorted(cart.items.items()):
        lines.append("%-8s %6.2f x %d" % (sku, price, qty))
    sub = cart.subtotal()
    lines.append("subtotal %.2f" % sub)
    lines.append("total    %.2f" % final_price(sub))
    return "\\n".join(lines)
''',
    "test_shop.py": '''import unittest

from shop.cart import Cart
from shop.discount import discount_for, final_price
from shop.receipt import render


class TestCart(unittest.TestCase):
    def test_add_twice_merges(self):
        c = Cart(); c.add("a", 2.0); c.add("a", 2.0, 3)
        self.assertEqual(c.items["a"], (2.0, 4))

    def test_count_is_total_quantity(self):
        c = Cart(); c.add("a", 1.0, 2); c.add("b", 1.0)
        self.assertEqual(c.count(), 3)

    def test_remove_all(self):
        c = Cart(); c.add("a", 1.0, 2); c.remove("a", 2)
        self.assertNotIn("a", c.items)

    def test_subtotal(self):
        c = Cart(); c.add("a", 2.5, 2); c.add("b", 1.0)
        self.assertEqual(c.subtotal(), 6.0)


class TestDiscount(unittest.TestCase):
    def test_exact_threshold_counts(self):
        self.assertEqual(discount_for(50), 8)

    def test_below(self):
        self.assertEqual(discount_for(19.99), 0)

    def test_highest_rule_wins(self):
        self.assertEqual(discount_for(150), 20)

    def test_final(self):
        self.assertEqual(final_price(100), 80)


class TestReceipt(unittest.TestCase):
    def test_render(self):
        c = Cart(); c.add("pen", 2.0, 3)
        self.assertIn("total    6.00", render(c))
''',
}


def setup_two_bugs(root):
    _lay(root, SHOP)


def check_two_bugs(root, answer):
    if _unchanged(root, SHOP, ["test_shop.py"]):
        return False, "改了测试"
    if _text(root, "shop/receipt.py") != SHOP["shop/receipt.py"]:
        return False, "动了没 bug 的 receipt.py"
    return _ran(_run(root, "-m", "unittest", "-q", "test_shop"), "测试没全绿")


# ---- 14. 三处重复的解析函数抽成一份 ----

DUP = {
    "app/__init__.py": "",
    "app/config.py": '''def parse_kv(text):
    """a=1;b=2 这种 空段跳过，两边空白去掉 """
    out = {}
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip()
    return out


def load_config(text):
    return {k: v for k, v in parse_kv(text).items() if not k.startswith("_")}
''',
    "app/headers.py": '''def parse_kv(text):
    out = {}
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip()
    return out


def content_type(header):
    main, _, rest = header.partition(";")
    return main.strip(), parse_kv(rest)
''',
    "app/query.py": '''def parse_kv(text):
    out = {}
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip()
    return out


def page_of(query):
    return int(parse_kv(query).get("page", "1"))
''',
    "test_app.py": '''import unittest

from app.config import load_config
from app.headers import content_type
from app.query import page_of


class T(unittest.TestCase):
    def test_config(self):
        self.assertEqual(load_config("a=1; _x=2 ;b = 3"), {"a": "1", "b": "3"})

    def test_headers(self):
        self.assertEqual(content_type("text/html; charset=utf-8"), ("text/html", {"charset": "utf-8"}))

    def test_query(self):
        self.assertEqual(page_of("sort=asc;page=4"), 4)
''',
}


def setup_dedup(root):
    _lay(root, DUP)


def check_dedup(root, answer):
    if _unchanged(root, DUP, ["test_app.py"]):
        return False, "改了测试"
    total = sum(p.read_text(encoding="utf-8").count("def parse_kv(") for p in root.glob("app/*.py"))
    if total != 1:
        return False, "parse_kv 的定义现在有 %d 份（要正好 1 份）" % total
    for name in ("app/config.py", "app/headers.py", "app/query.py"):
        if "parse_kv" not in _text(root, name):
            return False, "%s 不再用 parse_kv 了" % name
    return _ran(_run(root, "-m", "unittest", "-q", "test_app"), "抽完行为变了")


# ---- 15. 一个字段贯穿三层：存储 → 服务 → 命令行 ----

TODOAPP = {
    "todoapp/__init__.py": "",
    "todoapp/storage.py": '''import json


class Storage:
    """一行一个 JSON """

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def save(self, items):
        with open(self.path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\\n")
''',
    "todoapp/service.py": '''from todoapp.storage import Storage


class Service:
    def __init__(self, path):
        self.store = Storage(path)

    def add(self, title):
        items = self.store.load()
        item = {"id": len(items) + 1, "title": title, "done": False}
        items.append(item)
        self.store.save(items)
        return item

    def done(self, item_id):
        items = self.store.load()
        for item in items:
            if item["id"] == item_id:
                item["done"] = True
        self.store.save(items)

    def list(self):
        return self.store.load()
''',
    "todoapp/cli.py": '''import argparse

from todoapp.service import Service


def main(argv=None):
    ap = argparse.ArgumentParser(prog="todo")
    ap.add_argument("--db", default="todo.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("title")
    p_done = sub.add_parser("done")
    p_done.add_argument("id", type=int)
    sub.add_parser("list")
    args = ap.parse_args(argv)
    svc = Service(args.db)
    if args.cmd == "add":
        item = svc.add(args.title)
        print("added #%d" % item["id"])
    elif args.cmd == "done":
        svc.done(args.id)
        print("ok")
    else:
        for item in svc.list():
            print("%s #%d %s" % ("x" if item["done"] else " ", item["id"], item["title"]))


if __name__ == "__main__":
    main()
''',
    "test_todo.py": '''import os
import tempfile
import unittest

from todoapp.service import Service


class T(unittest.TestCase):
    def test_add_and_done(self):
        path = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        s = Service(path)
        s.add("a"); s.add("b"); s.done(1)
        items = s.list()
        self.assertEqual([i["done"] for i in items], [True, False])
''',
}


def setup_field(root):
    _lay(root, TODOAPP)


def check_field(root, answer):
    if not _kept(root, TODOAPP, "test_todo.py"):
        return False, "改了原有的测试"
    db = str(root / "x.jsonl")
    for argv in (["-m", "todoapp.cli", "--db", db, "add", "买菜", "--priority", "high"],
                 ["-m", "todoapp.cli", "--db", db, "add", "洗车"]):
        ok, why = _ran(_run(root, *argv), "add 跑不起来")
        if not ok:
            return False, why
    listed = _run(root, "-m", "todoapp.cli", "--db", db, "list")
    ok, why = _ran(listed, "list 跑不起来")
    if not ok:
        return False, why
    if "high" not in listed.stdout:
        return False, "list 里看不到优先级"
    import json
    rows = [json.loads(l) for l in _text(root, "x.jsonl").splitlines() if l.strip()]
    if rows[0].get("priority") != "high":
        return False, "存储里没有 priority 字段：%s" % rows[0]
    if "priority" not in rows[1] or rows[1]["priority"] in (None, ""):
        return False, "没给 --priority 的条目没有默认值：%s" % rows[1]
    return _ran(_run(root, "-m", "unittest", "-q", "test_todo"), "原有测试挂了")


# ---- 16. 看日志找原因：答案是一个具体的值 ----

LOGCASE = {
    "sync/__init__.py": "",
    "sync/client.py": '''import time


class Client:
    def __init__(self, base, retries=3, timeout=5):
        self.base = base
        self.retries = retries
        self.timeout = timeout

    def fetch(self, path, transport):
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                return transport(self.base + path, self.timeout)
            except TimeoutError as exc:
                last = exc
                time.sleep(0)
        raise RuntimeError("gave up on %s after %d attempts: %s" % (path, self.retries, last))
''',
    "sync/runner.py": '''import logging

from sync.client import Client

log = logging.getLogger("sync")

PATHS = ["/users", "/orders", "/invoices", "/reports/daily", "/reports/weekly"]


def run(transport, config):
    client = Client(config["base"], retries=config.get("retries", 3), timeout=config.get("timeout", 5))
    done = 0
    for path in PATHS:
        try:
            client.fetch(path, transport)
            done += 1
            log.info("ok %s", path)
        except RuntimeError as exc:
            log.error("fail %s: %s", path, exc)
    return done
''',
    "sync.log": "\n".join(
        ["2026-08-30 02:00:01 INFO sync: start base=https://api.example.com timeout=5 retries=3"]
        + ["2026-08-30 02:00:0%d INFO sync: ok %s" % (i + 2, p) for i, p in enumerate(["/users", "/orders", "/invoices"])]
        + ["2026-08-30 02:00:11 WARNING transport: /reports/daily attempt 1 took 5.00s (limit 5)",
           "2026-08-30 02:00:16 WARNING transport: /reports/daily attempt 2 took 5.00s (limit 5)",
           "2026-08-30 02:00:21 WARNING transport: /reports/daily attempt 3 took 5.00s (limit 5)",
           "2026-08-30 02:00:21 ERROR sync: fail /reports/daily: gave up on /reports/daily after 3 attempts: read timed out",
           "2026-08-30 02:00:23 INFO sync: ok /reports/weekly",
           "2026-08-30 02:00:23 INFO sync: done 4/5"]) + "\n",
}


def setup_logcase(root):
    _lay(root, LOGCASE)


def check_logcase(root, answer):
    text = answer.replace(" ", "")
    if "/reports/daily" not in answer:
        return False, "没指出是哪个路径挂了"
    if not any(w in answer for w in ("超时", "timeout", "timed out", "timeout=5", "5 秒", "5秒")):
        return False, "没说是超时"
    if "3" not in text and "三" not in text:
        return False, "没提到重试了三次"
    return True, ""


# ---- 17. 删死代码别过头：两个长得像的函数只删没人用的那个 ----

DEAD = {
    "export/__init__.py": "",
    "export/legacy.py": '''import csv
import io


def export_v1(rows):
    """老格式：分号分隔 已经没人调了 """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def export_v2(rows):
    """新格式：逗号分隔、带表头 """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name"])
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()
''',
    "export/api.py": '''from export.legacy import export_v2


def dump(rows):
    return export_v2(rows)
''',
    "export/report.py": '''from export.api import dump


def weekly(rows):
    return "WEEKLY\\n" + dump(rows)
''',
    "test_export.py": '''import unittest

from export.report import weekly


class T(unittest.TestCase):
    def test_weekly(self):
        out = weekly([(1, "a")])
        self.assertTrue(out.startswith("WEEKLY\\nid,name"))
''',
}


def setup_dead(root):
    _lay(root, DEAD)


def check_dead(root, answer):
    if _unchanged(root, DEAD, ["test_export.py"]):
        return False, "改了测试"
    legacy = _text(root, "export/legacy.py")
    if "def export_v1" in legacy:
        return False, "export_v1 还在"
    if "def export_v2" not in legacy:
        return False, "把还在用的 export_v2 也删了"
    if "import csv" not in legacy:
        return False, "csv 还在用，import 被删了"
    for name in ("export/api.py", "export/report.py"):
        if _text(root, name) != DEAD[name]:
            return False, "动了不该动的 %s" % name
    return _ran(_run(root, "-m", "unittest", "-q", "test_export"), "删完跑不过")


# ---- 18. 改常量：三处超时都改成 60，但注释里的「30 天」不能动 ----

CONST = {
    "svc/__init__.py": "",
    "svc/settings.py": '''# 网络超时（秒）
HTTP_TIMEOUT = 30
# 数据保留 30 天，跟超时无关
RETENTION_DAYS = 30
''',
    "svc/http.py": '''from svc.settings import HTTP_TIMEOUT


def get(url, timeout=30):
    return {"url": url, "timeout": timeout or HTTP_TIMEOUT}
''',
    "svc/worker.py": '''import svc.settings as settings


def poll():
    # 轮询等的时间跟 HTTP 超时一样长
    return {"wait": 30, "limit": settings.HTTP_TIMEOUT}
''',
    "README.md": "# svc\n\n数据保留 30 天。请求超时 30 秒。\n",
    "test_svc.py": '''import unittest

from svc.http import get
from svc.worker import poll
from svc.settings import RETENTION_DAYS


class T(unittest.TestCase):
    def test_retention_untouched(self):
        self.assertEqual(RETENTION_DAYS, 30)

    def test_shapes(self):
        self.assertIn("timeout", get("u"))
        self.assertIn("wait", poll())
''',
}


def setup_const(root):
    _lay(root, CONST)


def check_const(root, answer):
    if _unchanged(root, CONST, ["test_svc.py"]):
        return False, "改了测试"
    if "HTTP_TIMEOUT = 60" not in _text(root, "svc/settings.py"):
        return False, "settings 里的超时没改"
    if "RETENTION_DAYS = 30" not in _text(root, "svc/settings.py"):
        return False, "把保留天数也改了"
    if "timeout=60" not in _text(root, "svc/http.py").replace(" ", ""):
        return False, "http.py 的默认参数没改"
    if '"wait": 60' not in _text(root, "svc/worker.py"):
        return False, "worker.py 的等待时间没改"
    readme = _text(root, "README.md")
    if "保留 30 天" not in readme:
        return False, "README 里的 30 天被改了"
    if "60 秒" not in readme:
        return False, "README 里的超时秒数没跟着改"
    return _ran(_run(root, "-m", "unittest", "-q", "test_svc"), "改完跑不过")


# ---- 19. 六个文件里一个拼错的 import，报错信息在测试输出里 ----

TYPO = {
    "pkg/__init__.py": "",
    "pkg/a.py": "from pkg.b import f_b\n\n\ndef f_a():\n    return f_b() + 1\n",
    "pkg/b.py": "from pkg.c import f_c\n\n\ndef f_b():\n    return f_c() + 1\n",
    "pkg/c.py": "from pkg.d import f_d\n\n\ndef f_c():\n    return f_d() + 1\n",
    "pkg/d.py": "import jsson\n\n\ndef f_d():\n    return len(jsson.dumps({}))\n",
    "pkg/e.py": "from pkg.a import f_a\n\n\ndef f_e():\n    return f_a() * 2\n",
    "test_pkg.py": '''import unittest

from pkg.e import f_e


class T(unittest.TestCase):
    def test_chain(self):
        self.assertEqual(f_e(), 10)
''',
}


def setup_typo(root):
    _lay(root, TYPO)


def check_typo(root, answer):
    if _unchanged(root, TYPO, ["test_pkg.py"]):
        return False, "改了测试"
    if "jsson" in _text(root, "pkg/d.py"):
        return False, "拼错的 import 还在"
    for name in ("pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/e.py"):
        if _text(root, name) != TYPO[name]:
            return False, "动了没问题的 %s" % name
    return _ran(_run(root, "-m", "unittest", "-q", "test_pkg"), "还是跑不过")


# ---- 20. 给包写 API 文档：公开函数一个不漏、私有的一个不带 ----

APIDOC = {
    "mathkit/__init__.py": "from mathkit.core import clamp, lerp, mean, median, variance\n",
    "mathkit/core.py": '''def clamp(x, lo, hi):
    """把 x 夹在 [lo, hi] 里 """
    return max(lo, min(hi, x))


def lerp(a, b, t):
    """a 到 b 的线性插值，t 在 0..1 """
    return a + (b - a) * t


def mean(xs):
    return sum(xs) / len(xs)


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def variance(xs):
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def _check(xs):
    if not xs:
        raise ValueError("empty")
''',
}


def setup_apidoc(root):
    _lay(root, APIDOC)


def check_apidoc(root, answer):
    doc = _text(root, "docs/api.md")
    if not doc:
        return False, "没生成 docs/api.md"
    for name in ("clamp", "lerp", "mean", "median", "variance"):
        if name not in doc:
            return False, "文档里漏了 %s" % name
    if "_check" in doc:
        return False, "把私有函数 _check 也写进去了"
    if _unchanged(root, APIDOC, list(APIDOC)):
        return False, "改了源码"
    return True, ""


# ---- 21. 加一个配置来源：命令行 > 环境变量 > 默认值 ----

CFG = {
    "tool/__init__.py": "",
    "tool/config.py": '''import os

DEFAULTS = {"port": 8080, "host": "127.0.0.1"}


def load():
    cfg = dict(DEFAULTS)
    if os.environ.get("TOOL_PORT"):
        cfg["port"] = int(os.environ["TOOL_PORT"])
    if os.environ.get("TOOL_HOST"):
        cfg["host"] = os.environ["TOOL_HOST"]
    return cfg
''',
    "tool/main.py": '''import argparse

from tool.config import load


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tool")
    args = ap.parse_args(argv)
    cfg = load()
    print("%s:%d" % (cfg["host"], cfg["port"]))


if __name__ == "__main__":
    main()
''',
    "test_tool.py": '''import os
import unittest

from tool.config import load


class T(unittest.TestCase):
    def test_env_overrides_default(self):
        os.environ["TOOL_PORT"] = "9"
        try:
            self.assertEqual(load()["port"], 9)
        finally:
            del os.environ["TOOL_PORT"]

    def test_default(self):
        os.environ.pop("TOOL_PORT", None)
        self.assertEqual(load()["port"], 8080)
''',
}


def setup_cfg(root):
    _lay(root, CFG)


def check_cfg(root, answer):
    import os
    if not _kept(root, CFG, "test_tool.py"):
        return False, "改了原有的测试"
    env = dict(os.environ, TOOL_PORT="7000")
    done = subprocess.run([sys.executable, "-m", "tool.main", "--port", "9000"], cwd=root,
                         capture_output=True, text=True, timeout=60, env=env)
    ok, why = _ran(done, "带 --port 跑不起来")
    if not ok:
        return False, why
    if ":9000" not in done.stdout:
        return False, "命令行没压过环境变量：%r" % done.stdout.strip()
    done = subprocess.run([sys.executable, "-m", "tool.main"], cwd=root,
                         capture_output=True, text=True, timeout=60, env=env)
    if ":7000" not in done.stdout:
        return False, "不带参数时环境变量没生效：%r" % done.stdout.strip()
    return _ran(_run(root, "-m", "unittest", "-q", "test_tool"), "原有测试挂了")


# ---- 22. 提 PR：远端是本目录下的 bare 仓库，流程要照 CONTRIBUTING 走 ----
# 判据全是 git 里能查到的事实：main 没动、分支名、提交信息、远端有没有那条分支、
# PR.md 的三节 没法在本地开真 PR，所以描述写进文件；gh pr create 那一步不在判题范围里

PR_REPO = {
    "money/__init__.py": "",
    "money/fmt.py": """def money(cents):
    \"\"\"分 -> '1,234.56' \"\"\"
    if not cents:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    whole, frac = divmod(cents, 100)
    return "%s%s.%02d" % (sign, format(whole, ","), frac)
""",
    "test_money.py": """import unittest

from money.fmt import money


class T(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(money(123456), "1,234.56")

    def test_negative(self):
        self.assertEqual(money(-5), "-0.05")

    def test_zero(self):
        self.assertEqual(money(0), "0.00")


if __name__ == "__main__":
    unittest.main()
""",
    "CONTRIBUTING.md": """# 参与贡献

- 不要直接在 main 上提交 分支名用 `fix/<issue 号>-<简述>`，比如 `fix/7-empty-input`
- 提交信息第一行用 `fix: 一句话 (#issue 号)`，其余类型用 `feat:` / `docs:`
- 提交前跑 `python -m unittest test_money`，测试文件本身不要改
- PR 描述照 `.github/PULL_REQUEST_TEMPLATE.md` 三节写，issue 号写在「为什么」那节里
- push 到 origin 之后再开 PR
""",
    ".github/PULL_REQUEST_TEMPLATE.md": """## 改了什么

## 为什么

## 怎么验证
""",
}


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)


def setup_pr(root):
    _lay(root, PR_REPO)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "dev")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat: 金额格式化 (#1)")
    _git(root, "commit", "-q", "--allow-empty", "-m", "docs: 贡献指南 (#3)")
    _git(root, "init", "-q", "--bare", "-b", "main", str(root / "upstream.git"))
    _git(root, "remote", "add", "origin", str(root / "upstream.git"))
    _git(root, "push", "-q", "origin", "main")
    # 铺完再把 upstream.git 从工作树里排除掉，不然它会被当成未跟踪文件
    (root / ".git" / "info" / "exclude").write_text("upstream.git/\nPR.md\n", encoding="utf-8")


def check_pr(root, answer):
    if _text(root, "test_money.py") != PR_REPO["test_money.py"]:
        return False, "改了测试文件"
    base = _git(root, "rev-parse", "main").stdout.strip()
    remote_main = _git(root, "--git-dir=upstream.git", "rev-parse", "main").stdout.strip()
    if _git(root, "log", "-1", "--format=%s", "main").stdout.strip() != "docs: 贡献指南 (#3)":
        return False, "直接在 main 上提交了"
    if remote_main != base:
        return False, "把 main push 上去了"
    branch = _git(root, "branch", "--show-current").stdout.strip()
    if not branch.startswith("fix/12"):
        return False, "分支名不合规：%r" % branch
    subject = _git(root, "log", "-1", "--format=%s").stdout.strip()
    if not (subject.startswith("fix:") and "#12" in subject):
        return False, "提交信息不合规：%r" % subject
    if _git(root, "status", "--porcelain", "-uno", "--", "money").stdout.strip():
        return False, "money/ 下还有没提交的改动"
    remote = _git(root, "--git-dir=upstream.git", "branch", "--list", branch).stdout.strip()
    if not remote:
        return False, "分支没 push 到 origin"
    ok, why = _ran(_run(root, "-m", "unittest", "-q", "test_money"), "测试没过")
    if not ok:
        return False, why
    body = _text(root, "PR.md")
    for head in ("## 改了什么", "## 为什么", "## 怎么验证"):
        if head not in body:
            return False, "PR.md 缺「%s」" % head[3:]
    if "#12" not in body.split("## 为什么", 1)[1].split("## 怎么验证", 1)[0]:
        return False, "PR.md 的「为什么」里没带 issue 号"
    return True, ""


MEDIUM = [
    ("加 --json", setup_json_flag,
     "给 cli.py 加一个 --json 参数，输出 JSON 而不是文本 放在现有的输出层里，别在 cli 里直接 print dict",
     check_json_flag),
    ("两个 bug", setup_two_bugs,
     "test_shop.py 有几条挂了，修好让它全绿 不许改测试，没 bug 的文件别动", check_two_bugs),
    ("抽重复", setup_dedup,
     "app/ 里 parse_kv 抄了三份，抽成一份，其余地方 import 它 行为不能变", check_dedup),
    ("字段贯穿三层", setup_field,
     "给待办加一个 priority 字段：add 子命令接 --priority（默认 normal），存进文件，list 里显示出来",
     check_field),
    ("看日志", setup_logcase,
     "昨晚的 sync.log 里哪个路径失败了？为什么？重试了几次？先读日志再看代码，用一句话回答",
     check_logcase),
    ("删死代码", setup_dead,
     "export/legacy.py 里有个已经没人调的导出函数，删掉它 别动还在用的东西", check_dead),
    ("改常量", setup_const,
     "把 HTTP 超时从 30 秒改成 60 秒，所有地方都要一致，README 也要改 数据保留 30 天那个跟超时无关，别动",
     check_const),
    ("拼错的 import", setup_typo,
     "python -m unittest test_pkg 跑不过，修好", check_typo),
    ("写 API 文档", setup_apidoc,
     "给 mathkit 写一份 docs/api.md，列出所有公开函数、签名和一句说明 私有的不要", check_apidoc),
    ("三层配置", setup_cfg,
     "给 tool.main 加 --port 和 --host 参数 优先级：命令行 > 环境变量 > 默认值 原有的环境变量逻辑保留",
     check_cfg),
    ("提 PR", setup_pr,
     "issue #12：money(0) 返回空串，应该是 '0.00' 按 CONTRIBUTING.md 的流程修好并准备 PR："
     "远端 origin 指向本目录下的 upstream.git（当 GitHub 用，没法真开 PR），"
     "所以 PR 描述按模板写进 PR.md 就行，其余步骤照常",
     check_pr),
]
