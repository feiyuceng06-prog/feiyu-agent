"""`@路径`：用户在正文里指的文件，发出去之前就读好、附在这句话后面

为什么值得单独做一个模块：`read_file` 是日志里第二高频的工具（三十天 120 次），
而**用户自己已经知道要看哪个文件**的那一部分，本来不该再花一整轮往返去读
（本仓库一轮的中位数 4.1 秒，还要把整段历史重发一遍）。0.65.1 记下过同一件事：
一次白跑的工具调用比任何毫秒级优化都贵。

三条边界，一条都不能松：

- **只认真的存在的路径。** 判据是文件系统，不是正则。`@property`、`@media`、
  `a@b.com`、`@2x.png` 这些在仓库里落不到文件上，于是原样留在正文里当普通文字。
  想用正则分辨"这看着像不像路径"是分辨不出来的，别去试。
- **越界的不认。** 解析完必须落在工作区里，跟 `Toolbox._resolve` 是同一道闸，
  `@../../etc/passwd` 当普通文字。
- **贴过去的要记进 `files.Watcher`。** 不记的话模型下一步 `edit_file` 会被
  「你还没读过这个文件」挡回去 —— 它确实看过了，只是没经过工具。

`complete()` 也在这儿：终端的输入框和桌面端的编辑器共用同一份候选，
不各写一套（0.65.1 那条「这条路终端有的东西它有吗」反过来的一面）。
"""

import os
from pathlib import Path

from . import files, images
from .tools import read_document

# 一个文件最多贴多少行 比 read_file 的 READ_CAP 小得多：`@` 是随手打的，
# 一句话里指三四个文件很常见，每份都整贴等于拿上下文换排版
MAX_LINES = 400
# 一次输入里所有 @ 加起来的字符上限 到顶之后剩下的只报路径，让它自己去读
MAX_CHARS = 20_000
# 分到手不足这么多字就干脆不贴 半截东西说明不了任何事，白占地方
MIN_SHARE = 200
# `@` 一个目录时最多列几项
MAX_ENTRIES = 80
# 补全一次最多给几个候选
COMPLETE_CAP = 50

# `@` 前面只能是行首或者这几个开括号/空白，`a@b.com` 里的那个不算
_OPENERS = " \t\n\r([{（「【"
# 路径到这儿就算完了：句读、闭括号、引号 空白也算，只是它已经被 _tokens 切掉了
# `.` 在里面看着奇怪，但 `@feiyu/ui.py.` 这种句号结尾要靠它
_BREAK = "。，、；：！？）】》」』,.;:!?)]}>'\" \t\n\r"
# 一段 mention 原文最多这么长 再长的多半是粘进来的一整行，没必要挨个位置去试
MAX_TOKEN = 300


def _candidates(raw: str) -> list[str]:
    """一段 `@` 后面的原文，可能指的是哪几个路径（长的排前面）

    **不能只剥结尾的标点。** 中文里没人打空格：`@feiyu/ui.py，第 200 行`
    一路吃到"行"字，结尾那个字不是标点，剥不动。所以改成在原文里找每一个
    断点、按长到短挨个试，第一个真的存在的就是它。

    断点这条件同时挡住了误判：`@property` 里假如仓库刚好有个文件叫 `p`，
    "p" 后面跟的是 `r` 不是断点，于是不算数。
    """
    if len(raw) > 1 and raw[0] == '"' == raw[-1]:
        return [raw[1:-1]]
    out = [raw]
    out.extend(raw[:i] for i in range(len(raw) - 1, 0, -1) if raw[i] in _BREAK)
    return out


def _tokens(text: str):
    """扫出所有 `@…`，返回 (在正文里的原文, 起止位置)

    手写扫描而不是正则：要判"@ 前面那个字符"和"引号里可以有空格"两件事，
    写成正则之后没人看得懂它到底认不认某个形状。
    """
    i = 0
    while True:
        i = text.find("@", i)
        if i < 0:
            return
        if i > 0 and text[i - 1] not in _OPENERS:
            i += 1
            continue
        rest = text[i + 1:]
        if rest.startswith('"'):
            end = rest.find('"', 1)
            raw = rest[: end + 1] if end > 0 else ""
        else:
            raw = rest.split(" ")[0].split("\t")[0].split("\n")[0][:MAX_TOKEN]
        if not raw:
            i += 1
            continue
        yield raw, i, i + 1 + len(raw)
        i += 1 + len(raw)


def _inside(root: Path, shown: str) -> Path | None:
    """解析到工作区里的一个真实路径，否则 None

    越界靠"解析完必须落在工作区里"这一条挡，跟 `Toolbox._resolve` 同一道闸。
    另外**结尾是 `.` 或 `..` 的一律不认**：按断点剥前缀会剥出这种残片，
    而 `@../../etc/passwd` 剥到只剩 `.` 的时候，那个 `.` 解析出来正好是
    工作区根目录 —— 越界的写法反而"匹配上了"。
    """
    if not shown:
        return None
    if shown.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] in (".", ".."):
        return None
    try:
        target = (root / shown).resolve()
    except (OSError, ValueError):
        return None
    if target != root and root not in target.parents:
        return None
    try:
        return target if target.exists() else None
    except OSError:
        return None


def find(text: str, root: Path) -> list[tuple[str, Path]]:
    """按出现顺序返回 (正文里的写法, 解析到的路径)，同一个文件只算一次"""
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for raw, _start, _end in _tokens(text):
        for shown in _candidates(raw):
            target = _inside(root, shown)
            if target is None:
                continue
            if target not in seen:
                seen.add(target)
                out.append((shown, target))
            break
    return out


def _listing(target: Path, budget: int) -> tuple[str, str]:
    """**这一条也要吃预算。** 只按 MAX_ENTRIES 数项数是不够的：名字最长能到 255 字，
    八十项就是两万字 —— 正好是 MAX_CHARS 的全部。原来它一个字都不数，于是
    `@某个目录 @某个文件` 里那个文件分到的是**负数预算**，`_numbered` 的
    `first[:budget]` 变成从末尾切，报出来的话是「只截了前 -509 个字符」，
    而实际贴进去的是整整五万字（那道 20000 的闸等于不存在）。
    """
    names = []
    used = 0
    try:
        entries = sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name))
    except OSError as exc:
        return "（列不了这个目录：%s）" % exc, "列不了"
    full = True
    for entry in entries:
        if entry.is_dir():
            if entry.name in files.IGNORE_DIRS:
                continue
            name = entry.name + "/"
        else:
            name = entry.name
        if used + len(name) + 1 > budget:
            full = False
            break
        names.append(name)
        used += len(name) + 1
        if len(names) >= MAX_ENTRIES:
            full = False
            break
    if not names:
        return ("（空目录）", "空目录") if full else ("（这个目录列不下，用 list_files 看 ）", "列不下")
    body = "\n".join(names)
    if not full:
        # 截断要说出来，否则"这个目录里有哪些文件"的结论直接是错的
        body += "\n（只列了前 %d 项，用 list_files 看全部 ）" % len(names)
    return body, "%d 项" % len(names)


def _numbered(raw: bytes, budget: int) -> tuple[str, str]:
    """跟 read_file 一样的「行号 + tab + 正文」，超了就按行切"""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return "（空文件）", "空文件"
    kept: list[str] = []
    used = 0
    for i, line in enumerate(lines[:MAX_LINES]):
        one = "%d\t%s" % (i + 1, line)
        if used + len(one) + 1 > budget:
            break
        kept.append(one)
        used += len(one) + 1
    if not kept:
        # 一行都放不下 = 这是压缩过的 js/json 那种一整行几十万字的文件。
        # 按行切会切出个空的，模型只看到"共 1 行、给你 0 行"——等于什么都没说。
        # 硬切一刀，说清切了多少
        first = "1\t" + lines[0]
        return (("（第 1 行太长，只截了前 %d 个字符 要看全的用 read_file ）\n%s"
                 % (budget, first[:budget])), "截断")
    rest = len(lines) - len(kept)
    body = "\n".join(kept)
    if rest > 0:
        # 说清怎么接着读 只报行数的话模型会以为自己看完了整份，跟 read_file 同理
        head = ("（共 %d 行，这里只有前 %d 行；后面 %d 行用 "
                "read_file 加 offset=%d 接着看 ）" % (
                    len(lines), len(kept), rest, len(kept) + 1))
        return head + "\n" + body, "%d/%d 行" % (len(kept), len(lines))
    return body, "%d 行" % len(lines)


def _one(shown: str, target: Path, budget: int, watcher) -> tuple[str, str]:
    """一个 `@` 的正文和一句概要 概要是给人看的，正文是给模型看的"""
    if budget < MIN_SHARE:
        # 前面几个把预算吃光了 跟 expand 里"贴不下"那条同一个说法：
        # 半截东西说明不了任何事，而**预算绝不能变成负数**——负数会一路传到
        # `first[:budget]` 那种切片上，切出来的东西比不贴还糟
        return ("（这次没贴内容，一句话里指的东西太多了 要看就用 read_file 读它 ）", "未贴")
    if target.is_dir():
        return _listing(target, budget)
    odd = files.odd_kind(target)
    if odd:
        # 打开一个没有另一端的 FIFO 会一直阻塞 —— 而这条路是在**发消息之前**跑的，
        # 挂住的表现是"按了回车什么都没发生" 见 files.odd_kind
        return "（这是%s，不是普通文件，读它会把整个进程挂住 ）" % odd, odd
    if images.kind(target):
        # 图片不在这儿贴：能不能看图是按模型配置来的（`vision`），那道判断在
        # `_read_image` 里，重写一遍迟早会跟它漂开 路径已经确认了，让它自己去读
        return "（这是图片，路径没问题 要看内容就用 read_file 读它 ）", "图片"
    try:
        extracted = read_document(target)
        if extracted is not None:
            text = extracted.strip()[:budget]
            kind = target.suffix.lower().lstrip(".")
            if watcher is not None:
                watcher.record(target)
            return ("（从 %s 里抽出的文本，排版和图片没有保留）\n\n%s" % (
                kind, text or "（没抽到文字）"), kind)
        raw = target.read_bytes()
    except OSError as exc:
        return "（读不了：%s: %s）" % (type(exc).__name__, exc), "读不了"
    if b"\0" in raw[:8192]:
        return "（这是二进制文件，读不了 ）", "二进制"
    if watcher is not None:
        watcher.record(target, raw)
    return _numbered(raw, budget)


def expand(text: str, root: Path, watcher=None) -> tuple[str, list[str]]:
    """返回 (接在这句话后面的一段, 附上了哪些——给人看的短句)

    一个都没解析出来时返回 ("", [])：没有 `@` 的那条路一个字节都不多花。
    """
    hits = find(text, root)
    if not hits:
        return "", []
    blocks: list[str] = []
    said: list[str] = []
    left = MAX_CHARS
    # 分到手不足 MIN_SHARE 就谁都别贴了，所以先算清楚贴得下几个 —— 剩下的
    # 只报路径。**按顺序砍后面的**：边贴边判的话，前几个因为份小被跳过，
    # 分母跟着变小，后几个反而贴上了 —— 用户先说的那个没了，最后说的那个在
    keep = min(len(hits), max(1, MAX_CHARS // MIN_SHARE))
    for i, (shown, target) in enumerate(hits):
        if i >= keep:
            # 贴不下也要把路径报出去：它至少不用再猜一遍名字
            blocks.append("----- %s -----\n（这次没贴内容，一句话里指的东西太多了 "
                          "要看就用 read_file 读它 ）" % shown)
            said.append("%s（未贴）" % shown)
            continue
        # **按份分，不是先到先得。** 一句话里指三个文件是常事，让第一个把预算
        # 吃光的话后两个只剩零头甚至一个字没有 —— 而用户是把这三个一起指出来的。
        # 小的用不完，余下的自然滚给后面（除号里的分母跟着 i 走）。
        body, brief = _one(shown, target, max(0, left) // (keep - i), watcher)
        left -= len(body)
        blocks.append("----- %s -----\n%s" % (shown, body))
        said.append("%s（%s）" % (shown, brief))
    note = ("\n\n（下面是用户用 @ 指的东西，已经替你读好了，"
            "不用再调 read_file 读同一份 ）\n\n" + "\n\n".join(blocks))
    return note, said


def complete(root: Path, prefix: str, cap: int = COMPLETE_CAP) -> list[str]:
    """`@` 后面已经打了 prefix，能补上哪些（相对工作区，目录带 `/`）

    **一次只扫一层。** 给整棵树建索引再模糊匹配当然更聪明，但那要么每按一个键
    走一遍 os.walk（大仓库上几百毫秒），要么维护一份会过期的缓存。按目录
    `scandir` 永远是常数时间，手感也跟 shell 补全一样。
    """
    head, sep, tail = prefix.rpartition("/")
    try:
        here = (root / head).resolve() if sep else root
    except (OSError, ValueError):
        return []
    if here != root and root not in here.parents:
        return []
    try:
        entries = sorted(os.scandir(here), key=lambda e: (not e.is_dir(), e.name))
    except OSError:
        return []
    want = tail.lower()
    out = []
    for entry in entries:
        name = entry.name
        # 点开头的要自己先打个点才出来，跟 shell 一样
        if name.startswith(".") and not tail.startswith("."):
            continue
        is_dir = entry.is_dir()
        if is_dir and name in files.IGNORE_DIRS:
            continue
        if not name.lower().startswith(want):
            continue
        out.append(head + sep + name + ("/" if is_dir else ""))
        if len(out) >= cap:
            break
    return out


def typing(text: str) -> str | None:
    """光标停在一个还没打完的 `@…` 上时，返回 @ 后面已经打了什么，否则 None

    补全器和前端都要判这一件事，判法必须一致 —— 判错的表现是「打到一半
    菜单突然不见了」，两边各写一套的话会不一样地不见。
    """
    at = text.rfind("@")
    if at < 0:
        return None
    if at > 0 and text[at - 1] not in _OPENERS:
        return None
    tail = text[at + 1:]
    if any(ch in tail for ch in " \t\n"):
        return None
    return tail
