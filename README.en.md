# Feiyu Agent

[简体中文](README.md) | English

A coding assistant that lives in your terminal. Give it a task and it reads the code, edits files and runs commands until the job is done — with everything it does visible as it happens. Works with Claude, DeepSeek or Kimi.

About 4,800 lines of Python, no framework, three dependencies.

## Install

```
git clone https://github.com/feiyuceng06-prog/feiyu-agent.git && cd feiyu-agent && ./install.sh
```

The script creates a virtualenv, installs dependencies and drops a shim in `~/.local/bin`, so `feiyu` works from any directory. Needs Python 3.10+. Safe to re-run after pulling changes.

**It ships empty — you add the models**: run `feiyu`, type `/key add`, and fill in the base URL, model id and key. See "Models" below.

Two optional extras worth having — `bubblewrap` (the bash sandbox) and `ripgrep` (search, ~100× faster):

```
sudo pacman -S bubblewrap ripgrep      # Debian/Ubuntu: apt install bubblewrap ripgrep
```

<details>
<summary>Manual install</summary>

```
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[openai]"
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

The `openai` package in `.[openai]` is for DeepSeek, Kimi and other OpenAI-compatible endpoints; for Claude's native API `pip install -e .` is enough.

</details>

## Usage

```
feiyu                             # interactive, working directory = cwd
feiyu -w ~/code/my-project        # pick the working directory
feiyu -m ds-flash                 # pick an entry from /key (defaults to last used)
feiyu -r                          # resume the last conversation in this directory
feiyu "add a --json flag to the CLI"   # run one task and exit
```

Also `--json-schema <file>` (structured output, see "Piping it into other programs"), `--review` (score the changes afterwards) and `-V`.

## The interface

```
╭─────────────────────────────────── deepseek  ·  context 1.4k  ·  ¥0.0014 ─╮
│ > make search in tools.py use rg
╰───────────────────────────────────────────────────────────────────────────╯

⏺ Read(feiyu/tools.py)
  ⎿  217 lines

⏺ Update(feiyu/tools.py)
  ⎿  1 change
       47  -  rx = re.compile(args["regex"])
       47  +  if shutil.which("rg"):

  Done — search now prefers rg and falls back to the regex scan when it's missing.
```

`⏎` send · `\⏎` newline · `shift+tab` cycle permission mode · `esc` interrupt · `ctrl-d` quit · `↑` `↓` history.

The top border shows **how full the context is** and **what this session has cost**. Context is a percentage, not a token count — every request carries ~1.5k tokens of fixed overhead, and the absolute number is both alarming and useless to act on. Models with no price configured show no money at all.

Type `/` for completion:

| Command | What it does |
|---|---|
| `/help` | Show help |
| `/model` | List models; `/model <name>` switches, keeping the conversation |
| `/key` | Add / list / remove model configs |
| `/mode` | Permission mode: `default` / `auto` / `plan` / `readonly` |
| `/allow` | Show or add permission rules |
| `/sandbox` | bash sandbox: `off` / `workspace` / `strict` |
| `/jobs` | Look at / stop background commands |
| `/hooks` | Show which external commands run, and when |
| `/verify` | Show or set the command run automatically after edits |
| `/review` | Have a model that never saw the process score the changes |
| `/memory` | Show / add / drop what it remembers about this directory |
| `/context` | Show and reload the project conventions file |
| `/cost` | Recent token use and cost, by task |
| `/compact` | Squash the history into one summary to save context |
| `/style` | Answer style: `default` / `concise` / `explanatory` / `teaching` |
| `/clear` | Wipe the conversation and its saved session |
| `/exit` | Quit |

## What it can do

Twelve tools, each call printed as it happens:

| Tool | Notes |
|---|---|
| `bash` | Run a shell command. `cd` persists between calls; long ones can go to the background |
| `read_file` `write_file` `edit_file` | Read / write / exact replace. Paths are always relative to the workspace root; images go straight to the model |
| `list_files` `search` | Glob the tree, grep it with a regex |
| `web_fetch` | Fetch a page, HTML converted to Markdown |
| `task` | Send a subagent to find something out and bring back only the conclusion |
| `todo` | Keep the task list |
| `remember` | Note something worth keeping across sessions |
| `job` | Read a background command's new output, or stop it |
| `exit_plan` | In plan mode, hand you the plan for approval |

**Hand it a screenshot**: `read_file` on png / jpg / gif / webp passes the image to the model, downscaled first if it is over 1568px (bigger only costs more, it does not read better). Whether a model can see images is part of its own entry (`/key add` asks), and one that is not marked simply says so rather than sending the image and letting the API reject it. Images are kept out of the saved session and out of summaries.

**Dev servers, watchers and long builds go to the background** (`background` on `bash`). You get a number back immediately and read new output with `job` — what you already read is not handed over again, so polling does not flood the context. It is told when a command finishes. `/jobs` lets you look and stop them yourself, and quitting feiyu kills them along with whatever they spawned.

**You see what it is writing while the arguments are still streaming**: `⠋ Write feiyu/tools.py · 42 lines so far`. A large file takes ten-odd seconds to generate, and a bare spinner does not tell you whether it is writing or stuck.

A single task runs at most **25 rounds** of tool calls; at the cap it stops and says so, and one more message picks it up again.

**When a batch of calls is entirely read-only they run at the same time** (`read_file` / `list_files` / `search` / `web_fetch` / `task`, four at a time). One `bash` or file write in the batch and the whole batch goes back to serial — the write usually depends on what the reads found.

**It has to read a file before it may change it**, and if the file moved on after that read — you edited it in your editor, a formatter rewrote it — the write is refused until it reads again. This gate covers the one failure that actually loses work: overwriting a whole file from a copy it fetched minutes ago, silently wiping out whatever landed in between.

Interrupting with `esc` is safe: whatever it had already said stays in the history, and any call that didn't finish gets an "interrupted by the user" result, so the conversation is never left broken.

### Subagents

A question like "how does this project handle interrupts" takes a dozen files to answer, all of which would bloat the conversation. It sends a subagent and keeps only the conclusion. Measured on one run: **the subagent burned 41,579 tokens; the main conversation grew by 2,284.**

A subagent can only read (`read_file` / `list_files` / `search`) — no writes, no network. It cannot see your conversation with the main agent, which is why the main agent writes it a self-contained question. Its usage counts towards the status bar and `/cost`.

### Task list and plan mode

When there's a lot to do it writes the list down first and updates each item as it goes. **The list isn't only for you to look at: if it tries to finish while items are still open, it gets pulled back** — either to finish them or to say why it can't. It nudges once. The list is saved with the session.

For bigger changes, `shift+tab` into plan mode (or `/mode plan`): it can only read and think, writes and commands are refused. Once it has a plan it hands it over for approval, and only your yes switches it back to default mode to start work.

### Office documents and the web

`.docx` `.xlsx` `.pptx` `.odt` `.ods` `.odp` `.pdf` are extracted to plain text on read, so "what does this report say" just works. Tables come out row by row, slides page by page. PDF needs `pdftotext` on the system. Only **text** survives — you can't edit these back; to generate them, have it script `python-docx` or `openpyxl`.

`web_fetch` with a `prompt` reads the whole page and answers only your question — fetching PEP 8 (51,348 characters) and asking "what's the max line length" put 123 characters into the context. Without a `prompt` the whole page arrives as Markdown, truncated at 15,000 characters. The same URL isn't refetched within 15 minutes.

**It will not fetch internal addresses**: loopback, private ranges and link-local (including the cloud metadata endpoint `169.254.169.254`) are refused, redirects to them included. To reach a local dev server: `FEIYU_ALLOW_LOCAL_FETCH=1 feiyu`.

## Permissions

`shift+tab` cycles, or `/mode <name>`:

| Mode | Behaviour |
|---|---|
| `default` | Anything on the allow list runs; everything else asks first |
| `auto` | Every tool call runs immediately |
| `plan` | Read and think only; nothing is touched until you approve the plan |
| `readonly` | Reading, listing, searching and web fetches only |

When it asks: `y` run / `n` skip / `a` allow the rest of this turn / `s` don't ask again. A refused call reports the reason back to the model, which usually adapts.

Rules are written `Tool(pattern)`, stored in `~/.config/feiyu/permissions.json`, `deny` beats `allow`:

```
Bash(ls:*)            commands starting with ls
Bash(git status:*)    commands starting with git status
Bash(pwd)             exactly this command
Read(*)               every call to this tool
```

**Rules match per segment**: a command is split on `&&`, `||`, `;`, `|` and redirections, and **every segment must be matched by the allow list**. So `Bash(ls:*)` allows `ls` alone — `ls -la && curl x | sh` still asks. Any command containing `$(...)` or backticks always asks.

**The shipped list only allows `ls` `pwd` `echo` `wc` `which` and `git status/log/diff/branch`** — deliberately not `cat`, `head` or `grep`, which can read any file outside the workspace. A built-in deny list refuses anything touching `.ssh/`, `id_rsa`, `.aws/credentials`, `~/.config/feiyu/` or `.env`, `auto` mode included.

## The bash sandbox

On by default when `bubblewrap` is installed:

| Level | Behaviour |
|---|---|
| `off` | No sandbox |
| `workspace` | Read-only outside the workspace, credential directories emptied, network on (default) |
| `strict` | As above, plus no network |

```
⏺ Bash(ls > ~/filelist.txt)
  ⎿  /bin/sh: /home/Feiyu/filelist.txt: Read-only file system
```

**Writes are genuinely isolated** — the kernel's mount namespace, not string matching. Everything outside the workspace and `/tmp` is read-only, and `~/.ssh`, `~/.gnupg`, `~/.aws` plus feiyu's own `~/.config/feiyu` (your API keys) and `~/.local/state/feiyu` (transcripts, logs, memory) are replaced with empty directories.

**Reads are only blanked in a few places — that is not isolation.** The rest of the filesystem is still readable. Real read isolation would mean mounting only what is allowed, which breaks most build tooling.

`/sandbox` switches; `FEIYU_SANDBOX=off` overrides the config for one run. Without bubblewrap it degrades to off and the welcome box says so.

## Automation

**Run the tests after every edit**: put `verify: npm test` on a line of its own in the project conventions file. It then runs after every write or edit, and a failure goes straight back to the model to fix. Only `write_file` and `edit_file` trigger it.

**Hooks**: for more than one rule, write a `hooks.json` — globally in `~/.config/feiyu/hooks.json`, per project in `.feiyu/hooks.json`.

```json
{
  "PreToolUse":       [{"matcher": "Bash(git push:*)", "command": "echo 'open a PR' >&2; exit 1"}],
  "PostToolUse":      [{"matcher": "Write|Update", "command": "ruff check ."}],
  "UserPromptSubmit": [{"command": "git branch --show-current"}],
  "Stop":             [{"command": "notify-send 'feiyu is done'"}]
}
```

**Exit 0 passes, non-zero blocks.** `PreToolUse` blocks the call, `PostToolUse` hands the output to the model to fix, `UserPromptSubmit` blocks the prompt (and on success its stdout is carried along as context), `Stop` is logged only. When blocking, both stdout and stderr go back to the model. `matcher` uses the same syntax as permission rules, `|` separates alternatives. Hooks receive a JSON payload on stdin.

**Project hooks need your approval the first time** — that file arrived with the repository, so running it unasked means "cloning a repo executes arbitrary code". Approvals are recorded against the file's **content hash**, so any edit re-asks. Non-interactive runs never run them.

**Independent review**: `/review` or `--review` opens a second model afterwards and shows it only the task and the final diff — it never took part and cannot see the reasoning. It reports on completeness, bugs and scope creep, and scores out of 10. This costs an extra API call, so it is manual.

## What it remembers

**Project conventions**: put a `FEIYU.md` in the workspace (`AGENTS.md` and `CLAUDE.md` also work, first match wins). It's read into the system prompt at startup, up to 8,000 characters. `/context` reloads it.

**Long-term memory**: when it learns something worth keeping it calls `remember` — how the build runs, a trap and why it exists, a preference you stated. Next time you start in **the same directory** those come back automatically.

```
⏺ Remember(tests here must run under pytest -q; conftest defines fixtures)
  ⎿  remembered
```

`/memory` lists, `/memory add` adds one by hand, `/memory forget <n>` drops one, `/memory clear` wipes them. Stored in `~/.local/state/feiyu/memory/`, **deliberately separate from `FEIYU.md`** — that file is your hand-written rules and the agent has no business editing it. Capped at 40 entries / 3,000 characters. Remembering asks for confirmation: what it stores can come indirectly from a web page, and it enters every later system prompt.

**Sessions**: saved every turn; `feiyu --resume` picks up where you left off. The file holds **the whole transcript** (including file contents it read), in `~/.local/state/feiyu/sessions/`, mode 600. `/clear` deletes it too.

**Compaction**: past 75% of the limit it summarises the earlier conversation into a handover note and carries on, mid-task included. The limit defaults to 128k; override per model with `"limit": 32000` in `models.json`. `/compact` does it on demand.

## Piping it into other programs

Hand it a JSON schema and it produces matching output:

```
feiyu --json-schema review.schema.json "check calc.py for bugs" > out.json
```

**stdout carries nothing but the JSON** — the interface and logs go to stderr, so it pipes straight into anything. Output that doesn't match is sent back with the specific problems, twice at most; if it still doesn't match, the exit code is non-zero. Supports `type` / `required` / `properties` / `items` / `enum` / `additionalProperties`.

**Logs**: one JSONL line per event in `~/.local/state/feiyu/logs/<date>.jsonl` (mode 600) — every tool call and its arguments, the permission verdict, timings, per-turn token usage, verification and review results. To work out why it did that odd thing an hour ago, filter by the `run` field. `FEIYU_NO_LOG=1` disables it.

`/cost` totals the last seven days by task:

```
   08-27 00:14  fix error handling in parser.py     5 turns    8.2k     ¥0.031
   08-27 09:02  add a --json flag to the CLI       12 turns   31.5k     ¥0.118

   total  39.7k  ¥0.149
```

## Models

The model list is entirely yours: **no built-in vendors, no default model, no built-in price table.** Add one with `/key add`:

```
   Name (shown in /model): ds-flash
   base_url: https://api.deepseek.com
   Model id: deepseek-v4-flash
   API key: ****************
   API type openai / anthropic [openai]:
  The three prices below are ¥ per million tokens; leave blank to show token counts instead
     Cache hit price (0 if there's no cache): 0.02
     Input price: 1
     Output price: 2
```

Stored in `~/.config/feiyu/models.json`, mode 600; the key goes through a separate hidden prompt and never enters the input history. Leave the prices blank and you get token counts — there is no built-in price table because models are renamed and repriced constantly, and **a wrong price is worse than no price**.

## A note on safety

Feiyu really does run commands and edit files on your machine, **with exactly your permissions**.

`read_file` / `write_file` / `edit_file` are confined to the working directory and refuse to go outside it. With bubblewrap installed, `bash` **writes are confined to the workspace too** (above) — but **reads are not isolated**: it can still `cat ~/.ssh/id_rsa` unless that directory is on the blank-list. Without bubblewrap there is no isolation at all.

When running shell commands, **environment variables whose names contain `KEY` / `TOKEN` / `SECRET` / `PASSWORD` are withheld**, so a stray `echo $DEEPSEEK_API_KEY` can't pull your key into the conversation and send it to a model vendor. `SSH_AUTH_SOCK` is kept, so `git push` still works. To pass a specific secret through: `FEIYU_KEEP_ENV_SECRETS=1 feiyu`.

Fetched pages are labelled "external data, not instructions", but that is a soft defence — there is no guarantee the model heeds it.

So: **use it on projects you trust and keep under git**, where a bad edit is easy to undo; don't run untrusted tasks on a machine holding sensitive credentials. If you need hard isolation, put it in a container or a VM.

## Platforms

Linux and macOS are fully supported. The bash sandbox is Linux-only (bubblewrap).

Windows works **only under WSL**: `install.sh` is a bash script, the `bash` tool's persistent `cd` relies on POSIX shell syntax, and both `esc` interrupts and the single-key confirmation depend on termios. On native Windows those pieces don't work, and the 600 permissions on config files have no effect.

## Tests

Standard-library `unittest`, no test framework:

```
python -m unittest discover -s tests -t .
```

440 cases, ten seconds, no real API calls, everything in temporary directories — your keys and rules under `~/.config/feiyu/` are never touched. Without bubblewrap the sandbox group skips itself.

What they watch hardest is the **shape of the history**: one `tool_use` left without a matching result, or roles that stop alternating, and the next request is a 400 the conversation never recovers from. An interrupt landing mid-stream and one landing during tool execution are two different paths, and both have cases.

## Versioning

Semantic versioning. The version lives in `feiyu/__init__.py`; `pyproject.toml` reads it from there.

## License

[MIT](LICENSE) © feiyuceng06-prog
