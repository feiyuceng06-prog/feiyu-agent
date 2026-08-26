# Feiyu Agent

[简体中文](README.md) | English

Feiyu Agent is a coding assistant that runs in your terminal. You hand it a task and it reads the code, edits files, and runs commands on its own — working through the problem step by step, with everything it does visible to you as it goes. It works with Claude, DeepSeek, or Kimi.

## Install

Requires Python 3.10 or newer. Installing into a virtual environment first keeps things clean — and some systems (Arch, recent Debian/Ubuntu) won't let you install into the system Python anyway:

```
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
```

Then install:

```
pip install -e .
```

Remember to activate the .venv each time before using it. If that gets old, drop a shim on your PATH and you can just type `feiyu` anywhere:

```
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

To use DeepSeek or Kimi, also install the openai dependency:

```
pip install -e ".[openai]"
```

Then set the key for whichever model you want:

```
export ANTHROPIC_API_KEY=...    # Claude
export DEEPSEEK_API_KEY=...     # DeepSeek
export MOONSHOT_API_KEY=...     # Kimi
```

## Usage

Just run feiyu to start an interactive session, working in the current directory:

```
feiyu
```

You don't have to say which vendor to use — it picks the first one you've set a key for. To pin it to one, use `-p`:

```
feiyu -p deepseek
feiyu -p kimi
```

Point it at another project with `-w`:

```
feiyu -w ~/code/my-project
```

To run one task and exit, put the task on the command line:

```
feiyu "add a --json flag to the CLI and update the tests"
```

A few less common options: `-m` sets a specific model name, `--base-url` changes the API address (e.g. Kimi's China endpoint https://api.moonshot.cn/v1), and `-V` prints the version.

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

Keys: `⏎` send, `\⏎` newline, `esc` interrupt the current answer, `ctrl-d` quit, `↑` `↓` history.

Slash commands (typing `/` pops up completions):

| Command | What it does |
|---|---|
| `/help` | Show help |
| `/model` | List models available to the current vendor; `/model <name>` switches, keeping the conversation |
| `/style` | Switch answer style: `default` / `concise` / `explanatory` / `teaching` |
| `/clear` | Wipe the conversation and start over |
| `/exit` | Quit |

The two numbers on the top border: **context** is the size of the most recent request, **cost** is what this session has run up so far. They're separate on purpose — the agent resends the whole history every round, so cumulative token counts run several times larger than the conversation itself, and a single number is easy to misread.

Cost is computed from the price table in `feiyu/pricing.py`. Model names and prices change often, so prefer overriding it in `~/.config/feiyu/pricing.json`:

```json
{"deepseek-v4-flash": {"hit": 0.02, "input": 1.0, "output": 2.0}}
```

Units are CNY per million tokens; the three fields are cache hit, input cache miss, and output. Models missing from the table don't get a guessed price — the display falls back to a token count.

## What it does

Given a task, it doesn't just start rewriting things. It looks around first — reads the relevant files, searches the code with keywords or a regex, and gets a feel for the structure — and only then creates or edits files. When it's done, it usually runs the tests or a relevant command to make sure nothing broke.

All of this runs through a few basic abilities: run a shell command, read a file, write a file, replace an exact chunk of a file, list files by glob, and search contents with a regex. It prints each one as it goes, so you can always see what it's doing.

For shell commands, `cd` persists across calls: once it cds into a subdirectory, later commands run from there, so it doesn't have to keep writing `cd x && …`. File paths for read/write/edit stay relative to the workspace root regardless.

## Models

DeepSeek and Kimi both offer OpenAI-compatible APIs, so they work just like Claude. Vendor model names get retired fairly often and the defaults in this repo can go stale — once you're inside, `/model` shows what your key can actually reach right now, and `/model <name>` switches to it.

## A note on safety

Feiyu really does run commands and change files on your machine. It's limited to its working directory, but inside that directory it has the same permissions you do. So use it on projects you trust and keep under git, so anything it breaks is easy to roll back.

It also inherits your environment when running shell commands, which means it can read keys like `$DEEPSEEK_API_KEY`. Don't run it in a directory full of sensitive credentials.

## Versioning

Semantic versioning. The version lives in `feiyu/__init__.py`; `pyproject.toml` reads it from there.

## License

[MIT](LICENSE) © feiyuceng06-prog
