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

Remember to activate the .venv each time before using it.

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

Just run feiyu to start an interactive session. It uses Claude by default and works in the current directory:

```
feiyu
```

Once inside, describe your task like you're chatting, e.g. "fix the bug in utils.py". Type /exit to quit.

Switch models with -p:

```
feiyu -p deepseek
feiyu -p kimi
```

Point it at another project with -w:

```
feiyu -w ~/code/my-project
```

To run one task and exit, put the task on the command line:

```
feiyu "add a --json flag to the CLI and update the tests"
```

Two less common options: -m sets a specific model name, and --base-url changes the API address (e.g. Kimi's China endpoint https://api.moonshot.cn/v1).

## What it does

Given a task, it doesn't just start rewriting things. It looks around first — reads the relevant files, searches the code with keywords or a regex, and gets a feel for the structure — and only then creates or edits files. When it's done, it usually runs the tests or a relevant command to make sure nothing broke.

All of this runs through a few basic abilities: run a shell command, read a file, write a file, replace an exact chunk of a file, list files by glob, and search contents with a regex. It prints each one as it goes, so you can always see what it's doing.

## Models

The default is Claude (claude-opus-4-8). DeepSeek and Kimi both offer OpenAI-compatible APIs, so they work too; the defaults there are deepseek-chat and kimi-k2.6. Vendor model names change now and then, so use -m when you need a specific one.

## A note on safety

Feiyu really does run commands and change files on your machine. It's limited to its working directory, but inside that directory it has the same permissions you do. So use it on projects you trust and keep under git, so anything it breaks is easy to roll back.

## License

[MIT](LICENSE) © feiyuceng06-prog
