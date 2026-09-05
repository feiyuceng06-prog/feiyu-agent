"""结构化事件：让 ui 除了画到终端，还能把同一件事报给别的前端

`feiyu/ui.py` 是唯一给人看的输出层（见 CLAUDE.md 的分层原则），所以「换一个前端」
的接缝就在这儿，而不是去改 agent.py 里那三十处 `print(ui.xxx(...))`。

装上 sink 之后，ui 的那几个函数照旧返回 ANSI 字符串（终端那条路一个字没变），
同时把同一件事按结构报一份出来 网页端拿到的是 `{"kind": "tool_start",
"name": "edit_file", ...}` 而不是画好的字符画——**画什么样是前端的事**，
把 ANSI 发给浏览器再解析回来是最糟的一种做法。

没装 sink 时 `emit()` 是一次字典查表加一次 falsy 判断，终端跑的就是这条路。
"""

_sink = None


def install(fn) -> None:
    """装一个 (kind, payload) -> None 的回调 装第二个会顶掉第一个 """
    global _sink
    _sink = fn


def clear() -> None:
    global _sink
    _sink = None


def active() -> bool:
    return _sink is not None


class paused:
    """这段里 ui 的输出不报事件

    斜杠命令的那些函数（`__main__._mode` 之类）内部就是拿 `ui.notice()` 拼出来的，
    不挡的话一条命令会报两遍：内部那次一条、调用方把返回值发出去又一条
    """

    def __enter__(self):
        global _sink
        self._saved, _sink = _sink, None
        return self

    def __exit__(self, *exc):
        global _sink
        _sink = self._saved
        return False


def emit(kind: str, **payload) -> None:
    """报一件事 **绝不能因为前端出问题把 agent 弄崩**——理由同 trace.write """
    if _sink is None:
        return
    try:
        _sink(kind, payload)
    except Exception:
        pass
