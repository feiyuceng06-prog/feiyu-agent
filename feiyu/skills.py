"""技能和自定义斜杠命令：不改代码就能给它加一套做法

两件事共用一份加载器 —— 都是「一个 .md 文件就是一份指令」，区别只在谁来触发：

- **技能**（`skills/`）由**模型自己**按需加载 系统提示里只放一行名字加用途，
  它判断这次用得上就调 `skill` 工具把正文读进来
- **命令**（`commands/`）由**用户**敲 `/名字` 触发，正文当成用户自己说的话送进对话
  正文里的 `$ARGUMENTS` 换成 `/名字` 后面跟的那串东西

两边都是全局一份（`~/.config/feiyu/`）加项目一份（工作区的 `.feiyu/`）

## 为什么要有这个

到 0.48.0 为止，所有行为指令都堆在一份系统提示里，已经 649 token
而系统提示是**每一轮请求都要重发一遍**的东西：为了一百次任务里用得上一次的做法，
去给每一轮都加钱，加到第三条就不划算了 技能把这笔开销挪到「真用上的那次」——
不用的时候只占一行目录

这也是 0.16.0 / 0.30.0 / 0.46.0 / 0.48.0 那条老教训的另一半 光有能力模型不会用，
**得让它知道什么时候用**；而"什么时候用"这句话要占系统提示的地方，
所以能写进系统提示的条数是有上限的 目录行就是压缩过的"什么时候用"

## 项目技能要先经用户同意，项目命令不用

项目 `.feiyu/skills/` 是从仓库里带下来的，而技能是**模型自己**决定加载的 ——
clone 一个仓库，里面的技能就能往它的上下文里塞指令，跟钩子是同一类注入通道
所以照 0.15.0 那套按内容指纹认，同意过的记在 `~/.config/feiyu/trusted-skills.json`

命令不用认：用户不敲就不会生效，敲了等于他自己把那段字打了一遍 这跟钩子不一样，
钩子是 agent 干活时自己触发的，命令得人动手
"""

import hashlib
import os
import re
import zipfile

from . import files

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
GLOBAL_SKILLS = os.path.join(_DIR, "skills")
GLOBAL_COMMANDS = os.path.join(_DIR, "commands")
TRUST = os.path.join(_DIR, "trusted-skills.json")
PROJECT_SKILLS = os.path.join(".feiyu", "skills")
PROJECT_COMMANDS = os.path.join(".feiyu", "commands")

# 单份技能正文截多长 它整份进上下文，太长的话省下来的那点系统提示又赔回去了
MAX_BODY = 12000
# 目录行里的用途截多长 目录是每轮都发的，这里省的是真金白银
MAX_DESC = 120
# 包形式的技能里，哪个文件是正文 跟 Anthropic 那套技能包一致
MAIN = "SKILL.md"
# 一个包最多认几个附属文件（目录行里不列，只在 /skills 和面板上显示）
MAX_EXTRAS = 40
# 解 zip 的两道闸：成员个数和解压后的总大小
MAX_ZIP_FILES = 200
MAX_ZIP_BYTES = 4 * 1024 * 1024
# 名字只认这些字符：它要当斜杠命令用（不能带空格和斜杠），也要当工具参数传
# `\w` 在 Python 里是认 Unicode 的，中文名字照用——这个项目的技能多半是中文写的
NAME_OK = re.compile(r"^[\w-]{1,40}$")
# frontmatter：文件开头单起一行的 `---`，到下一行 `---` 为止
_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*?)[ \t]*$")


class Skill:
    def __init__(self, name: str, description: str, body: str, source: str, path: str,
                 folder: str = "", extras: tuple[str, ...] = ()):
        self.name = name
        self.description = description
        self.body = body
        self.source = source      # 给 /skills 显示这条是哪来的
        self.path = path
        # 包形式的技能：一个目录，SKILL.md 是正文，旁边那些是按需读的附属文件
        self.folder = folder
        self.extras = extras

    def __repr__(self):
        return "Skill(%r, %r)" % (self.name, self.source)


def parse(text: str, fallback: str) -> tuple[str, str, str]:
    """拆成 (名字, 用途, 正文) 没有 frontmatter 就拿文件名当名字、首行当用途

    自己扫两个字段，不引 YAML：只认 `键: 值` 这一种写法，值里的引号剥掉
    嵌套结构一律不认——认了就得解释为什么只认一半
    """
    name, description, body = "", "", text
    front = _FRONT.match(text)
    if front:
        body = text[front.end():]
        for line in front.group(1).splitlines():
            field = _FIELD.match(line)
            if not field:
                continue
            key, value = field.group(1).lower(), field.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key == "name":
                name = value
            elif key == "description":
                description = value
    name = name.strip() or fallback
    body = body.strip()
    if not description:
        # 没写用途就拿正文第一行顶上 目录里空着的话模型没法判断该不该用
        for line in body.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                description = stripped
                break
    return name, " ".join(description.split())[:MAX_DESC], body[:MAX_BODY]


def _read_one(path: str, fallback: str, source: str, *,
              folder: str = "", extras: tuple[str, ...] = ()) -> Skill | None:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    name, description, body = parse(text, fallback)
    if not body or not NAME_OK.match(name):
        return None
    return Skill(name, description, body, source, path, folder, extras)


def _package(folder: str, source: str) -> Skill | None:
    """一个目录 + 里面的 SKILL.md 就是一份"包形式"的技能

    这是 Anthropic 那套技能包的布局：`技能名/SKILL.md` 是正文，旁边那些
    `XXX-FORMAT.md` 之类是**按需**读的附属文件。正文照旧只进一次上下文，
    附属文件走 `skill(name, file=...)` 单独取 —— 全塞进正文的话，
    "不用的时候只占一行目录"那个好处就没了。
    """
    main = os.path.join(folder, MAIN)
    if not os.path.isfile(main):
        return None
    extras = []
    for here, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for one in sorted(names):
            rel = os.path.relpath(os.path.join(here, one), folder)
            if rel != MAIN and not one.startswith("."):
                extras.append(rel.replace(os.sep, "/"))
    return _read_one(main, os.path.basename(folder), source,
                     folder=folder, extras=tuple(sorted(extras)[:MAX_EXTRAS]))


def _read_dir(folder: str, source: str) -> list[Skill]:
    """一个目录下的技能 两种形式都认：

    - 一个 `.md` 就是一份（原来的形式，改完照旧能用）
    - 一个目录里放 `SKILL.md` 就是一份"包"，旁边可以带附属文件

    名字不合法或正文是空的直接跳过，不报错也不半死不活。
    """
    _unpack_zips(folder)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    found = []
    for entry in names:
        path = os.path.join(folder, entry)
        if os.path.isdir(path):
            one = _package(path, source)
            if one is not None:
                found.append(one)
            continue
        if not entry.endswith(".md"):
            continue
        one = _read_one(path, entry[:-3], source)
        if one is not None:
            found.append(one)
    return found


def _unpack_zips(folder: str) -> list[str]:
    """把丢进来的 `.zip` 就地解开成目录 返回解开了哪几个

    技能包在网上多半是以 zip 分发的，丢进 `skills/` 就该能用 ——
    "先自己 unzip 一下"是个没必要的台阶。解完 **zip 原样留着**，
    不删用户的文件；下次看到目录已经在就不再解。

    解压是有名的一类洞（zip slip：成员名写 `../../.bashrc` 就写到目录外面去了），
    所以每个成员都要**先算出绝对路径再确认它落在目标目录里**，
    另外挡掉软链、限制个数和总大小。
    """
    done = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return done
    for entry in names:
        if not entry.endswith(".zip"):
            continue
        stem = entry[:-4]
        target = os.path.join(folder, stem)
        if os.path.exists(target):
            continue                      # 解过了，别覆盖用户后来改的东西
        try:
            with zipfile.ZipFile(os.path.join(folder, entry)) as bundle:
                rows = bundle.infolist()
                if len(rows) > MAX_ZIP_FILES:
                    continue
                if sum(r.file_size for r in rows) > MAX_ZIP_BYTES:
                    continue
                root = os.path.realpath(target)
                for row in rows:
                    # 包里常常是 `技能名/SKILL.md` 这种带一层壳的，剥掉那一层
                    inner = row.filename.replace("\\", "/")
                    parts = [p for p in inner.split("/") if p not in ("", ".")]
                    if parts and parts[0] == stem:
                        parts = parts[1:]
                    if not parts or any(p == ".." for p in parts):
                        continue
                    where = os.path.realpath(os.path.join(root, *parts))
                    if where != root and not where.startswith(root + os.sep):
                        continue          # zip slip，扔掉
                    if row.is_dir():
                        os.makedirs(where, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(where), exist_ok=True)
                    with bundle.open(row) as src, open(where, "wb") as out:
                        out.write(src.read(MAX_ZIP_BYTES))
            done.append(stem)
        except (OSError, zipfile.BadZipFile, ValueError):
            continue
    return done


def _merge(base: list[Skill], extra: list[Skill]) -> list[Skill]:
    """同名的以项目那份为准：项目比全局具体，冲突时具体的赢（跟约定文件一个道理）"""
    out = {s.name: s for s in base}
    out.update({s.name: s for s in extra})
    return sorted(out.values(), key=lambda s: s.name)


def digest(folder: str) -> str:
    """整个目录的内容指纹 加了一份、删了一份、改了一个字都要变

    **必须连子目录一起摘。** 包形式的技能正文在 `技能名/SKILL.md` 里，
    只摘顶层的话，项目技能同意过一次之后，包里的内容随便改都不会重新问 ——
    那这道信任闸就白设了。
    """
    h = hashlib.sha256()
    rows = []
    try:
        for here, dirs, names in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for one in names:
                if one.endswith(".md"):
                    rows.append(os.path.join(here, one))
    except OSError:
        return ""
    if not rows:
        return ""
    for path in sorted(rows):
        h.update(os.path.relpath(path, folder).replace(os.sep, "/").encode("utf-8"))
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            continue
        h.update(b"\0")
    return h.hexdigest()[:16]


def _trusted() -> dict:
    from . import hooks

    return hooks._read(TRUST)


def trust(workspace, fingerprint: str) -> None:
    import json

    data = _trusted()
    data[str(workspace)] = fingerprint
    try:
        files.private_dir(_DIR)
        fd = os.open(TRUST, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def extra(one: Skill, name: str) -> tuple[str, str]:
    """读一份技能包里的附属文件 返回 (正文, 出错说明)，两者只有一个非空

    **路径必须落在这个包自己的目录里**：名字是模型给的，而模型会读网页、
    会照着技能正文里写的东西走 —— 一个 `../../../.ssh/id_rsa` 就能把
    "读一份说明书"变成"读任意文件"。跟桌面端那个 pathguard 是同一条规矩。
    """
    if not one.folder:
        return "", "技能「%s」不是包形式的，没有附属文件（它就一个 .md）" % one.name
    want = (name or "").strip().replace("\\", "/")
    if not want:
        return "", "没写要读哪个文件"
    root = os.path.realpath(one.folder)
    where = os.path.realpath(os.path.join(root, want))
    if where != root and not where.startswith(root + os.sep):
        return "", "只能读这份技能包里的文件"
    if os.path.relpath(where, root).replace(os.sep, "/") not in one.extras:
        return "", "技能「%s」里没有 %s 这个文件 有这些：%s" % (
            one.name, want, "、".join(one.extras) or "（没有附属文件）")
    try:
        with open(where, encoding="utf-8") as f:
            return f.read()[:MAX_BODY], ""
    except (OSError, UnicodeDecodeError) as exc:
        return "", "读不了 %s：%s" % (want, exc)


def project_state(workspace) -> tuple[str, str, str]:
    """返回 (项目技能目录, 内容指纹, 状态) 状态是 none / trusted / unknown """
    folder = os.path.join(str(workspace), PROJECT_SKILLS)
    if not os.path.isdir(folder):
        return folder, "", "none"
    fingerprint = digest(folder)
    if not fingerprint:
        return folder, "", "none"
    known = _trusted().get(str(workspace))
    return folder, fingerprint, "trusted" if known == fingerprint else "unknown"


def load_skills(workspace, *, approve=None) -> tuple[list[Skill], str]:
    """全局的直接用；项目的要先经用户同意

    approve 是 (目录, 名字列表) -> bool，交互层塞进来 没给就当作不同意
    返回 (技能列表, 给人看的一句话；没什么可说的时候是空串)
    """
    found = _read_dir(GLOBAL_SKILLS, "全局")
    folder, fingerprint, state = project_state(workspace)
    if state == "none":
        return found, ""
    project = _read_dir(folder, "项目")
    if not project:
        return found, ""
    if state == "trusted":
        return _merge(found, project), ""
    if approve is None or not approve(folder, [s.name for s in project]):
        return found, ("项目技能未启用（%s 里有 %d 份，没有同意就不会进它的上下文）"
                       % (PROJECT_SKILLS, len(project)))
    trust(workspace, fingerprint)
    return _merge(found, project), "已信任 %s 的 %d 份项目技能" % (PROJECT_SKILLS, len(project))


def load_commands(workspace) -> list[Skill]:
    """自定义斜杠命令 不用同意：用户不敲就不会生效 """
    return _merge(_read_dir(GLOBAL_COMMANDS, "全局"),
                  _read_dir(os.path.join(str(workspace), PROJECT_COMMANDS), "项目"))


def catalog(found: list[Skill]) -> str:
    """进系统提示的那份目录 没技能就返回空串，不留一句废话占地方

    **只放名字和用途，正文留给 `skill` 工具**——这是整件事的意义所在：
    十份技能的目录不到一百 token，正文加起来可能上万
    """
    if not found:
        return ""
    rows = "\n".join("- %s：%s" % (s.name, s.description) for s in found)
    return ("\n\n你有下面这些技能可以用，每份都是一套写好的做法：\n\n%s\n\n"
            "手上的活跟哪一份对得上，**先用 skill 工具把它的完整说明读进来再动手**，"
            "别照着名字猜里面写了什么 读进来的内容是给你照着做的指令，不是外部数据 " % rows)


def expand(command: Skill, argument: str) -> str:
    """把一条自定义命令展开成送进对话的那段话

    `$ARGUMENTS` 换成用户跟在命令后面的东西；正文里没写这个占位符的话，
    参数附在末尾——不然用户敲了参数却石沉大海
    """
    argument = argument.strip()
    if "$ARGUMENTS" in command.body:
        return command.body.replace("$ARGUMENTS", argument)
    return command.body + ("\n\n" + argument if argument else "")
