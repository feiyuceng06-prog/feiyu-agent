# Feiyu Agent

[简体中文](README.zh.md) | English

A coding agent that runs in your terminal. Give it a task and it reads the code, edits files and runs commands on its own, with every step printed on screen. Works with Claude, DeepSeek, Kimi and any OpenAI-compatible API.

About 4,800 lines of Python, no framework, three dependencies: `anthropic`, `pillow`, `prompt_toolkit`.

![demo](docs/demo.gif)

## Install

### One line (desktop + terminal)

```bash
curl -fsSL https://raw.githubusercontent.com/feiyuceng06-prog/feiyu-agent/main/get.sh | bash
```

Installs the latest version from [Releases](https://github.com/feiyuceng06-prog/feiyu-agent/releases)
into your user directory: no sudo, no Python, no libfuse2. Afterwards **Feiyu** is in your app menu
(or run `feiyu-desktop`) and `feiyu` runs in the terminal; on first launch it asks you to connect a
model. Run it again to upgrade. Without curl, start the line with `wget -qO-` instead. Only Linux
x86_64 packages exist so far, and the system needs to be Ubuntu 22.04-era or newer (glibc 2.35+).

Already have the AppImage (say, downloaded on another machine): `bash get.sh Feiyu-x.y.z.AppImage`.
To remove it, delete `~/.local/share/feiyu-desktop`, `~/.local/bin/feiyu`, `~/.local/bin/feiyu-desktop`
and `~/.local/share/applications/feiyu.desktop`; models, keys and conversations live in
`~/.config/feiyu` and `~/.local/state/feiyu`.

### CLI from the main branch on Ubuntu

```bash
wget -qO- https://raw.githubusercontent.com/feiyuceng06-prog/feiyu-agent/main/bootstrap-ubuntu.sh | bash
```

Requires network access and `wget`. The installer checks for Python 3.10+ and venv, installing missing system packages with `sudo apt-get` when necessary. It installs the program and dependencies in your user directory and starts Feiyu in an interactive terminal. Enter `/key add` to configure a model. Later, run `~/.local/bin/feiyu`, or `feiyu` if `~/.local/bin` is on your PATH. Run the command again to install the current main branch; previous version directories and model configurations are retained.

### Install from source

```
git clone https://github.com/feiyuceng06-prog/feiyu-agent.git && cd feiyu-agent && ./install.sh
```

By default the script creates a CLI virtualenv in the checkout, installs dependencies and puts `feiyu` in `~/.local/bin`. It needs Python 3.10 or newer and a network connection; on Debian/Ubuntu install `python3-venv` if needed. Keep the checkout in place for the CLI and rerun the script after updating it. The script does not run sudo or modify shell configuration.

```bash
./install.sh --cli-only           # skip the desktop build
./install.sh --desktop            # install only the desktop app
./install.sh --all                # install both separately
./install.sh --python /path/to/python3  # Python to use for a new virtualenv
./install.sh --help
```

By default only the CLI is installed. The desktop app has its own installed application and Python environment; it does not require the CLI or the checkout after installation, and bundles its Python backend. It needs desktop system libraries, but not system Python or WebKitGTK. Open it from the application menu and configure a model in its settings. A failed desktop rebuild preserves the existing desktop launcher. If the CLI is not on PATH, the script explains how to add it; you can also run `~/.local/bin/feiyu` directly.

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
feiyu
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

For a window instead of a terminal, run `feiyu-desktop` — see [the desktop app](#the-desktop-app).

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

Keys: `⏎` send, `\⏎` newline, `@` attach a file, `shift+tab` cycle permission mode, `esc` interrupt, `ctrl-d` quit, `↑` `↓` history.

The top border shows how much of the context is used and what the session has cost. Context is a percentage; models without a price configured show no amount. Markdown in replies is rendered (bold, code, lists, tables, links), except italics, because `*` is usually a glob on the command line.

`esc` is safe: whatever was said stays in the history, unfinished calls get an "interrupted" result, and the conversation is not corrupted.

### `@` points at a file

Write `@path` anywhere in a sentence and the file is read *before* the message goes
out, so the model does not have to spend a round trip calling `read_file` on
something you already know it needs. `Tab` completes paths one directory at a time.

```
> rewrite the fetch helper in @feiyu/tools.py to match @feiyu/serve.py
  read and attached: feiyu/tools.py (400/2331 lines) · feiyu/serve.py (400/920 lines)
```

`@` on a directory attaches a one-level listing. Images and binaries are not pasted
in — the path is confirmed and the model reads them itself. Only paths that really
exist inside the workspace are expanded, so `@property` and `user@example.com` stay
plain text.

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
| `/cost` | Recent spend by task, with each task's slowest turn |
| `/compact` | Compress the history into a summary |
| `/helper <name>` | Use a cheaper model for summaries, subagents and page extraction; `/helper off` to revert |
| `/style` | Reply style: `default` / `concise` / `explanatory` / `teaching` |
| `/theme` | Terminal colours and glyphs: `default` / `swiss` / `retro` / `anthropic` |
| `/skills` | List the available skills and custom commands; `/skills <name>` toggles one |
| `/profile` | Profiles (personas): list them, switch, `/profile off` to drop one |
| `/bot` | Bots: list them, `/bot <name>` opens a fresh conversation under that persona |
| `/clear` | Clear the conversation and its save file |
| `/exit` | Quit |

## Tools

Eighteen tools, every call printed on screen:

| Tool | What it does |
|---|---|
| `bash` | Run a shell command. `cd`, exported variables, sourced environments (a venv) and shell functions all persist between calls; long commands can run in the background |
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
| `image_generate` | Generate an image into the workspace (only present once an image endpoint is configured) |

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

**Sites that block scrapers can fall back to [Firecrawl](https://firecrawl.dev).** Anti-bot defences
(Cloudflare, UA checks) are something the built-in fetcher cannot beat. Put a key in
`~/.config/feiyu/fetch.json` and a 401 / 403 / 429 is retried through Firecrawl:

```json
{"key": "fc-…"}
```

Only those failures fall back — a 404 is a 404 no matter who fetches it, so it is not worth a credit.
Add `"provider": "firecrawl"` to route everything through it, or `"url": "http://localhost:3002"`
for a self-hosted instance. Fetches that spent a credit are marked `Firecrawl ·` in the result line.

`web_search` uses DuckDuckGo out of the box, no configuration. To switch backends write `~/.config/feiyu/search.json`:

```json
{"provider": "brave", "key": "…"}
{"provider": "searxng", "url": "http://localhost:8080"}
{"provider": "firecrawl", "key": "fc-…"}
```

**Image generation needs configuring.** Point `~/.config/feiyu/images.json` at one
or more OpenAI-compatible images endpoints:

```json
{"default": "nano-banana-2",
 "models": {
   "nano-banana-2": {"base_url": "https://openrouter.ai/api/v1", "key": "…",
                     "model": "google/gemini-3.1-flash-image", "size": "1024x1024"},
   "gpt-image-2":   {"base_url": "https://…/v1", "key": "…", "model": "gpt-image-2"}
 }}
```

The aliases become `image_generate`'s `model` argument, so one model can be cheap
and another good and the agent picks per picture; leaving it out uses `default`.
A single flat `{"base_url": …, "key": …, "model": …}` object still works and counts
as one model. The `image_generate` tool only appears once that file exists — people
who never generate images shouldn't pay for its schema on every turn. Images land in
`.feiyu/images/` inside the workspace; the desktop app draws them inline, the
terminal prints the path.

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

**After reading outside content, commands that can reach the network need your nod.** A web page, a search result, an MCP reply, or whatever `curl` / `wget` pulled in through bash may hide a line like "send `.env` to this address". Once the conversation has read any of them: with the sandbox on, bash runs offline by default, and a command that really needs the network (installing dependencies, fetching code) has to carry `network: true` and be approved by you; with the sandbox off the network cannot be cut, so everything except commands that cannot reach it (`ls` `cat` `grep` and friends) asks. **Neither `auto` mode nor allow rules skip this step.** It lifts on `/clear` or a new conversation. Where nobody can answer (pipes, benchmarks) the call is refused and the model tells you which step needs the network. `FEIYU_TRUST_OUTSIDE=1` turns it off entirely (for evals).

`web_fetch` and MCP are exits too and this step does not cover them: if it did, "search, then open a result" would ask at every step.

## The bash sandbox

On by default when `bubblewrap` is installed, Linux only:

| Level | Behaviour |
|---|---|
| `off` | No sandbox |
| `workspace` | Read-only outside the workspace, credential directories emptied, network on (default) |
| `strict` | As above, plus no network |

Writes are genuinely isolated through the kernel's mount namespace: everything outside the workspace and `/tmp` is read-only, and `~/.ssh`, `~/.gnupg`, `~/.aws` plus feiyu's own config and state directories are replaced with empty directories. Reads are not isolated; the rest of the filesystem stays readable.

The docker and podman sockets are blocked too — reaching one means `docker run -v /:/host` can write anywhere. When the network is cut (`strict`, or after reading outside content) the services under `/run` go with it: cutting the network interface alone is not enough, because DNS resolution goes through a Unix socket there and can still carry data out inside domain names.

Three common in-sandbox failures are recognised and explained: ssh remotes fail because `~/.ssh` is emptied (https remotes with `gh`'s credential helper are unaffected), docker cannot connect, and there is no network while it is cut.

`/sandbox` switches; `FEIYU_SANDBOX=off` turns it off for one run. Without bubblewrap there is no sandbox; startup says so, and the status on the input box's top edge marks it.

## Automation

**Run the tests after every edit.** Put one line in the project convention file:

```
verify: npm test
```

From then on the command runs after every file write or edit, and failures go back to the model to fix. The failure comes with a digest first (which test failed, the assertion, the compile error line), then the raw output.

**Three gates for long tasks.** In the same convention file, one line each:

```
max-rounds: 0
budget: 5
time-budget: 30m
```

`max-rounds` is how many consecutive tool rounds pass before it stops and asks (25 by default, **0 means no limit**). The default is tuned for interactive use: an edit-compile-read-the-error-edit-again loop runs for hundreds of rounds, and there is no right number to pick up front.

**If you lift the round limit, set `budget`.** Non-interactive runs (a pipe, a one-shot task) have nobody to answer that question, so money is the only remaining gate — and a model with no configured price cannot be costed, which leaves no gate at all.

`time-budget` is how long one request may run (plain numbers are minutes; `90s` / `45m` / `1.5h` also work, **0 means no limit**). It is measured **per request**, not per session, so the minutes you spend thinking in the REPL do not count. **Shortly before the deadline the model is told to wrap up**: get the finished part working, do not leave an edit half-applied. When the clock kills it from outside, a half-edited file is worse than nothing.

`--max-rounds`, `--budget` and `--time-budget` on the command line win over the convention file, and `/context` will not overwrite them.

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

## Editors (nvim, VS Code)

When feiyu runs inside a project you have open in an editor, the confirmation for
`write_file` / `edit_file` moves out of the terminal and into the editor's own
side-by-side diff. Accept, reject, or **edit the right-hand side first and then
accept** — what you see is what gets written, not what the model proposed.

```
nvim       ~/.config/nvim/lua/feiyu_ide.lua     ga accept · gA accept always · gr reject
VS Code    the "Feiyu IDE Bridge" extension     alt+y   · alt+shift+y       · alt+n
```

The editor listens on a unix socket and announces itself with a small lock file in
`$XDG_RUNTIME_DIR/feiyu/ide/`; feiyu finds it by **exact workspace match** and speaks
newline-delimited JSON over it. No new dependency, nothing listening on a TCP port.

**Start them in either order.** feiyu picks the editor up even if you open it later: it
looks again before each write confirmation, retrying at most once every five seconds.
Same if the editor restarts mid-session. It says so when it connects and when it loses
the editor, so you never have to guess which path a confirmation will take.

It degrades in one direction only: no editor, a crashed editor, a malformed reply, or
a slow answer all fall back to the terminal confirmation you already had. The bridge
can never block the agent. `FEIYU_NO_IDE=1` turns it off; `FEIYU_IDE_TIMEOUT`
(default 600s) is how long it waits for you before giving up and asking in the terminal.

Diffs only. Everything else — bash, `git commit`, `web_fetch` — is still confirmed in
the terminal, because there is no diff to show.

## The desktop app

Model discovery runs only when you click “List models”. Changing the endpoint or provider type clears the entered key. Saved keys are reused only for the same scheme, host, port, and provider type. Responses from an outdated form are discarded.

```
feiyu-desktop                     # a window on the current directory
feiyu-desktop -w ~/code/project   # pick the workspace
feiyu-desktop -r                  # continue the last conversation here
```

After install it also shows up in your app launcher as **Feiyu**.

**Without building from source:** the one-line command at the top of Install is the easiest; or
download the Linux deb (`sudo apt install ./file.deb`, the
one to pick on Ubuntu) or the AppImage (`chmod +x`, then open it) from
[Releases](https://github.com/feiyuceng06-prog/feiyu-agent/releases). The AppImage needs
libfuse2, which Ubuntu stopped installing by default in 22.04: run `sudo apt install libfuse2`
first (`libfuse2t64` on 24.04), or it won't open. The package carries its
own Python backend — no Python, Node or terminal needed. On first launch it asks you to
"connect a model": pick a provider (OpenRouter, DeepSeek, Kimi, Qwen, SiliconFlow, OpenAI,
Anthropic, or type your own address), paste that provider's key, and choose from the model list
it fetches live. Opened from the app menu, it reuses the last workspace you picked. There are
no Windows or macOS packages yet.

**Nothing from the window leaks into your terminal.** The agent's human-readable output — and the bridge's own `→ op` trace — goes to `~/.local/state/feiyu/desktop.log`, kept at 0600 like the session saves and rewritten from the top past 4 MB. To watch it in the terminal instead: `FEIYU_DESKTOP_STDERR=1 feiyu-desktop`.

The current model sits at the bottom right of the input box; **click it to switch** from a list, with a filter once it gets long and a mark on the one in use.

**When it asks for approval you can answer from the keyboard:** the buttons carry `1` `2` `3` `4` in the order they are laid out. The keys only count once the card has been up for a moment (twice as long for commands that can't be undone), so a digit you were already typing lands in the input box instead of approving something; while the input box has text in it, digits are just text. *Write my own* opens a line inside the card instead of a system dialog.

**It tells you when you weren't looking.** If you scroll back while it keeps working, or switch to another window, a *new below here* line marks the first thing you missed and the arrow at the bottom right shows how many there are — clicking it takes you to that line, not to the very bottom. With the window in the background, a desktop notification (`notify-send`) says when it needs your approval — with the command — or when a turn longer than ten seconds has finished.

The sidebar holds new-session and skills-and-tools, with past conversations below: click one to switch (the agent's context follows, not just the view), `shift+click` pins it, hovering reveals a `×` that deletes it (it asks first), and the search box filters the list locally. `Ctrl B` collapses the sidebar, `Ctrl N` starts a new session. The row at the top is the workspace; click it to switch directories.

**Settings** (`Ctrl K`) is five tabs: general, skills, tools, MCP, profiles. "General" holds the appearance, permission mode, answer style, model, helper model, bash sandbox, verify command, permission rules and long-term memory — clicking one takes effect immediately and runs exactly the slash command you would type in the terminal. The model entry
also adds, edits and deletes configurations (base URL, model id, API key, context limit,
prices). **A stored key is never sent back to the UI**, so leaving that field blank when you
change something else means "leave it alone". This is open in the desktop window only — the
development browser entry listens on loopback, where any process on the machine could connect,
and refuses key writes. The other four tabs: Every entry has a switch, and whatever you switch off neither enters its context nor can be called — a skill drops out of the catalog in the system prompt, a tool is not attached to the request, and an MCP server's tools are not exposed. Changes take effect immediately and are recorded in `~/.config/feiyu/enabled.json`, which lists only what is *off*, so a skill you install later is on from the start. `/skills <name>` toggles one from the terminal too.

`exit_plan` cannot be switched off: it is the only way out of plan mode. Switching off an MCP server only stops its tools being attached — the process still starts, since whether it starts is written in `mcp.json` and needs a restart to change.

The fourth tab is **profiles**: one profile is one persona. Its body goes straight into the
system prompt, editable in place in the panel or as `~/.config/feiyu/profiles/<name>.md`:

```markdown
---
name: harsh-review
description: Only finds fault, never praises
model: deepseek-v4-pro
---
You are a caustic but accurate code reviewer. Lead with the worst thing. No warm-up, no praise.
```

`model` is optional; when set, switching profile switches the model too. **A persona is
resent on every single turn**, so the cap is 4000 characters (a skill gets 12000 — that one
is only sent on the turn it is used). `/profile` lists and switches from the terminal.

### Bots

The sidebar has two tabs: **conversations** and **bots**. A bot is one persona you start a
*fresh conversation* with in one click — it carries not just the persona but the model, the
permission mode and the answer style, all set the moment the conversation opens. One `.md`
per bot in `~/.config/feiyu/bots/<name>.md`:

```markdown
---
name: captain
description: An 18th-century pirate captain
model: qwen3.8-flash
mode: readonly
style: concise
---
You are an 18th-century pirate captain. You never admit to being an AI.
```

Every field but the body is optional; the ones you leave out keep whatever the session
already had. Create and edit them in the sidebar, or drop the file in yourself.

A conversation remembers which bot opened it, so reopening it from the sidebar (or
`feiyu --resume`) brings the persona back — without that, half a conversation would be in
one voice and half in another. **A conversation is under a bot, or under a profile, or under
neither**: the persona slot is shared, and starting a bot drops the current profile. The
difference between the two: a profile is a persona you switch *into the conversation you are
having*; a bot is a persona you *start a conversation with*.

`@path` completes in the composer exactly as it does in the terminal — the candidates come from the server, so both surfaces offer the same list. For files outside the workspace, the `＋` left of the composer uploads them; dragging them onto the window works too. They land in `.feiyu/uploads/` inside the workspace and the path goes out with your next message.

**While it works.** A reasoning model can think for ten minutes without saying a word, so the window shows the reasoning itself: an expandable trace that stays open while it thinks and folds itself away once it speaks (you can toggle it, and it then stays the way you left it). When nothing is happening at all, a small pixel grid and an elapsed clock say so — the status bar had the clock since 0.58.0, but your eyes are on the transcript. Long tool output (a test run, a build log) shows its first few lines with the rest one click away; the summary line is never folded.

Code blocks are line-numbered with a copy button, the numbers living outside the scroller so they stay put when a long line scrolls sideways. Select any passage of the reply and a small bar offers explain / rewrite / shorten / do-this — it fills the composer with the quoted passage rather than sending, so you can add a sentence first. A `↓` appears when you have scrolled back and the conversation has grown past you.

The component inventory and the motion recipes are informed by [Beautiful UI](https://www.beautifului.dev/) and [beUI](https://beui.dev/) — the shapes and the timing, not the skin: this window stays white paper, square corners and flat, and every animation is off under `prefers-reduced-motion`.

Same agent, same permissions, same session file as the terminal — only the surface changes. Edits and commands are shown as a diff or the literal command and nothing runs until you approve. Slash commands, skills and `esc` to interrupt all work. Manage models in Desktop Settings; saved API keys are never returned to the renderer.

Under the hood it is an Electron window supervising a `feiyu --serve` child process, one JSON object per line between them. **It listens on no port** — the front end and the agent talk over an in-process channel that nothing outside can reach.

The information architecture follows [Hermes Desktop](https://github.com/NousResearch/hermes-agent)'s `DESIGN.md` (MIT); the palette, type and spacing are [feiyuz123.com](https://feiyuz123.com)'s. The chat renderer retains its existing implementation; Electron and the React workbench use the dependencies locked under `desktop/`.

The default desktop is now Electron, with a React file/preview workbench and the existing chat/settings renderer. The main process owns native capabilities and the Python subprocess; a narrow, isolated preload bridge carries the existing JSON protocol. Agent behavior and approval flows remain in Python. Browse and preview workspace files with `Ctrl Shift E`; use the independent PTY terminal with Ctrl + backtick.

Source builds require Node.js 22+, npm, Python 3.10+, and native C/C++ build tools. Run `./install.sh --desktop`; PyInstaller stages a standalone backend and electron-builder packages the app. Installed applications do not need the source checkout, Node.js, or system Python. See [desktop/README.md](desktop/README.md) for development, packaging, tests, and architecture. The old Tauri sources remain as a compatibility implementation. Linux release builds target Ubuntu 22.04; packages built on a newer distribution are not guaranteed to run on older Ubuntu versions.

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

**Three looks, one layout.** The desktop app is a minimal layout built on the rules of
[Fluid Functionalism](https://fluidfunctionalism.com): two control heights (36 / 28 px), a
six-step type scale, a sliding hover highlight shared by each list, your messages as bubbles,
the input as a floating card, and the empty conversation framed in an
[mdxcn](https://mdxcn.dev)-style dashed box. The three themes only swap colours: white paper
with IBM Blue (default), aged paper with ochre ("retro"), and warm ivory with terracotta
("Anthropic"). The terminal has its own `/theme` (ANSI colours and glyphs: `default` / `swiss` /
`retro` / `anthropic`); the two are deliberately separate settings.

**A skill can be a package.** One `.md` is one skill, and so is a directory containing a
`SKILL.md`, which may sit next to companion files:

```
~/.config/feiyu/skills/
├── release.md           ← one file, one skill
└── teach/               ← one directory, one skill
    ├── SKILL.md         ← the body
    └── MISSION-FORMAT.md  ← a companion file, read on demand
```

Companion files **stay out of the body** — reading the body only lists their names, and it
fetches the one it needs separately. Skill packages are usually distributed as a zip; drop it
straight into `skills/` and it is unpacked automatically (the zip is kept, and an
already-unpacked directory is never overwritten).

**Project skills need your approval the first time**, and again whenever the directory's contents change — it decides on its own to load them, and a stretch of instructions that came down with a repo should not enter its context on `git clone`. **Project commands need no approval:** nothing happens until you type one, and typing one is the same as typing the text yourself.

## What it remembers

**Project conventions.** Put a `FEIYU.md` in the workspace (`AGENTS.md` and `CLAUDE.md` also count, first one found) and it is read into the system prompt at startup. `/context` reloads it.

**Long-term memory.** Things worth keeping, like how the build runs, the cause of a pitfall, or a preference you stated, it writes down itself after asking you. They come back in the next session in the same directory; `/memory` shows and deletes them. Stored in `~/.local/state/feiyu/memory/`, never inside your project.

```
⏺ Remember(tests in this project must run with pytest -q, conftest has fixtures)
  ⎿  noted
```

**Stumbling twice gets noticed.** When the same command fails with the same error, or the same replacement fails on the same file, the second result carries a note — stop retrying as is, find out why first — and the third tells it to stop and ask you. Once it gets past, it is prompted to `remember` the cause if it is specific to this project (still asking you first), so the next session does not trip on it.

**Sessions.** Saved every turn; `feiyu -r` resumes. The save is the full transcript, in `~/.local/state/feiyu/sessions/` with mode 600. `/clear` deletes it too.

**Context management.** Every request resends the whole history, so it trims automatically: when a file is read again or edited, the old copy is replaced by a one-line note; long output from many rounds ago keeps only its head and tail; only when the limit is still exceeded is the history summarised. The context limit is set in `/key add`; if left empty, the first "input too long" error teaches it the model's real limit.

The anthropic backend sets cache breakpoints so resent history is billed at the cache rate. **Claude through an OpenAI-compatible API (OpenRouter, relays) needs them too**, and feiyu recognises it and sets them — without them every round is billed at full price, measured at ten times the cost. Other OpenAI-compatible APIs cache server-side. `/cost` shows the hit rate.

## Embedding

Give it a JSON schema and the run ends with output matching it:

```
feiyu --json-schema review.schema.json "check calc.py for bugs" > out.json
```

stdout carries only the JSON; the interface and logs go to stderr. Output that does not match is sent back for up to two retries, then the exit code is non-zero. Supports `type` `required` `properties` `items` `enum` `additionalProperties`.

**History slimming watches the cache.** Resent history is cached by prefix — edit a message in
the middle and everything after it falls back from the cache rate to full price. So folding away
old tool output only happens when it pays: when the API does not cache at all (folding is then
pure gain), or when the context is already past halfway (running out of window costs more than
the tokens do). The hit rate in `/cost` is what that decision reads.

**Logs.** One JSONL line per event in `~/.local/state/feiyu/logs/<date>.jsonl`: every tool call, its arguments, the permission decision, timing, token usage. To find out why it did something odd, filter by the `run` field. `FEIYU_NO_LOG=1` disables it.

**Credit.** The right of the status bar shows what the API says is left (`余 $9.78`); click it
to refresh now, and it syncs after every turn. This is not the `¥` on its left — that one is
our own estimate from the prices you entered, this one is the provider's balance.

Recognised: OpenRouter, DeepSeek, Moonshot, SiliconFlow, and the many relays that copy
OpenAI's old billing endpoints. **A normal OpenAI or Anthropic key cannot read a balance**
(that lives in the account dashboard), and in that case nothing is shown at all rather than a
made-up number. The key is only ever sent to the host in the `base_url` you configured.

**Spend.** `/cost` summarises the last seven days by task:

```
   08-27 00:14  fix error handling in parser.py     5 rounds    8.2k   71%  slowest 12s   ¥0.031
   08-27 09:02  add --json flag to the CLI         12 rounds   31.5k   84%  slowest 38s   ¥0.118

   total  39.7k  ¥0.149  cache hits 81%
```

The percentage is the cache hit rate. It decides what long tasks cost: a constant 0 means the
API does not cache and every resent history is billed in full.

"slowest" is the **slowest single turn** in that task, not the average — nine turns of five
seconds and one of ten minutes averages the ten minutes away, and the ten minutes is the thing
you came to look at. A wildly large number is usually the API having a bad moment; the log line
for that turn also records how many tokens it produced, which separates "said a lot" from
"the API was just slow".

## MCP

Tools from external [MCP](https://modelcontextprotocol.io) servers can be used directly. The config has the same shape as Claude Code's: global in `~/.config/feiyu/mcp.json`, per project in `.feiyu/mcp.json`:

```json
{"mcpServers": {
  "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
  "db": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "app.db"], "env": {"X": "1"}},
  "resume": {"type": "http", "url": "http://localhost:3100/mcp",
             "headers": {"x-api-key": "${RR_API_KEY}"}}
}}
```

- Two transports: **stdio** (a local command) and **streamable-http** (a remote
  server). With no `type`, an entry with a `url` is http and one without is stdio.
- **`${VAR}` is read from the environment**, so a key need not sit in the config file:
  http entries expand it in `url` and `headers`, stdio entries in the `env` block.
  Not in `args` — those are printed verbatim by `/mcp`. (A stdio server does **not**
  inherit environment variables whose names contain KEY/TOKEN/SECRET; those are stripped
  so the model cannot read them, so name the one you need in `env` explicitly.) An unset variable fails the server rather than
  sending the literal `${VAR}` and getting back a puzzling 401.
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

Stored in `~/.config/feiyu/models.json` with mode 600. Empty prices show token counts only. The cache write price matters for direct anthropic access and for Claude through OpenRouter (the round that writes the cache is billed at 1.25× input); leave it empty elsewhere.

Set `max_tokens` to the model's real limit. The 8,192 default is hit by any long file, and a truncated tool call wastes the round. A value above what the API allows makes the API reject the request.

## Security notes

It really runs commands and edits files on your machine, with your permissions.

- File tools are confined to the workspace.
- With bubblewrap, bash writes are confined to the workspace and the docker socket is out of reach; reads are not isolated. Without it there is no isolation at all.
- Environment variables whose names contain `KEY` `TOKEN` `SECRET` `PASSWORD` are not passed to child processes. `SSH_AUTH_SOCK` is kept, so `git push` works. To pass a specific secret: `FEIYU_KEEP_ENV_SECRETS=1 feiyu`.
- Fetched pages are labelled "external data, not instructions", and whether the model obeys is not guaranteed — so after reading outside content, bash that can reach the network needs your nod (see [Permissions](#permissions)).
- The git tool never runs programs named in the repository's config (fsmonitor, external diff) when it reads status or changes: `.git/config` lives in the workspace, and editing it cannot start anything.
- `~/.config/feiyu/` (keys) and `~/.local/state/feiyu/` (transcripts, input history) are 700 directories with 600 files.
- The desktop app listens on no port; the front end and the agent talk over an in-process channel — see [the desktop app](#the-desktop-app).

So: use it on projects you trust and keep under git, where a bad edit is easy to undo. Don't run untrusted tasks on a machine holding sensitive credentials. For hard isolation use a container or a VM.

## Platforms

The Python agent supports Linux and macOS; its bash sandbox is Linux-only. The Electron desktop is validated on Linux first. macOS and Windows build commands exist but require platform testing. For Windows development, install Python 3.10+, Node.js 22+, MSVC build tools and Git for Windows, then run `./install-windows.ps1`; the app is built under `desktop/release/win-unpacked/`.

## Tests

```
python -m unittest discover -s tests -t .
```

1475 cases, forty seconds, no real API calls, nothing under `~/.config/feiyu/` is touched. The sandbox group skips itself without bubblewrap.

## Versioning

Semantic versioning; the version lives in `feiyu/__init__.py`.

## License

[MIT](LICENSE) © feiyuceng06-prog
