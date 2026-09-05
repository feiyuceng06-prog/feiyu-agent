# Feiyu Agent

[简体中文](README.zh.md) | English

A coding agent that runs in your terminal. Give it a task and it reads the code, edits files and runs commands on its own, with every step printed on screen. Works with Claude, DeepSeek, Kimi and any OpenAI-compatible API.

About 4,800 lines of Python, no framework, three dependencies: `anthropic`, `pillow`, `prompt_toolkit`.

![demo](docs/demo.gif)

## Install

```
git clone https://github.com/feiyuceng06-prog/feiyu-agent.git && cd feiyu-agent && ./install.sh
```

The script creates a virtualenv, installs the dependencies and puts a `feiyu` command in `~/.local/bin`. Needs Python 3.10 or newer. Run it again after pulling new code.

Then add a model: run `feiyu`, type `/key add`, and fill in the API base URL, model id and key. See [Models](#models).

Two optional system packages worth installing:

```
sudo pacman -S bubblewrap ripgrep      # Debian/Ubuntu: apt install bubblewrap ripgrep
```

`bubblewrap` sandboxes bash, `ripgrep` makes search much faster.

<details>
<summary>Manual install</summary>

```
python -m venv .venv
source .venv/bin/activate
pip install -e ".[openai]"
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

`[openai]` pulls in the `openai` package, which DeepSeek, Kimi and other OpenAI-compatible APIs use. For Claude only, `pip install -e .` is enough.

</details>

## Usage

```
feiyu                             # start a conversation in the current directory
feiyu -w ~/code/my-project        # pick the workspace
feiyu -m ds-flash                 # pick a model from /key (defaults to the last one used)
feiyu -r                          # resume the last conversation in this directory
feiyu "add a --json flag to the CLI"   # run one task and exit
```

Other flags:

- `--mode default|auto|plan|readonly`: permission mode. One-shot tasks and pipes have nobody to answer confirmations, so add `--mode auto` if it should act on its own.
- `--json-schema <file>`: emit JSON matching a schema, see [Embedding](#embedding).
- `--review`: have a second model grade the change afterwards.

For a browser instead of a terminal, run `feiyu-web` — see [the web front end](#the-web-front-end).

## Interface

```
╭─────────────────────────────────── deepseek  ·  context 1.4k  ·  ¥0.0014 ─╮
│ > make search in tools.py use rg
╰──────────────────────────────────────────────────────────────────────────╯

⏺ Read(feiyu/tools.py)
  ⎿  read 217 lines

⏺ Update(feiyu/tools.py)
  ⎿  1 edit
       47  -  rx = re.compile(args["regex"])
       47  +  if shutil.which("rg"):

  Done. search now prefers rg and falls back to the regex implementation.
```

Keys: `⏎` send, `\⏎` newline, `shift+tab` cycle permission mode, `esc` interrupt, `ctrl-d` quit, `↑` `↓` history.

The top border shows how much of the context is used and what the session has cost. Context is a percentage; models without a price configured show no amount. Markdown in replies is rendered (bold, code, lists, tables, links), except italics, because `*` is usually a glob on the command line.

`esc` is safe: whatever was said stays in the history, unfinished calls get an "interrupted" result, and the conversation is not corrupted.

Type `/` for command completion:

| Command | Purpose |
|---|---|
| `/help` | Show help |
| `/model` | List models; `/model <name>` switches, keeping the conversation |
| `/key` | Add, list, remove model configs |
| `/mode` | Permission mode |
| `/allow` | Show or add permission rules |
| `/sandbox` | Bash sandbox: `off` / `workspace` / `strict` |
| `/jobs` | Show or stop background commands |
| `/mcp` | Show connected MCP servers |
| `/undo` | Revert its latest file write, repeatable |
| `/hooks` | Show hooks |
| `/verify` | Show or set the command run after every edit |
| `/review` | Have another model grade this change |
| `/memory` | Show, add, delete long-term memory for this directory |
| `/context` | Reload the project convention file |
| `/cost` | Recent spend by task |
| `/compact` | Compress the history into a summary |
| `/helper <name>` | Use a cheaper model for summaries, subagents and page extraction; `/helper off` to revert |
| `/style` | Reply style: `default` / `concise` / `explanatory` / `teaching` |
| `/theme` | UI theme: `default` / `swiss` |
| `/skills` | List the available skills and custom commands |
| `/clear` | Clear the conversation and its save file |
| `/exit` | Quit |

## Tools

Seventeen tools, every call printed on screen:

| Tool | What it does |
|---|---|
| `bash` | Run a shell command. `cd` persists between calls; long commands can run in the background |
| `read_file` `write_file` `edit_file` | Read, write, exact replace. Paths are relative to the workspace root. `edit_file` takes several edits to one file in one call and points at the nearest match when it fails; `write_file` can append |
| `list_files` `search` | Glob the tree, grep it with a regex |
| `symbol` | Definitions and references through a language server (pyright, clangd, rust-analyzer, typescript-language-server, gopls, lua-language-server; only when installed) |
| `git` | Status, diff, log, commit. It never commits unless asked |
| `web_search` `web_fetch` | Search and fetch pages |
| `task` | Send a subagent to find something out and bring back only the conclusion |
| `todo` | Task list |
| `remember` | Note something for future sessions |
| `job` | Read a background command's output, or stop it |
| `exit_plan` | Hand you the plan in plan mode |
| `ask_user` | Ask you a question when unsure |
| `skill` | Pull in the full text of one skill |

A few things that come up daily:

**Screenshots can go straight to it.** `read_file` on a png, jpg, gif or webp sends the image to the model. Whether a model can see images is set in `/key add`; models without the flag say so instead of failing.

**Long-running commands go to the background.** Dev servers, watchers and long builds start with the `background` parameter of `bash`; `job` reads new output later. It is told when the command finishes. Quitting feiyu kills these processes.

**It must read a file before editing it.** If the file changed after it was read (you edited it, or a formatter did), the write is blocked until it reads again. This guards against it overwriting a whole file with stale content and silently dropping your changes.

**Read-only calls run in parallel.** A batch of reads, searches and fetches runs up to four at a time; one write in the batch makes the whole batch sequential.

**After 25 tool rounds in a row it stops and asks.** Big refactors legitimately take dozens of rounds; pick "continue" and it carries on. In a one-shot task nobody can answer, so it stops with progress saved, and `feiyu -r` picks it up.

### Subagents

"How does this project handle interrupts?" means reading a dozen files, which would flood the main conversation. It sends a subagent to look and brings back only the conclusion. Subagents can only read, cannot change anything or reach the network, and their usage shows up in `/cost`.

### Task list and plan mode

When there is a lot to do it writes a task list first and updates it step by step. If it tries to finish with items still open, it is sent back to them. If you quit halfway, the progress is carried into the next session in the same directory, and expires after seven days.

For big changes switch to plan mode (`shift+tab` or `/mode plan`): it can only read and think, writes and commands are refused. It hands you the plan and starts only after you approve.

### It asks when unsure

When the answer changes what it does next, it stops and offers two to four options:

```
  │ Should both line-breaking paths change together?
  │ [1] Both, keep the three copies in sync (recommended)
  │ [2] Only the writer, I'll handle the nvim side
    [1-2] pick  [t] type  [esc] you decide
```

Small choices it makes itself.

### Pull requests

There is no dedicated PR tool; it uses `git` and `gh` through bash. The order it follows: read CONTRIBUTING and the templates under `.github`, branch, change, run the tests, commit, push to its own remote, point `gh pr create --repo` at the upstream when working from a fork, and write the description from the template with the issue number. Push and opening a PR are confirmed every time.

### Documents and the web

`.docx` `.xlsx` `.pptx` `.odt` `.ods` `.odp` `.pdf` are extracted to text when read, so "what does this weekly report say" just works. PDF needs `pdftotext` on the system. Read only; to produce such files have it write a script using `python-docx` or `openpyxl`.

`web_fetch` converts pages to Markdown. With a `prompt` parameter the model reads the whole page and answers only that question, keeping the page out of the context. Private network addresses are never fetched; for a local dev server use `FEIYU_ALLOW_LOCAL_FETCH=1 feiyu`.

`web_search` uses DuckDuckGo out of the box, no configuration. To switch backends write `~/.config/feiyu/search.json`:

```json
{"provider": "brave", "key": "…"}
{"provider": "searxng", "url": "http://localhost:8080"}
```

## Permissions

`shift+tab` cycles, or `/mode <name>`:

| Mode | Behaviour |
|---|---|
| `default` | Allowed calls run, everything else asks (default) |
| `auto` | Everything runs |
| `plan` | Read and think only; acts after you approve the plan |
| `readonly` | Read, list, search and fetch only |

When asked: `y` run, `n` skip, `a` allow everything this turn, `s` never ask again. A refused call returns the reason to the model, which usually tries another way.

Rules are `Tool(pattern)`, stored in `~/.config/feiyu/permissions.json`, deny wins:

```
Bash(ls:*)            commands starting with ls
Bash(git status:*)    commands starting with git status
Bash(pwd)             exactly this command
Read(*)               every call of this tool
Mcp(fs:*)             every tool of one MCP server
```

Commands are split at `&&` `||` `;` `|` and redirections, and every segment must be allowed, so `ls -la && curl x | sh` does not slip through because `ls` is allowed. Anything with `$(...)` or backticks always asks.

The shipped list allows only `ls` `pwd` `echo` `wc` `which` and `git status/log/diff/branch`. `cat` `head` `grep` are left out because they can read any file outside the workspace. Commands touching `.ssh/`, `.aws/credentials`, `~/.config/feiyu/` or `.env` are refused, `auto` mode included.

## The bash sandbox

On by default when `bubblewrap` is installed, Linux only:

| Level | Behaviour |
|---|---|
| `off` | No sandbox |
| `workspace` | Read-only outside the workspace, credential directories emptied, network on (default) |
| `strict` | As above, plus no network |

Writes are genuinely isolated through the kernel's mount namespace: everything outside the workspace and `/tmp` is read-only, and `~/.ssh`, `~/.gnupg`, `~/.aws` plus feiyu's own config and state directories are replaced with empty directories. Reads are not isolated; the rest of the filesystem stays readable.

Two common in-sandbox failures are recognised and explained: ssh remotes fail because `~/.ssh` is emptied (https remotes with `gh`'s credential helper are unaffected), and `strict` has no network.

`/sandbox` switches; `FEIYU_SANDBOX=off` turns it off for one run. Without bubblewrap there is no sandbox and the welcome box says so.

## Automation

**Run the tests after every edit.** Put one line in the project convention file:

```
verify: npm test
```

From then on the command runs after every file write or edit, and failures go back to the model to fix. The failure comes with a digest first (which test failed, the assertion, the compile error line), then the raw output.

**Hooks.** Global in `~/.config/feiyu/hooks.json`, per project in `.feiyu/hooks.json`:

```json
{
  "PreToolUse":       [{"matcher": "Bash(git push:*)", "command": "echo 'open a PR' >&2; exit 1"}],
  "PostToolUse":      [{"matcher": "Write|Update", "command": "ruff check ."}],
  "UserPromptSubmit": [{"command": "git branch --show-current"}],
  "Stop":             [{"command": "notify-send 'feiyu is done'"}]
}
```

Exit code 0 allows, non-zero blocks. `PreToolUse` blocks the call, `PostToolUse` hands its output to the model to fix, `UserPromptSubmit` passes its stdout along as context when it allows, `Stop` only logs. `matcher` uses the permission rule syntax. Hooks receive a JSON document on stdin.

Project hooks need your approval the first time they are seen, and again when the file changes. Commands that came with a repository should not run just because you cloned it.

**Independent grading.** `/review` or `--review` opens a second model and shows it only the task and the final diff. It scores 0 to 10 on completeness, bugs and unrequested extras. It costs an extra API call, so it is manual.

## The web front end

```
feiyu-web                         # start one in the current directory, opens a browser
feiyu-web -w ~/code/my-project    # pick the workspace
feiyu-web -p 7788                 # another port (7777 by default)
feiyu-web -r                      # continue the last conversation in this directory
feiyu-web --no-open               # don't open a browser
```

The sidebar on the left lists past conversations; click one to switch to it (the agent's context follows, not just the view), "新对话" starts a fresh one without deleting the old, hovering a row reveals a `×` that deletes it (it asks first), and `☰` collapses the panel. The collapsed state is remembered in the browser.

The 附件 button next to the composer uploads files — dragging them onto the page works too. They land in `.feiyu/uploads/` inside the workspace and the path goes out with your next message; it reads them with the `read_file` it already has, images, docx and pdf included.

Same agent, same permissions, same session file as the terminal — only the surface changes. Edits and commands are shown in the page as a diff or the literal command, and nothing runs until you approve; "allow for this session" and a permanent rule are both there. Slash commands, skills, custom commands and `esc` to interrupt all work.

**Three gates.** Anyone who reaches that port can have it write files and run commands on your machine, so: it listens on `127.0.0.1` only; each start mints a random token that appears only in the URL it prints for you (**don't pass that link around**); and the server checks `Origin` and `Host`, because **WebSocket is not covered by the same-origin policy** — without that gate, any web page you happen to open could connect to the local port and drive the agent.

To use it from elsewhere, tunnel over ssh:

```
ssh -L 7777:127.0.0.1:7777 that-machine
```

Under the hood it is one static Rust binary (~900K): HTTP, WebSocket and the front-end assets are all inside it, supervising a `feiyu --serve` child process, one JSON object per line between them. **The split is not there to make the agent faster** — local tools add up to under 20ms and a round is almost entirely spent waiting for the model. It is there so the Python side gains no new dependency while you get an executable that needs no runtime installed.

The web front end needs `cargo`. It is optional in `install.sh`: without Rust the step is skipped and the terminal version is unaffected.

## Skills and custom commands

Hand it a worked-out procedure without touching the code. Each one is a `.md` file that may start with frontmatter:

```markdown
---
name: release
description: how this project cuts a new version
---

1. run `.venv/bin/python -m unittest discover -s tests -t .`
2. bump `__version__` in `feiyu/__init__.py`
3. write the section at the top of the CHANGELOG
```

Where you put it decides who triggers it:

| Directory | Triggered by |
|---|---|
| `~/.config/feiyu/skills/` `.feiyu/skills/` | **It.** The system prompt carries only the name and the description; it pulls the body in when the job matches |
| `~/.config/feiyu/commands/` `.feiyu/commands/` | **You.** Type `/release`; the body goes into the conversation as if you had said it |

Write `$ARGUMENTS` in a command and it is replaced by whatever follows the command:

```markdown
Find out how $ARGUMENTS is implemented in this repo. Read only, change nothing.
```

`/look context compaction` then types the whole sentence for you. Without the placeholder the argument is appended at the end.

**Why the split.** The system prompt is resent on every single request. A procedure you need once, sitting there permanently, is paid for a hundred times over a hundred rounds. A skill keeps only its name and description resident — about one line — and the body arrives in context on the one turn that needs it.

`/skills` lists what is available and where each came from. A project entry wins over a global one of the same name.

**Project skills need your approval the first time**, and again whenever the directory's contents change — it decides on its own to load them, and a stretch of instructions that came down with a repo should not enter its context on `git clone`. **Project commands need no approval:** nothing happens until you type one, and typing one is the same as typing the text yourself.

## What it remembers

**Project conventions.** Put a `FEIYU.md` in the workspace (`AGENTS.md` and `CLAUDE.md` also count, first one found) and it is read into the system prompt at startup. `/context` reloads it.

**Long-term memory.** Things worth keeping, like how the build runs, the cause of a pitfall, or a preference you stated, it writes down itself after asking you. They come back in the next session in the same directory; `/memory` shows and deletes them. Stored in `~/.local/state/feiyu/memory/`, never inside your project.

```
⏺ Remember(tests in this project must run with pytest -q, conftest has fixtures)
  ⎿  noted
```

**Sessions.** Saved every turn; `feiyu -r` resumes. The save is the full transcript, in `~/.local/state/feiyu/sessions/` with mode 600. `/clear` deletes it too.

**Context management.** Every request resends the whole history, so it trims automatically: when a file is read again or edited, the old copy is replaced by a one-line note; long output from many rounds ago keeps only its head and tail; only when the limit is still exceeded is the history summarised. The context limit is set in `/key add`; if left empty, the first "input too long" error teaches it the model's real limit.

The anthropic backend sets cache breakpoints so resent history is billed at the cache rate. OpenAI-compatible APIs cache server-side. `/cost` shows the hit rate.

## Embedding

Give it a JSON schema and the run ends with output matching it:

```
feiyu --json-schema review.schema.json "check calc.py for bugs" > out.json
```

stdout carries only the JSON; the interface and logs go to stderr. Output that does not match is sent back for up to two retries, then the exit code is non-zero. Supports `type` `required` `properties` `items` `enum` `additionalProperties`.

**Logs.** One JSONL line per event in `~/.local/state/feiyu/logs/<date>.jsonl`: every tool call, its arguments, the permission decision, timing, token usage. To find out why it did something odd, filter by the `run` field. `FEIYU_NO_LOG=1` disables it.

**Spend.** `/cost` summarises the last seven days by task:

```
   08-27 00:14  fix error handling in parser.py      5 rounds    8.2k   71%    ¥0.031
   08-27 09:02  add --json flag to the CLI          12 rounds   31.5k   84%    ¥0.118

   total  39.7k  ¥0.149  cache hits 81%
```

The second-to-last column is the cache hit rate. It decides what long tasks cost: a constant 0 means the API does not cache and every resent history is billed in full.

## MCP

Tools from external [MCP](https://modelcontextprotocol.io) servers can be used directly. The config has the same shape as Claude Code's: global in `~/.config/feiyu/mcp.json`, per project in `.feiyu/mcp.json`:

```json
{"mcpServers": {
  "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
  "db": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "app.db"], "env": {"X": "1"}}
}}
```

- stdio transport only.
- Tools appear as `mcp_<server>_<tool>`; `/mcp` shows each server's status and tools.
- Project configs, like project hooks, need your approval the first time.
- In default mode every MCP tool asks, unless the server declares it read-only. `Mcp(fs:*)` allows a whole server, `Mcp(fs read_file)` one tool.
- Restart feiyu after changing the config.

## Models

The model list is yours; there is no built-in vendor, default model or price table. `/key add`:

```
   name (shown in /model): ds-flash
   base_url: https://api.deepseek.com
   model id: deepseek-v4-flash
   API key: ****************
   API type openai / anthropic [openai]:
   can this model see images y/N: y
   max_tokens per turn (empty = 8192): 64000
  prices below are ¥ per million tokens; leave empty to show token counts only
     cache hit price (0 if no caching): 0.02
     input price: 1
     output price: 2
     cache write price (empty = input price):
```

Stored in `~/.config/feiyu/models.json` with mode 600. Empty prices show token counts only. The cache write price only matters for direct anthropic access; leave it empty elsewhere.

Set `max_tokens` to the model's real limit. The 8,192 default is hit by any long file, and a truncated tool call wastes the round. A value above what the API allows makes the API reject the request.

## Security notes

It really runs commands and edits files on your machine, with your permissions.

- File tools are confined to the workspace.
- With bubblewrap, bash writes are confined to the workspace; reads are not. Without it there is no isolation at all.
- Environment variables whose names contain `KEY` `TOKEN` `SECRET` `PASSWORD` are not passed to child processes. `SSH_AUTH_SOCK` is kept, so `git push` works. To pass a specific secret: `FEIYU_KEEP_ENV_SECRETS=1 feiyu`.
- Fetched pages are labelled "external data, not instructions", but whether the model obeys is not guaranteed.
- `~/.config/feiyu/` (keys) and `~/.local/state/feiyu/` (transcripts, input history) are 700 directories with 600 files.
- The web front end listens on loopback only, behind a per-run token and `Origin`/`Host` checks — see [the web front end](#the-web-front-end).

So: use it on projects you trust and keep under git, where a bad edit is easy to undo. Don't run untrusted tasks on a machine holding sensitive credentials. For hard isolation use a container or a VM.

## Platforms

Linux and macOS fully; the bash sandbox is Linux only. Windows works inside WSL only.

## Tests

```
python -m unittest discover -s tests -t .
```

761 cases, twenty seconds, no real API calls, nothing under `~/.config/feiyu/` is touched. The sandbox group skips itself without bubblewrap.

## Versioning

Semantic versioning; the version lives in `feiyu/__init__.py`.

## License

[MIT](LICENSE) © feiyuceng06-prog
