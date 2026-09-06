"""机器人：一键开一段带人格、模型、权限模式的新对话

`~/.config/feiyu/bots/<名字>.md`，一个文件一个：

    ---
    name: 代码评审
    description: 只挑毛病，不说好话
    model: deepseek-v4-pro
    mode: readonly
    style: concise
    ---
    你是一个刻薄但准确的评审 先说最要命的那处，不铺垫、不夸奖 ...

## 跟配置档案（`profiles.py`）的界限

两个都是"往系统提示里塞一段人格"，**但用法完全不同，所以没合并**：

| | 档案 | 机器人 |
|---|---|---|
| 是什么 | 手动切的人格 | 一键开一段新对话 |
| 带什么 | 只有人格（外加可选的模型） | 人格 + 模型 + 权限模式 + 风格 |
| 在哪儿 | 设置里的一项 | 侧栏跟"会话"平级 |
| 什么时候生效 | 当场换掉现在这段 | **开一段新的** |

**人格那个位置是共用的**（`agent._persona()`）：一段对话要么套着机器人、
要么套着档案、要么什么都不套 —— 同时套两个人格只会互相打架，
而"你是谁"这个问题只该有一个答案。开机器人时把档案清掉，反之亦然。

## 一段对话记得自己是哪个机器人开的

存进 session（`bot` 字段），`--resume` 或者从侧栏切回来时照旧是那个机器人 ——
不然切回去人格就没了，而那段对话前面的内容全是按那个人格说的。
"""

import os
import re

from . import files, skills

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
DIR = os.path.join(_DIR, "bots")

# 跟技能、档案同一条：`\w` 认 Unicode，中文名字照用；排掉空格和斜杠
NAME_OK = re.compile(r"^[\w-]{1,40}$")
# 人格设定**每一轮都要重发**，所以上限跟档案一样，比技能正文小一个数量级
MAX_BODY = 4000
# frontmatter 里认这几项 认不出的键一律忽略，不报错
FIELDS = ("description", "model", "mode", "style")


class Bot:
    def __init__(self, name: str, body: str, path: str, **fields):
        self.name = name
        self.body = body
        self.path = path
        self.description = fields.get("description", "")
        self.model = fields.get("model", "")
        self.mode = fields.get("mode", "")
        self.style = fields.get("style", "")

    def row(self, current: str = "") -> dict:
        return {"name": self.name, "description": self.description,
                "model": self.model, "mode": self.mode, "style": self.style,
                "path": self.path, "chars": len(self.body),
                "on": self.name == current}

    def __repr__(self):
        return "Bot(%r)" % self.name


def _path(name: str) -> str:
    return os.path.join(DIR, name + ".md")


def _parse(text: str, stem: str) -> Bot:
    """**名字一律用文件名。** 理由同 profiles：切换、保存、删除都按文件找，
    两个来源不一致会出现"列得出来却删不掉"（0.58.1 那条）。 """
    _, description, body = skills.parse(text, stem)
    fields = {"description": description}
    front = skills._FRONT.match(text)
    if front:
        for line in front.group(1).splitlines():
            hit = skills._FIELD.match(line)
            if hit and hit.group(1).lower() in FIELDS:
                fields[hit.group(1).lower()] = hit.group(2).strip().strip("\"'")
    return Bot(stem, body[:MAX_BODY], _path(stem), **fields)


def load() -> list[Bot]:
    try:
        names = sorted(os.listdir(DIR))
    except OSError:
        return []
    out = []
    for entry in names:
        if not entry.endswith(".md"):
            continue
        stem = entry[:-3]
        if not NAME_OK.match(stem):
            continue
        try:
            with open(os.path.join(DIR, entry), encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        out.append(_parse(text, stem))
    return out


def get(name: str) -> Bot | None:
    return next((b for b in load() if b.name == name), None)


def write(name: str, body: str, **fields) -> bool:
    """写一个 没带的字段保留原来的（跟改模型配置那条一个规矩）"""
    if not NAME_OK.match(name or ""):
        return False
    old = get(name)
    head = ["---", "name: " + name]
    for key in FIELDS:
        got = fields.get(key)
        if got is None and old is not None:
            got = getattr(old, key, "")
        if got:
            head.append("%s: %s" % (key, got))
    head.append("---")
    body = (body or "").strip()[:MAX_BODY]
    try:
        files.private_dir(DIR)
        fd = os.open(_path(name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(head) + "\n\n" + body + "\n")
    except OSError:
        return False
    return True


def remove(name: str) -> bool:
    if not NAME_OK.match(name or ""):
        return False
    try:
        os.remove(_path(name))
    except OSError:
        return False
    return True


def as_prompt(name: str) -> str:
    """拼进系统提示的那一段 没套机器人就是空串，不留一句废话占地方 """
    found = get(name) if name else None
    if found is None or not found.body:
        return ""
    return ("\n\n下面这段是用户给你定的人格设定（机器人「%s」），"
            "它说的是你**是谁、怎么说话**，照着来：\n\n%s" % (found.name, found.body))
