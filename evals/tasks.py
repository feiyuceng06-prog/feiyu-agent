"""一组有客观判据的小任务 每题：铺现场 → 交任务 → 机器判对错

判据全是确定性的（文件内容、跑一遍的退出码、答案里有没有那个数），
**不让另一个模型来打分** —— 打分模型自己就会波动，那样测的是两个模型的乘积

铺现场的文件内容一律写成三引号常量，不要在 write_text 里拼转义 ——
第一版就是这么写的，`\\n` 多写了一个反斜杠，十道题里七道铺出来的是语法错误的文件
"""

import subprocess
import sys
from pathlib import Path


def _run(cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60)


def _ran(done, label: str):
    """判题失败时要带上真实报错 —— 只报「跑不起来」的话没法知道是它错还是判据错 """
    if done.returncode == 0:
        return True, ""
    tail = (done.stderr or done.stdout or "").strip().splitlines()
    return False, "%s：%s" % (label, tail[-1][:70] if tail else "退出码 %d" % done.returncode)


def _text(root: Path, name: str) -> str:
    try:
        return (root / name).read_text(encoding="utf-8")
    except OSError:
        return ""


CALC = '''def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b
'''

BUGGY = '''def biggest(nums):
    """返回最大的数 """
    best = nums[0]
    for i in range(1, len(nums) - 1):
        if nums[i] > best:
            best = nums[i]
    return best
'''

BUGGY_TEST = '''import unittest

from lib import biggest


class T(unittest.TestCase):
    def test_末尾最大(self):
        self.assertEqual(biggest([1, 5, 9]), 9)

    def test_开头最大(self):
        self.assertEqual(biggest([9, 5, 1]), 9)

    def test_单个(self):
        self.assertEqual(biggest([7]), 7)
'''

SLUG = '''import re


def slugify(text):
    """把标题变成网址里那一段 """
    out = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return out.strip("-")
'''

CONF = '''SETTINGS = {
    "host": "0.0.0.0",
    "port": 8080,
    "workers": 4,
}

# 每个连接的接收缓冲区，单位字节
RECV_BUFFER = 65536
MAX_HEADERS = 100
'''

NOISE = '''RECV_BUFFER = 1024   # 这是另一个模块的，别拿错
'''

CORE = '''def fetch_data(url):
    return url
'''

APP = '''from core import fetch_data

print(fetch_data("x"))
'''

CLI = '''from core import fetch_data


def main():
    return fetch_data("y")
'''

SCOPE_A = '''VERSION = "1.0"
'''

SCOPE_B = '''VERSION = "1.0"   # 这个别动
'''

SCOPE_DOC = '''# 项目

版本 1.0
'''

CIRCLE = '''def area(r):
    return 3.14 * r * r
'''

TARGET = '''def compute_checksum(data):
    return sum(data) % 251
'''

GREET = '''def greet(name):
    return "hi " + name
'''


# ---- 1. 改名 ----

def setup_rename(root):
    (root / "calc.py").write_text(CALC, encoding="utf-8")


def check_rename(root, answer):
    src = _text(root, "calc.py")
    if "def sub" in src:
        return False, "sub 还在"
    if "def minus" not in src:
        return False, "没有 minus"
    if "def add" not in src or "def mul" not in src:
        return False, "把别的函数弄丢了"
    return True, ""


# ---- 2. 修 bug（有现成的测试盯着）----

def setup_bug(root):
    (root / "lib.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_lib.py").write_text(BUGGY_TEST, encoding="utf-8")


def check_bug(root, answer):
    done = _run(root, "-m", "unittest", "discover", "-s", ".")
    if done.returncode != 0:
        return _ran(done, "测试还是没过")
    if "len(nums) - 1" in _text(root, "lib.py"):
        return False, "改的是测试不是代码"
    return True, ""


# ---- 3. 加测试 ----

def setup_tests(root):
    (root / "slug.py").write_text(SLUG, encoding="utf-8")


def check_tests(root, answer):
    made = list(root.glob("test*.py"))
    if not made:
        return False, "没写测试文件"
    done = _run(root, "-m", "unittest", "discover", "-s", ".")
    if done.returncode != 0:
        return _ran(done, "它自己写的测试跑不过")
    body = "\n".join(_text(root, p.name) for p in made)
    if body.count("def test") < 3:
        return False, "只写了 %d 个用例" % body.count("def test")
    return True, ""


# ---- 4. 读懂代码回答（不该改任何东西）----

def setup_read(root):
    (root / "conf.py").write_text(CONF, encoding="utf-8")
    (root / "noise.py").write_text(NOISE, encoding="utf-8")


def check_read(root, answer):
    if "65536" not in answer:
        return False, "没答出 65536"
    if _text(root, "conf.py") != CONF:
        return False, "只是问问题，它却改了文件"
    return True, ""


# ---- 5. 跨文件改名 ----

def setup_cross(root):
    (root / "core.py").write_text(CORE, encoding="utf-8")
    (root / "app.py").write_text(APP, encoding="utf-8")
    (root / "cli.py").write_text(CLI, encoding="utf-8")


def check_cross(root, answer):
    for name in ("core.py", "app.py", "cli.py"):
        src = _text(root, name)
        if "fetch_data" in src:
            return False, "%s 里还有旧名字" % name
        if "load_data" not in src:
            return False, "%s 没改成新名字" % name
    return _ran(_run(root, "-c", "import cli; assert cli.main() == 'y'"), "改完跑不起来")


# ---- 6. 别动不该动的 ----

def setup_scope(root):
    (root / "a.py").write_text(SCOPE_A, encoding="utf-8")
    (root / "b.py").write_text(SCOPE_B, encoding="utf-8")
    (root / "README.md").write_text(SCOPE_DOC, encoding="utf-8")


def check_scope(root, answer):
    if "2.0" not in _text(root, "a.py"):
        return False, "a.py 没改"
    if _text(root, "b.py") != SCOPE_B:
        return False, "动了 b.py"
    if _text(root, "README.md") != SCOPE_DOC:
        return False, "顺手改了 README"
    return True, ""


# ---- 7. 改完自己验证 ----

def setup_verify(root):
    (root / "m.py").write_text(CIRCLE, encoding="utf-8")


def check_verify(root, answer):
    return _ran(_run(root, "-c", "import m; assert abs(m.perimeter(2) - 12.56) < 0.01"),
                "perimeter 不对或者不存在")


# ---- 8. 在一堆文件里找定义 ----

def setup_find(root):
    for i in range(12):
        (root / ("mod%02d.py" % i)).write_text(
            "def helper_%d():\n    pass\n" % i, encoding="utf-8")
    (root / "deep").mkdir(exist_ok=True)
    (root / "deep" / "target.py").write_text(TARGET, encoding="utf-8")


def check_find(root, answer):
    if "target.py" not in answer:
        return False, "没说出在哪个文件"
    if "251" not in answer:
        return False, "没说出它怎么算的"
    return True, ""


# ---- 9. 三件事一起 ----

def setup_multi(root):
    (root / "s.py").write_text(GREET, encoding="utf-8")


def check_multi(root, answer):
    src = _text(root, "s.py")
    if '"""' not in src and "'''" not in src:
        return False, "没加 docstring"
    if "farewell" not in src:
        return False, "没加 farewell"
    return _ran(_run(root, "-c",
                     "import s; assert s.greet('a') == 'hi a'; assert 'a' in s.farewell('a')"),
                "跑不起来或行为变了")


# ---- 10. 文件不存在时别硬编 ----

def setup_missing(root):
    (root / "real.py").write_text("x = 1\n", encoding="utf-8")


def check_missing(root, answer):
    if (root / "nowhere.py").exists():
        return False, "把不存在的文件建出来了"
    hints = ("不存在", "没有", "找不到", "没找到", "未找到")
    if not any(h in answer for h in hints):
        return False, "没说清楚这个文件不存在"
    return True, ""


# ---- 11. 长任务：跨六个文件改名，还要动测试和文档 ----
# 日志里出事的全是三四十轮的长任务，前十题几轮就完，测不到那种情况
# 这题失败时一眼能看出漏了哪个文件；配合 run.py 记的上下文大小，能看出历史瘦身管不管用

LONG_MODULES = {
    "store.py": '''def fetch_record(key):
    return {"key": key}
''',
    "api.py": '''from store import fetch_record


def handle(key):
    return fetch_record(key)["key"]
''',
    "cli.py": '''from store import fetch_record


def main(key):
    print(fetch_record(key))
''',
    "report.py": '''from store import fetch_record


def summarize(keys):
    return [fetch_record(k)["key"] for k in keys]
''',
    "cache.py": '''import store


def cached(key):
    return store.fetch_record(key)
''',
    "test_all.py": '''import unittest

import api
import cache
import report
from store import fetch_record


class T(unittest.TestCase):
    def test_fetch(self):
        self.assertEqual(fetch_record("a")["key"], "a")

    def test_api(self):
        self.assertEqual(api.handle("b"), "b")

    def test_report(self):
        self.assertEqual(report.summarize(["c", "d"]), ["c", "d"])

    def test_cache(self):
        self.assertEqual(cache.cached("e")["key"], "e")
''',
    "README.md": '''# demo

调 `fetch_record(key)` 拿一条记录 
''',
}


def setup_long(root):
    for name, body in LONG_MODULES.items():
        (root / name).write_text(body, encoding="utf-8")


def check_long(root, answer):
    for name in LONG_MODULES:
        if "fetch_record" in _text(root, name):
            return False, "%s 里还有 fetch_record" % name
    if "load_record" not in _text(root, "README.md"):
        return False, "README 没改"
    return _ran(_run(root, "-m", "unittest", "test_all"), "改完测试没过")


TASKS = [
    ("改名", setup_rename, "把 calc.py 里的 sub 函数改名成 minus，别动别的函数", check_rename),
    ("修 bug", setup_bug, "test_lib.py 跑不过，修好 lib.py 让它全绿", check_bug),
    ("加测试", setup_tests,
     "给 slug.py 里的 slugify 写单元测试，用标准库 unittest，至少三个用例，"
     "写完跑一遍确认能过", check_tests),
    ("读懂后回答", setup_read,
     "conf.py 里接收缓冲区是多少字节？只回答，不要改任何文件", check_read),
    ("跨文件改名", setup_cross,
     "把 fetch_data 这个函数改名成 load_data，所有用到的地方都要改", check_cross),
    ("别动不该动的", setup_scope, "把 a.py 里的版本号改成 2.0", check_scope),
    ("改完自己验证", setup_verify,
     "给 m.py 加一个 perimeter(r) 算圆周长，用和 area 一样的 3.14，改完跑一下确认",
     check_verify),
    ("找定义", setup_find, "compute_checksum 定义在哪个文件里？它是怎么算的？", check_find),
    ("三件事", setup_multi,
     "给 s.py 做三件事：给 greet 加 docstring、加一个 farewell(name) 函数、"
     "最后跑一下确认两个函数都能用", check_multi),
    ("文件不存在", setup_missing, "把 nowhere.py 里的 foo 改成 bar", check_missing),
    ("长任务", setup_long,
     "把 fetch_record 改名成 load_record：所有用到它的模块、测试、README 都要改 "
     "每改完一个文件都跑一遍 python -m unittest test_all 确认没弄坏，最后再跑一次总结", check_long),
]
