"""历史体积管理：把已经没用的工具结果换成一行说明

每轮请求都要重发全部历史，所以历史里每留一份文件原文，后面**每一轮**都要
为它付一次钱 实测本仓库 read_file 一次 tools.py 是 16180 token，
比整套工具 schema（2246）还贵七倍——省 token 要先省在这儿

**只动 tool_result 的内容，不删消息、不碰配对**：少一条配对结果不是
"下次凑合能跑"，是整个会话废掉（0.12.0 踩过）
"""

from dataclasses import dataclass
from typing import Callable, Iterator


# 折叠过的开头都带这个记号 认它是为了幂等：同一条不会被折叠两次，
# 也不会把说明本身再压一遍
MARK = "⟨已折叠⟩"


@dataclass
class Slot:
    """历史里的一条工具结果，能就地换掉它的文本 """
    id: str
    text: str
    pos: int
    _set: Callable[[str], None]

    def replace(self, text: str) -> None:
        self._set(text)


def _into(target: dict, key: str) -> Callable[[str], None]:
    def setter(text: str) -> None:
        target[key] = text
    return setter


def slots(messages) -> Iterator[Slot]:
    """两种后端的形状都认

    anthropic 是 user 消息 content 列表里的 tool_result 块，
    openai 兼容是独立的一条 role=tool 消息
    带图的那种 content 是列表，跳过——图片另有 without_images() 管
    """
    for pos, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            body = msg.get("content")
            if isinstance(body, str):
                yield Slot(msg.get("tool_call_id", ""), body, pos, _into(msg, "content"))
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            # assistant 那条的 content 直出时是 SDK 对象，存盘回来是 dict 但
            # type 不是 tool_result，两种都会在这里被滤掉
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            body = block.get("content")
            if isinstance(body, str):
                yield Slot(block.get("tool_use_id", ""), body, pos,
                           _into(block, "content"))


def expire(messages, ids: set[str], why: str) -> int:
    """把这些调用的结果换成一句说明，返回换掉几条

    用在"同一个文件后来又读过 / 被改过"上：旧的那几份对模型已经没有价值，
    它要的是最新那版，而旧版还在每轮陪着一起发
    """
    if not ids:
        return 0
    done = 0
    for slot in slots(messages):
        if slot.id in ids and not slot.text.startswith(MARK):
            slot.replace("%s %s" % (MARK, why))
            done += 1
    return done


def trim(messages, *, keep_last: int, limit: int, keep: int) -> int:
    """把靠前的大块工具输出压成头尾，返回压掉几条

    只动最近 keep_last 条消息之前的：正在用的那几条不能碰
    报错通常在头尾，中间的堆栈没那么重要——跟 agent._clip_middle 一个道理
    """
    edge = len(messages) - keep_last
    done = 0
    for slot in slots(messages):
        if slot.pos >= edge or slot.text.startswith(MARK) or len(slot.text) <= limit:
            continue
        cut = len(slot.text) - keep * 2
        slot.replace(
            "%s 这条工具输出很长，中间 %d 字符已经从上下文里去掉了 "
            "要完整内容就重新跑一次 \n\n%s\n\n…（中间省略）…\n\n%s"
            % (MARK, cut, slot.text[:keep], slot.text[-keep:]))
        done += 1
    return done
