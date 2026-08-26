import difflib
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


TOOL_SCHEMAS = [
    {
        "name": "bash",
        "description": (
            "在工作区里执行一条 shell 命令，返回合并后的 stdout+stderr 和退出码 "
            "用于构建、跑测试、git、安装依赖，以及其他没有专门工具覆盖的操作 "
            "cd 会在多次调用之间保持：一旦 cd 进子目录，后续命令都从那里执行，"
            "不用每次都写 cd x && … 目录不能离开工作区，越界会被弹回 "
            "read_file/write_file/edit_file 的路径始终相对工作区根目录，不受 cd 影响 "
            "过长的输出会被截断 "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令 "},
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数（默认 120） ",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "读取工作区内的一个 UTF-8 文本文件，返回带 1 起始行号的内容，"
            "方便你在编辑时引用行 "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区根目录的文件路径 "},
                "offset": {"type": "integer", "description": "从第几行开始读，1 起始（可选） "},
                "limit": {"type": "integer", "description": "最多读多少行（可选） "},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "在工作区内创建或覆盖一个文本文件，父目录会自动创建 "
            "如果只是改动已有文件的一部分，优先用 edit_file "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区根目录的文件路径 "},
                "content": {"type": "string", "description": "要写入的完整文件内容 "},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "替换已有文件里的一段精确文本 old_str 必须在文件中恰好出现一次，"
            "否则编辑会被拒绝 多带一些上下文，让 old_str 唯一 "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区根目录的文件路径 "},
                "old_str": {"type": "string", "description": "要替换的精确文本（必须唯一） "},
                "new_str": {"type": "string", "description": "替换后的文本 "},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "按 glob 模式（如 '**/*.py'）列出工作区内的文件，"
            "会忽略 .git、node_modules 等常见噪音目录 "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式（默认 '**/*'） "},
            },
            "required": [],
        },
    },
    {
        "name": "search",
        "description": (
            "用正则表达式搜索文件内容，返回匹配的行及其所在文件和行号，类似 grep -rn "
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regex": {"type": "string", "description": "Python 正则表达式 "},
                "glob": {"type": "string", "description": "只搜匹配该 glob 的文件（可选） "},
            },
            "required": ["regex"],
        },
    },
]

_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build"}
_MAX_OUTPUT = 30_000


@dataclass
class ToolResult:
    content: str                                  # 喂给模型的
    is_error: bool = False
    summary: str = ""                             # 给人看的一行
    detail: list[tuple[str, str]] = field(default_factory=list)  # (kind, 文本)，kind 为 '-'/'+'/''


def _peek(lines: list[str], keep: int = 3) -> list[tuple[str, str]]:
    shown = [("", line) for line in lines[:keep]]
    if len(lines) > keep:
        shown.append(("", f"… 还有 {len(lines) - keep} 行"))
    return shown


def _diff(old_text: str, new_text: str, keep: int = 6) -> list[tuple[str, str]]:
    old, new = old_text.splitlines(), new_text.splitlines()
    rows: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new).get_opcodes():
        if tag == "equal":
            continue
        rows += [("-", f"{k + 1:>4}  - {old[k]}") for k in range(i1, i2)]
        rows += [("+", f"{k + 1:>4}  + {new[k]}") for k in range(j1, j2)]
    if len(rows) > keep:
        rows = rows[:keep] + [("", f"… 还有 {len(rows) - keep} 行改动")]
    return rows


class Toolbox:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cwd = self.root      # bash 的当前目录，cd 之后一直保持到下次改变

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"路径 {path!r} 超出了工作目录")
        return target

    def run(self, name: str, args: dict) -> ToolResult:
        try:
            handler = getattr(self, f"_tool_{name}")
        except AttributeError:
            return ToolResult(f"未知工具: {name}", is_error=True)
        try:
            return handler(args)
        except Exception as exc:
            return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)

    def _tool_bash(self, args: dict) -> ToolResult:
        command = args["command"]
        timeout = int(args.get("timeout", 120))
        # 命令末尾把 PWD 写进临时文件，这样 cd 能跨调用保持；
        # 走文件而不是 stdout，免得污染给模型看的输出
        fd, pwd_file = tempfile.mkstemp(prefix="feiyu-pwd-")
        os.close(fd)
        wrapped = "%s\n__feiyu_rc=$?\npwd > %s 2>/dev/null\nexit $__feiyu_rc" % (
            command,
            shlex.quote(pwd_file),
        )
        try:
            proc = subprocess.run(
                wrapped,
                shell=True,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            self._track_cwd(pwd_file)
        finally:
            try:
                os.unlink(pwd_file)
            except OSError:
                pass
        out = (proc.stdout or "") + (proc.stderr or "")
        out = _truncate(out)
        body = out if out.strip() else "（无输出）"
        lines = [line for line in out.splitlines() if line.strip()]
        if proc.returncode != 0:
            summary, detail = f"退出码 {proc.returncode}", _peek(lines, 4)
        else:
            summary = lines[0] if lines else "（无输出）"
            detail = _peek(lines[1:]) if len(lines) > 1 else []
        return ToolResult(
            f"退出码: {proc.returncode}\n{body}",
            is_error=proc.returncode != 0,
            summary=summary,
            detail=detail,
        )

    def _track_cwd(self, pwd_file: str) -> None:
        """命令跑完后更新 cwd 走出工作区的一律弹回根目录 """
        try:
            with open(pwd_file, encoding="utf-8") as f:
                reported = f.read().strip()
        except OSError:
            return
        if not reported:
            return
        target = Path(reported).resolve()
        if target.is_dir() and (target == self.root or self.root in target.parents):
            self.cwd = target
        else:
            self.cwd = self.root

    def _tool_read_file(self, args: dict) -> ToolResult:
        target = self._resolve(args["path"])
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", len(lines)))
        chunk = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{offset + i}\t{line}" for i, line in enumerate(chunk))
        return ToolResult(
            _truncate(numbered) or "（空文件）",
            summary=f"读取 {len(chunk)} 行" if chunk else "空文件",
        )

    def _tool_write_file(self, args: dict) -> ToolResult:
        target = self._resolve(args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(args["content"], encoding="utf-8")
        n = args["content"].count("\n") + 1
        verb = "覆盖" if existed else "新建"
        return ToolResult(f"已写入 {n} 行到 {args['path']}", summary=f"{verb} {n} 行")

    def _tool_edit_file(self, args: dict) -> ToolResult:
        target = self._resolve(args["path"])
        text = target.read_text(encoding="utf-8")
        old, new = args["old_str"], args["new_str"]
        count = text.count(old)
        if count == 0:
            return ToolResult("文件中找不到 old_str", is_error=True)
        if count > 1:
            return ToolResult(f"old_str 匹配到 {count} 处，请让它唯一", is_error=True)
        updated = text.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        return ToolResult(f"已编辑 {args['path']}", summary="已修改 1 处", detail=_diff(text, updated))

    def _tool_list_files(self, args: dict) -> ToolResult:
        pattern = args.get("pattern", "**/*")
        matches = []
        for p in self.root.glob(pattern):
            if not p.is_file():
                continue
            if any(part in _IGNORE_DIRS for part in p.relative_to(self.root).parts):
                continue
            matches.append(str(p.relative_to(self.root)))
        matches.sort()
        return ToolResult(
            "\n".join(matches) or "（无匹配）",
            summary=f"{len(matches)} 个文件" if matches else "无匹配",
            detail=_peek(matches),
        )

    def _tool_search(self, args: dict) -> ToolResult:
        rx = re.compile(args["regex"])
        glob = args.get("glob", "**/*")
        hits = []
        for p in self.root.glob(glob):
            if not p.is_file():
                continue
            if any(part in _IGNORE_DIRS for part in p.relative_to(self.root).parts):
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{p.relative_to(self.root)}:{i}: {line.strip()}")
                        if len(hits) >= 200:
                            break
            except OSError:
                continue
            if len(hits) >= 200:
                break
        return ToolResult(
            "\n".join(hits) or "（无匹配）",
            summary=f"{len(hits)} 处匹配" if hits else "无匹配",
            detail=_peek(hits),
        )


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    head = text[: _MAX_OUTPUT // 2]
    tail = text[-_MAX_OUTPUT // 2 :]
    return f"{head}\n\n... [已截断 {len(text) - _MAX_OUTPUT} 字符] ...\n\n{tail}"
