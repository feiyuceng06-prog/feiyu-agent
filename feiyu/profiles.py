"""配置档案：一份档案就是一套人格

`~/.config/feiyu/profiles/<名字>.md`，一个文件一份。frontmatter 里可以写
`name` / `description` / `model`，正文是**直接拼进系统提示**的那段人格设定。

    ---
    name: 严厉的代码评审
    description: 只挑毛病，不说好话
    model: deepseek-v4-pro
    ---
    你是一个刻薄但准确的评审 先说最要命的那处，不铺垫、不夸奖 ...

## 跟别的几种"往系统提示里塞东西"的区别

到 0.56.0 为止，系统提示里已经有四种来源，各有各的规矩，**这一份跟它们都不一样**：

| 来源 | 谁写的 | 谁改得了 |
|---|---|---|
| `SYSTEM_PROMPT` | 我们 | 改代码 |
| `CONTEXT_FILES`（FEIYU.md…） | 用户，按项目 | 编辑器 |
| `memory` | **agent 自己** | `remember` 工具 |
| 技能目录 | 用户 | 加 .md |
| **档案** | 用户，按人格 | 这个文件 / 面板 |

所以它**不进 memory**（那是 agent 自己记的事实，不该被人格设定污染），
也**不进项目约定**（人格跟在哪个目录干活无关，换工作台不该换人格）。

## 位置：在风格之后，在项目约定之前

风格（`STYLES`）管的是"话说多长"，档案管的是"你是谁"——档案更具体，所以排在后面。
项目约定（`FEIYU.md`）更具体，排在档案后面：**同一句话，项目里写的赢**。
这跟"具体的压过通用的"那条一以贯之。

## 当前用哪份记在 state.json

跟 `helper` / `theme` 一样，是 agent 自己的状态不是模型配置，所以在 `state.json`
不在 `models.json`。**认不出名字时回空串**（那份被删掉了），别让它拿一个不存在的档案。
"""

import os
import re

from . import files, keys, skills

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
DIR = os.path.join(_DIR, "profiles")

# 名字要当文件名，也要能在 `/profile <名字>` 里敲
# `\w` 在 Python 里认 Unicode，所以中文名字照用（跟技能同一条规矩）
NAME_OK = re.compile(r"^[\w-]{1,40}$")
# 一份人格设定最多这么长 它**每一轮都要重发**，跟技能正文（用上那次才发）完全不同，
# 所以这个上限比 skills.MAX_BODY 小一个数量级
MAX_BODY = 4000


class Profile:
    def __init__(self, name: str, description: str, body: str, model: str, path: str):
        self.name = name
        self.description = description
        self.body = body
        self.model = model            # 空串就是不换模型
        self.path = path

    def row(self, current: str) -> dict:
        return {"name": self.name, "description": self.description, "model": self.model,
                "path": self.path, "chars": len(self.body), "on": self.name == current}


def _path(name: str) -> str:
    return os.path.join(DIR, name + ".md")


def _parse(text: str, stem: str) -> Profile:
    """**名字一律用文件名，frontmatter 里的 `name` 只当注释。**

    `skills.parse` 是认 frontmatter 那个 name 的（技能按名字调，无所谓文件叫什么），
    档案不行：切换、保存、删除全都按文件找。两个来源不一致的话，
    `我的档案.md` 里写 `name: 严厉评审` 会让面板上出现一行叫「严厉评审」的档案，
    而它**删不掉也存不对**（remove 去删 `严厉评审.md`，那个文件不存在）——
    跟 0.56.1 那条删不掉的会话是同一类错：一个东西有两个名字来源。
    """
    _, description, body = skills.parse(text, stem)
    name = stem
    model = ""
    front = skills._FRONT.match(text)
    if front:
        for line in front.group(1).splitlines():
            field = skills._FIELD.match(line)
            if field and field.group(1).lower() == "model":
                model = field.group(2).strip().strip("\"'")
    return Profile(name, description, body[:MAX_BODY], model, _path(stem))


def load() -> list[Profile]:
    try:
        names = sorted(os.listdir(DIR))
    except OSError:
        return []
    out = []
    for entry in names:
        if not entry.endswith(".md"):
            continue
        try:
            with open(os.path.join(DIR, entry), encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        # **名字以文件名为准**，不认 frontmatter 里的 name —— 切换、保存、删除
        # 全都要按文件找，两个来源不一致的话就会出现"切得过去但存不回来"
        stem = entry[:-3]
        if not NAME_OK.match(stem):
            continue
        out.append(_parse(text, stem))
    return out


def get(name: str) -> Profile | None:
    return next((p for p in load() if p.name == name), None)


def current() -> str:
    """当前这份的名字 那份被删掉了就回空串，别让它拿一个不存在的档案 """
    name = keys._state().get("profile") or ""
    return name if any(p.name == name for p in load()) else ""


def set_current(name: str) -> None:
    keys._set_state(profile=name)


def write(name: str, body: str, *, description: str = "", model: str = "") -> bool:
    """写一份档案 已经有的就只换正文，frontmatter 原样留着 """
    if not NAME_OK.match(name or ""):
        return False
    body = (body or "").strip()[:MAX_BODY]
    old = get(name)
    if old is not None:
        description = description or old.description
        model = model or old.model
    front = ["---", "name: " + name]
    if description:
        front.append("description: " + description)
    if model:
        front.append("model: " + model)
    front.append("---")
    try:
        files.private_dir(DIR)
        fd = os.open(_path(name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(front) + "\n\n" + body + "\n")
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
    if current() == name:
        set_current("")
    return True


def as_prompt(name: str) -> str:
    """拼进系统提示的那一段 没选档案就是空串，不留一句废话占地方 """
    found = get(name) if name else None
    if found is None or not found.body:
        return ""
    return ("\n\n下面这段是用户给你定的人格设定（配置档案「%s」），"
            "它说的是你**是谁、怎么说话**，照着来：\n\n%s" % (found.name, found.body))
