# Feiyu Agent

[简体中文](README.md) | English

Feiyu Agent is a coding assistant that runs in your terminal. You hand it a task and it reads the code, edits files, and runs commands on its own — working through the problem step by step, with everything it does visible to you as it goes. It works with Claude, DeepSeek, or Kimi.

## Install

One command:

```
git clone https://github.com/feiyuceng06-prog/feiyu-agent.git && cd feiyu-agent && ./install.sh
```

It creates a virtualenv, installs the dependencies, and drops a shim in `~/.local/bin` so `feiyu` works from any directory. Needs Python 3.10 or newer — it'll tell you if that's missing. Re-running is safe; run it again after pulling new code.

**It ships empty — you add the models yourself.** Run `feiyu`, type `/key add`, and fill in the endpoint, model id and key. That's all it takes; see "Models" below.

<details>
<summary>Manual install</summary>

```
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[openai]"
```

Then source that .venv before each use, or drop a shim yourself:

```
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

The openai package in `.[openai]` is for DeepSeek, Kimi and other OpenAI-compatible endpoints; `pip install -e .` is enough if you only use Claude's native API.

</details>

## Usage

Just run feiyu to start an interactive session, working in the current directory:

```
feiyu
```

It starts on whichever model you used last. To pick another, pass `-m` with a name from `/key`:

```
feiyu -m ds-flash
```

Point it at another project with `-w`:

```
feiyu -w ~/code/my-project
```

To run one task and exit, put the task on the command line:

```
feiyu "add a --json flag to the CLI and update the tests"
```

`-r` / `--resume` picks up the last conversation in this directory, and `-V` prints the version.

## The interface

Inside, you get a bordered input area; tool calls, file diffs, and the model's replies all print below it.

```
╭──────────────────────────────────── deepseek  ·  context 1.4k  ·  ¥0.0014 ─╮
│ > make search in tools.py use rg
╰────────────────────────────────────────────────────────────────────────────╯

⏺ Read(feiyu/tools.py)
  ⎿  217 lines

⏺ Update(feiyu/tools.py)
  ⎿  1 change
       47  -  rx = re.compile(args["regex"])
       47  +  if shutil.which("rg"):

  Done — search now prefers rg and falls back to the regex path when it's missing.
```

Keys: `⏎` send, `\⏎` newline, `shift+tab` cycle permission mode, `esc` interrupt the current answer, `ctrl-d` quit, `↑` `↓` history.

Slash commands (typing `/` pops up completions):

| Command | What it does |
|---|---|
| `/help` | Show help |
| `/model` | List configured models; `/model <name>` switches, keeping the conversation |
| `/style` | Switch answer style: `default` / `concise` / `explanatory` / `teaching` |
| `/key` | Add / list / remove model configs: `/key add`, `/key`, `/key rm <name>` |
| `/mode` | Switch permission mode: `default` / `auto` / `readonly` |
| `/allow` | Show or add permission rules |
| `/verify` | Show or set the command run automatically after edits |
| `/review` | Have a model that never saw the process score the changes |
| `/cost` | Recent token use and cost, by task |
| `/memory` | Show / add / drop what it remembers about this directory |
| `/hooks` | Show which external commands run, and when |
| `/context` | Show and reload the project conventions file |
| `/compact` | Squash the history into one summary to save context |
| `/clear` | Wipe the conversation and its saved session |
| `/exit` | Quit |

The top border shows **how full the context is** and **what the session has cost so far**.

Context is a percentage, not a token count. Every request carries roughly 1.5k tokens of fixed overhead (tool definitions plus the system prompt), so even a bare "hi" shows a four-figure number — alarming to look at and useless to act on. The percentage tells you what's left. Models with no price configured show no money at all, rather than a token count standing in for one.

Cost comes from the three price fields you fill in during `/key add`; leave them blank and it shows a token count instead. See "Models" below.

### Subagents

A question like "how does this project handle interrupts" takes a dozen files and hundreds
of lines to answer — all of which would bloat the conversation. Instead it sends a subagent
and keeps only the conclusion:

```
⏺ Task(work out how KeyboardInterrupt is handled and which files are involved)
     read_file feiyu/ui.py → 80 lines
     search def _run_tools → 1 match
     read_file feiyu/agent.py → 100 lines
     …
  ⎿  done, 812 characters back
```

Measured on that run: **the subagent burned 41,579 tokens; the main conversation grew by
2,284.** Not one line of those twenty file reads entered the main context.

A subagent **can only read** — `read_file`, `list_files`, `search`, and nothing else. That
isn't laziness; three properties come with it. It cannot change anything, so it never needs
to stop and ask you (it runs on a worker thread and cannot read the keyboard). That in turn
makes it safe to run several at once for unrelated questions. And with no `web_fetch` there
is no "subagent read a page and got talked into something else" path.

The cost is that it cannot do anything needing the network or a write; the main agent does
those itself.

It cannot see your conversation with the main agent — deliberately, which is why the main
agent writes it a self-contained question. Its usage counts towards the status bar and
`/cost`: **spending you cannot see is worse than spending you can**.

### Parallel reads

When a batch of calls is **entirely read-only**, they run at the same time:

```
⏺ Read(mod1.py)
⏺ Read(mod2.py)
⏺ Read(mod3.py)
⏺ Read(mod4.py)
  ⎿  mod1.py — 3 lines
  ⎿  mod2.py — 3 lines
  ⎿  mod3.py — 3 lines
  ⎿  mod4.py — 3 lines
```

`read_file`, `list_files`, `search` and `web_fetch` count as read-only, four at a time.
Fetching four pages goes from one-after-another to one wait, which is very noticeable.

**One `bash` or file write in the batch and the whole batch goes back to serial.** The
decision is made per batch rather than per call: in a batch like "read three files, then
write one based on them", the write depends on the reads, so cherry-picking what to
parallelise would just execute things out of order. `bash` can never be parallel anyway —
its `cd` persists across calls, and two concurrent `cd`s would clobber each other.

Confirmation prompts always happen one at a time on the main thread, and results are
returned to the model in call order (which one finishes first is not deterministic, but the
order must be). Interrupt with `esc` partway through and the calls that never ran still get
results — the history stays intact.

### Permission modes

`shift+tab` cycles through them, or use `/mode <name>`. The current mode shows on the left of the input box's top border.

| Mode | Behaviour |
|---|---|
| `default` | Anything on the allow list runs; everything else asks first (startup default) |
| `auto` | Every tool call runs immediately, no interruption |
| `plan` | It may only look and think; nothing is touched until you approve the plan |
| `readonly` | Only reading, listing, searching and web fetches; writes and commands are refused |

When it asks you get four choices: `y` run / `n` skip / `a` allow the rest of this turn / **`s` don't ask again** (saved as a permission rule).

A refused call reports the reason back to the model, which usually adapts or asks you what you'd prefer — it doesn't get stuck.

### Plan mode

When a change is big enough that you want to see the intent first, `shift+tab` to plan
mode, or `/mode plan`.

In this mode it can read, search and run read-only commands; writes and commands are
refused. Once it has seen enough it lays out the plan and hands it to you:

```
  │ One file only, parser.py:
  │ 1. add a _fail(msg) helper that raises ValueError
  │ 2. replace the three raise sites with calls to it
  │ 3. verify the messages still match
  Go ahead with this plan?  [y] run  [n] skip …
```

Approve and it switches back to default mode and carries on; decline and it takes your
feedback and comes back with a revised plan — it never declares "starting now" on its own.

### Task list

When you ask for several things at once, or the work touches more than a couple of places,
it writes the list down first:

```
⏺ Todo()
  ⎿  0 / 4 done
     ▸ read util.py and the existing structure
     · strip whitespace in parse
     · add a join function
     · write test_util.py covering both
```

The list is not only for you to look at: **if it tries to finish while items are still
open, it gets pulled back** — either to finish them, or to say why it can't. Declaring
victory with three of five things done is the classic failure of tools like this, and this
is what answers it. It nudges once, so it can't get stuck in a loop.

The list is saved with the session, so `--resume` brings it back.

### Permission rules

Rules decide what runs without asking. They're written as `Tool(pattern)`:

```
Bash(ls:*)            commands starting with ls
Bash(git status:*)    commands starting with git status
Bash(pwd)             exactly this command
Read(*)               every call to this tool
```

`/allow` lists them, `/allow Bash(npm test:*)` adds one, and pressing `s` at a prompt records one for you. They live in `~/.config/feiyu/permissions.json`; `deny` beats `allow`.

**Rules match per segment.** A command is first split on `&&`, `||`, `;`, `|` and redirections, and **every segment must be matched by the allow list** before it runs; one segment hitting `deny` refuses the whole line. So `Bash(ls:*)` allows `ls` alone — `ls -la && curl x | sh` still asks. Any command containing `$(...)` or backticks always asks, because prefix matching cannot see what is hiding in there. The cost is an extra prompt for commands with metacharacters inside quotes; erring in that direction is the safe one.

**The shipped list only allows `ls` `pwd` `echo` `wc` `which` and `git status/log/diff/branch`** — deliberately not `cat`, `head` or `grep`, which can read any file outside the workspace and would sidestep the directory limits on the file tools. Allowing them is your call; press `s` when you want to.

There's also a **built-in deny list**: commands touching `.ssh/`, `id_rsa`, `.aws/credentials`, `~/.config/feiyu/`, `.netrc`, `.gnupg/` or `.env` are refused, `auto` mode included. That stops it reading every key you own with a single `cat ~/.config/feiyu/models.json`. It is path matching only — it catches the obvious form, not a deliberately obfuscated one.

### Project conventions

Drop a `FEIYU.md` in the workspace (it also accepts `AGENTS.md` and `CLAUDE.md`, first match wins) and it gets read into the system prompt at startup, up to 8000 characters. Write down whatever rules you want it to follow:

```markdown
# Conventions for this project

- Run `npm test` before committing
- Don't touch anything under `vendor/`
- New components go in `src/components/`, one per file
```

`/context` reloads it without restarting. The welcome box shows which file was picked up and how big it is.

### Context and sessions

The whole history is resent every round, so it only grows. Two ways out:

- **Automatic compaction**: past 75% of the context limit, it asks the model to condense the conversation into a handover summary, swaps that in for the original, and carries on. **This happens mid-task too**, so a long job that reads a dozen files won't stall on a full context. The limit defaults to 128k; if a model differs, add `"limit": 32000` to its entry in `~/.config/feiyu/models.json`.
- **Manual**: `/compact` any time, or `/clear` to wipe it.

Conversations are saved automatically — `feiyu --resume` picks up where you left off. **The save file holds the full transcript** (including file contents the agent read), lives in `~/.local/state/feiyu/sessions/` with mode 600, one per working directory. `/clear` deletes it too.

### Models

The model list is entirely yours — **no built-in vendors, no default model, nothing configured out of the box**. Add one with `/key add`:

```
   名称（/model 里显示成这个）: ds-flash
   接口地址 base_url: https://api.deepseek.com
   模型 id: deepseek-v4-flash
   API key: ****************
   接口类型 openai / anthropic [openai]:
  下面三项是价格，人民币每百万 token，留空就只显示 token 数不算钱
     缓存命中价（没有缓存就填 0）: 0.02
     输入价: 1
     输出价: 2
```

After that, `/model` lists them, `/model ds-flash` switches (keeping the conversation), `/key` shows what's stored with keys masked, and `/key rm <name>` deletes one. Next launch starts on whichever you used last, or pass `feiyu -m ds-flash`.

Configs live in `~/.config/feiyu/models.json`, mode 600. The key is typed into a separate hidden prompt, so it never reaches feiyu's input history.

The three price fields are optional: fill them in and the top border shows what the session has cost; leave them blank and it shows a token count. **There is no built-in price table** — model names and prices churn too fast, and a stale table quoting the wrong number is worse than quoting none.

## What it does

Given a task, it doesn't just start rewriting things. It looks around first — reads the relevant files, searches the code with keywords or a regex, and gets a feel for the structure — and only then creates or edits files. When it's done, it usually runs the tests or a relevant command to make sure nothing broke.

All of this runs through seven basic abilities: run a shell command, read a file, write a file, replace an exact chunk of a file, list files by glob, search contents with a regex, and fetch a web page. It prints each one as it goes, so you can always see what it's doing.

### Office documents

Reading a `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp` or `.pdf` extracts the text automatically, so you can just ask what a report says. Spreadsheets come back row by row, presentations slide by slide. The first six are handled with the standard library (these formats are ZIP archives of XML); PDF needs `pdftotext` on the system.

Only the **text** survives — layout, styling and images do not, and you can't edit the original file with `edit_file`. To create or modify these files, have it write a script using `python-docx`, `openpyxl` and friends, or drive LibreOffice from the command line for format conversion.

Other binary formats report a clear error instead of returning mojibake.

`web_fetch` accepts http/https only and converts HTML to **Markdown**, keeping headings, links and code blocks.

It has two modes, and the difference matters:

- **With `prompt`**: the model reads the whole page and answers only what you asked. Fetching PEP 8 (51,348 chars) and asking about line length put just 123 characters into the context — 99% less than a raw fetch, and it reads the full text so a truncated tail can't hide the answer.
- **Without `prompt`**: the whole page comes back as Markdown, truncated at 15000 characters. Fine for short pages or when you want it quoted verbatim.

The same URL isn't refetched within 15 minutes.

**It refuses internal addresses**: loopback (`127.0.0.1`, `localhost`), private ranges, and link-local (including the `169.254.169.254` cloud-metadata endpoint) are all rejected, redirects into them included. Otherwise a page only has to talk it into fetching the metadata endpoint and your cloud credentials walk out the door. To reach a local dev server on purpose, start it as `FEIYU_ALLOW_LOCAL_FETCH=1 feiyu`.

What comes back is labelled as external data, not instructions — whatever a page says, it isn't a command. It isn't on the shipped allow list, so `default` mode asks before it sends the request (the content leaves your machine); `readonly` lets it through, since it changes nothing.

A single task runs at most **25 rounds** of tool calls. At the cap it stops and says so rather than spinning forever; one more message from you and it picks up again. Press `esc` to cut a turn short.

Interrupting with `esc` is safe: whatever it had already said stays in the history, and any tool call that didn't finish gets an "interrupted by the user" result of its own. The conversation is never left in a state the API rejects — just say your next thing.

For shell commands, `cd` persists across calls: once it cds into a subdirectory, later commands run from there, so it doesn't have to keep writing `cd x && …`. File paths for read/write/edit stay relative to the workspace root regardless.

## Long-term memory

Saved sessions (`--resume`) bring back **the whole transcript**; long-term memory keeps
**the conclusions that outlived it**, and applies with or without `--resume`.

While working it calls `remember` on things worth keeping — how the build runs, a trap and
why it exists, a preference you stated, an external dependency you can't infer from the
code. Next time you start in **the same directory** those go into the system prompt
automatically, with nothing for you to repeat.

```
⏺ Remember(tests here must run under pytest -q; conftest defines fixtures)
  ⎿  remembered
```

`/memory` lists them, `/memory add <one line>` adds one by hand, `/memory forget <n>` drops
one, `/memory clear` wipes them.

**This is deliberately separate from `FEIYU.md`.** That file is your hand-written rules and
the agent has no business editing it; memory is the agent's own notebook, kept in
`~/.local/state/feiyu/memory/<directory hash>.md` (mode 600) — never inside your repository,
so it can't be committed by accident. The prompt also states plainly that these notes live
in no project file, so it won't describe its own notes as "your project conventions".

Capped at 40 entries / 3000 characters; the oldest go first. It travels with every request,
so it cannot grow without limit.

**Why remembering asks for confirmation**: what `remember` stores can come indirectly from
a web page (something fetched persuades it to note a line), and what it stores enters
**every** later system prompt — a persistent injection channel across sessions. So it is
confirmed in `default` mode like a file write, refused outright in `readonly`, and every
entry is logged. The confirmation line shows the exact text, so you are never signing blind.

## Automatic verification

Put a line of its own in the project context file:

```
verify: npm test
```

From then on it runs that command every time it writes or edits a file. **If it fails, the
output goes straight back to the model to fix**, without you watching over it. `/verify`
shows the current command, `/verify <command>` changes it for the session, `/verify off`
turns it off.

Only `write_file` and `edit_file` trigger it. `bash` can change files too, but counting it
would mean running the test suite after every `ls` and `git status` — far more cost than
it's worth.

That line is shorthand for the hooks below — it is turned into a `PostToolUse` hook.

## Hooks

When one rule isn't enough, write a `hooks.json`:

```json
{
  "PreToolUse": [
    {"matcher": "Bash(git push:*)",
     "command": "echo 'no direct pushes here, open a PR' >&2; exit 1"}
  ],
  "PostToolUse": [
    {"matcher": "Write|Update", "command": "ruff check ."}
  ],
  "UserPromptSubmit": [
    {"command": "git branch --show-current"}
  ],
  "Stop": [
    {"command": "notify-send 'feiyu is done'"}
  ]
}
```

Four moments:

| Event | When | What a non-zero exit does |
|---|---|---|
| `PreToolUse` | before a tool runs | blocks the call; the reason goes to the model |
| `PostToolUse` | after files change | hands the output to the model to fix |
| `UserPromptSubmit` | when you hit enter | blocks the prompt |
| `Stop` | when a turn finishes | logged only, changes nothing |

**Exit 0 passes, non-zero blocks**, same as Claude Code. When blocking, both stdout and
stderr go back to the model (unittest writes failures to stderr, pytest to stdout — take
only one and half your tools report nothing). When passing, a `UserPromptSubmit` hook's
stdout is carried along as extra context — which is what makes the example above tell it
your current branch every time.

Hooks receive a JSON payload on stdin (`event`, `tool`, `subject`, `workspace`). `matcher`
uses the same syntax as permission rules, `|` separates alternatives, and a bare tool name
matches everything for that tool. Secret-looking environment variables are withheld from
hooks, exactly as for the `bash` tool.

Two locations, both active:

- `~/.config/feiyu/hooks.json` — yours, effective immediately
- `.feiyu/hooks.json` in the workspace — **needs your approval the first time**

**Why project hooks ask**: that file arrived with the repository, so running it unasked
means "cloning a repo executes arbitrary code on your machine". Approvals are recorded
against the file's **content hash** in `~/.config/feiyu/trusted-hooks.json`, so any edit
re-asks — otherwise one approval would keep covering the file after someone swaps in
`rm -rf`. Non-interactive runs (pipes, scripts) never run project hooks.

`/hooks` shows what is active and where each entry came from.

## Independent review

`/review`, or `--review` on a one-shot task:

```bash
feiyu --review "finish the error handling in parser.py"
```

Afterwards it opens **a second model** and shows it only the task and the final diff — it
never took part in the task and cannot see the reasoning, which is the point: no marking
your own homework. It reports on completeness, bugs, and scope creep, and ends with a
score out of 10.

With git it reads `git diff HEAD`; without a repository it diffs the before/after contents
it recorded itself.

**This costs an extra API call**, so it is manual — never automatic every turn.

## Structured output

To feed the result into another program, hand it a JSON schema:

```bash
feiyu --json-schema review.schema.json "check calc.py for bugs" > out.json
```

The task runs as usual (tools, confirmations, verification all unchanged); one extra turn
at the end produces the JSON. **stdout carries nothing but the JSON** — the interface and
logs all go to stderr — so it pipes straight into anything. Output that doesn't match the
schema is sent back with the specific problems, twice at most; if it still doesn't match,
the exit code is non-zero.

Supported keywords are `type`, `required`, `properties`, `items`, `enum` and
`additionalProperties` — enough for this, and no `jsonschema` dependency added for it.

## Logs and cost

Every event is one JSONL line in `~/.local/state/feiyu/logs/<date>.jsonl` (mode 600): each
tool call and its arguments, the permission verdict, how long it took, per-turn token
usage, verification and review results. To work out why it did that odd thing an hour ago,
filter by the `run` field.

`/cost` totals the last seven days by task:

```
   08-27 00:14  fix error handling in parser.py     5 turns    8.2k     ¥0.031
   08-27 09:02  add a --json flag to the CLI       12 turns   31.5k     ¥0.118

   total  39.7k  ¥0.149
```

Models with no price configured show token counts only. `FEIYU_NO_LOG=1` disables logging
entirely. **The logs contain command text and file paths**, stored 600 like the session
files — but they are not encrypted.

## A note on safety

Feiyu really does run commands and change files on your machine, **with exactly your permissions**. The startup `default` mode only lets allow-listed calls through and asks about the rest; `shift+tab` into `auto` skips that, but then nothing is confirmed.

The three file tools (`read_file` / `write_file` / `edit_file`) are confined to the working directory and reject anything outside it. The **`bash` tool is not** — it can read and write any path your account can reach, and it has network access. A `cd` outside the workspace is bounced back, but that only controls where the *next* command starts; it doesn't stop the current one from touching whatever it likes.

Shell commands run with **environment variables whose names contain `KEY`, `TOKEN`, `SECRET` or `PASSWORD` stripped out**, so a stray `echo $DEEPSEEK_API_KEY` can't pull your key into the conversation and ship it back to the model provider. Names without those words — `SSH_AUTH_SOCK` and friends — are kept, so `git push` still works. If a script it runs genuinely needs a key, start it as `FEIYU_KEEP_ENV_SECRETS=1 feiyu`.

That only closes the casual path, though. **`bash` is not sandboxed, so it can still `cat ~/.ssh/id_rsa` directly** — stripping the environment reduces accidental and lightly-coaxed leaks; it is not isolation. For real isolation, use a container.

So: use it on projects you trust and keep under git, so anything it breaks is easy to roll back, and don't run untrusted tasks on a machine holding sensitive credentials. If you need real isolation, put it in a container or VM.

## Platforms

Linux and macOS are fully supported.

Windows works **only under WSL** for now: `install.sh` is a bash script, the `bash` tool's persistent `cd` relies on POSIX shell syntax, and both `esc` interrupts and the single-key confirmation prompt depend on termios. On native Windows those pieces don't work, and the 600 permissions on config files have no effect.

## Tests

Standard-library `unittest`, no test framework pulled in:

```bash
python -m unittest discover -s tests -t .
```

175 cases, about a second, and no real API calls — the backends are fakes, and only the
`bash` tool spawns a real subprocess. Everything runs in temporary directories, so your
keys and permission rules under `~/.config/feiyu/` are never touched.

What they watch hardest is the **shape of the history**: one `tool_use` left without a
matching result, or roles that stop alternating, and the next request is a 400 the
conversation never recovers from. An interrupt landing mid-stream and one landing during
tool execution are two different paths, and both have cases.

## Versioning

Semantic versioning. The version lives in `feiyu/__init__.py`; `pyproject.toml` reads it from there.

## License

[MIT](LICENSE) © feiyuceng06-prog
