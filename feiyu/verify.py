"""从测试 / 编译输出里抠出要点：哪个用例挂了、断言是什么、哪一行报错

verify 失败时原来是把几千字原文整个塞回去，模型得自己从堆栈里找
这里只认几种最常见的格式，认不出就返回空，调用方照旧给原文
"""

import re

MAX_ITEMS = 12

# 一行一条的"结论行"：谁挂了 + 为什么
_PATTERNS = [
    # unittest: FAIL: test_a (mod.T.test_a) / ERROR: test_b (...)
    re.compile(r"^((?:FAIL|ERROR): \S.*)$"),
    # pytest 短总结：FAILED tests/x.py::T::test_a - AssertionError: 1 != 2
    re.compile(r"^((?:FAILED|ERROR) \S+::\S+.*)$"),
    # jest / vitest：● Suite › test name
    re.compile(r"^\s*● (.+)$"),
    # gcc / clang / go / tsc：file:line:col: error: msg  或  file(line,col): error TS…
    re.compile(r"^(\S+?:\d+(?::\d+)?: (?:fatal )?error: .+)$"),
    re.compile(r"^(\S+\(\d+,\d+\): error \S+: .+)$"),
    re.compile(r"^(\.?/?\S+\.go:\d+:\d+: .+)$"),
    # rustc / cargo：error[E0425]: … 然后下一行 --> src/main.rs:2:5
    re.compile(r"^(error(?:\[E\d+\])?: .+)$"),
    # 非 pytest 的 Python 异常收尾行：AssertionError: 1 != 2 / ValueError: boom
    re.compile(r"^((?:[A-Z]\w*Error|AssertionError|Exception|[A-Z]\w*Exception)(?:: .*)?)$"),
    # pytest 的断言细节行
    re.compile(r"^(E\s{2,}.+)$"),
]
_RUST_WHERE = re.compile(r"^\s*--> (\S+:\d+:\d+)")
# 最后那行总结：Ran 3 tests … FAILED (failures=1) / 2 failed, 1 passed / Tests: 1 failed
_TOTALS = re.compile(
    r"^(FAILED \(.*\)|=+ .*(?:failed|error).* =+|Tests:\s+.*failed.*|"
    r"\d+ failing|error: could not compile.*)$")


def digest(output: str) -> list[str]:
    """返回要点行（去重、保序、最多 MAX_ITEMS 条）认不出格式就是空列表 """
    found: list[str] = []
    seen = set()

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            found.append(text)

    lines = output.splitlines()
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        for rx in _PATTERNS:
            hit = rx.match(line)
            if hit:
                text = hit.group(1)
                # rustc 的位置在下一行，拼到一起才知道在哪
                if text.startswith("error") and i + 1 < len(lines):
                    where = _RUST_WHERE.match(lines[i + 1])
                    if where:
                        text += "  @ " + where.group(1)
                add(text)
                break
    if not found:
        return []
    total = next((l.strip() for l in reversed(lines) if _TOTALS.match(l.strip())), "")
    if len(found) > MAX_ITEMS:
        found = found[:MAX_ITEMS] + ["…还有 %d 条" % (len(found) - MAX_ITEMS)]
    if total:
        found.append(total)
    return found
