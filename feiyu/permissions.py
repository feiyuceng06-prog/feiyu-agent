"""命令级权限：哪些工具调用可以直接跑，哪些要问，哪些一律拒

规则写成 `工具(模式)`，跟 Claude Code 的写法一致：

    Bash(ls:*)          以 ls 开头的命令
    Bash(git status)    正好是这条命令
    Read(*)             这个工具的所有调用
    Bash(rm:*)          放进 deny 就是一律拒

存在 ~/.config/feiyu/permissions.json，deny 优先于 allow
"""

import json
import os
import re

_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "feiyu"
)
STORE = os.path.join(_DIR, "permissions.json")

# 规则里的工具名用界面上显示的那套
RULE_NAME = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Update",
    "list_files": "List",
    "search": "Search",
    "web_fetch": "Fetch",
}

# 出厂放行的：只暴露路径和版本库状态，读不到任意文件内容
# 刻意不含 cat/head/tail/grep —— 那几个能读工作区外的任何文件，
# 等于绕开 read_file 的目录限制 想放行请自己按 [s] 添加
DEFAULT_ALLOW = [
    "Bash(ls:*)",
    "Bash(pwd)",
    "Bash(echo:*)",
    "Bash(wc:*)",
    "Bash(which:*)",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git branch:*)",
    "Read(*)",
    "List(*)",
    "Search(*)",
]

# 出厂拦截的：碰这些路径的命令一律不放行，deny 优先所以 allow 里写了也没用
# 这是 Claude Code 那个分类器的粗糙替代品——挡不住变形写法，但挡得住直白的读取
DENY_PATHS = re.compile(
    r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.config/feiyu/|"
    r"\.netrc|\.pgpass|\.gnupg/|private-env|\.env\b)",
    re.I,
)

_RULE = re.compile(r"^\s*(\w+)\s*\((.*)\)\s*$", re.S)


def load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    allow = raw.get("allow")
    return {
        "allow": list(allow) if isinstance(allow, list) else list(DEFAULT_ALLOW),
        "deny": list(raw.get("deny") or []),
    }


def save(rules: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(STORE, "w", encoding="utf-8") as f:
            json.dump({"allow": rules["allow"], "deny": rules["deny"]}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def add_allow(rule: str) -> bool:
    rules = load()
    if rule in rules["allow"]:
        return False
    rules["allow"].append(rule)
    save(rules)
    return True


def subject(tool: str, args: dict) -> str:
    """规则要匹配的那段文本：bash 是整条命令，其余是主参数 """
    if tool == "bash":
        return " ".join((args.get("command") or "").split())
    for key in ("path", "pattern", "regex", "url"):
        if args.get(key):
            return str(args[key])
    return ""


def matches(rule: str, tool: str, text: str) -> bool:
    hit = _RULE.match(rule)
    if not hit:
        return False
    who, pattern = hit.group(1), hit.group(2).strip()
    if who.lower() != RULE_NAME.get(tool, tool).lower():
        return False
    if pattern in ("*", ""):
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2].strip()
        return text == prefix or text.startswith(prefix + " ")
    return text == pattern


def verdict(tool: str, args: dict) -> str:
    """返回 allow / deny / ask """
    text = subject(tool, args)
    if tool == "bash" and DENY_PATHS.search(text):
        return "deny"
    rules = load()
    if any(matches(r, tool, text) for r in rules["deny"]):
        return "deny"
    if any(matches(r, tool, text) for r in rules["allow"]):
        return "allow"
    return "ask"


def suggest(tool: str, args: dict) -> str:
    """按下"永久允许"时该写哪条规则 bash 取命令的头一两个词 """
    name = RULE_NAME.get(tool, tool)
    if tool != "bash":
        return "%s(*)" % name
    words = (args.get("command") or "").split()
    if not words:
        return "%s(*)" % name
    # git status / npm run 这类两段式命令，带上第二个词更准
    head = words[0]
    if head in ("git", "npm", "pnpm", "yarn", "cargo", "go", "uv", "pip", "docker") and len(words) > 1:
        head = head + " " + words[1]
    return "%s(%s:*)" % (name, head)
