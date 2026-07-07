import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


TOOL_SCHEMAS = [
    {
        "name": "bash",
        "description": (
            "在工作区里执行一条 shell 命令，返回合并后的 stdout+stderr 和退出码 "
            "用于构建、跑测试、git、安装依赖，以及其他没有专门工具覆盖的操作 "
            "命令从工作区根目录执行 过长的输出会被截断 "
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
    content: str
    is_error: bool = False


class Toolbox:
    def __init__(self, root: Path):
        self.root = root.resolve()

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
        proc = subprocess.run(
            command,
            shell=True,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        out = _truncate(out)
        body = out if out.strip() else "（无输出）"
        return ToolResult(f"退出码: {proc.returncode}\n{body}", is_error=proc.returncode != 0)

    def _tool_read_file(self, args: dict) -> ToolResult:
        target = self._resolve(args["path"])
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", len(lines)))
        chunk = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{offset + i}\t{line}" for i, line in enumerate(chunk))
        return ToolResult(_truncate(numbered) or "（空文件）")

    def _tool_write_file(self, args: dict) -> ToolResult:
        target = self._resolve(args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"], encoding="utf-8")
        n = args["content"].count("\n") + 1
        return ToolResult(f"已写入 {n} 行到 {args['path']}")

    def _tool_edit_file(self, args: dict) -> ToolResult:
        target = self._resolve(args["path"])
        text = target.read_text(encoding="utf-8")
        old, new = args["old_str"], args["new_str"]
        count = text.count(old)
        if count == 0:
            return ToolResult("文件中找不到 old_str", is_error=True)
        if count > 1:
            return ToolResult(f"old_str 匹配到 {count} 处，请让它唯一", is_error=True)
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return ToolResult(f"已编辑 {args['path']}")

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
        return ToolResult("\n".join(matches) or "（无匹配）")

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
        return ToolResult("\n".join(hits) or "（无匹配）")


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    head = text[: _MAX_OUTPUT // 2]
    tail = text[-_MAX_OUTPUT // 2 :]
    return f"{head}\n\n... [已截断 {len(text) - _MAX_OUTPUT} 字符] ...\n\n{tail}"
