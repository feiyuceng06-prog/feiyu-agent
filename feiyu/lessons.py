"""同一件事栽第二次时当场提醒换做法；缓过来之后提一句值不值得记下来

边界是 0.30.0 时跟用户定的：**只到「记教训」为止** —— 提醒它换做法、引导它用
remember 记一句，不碰自己的代码和系统提示 记下来的东西走 remember 那条老路：
default 模式下要人点头，进的是这个目录的长期记忆

**为什么要在运行时说，不往系统提示里加一句「别重复犯错」**：0.16.0 / 0.30.0 / 0.46.0
三次证明了光加提示词没用，真正见效的是「它正在用笨办法的当口，在工具结果里说一句」

「同一件事」认得很窄，宁可漏认不可错认 —— 错认一次就是在它好好干活的时候
叫它停下来：

- bash 是**同一条命令、同样的输出**：改完代码再跑测试、挂的换了一批，那是在往前走，
  不算；挂的还是那几条、报的还是那句，才算
- 别的工具是**同一个工具、同一个对象、同样的报错**：两个不同的文件都不存在不算
- 数字一律抹掉再比：耗时、临时目录编号、行号每次都不一样，不抹的话永远认不出来
"""

import hashlib
import os
import re

from . import permissions

REPEAT_AT = 2       # 同样的失败第几次开始说
STOP_AT = 3         # 第几次叫它停下来
TAIL = 600          # bash 比输出的最后这么多字：报错几乎都在末尾

_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACE.sub(" ", _DIGITS.sub("#", text or "")).strip()


def _program(command: str) -> str:
    """一条命令是哪个程序跑的 `pytest x` 和 `.venv/bin/pytest x` 是同一个"""
    parts = permissions.segments(command or "")
    words = parts[0].split() if parts else []
    return os.path.basename(words[0]) if words else ""


class Stumbles:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.counts: dict[str, int] = {}
        # 栽过两次以上、还没缓过来的：(工具, 用来认"缓过来了"的对象) -> 栽了几次
        self.open: dict[tuple[str, str], int] = {}
        self.nudged = False     # 「值不值得记下来」一段对话只说一次

    @staticmethod
    def _key(name: str, args: dict, content: str) -> str:
        if name == "bash":
            raw = _norm(str(args.get("command") or "")) + "\0" + _norm(content)[-TAIL:]
        else:
            raw = name + "\0" + permissions.subject(name, args) + "\0" + _norm(content)[:TAIL]
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()

    @staticmethod
    def _spot(name: str, args: dict) -> tuple[str, str]:
        """缓过来 = 同一个工具在同一个对象上成了；bash 看的是同一个程序"""
        if name == "bash":
            return name, _program(str(args.get("command") or ""))
        return name, permissions.subject(name, args)

    def after(self, name: str, args, is_error: bool, content: str, *,
              can_remember: bool) -> tuple[str, int]:
        """看一眼这次调用的结果 返回 (该追加在结果后面的话, 这是第几次同样的失败)
        第二个数只给 trace 用，0 就是这次没有值得说的"""
        args = args if isinstance(args, dict) else {}
        spot = self._spot(name, args)
        if not is_error:
            times = self.open.pop(spot, 0)
            if times and can_remember and not self.nudged:
                self.nudged = True
                return ("\n\n（刚才这一步同样的错栽了 %d 次才过去 如果原因是这个项目特有的 —— "
                        "命令得带什么参数、要先做什么、东西其实在哪 —— 用 remember 记一句，"
                        "以后在这个目录里就不会再踩；通用常识不用记 ）" % times), 0
            return "", 0
        key = self._key(name, args, content)
        n = self.counts[key] = self.counts.get(key, 0) + 1
        if n >= REPEAT_AT:
            self.open[spot] = n
        if n == REPEAT_AT:
            return ("\n\n⚠ 这一步用同样的方式失败两次了，报的是同一个错 别再原样重试 —— "
                    "先弄清楚为什么：看完整的报错、重读一遍相关的文件，或者换一种做法 "), n
        if n == STOP_AT:
            return ("\n\n⚠ 第三次同样的失败 这条路先别走了：把卡在哪、试过什么告诉用户"
                    "（可以用 ask_user 问他），或者换一个完全不同的方向 "), n
        return "", n
