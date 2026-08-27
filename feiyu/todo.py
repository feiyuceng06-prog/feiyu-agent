"""任务清单：让多步任务的进度变成显式的东西

解决的是一个很具体的毛病：五件事做完三件就宣布干完了 清单摆在那儿，
模型每做一步要回来改状态，收尾时还没做完的会被点出来（`unfinished()`）

**整份替换，不做增量更新**：让模型每次交完整清单，比让它按 id 改某一条可靠得多——
它经常记错 id，而整份替换错不了，代价只是多几十个 token
"""

PENDING, DOING, DONE = "pending", "doing", "done"
STATUSES = (PENDING, DOING, DONE)

MAX_ITEMS = 30
MAX_TEXT = 120


class Todos:
    def __init__(self):
        self.items: list[dict] = []

    def replace(self, raw) -> tuple[bool, str]:
        """整份换掉 返回 (成没成, 给模型看的说明)"""
        if not isinstance(raw, list):
            return False, "items 得是一个数组"
        if len(raw) > MAX_ITEMS:
            return False, "最多 %d 条 拆得太碎了，把小步骤并一并 " % MAX_ITEMS
        cleaned = []
        for i, item in enumerate(raw, 1):
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict):
                return False, "第 %d 条既不是字符串也不是对象" % i
            text = " ".join(str(item.get("text") or "").split())
            if not text:
                return False, "第 %d 条没有 text" % i
            status = str(item.get("status") or PENDING).lower()
            if status not in STATUSES:
                return False, "第 %d 条的 status 是 %r，只能是 %s" % (
                    i, status, " / ".join(STATUSES))
            cleaned.append({"text": text[:MAX_TEXT], "status": status})
        doing = [c for c in cleaned if c["status"] == DOING]
        if len(doing) > 1:
            return False, "同时只能有一条 doing，你标了 %d 条 " % len(doing)
        self.items = cleaned
        return True, self.summary()

    def summary(self) -> str:
        if not self.items:
            return "清单清空了"
        done = sum(1 for i in self.items if i["status"] == DONE)
        return "%d / %d 做完" % (done, len(self.items))

    def unfinished(self) -> list[dict]:
        return [i for i in self.items if i["status"] != DONE]

    def restore(self, raw) -> None:
        """从存盘恢复 坏数据当作没有，不能让存盘把会话带崩 """
        if isinstance(raw, list):
            self.replace(raw)


NUDGE = (
    "等一下——任务清单上还有没做完的：\n%s\n\n"
    "如果确实还没做完就接着做 如果是做完了忘了改状态，就用 todo 把它们标成 done "
    "如果是做不了或者不该做了，说清楚为什么 "
)


def nudge_text(items: list[dict]) -> str:
    return NUDGE % "\n".join("- %s（%s）" % (i["text"], i["status"]) for i in items)
