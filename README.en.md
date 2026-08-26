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
| `/context` | Show and reload the project conventions file |
| `/compact` | Squash the history into one summary to save context |
| `/clear` | Wipe the conversation and its saved session |
| `/exit` | Quit |

The top border shows **how full the context is** and **what the session has cost so far**.

Context is a percentage, not a token count. Every request carries roughly 1.5k tokens of fixed overhead (tool definitions plus the system prompt), so even a bare "hi" shows a four-figure number — alarming to look at and useless to act on. The percentage tells you what's left. Models with no price configured show no money at all, rather than a token count standing in for one.

Cost comes from the three price fields you fill in during `/key add`; leave them blank and it shows a token count instead. See "Models" below.

### Permission modes

`shift+tab` cycles through them, or use `/mode <name>`. The current mode shows on the left of the input box's top border.

| Mode | Behaviour |
|---|---|
| `default` | Anything on the allow list runs; everything else asks first (startup default) |
| `auto` | Every tool call runs immediately, no interruption |
| `readonly` | Only reading, listing, searching and web fetches; writes and commands are refused |

When it asks you get four choices: `y` run / `n` skip / `a` allow the rest of this turn / **`s` don't ask again** (saved as a permission rule).

A refused call reports the reason back to the model, which usually adapts or asks you what you'd prefer — it doesn't get stuck.

### Permission rules

Rules decide what runs without asking. They're written as `Tool(pattern)`:

```
Bash(ls:*)            commands starting with ls
Bash(git status:*)    commands starting with git status
Bash(pwd)             exactly this command
Read(*)               every call to this tool
```

`/allow` lists them, `/allow Bash(npm test:*)` adds one, and pressing `s` at a prompt records one for you. They live in `~/.config/feiyu/permissions.json`; `deny` beats `allow`.

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

`web_fetch` accepts http/https only and converts HTML to **Markdown**, keeping headings, links and code blocks.

It has two modes, and the difference matters:

- **With `prompt`**: the model reads the whole page and answers only what you asked. Fetching PEP 8 (51,348 chars) and asking about line length put just 123 characters into the context — 99% less than a raw fetch, and it reads the full text so a truncated tail can't hide the answer.
- **Without `prompt`**: the whole page comes back as Markdown, truncated at 15000 characters. Fine for short pages or when you want it quoted verbatim.

The same URL isn't refetched within 15 minutes.

**It refuses internal addresses**: loopback (`127.0.0.1`, `localhost`), private ranges, and link-local (including the `169.254.169.254` cloud-metadata endpoint) are all rejected, redirects into them included. Otherwise a page only has to talk it into fetching the metadata endpoint and your cloud credentials walk out the door. To reach a local dev server on purpose, start it as `FEIYU_ALLOW_LOCAL_FETCH=1 feiyu`.

What comes back is labelled as external data, not instructions — whatever a page says, it isn't a command. It isn't on the shipped allow list, so `default` mode asks before it sends the request (the content leaves your machine); `readonly` lets it through, since it changes nothing.

A single task runs at most **25 rounds** of tool calls. At the cap it stops and says so rather than spinning forever; one more message from you and it picks up again. Press `esc` to cut a turn short.

For shell commands, `cd` persists across calls: once it cds into a subdirectory, later commands run from there, so it doesn't have to keep writing `cd x && …`. File paths for read/write/edit stay relative to the workspace root regardless.

## A note on safety

Feiyu really does run commands and change files on your machine, **with exactly your permissions**. The startup `default` mode only lets allow-listed calls through and asks about the rest; `shift+tab` into `auto` skips that, but then nothing is confirmed.

The three file tools (`read_file` / `write_file` / `edit_file`) are confined to the working directory and reject anything outside it. The **`bash` tool is not** — it can read and write any path your account can reach, and it has network access. A `cd` outside the workspace is bounced back, but that only controls where the *next* command starts; it doesn't stop the current one from touching whatever it likes.

Shell commands run with **environment variables whose names contain `KEY`, `TOKEN`, `SECRET` or `PASSWORD` stripped out**, so a stray `echo $DEEPSEEK_API_KEY` can't pull your key into the conversation and ship it back to the model provider. Names without those words — `SSH_AUTH_SOCK` and friends — are kept, so `git push` still works. If a script it runs genuinely needs a key, start it as `FEIYU_KEEP_ENV_SECRETS=1 feiyu`.

That only closes the casual path, though. **`bash` is not sandboxed, so it can still `cat ~/.ssh/id_rsa` directly** — stripping the environment reduces accidental and lightly-coaxed leaks; it is not isolation. For real isolation, use a container.

So: use it on projects you trust and keep under git, so anything it breaks is easy to roll back, and don't run untrusted tasks on a machine holding sensitive credentials. If you need real isolation, put it in a container or VM.

## Platforms

Linux and macOS are fully supported.

Windows works **only under WSL** for now: `install.sh` is a bash script, the `bash` tool's persistent `cd` relies on POSIX shell syntax, and both `esc` interrupts and the single-key confirmation prompt depend on termios. On native Windows those pieces don't work, and the 600 permissions on config files have no effect.

## Versioning

Semantic versioning. The version lives in `feiyu/__init__.py`; `pyproject.toml` reads it from there.

## License

[MIT](LICENSE) © feiyuceng06-prog
