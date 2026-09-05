# Feiyu Agent

简体中文 | [English](README.md)

一个跑在终端里的编程助手。你把任务交给它，它自己读代码、改文件、跑命令，每一步都打在屏幕上。支持 Claude、DeepSeek、Kimi，以及任何 OpenAI 兼容接口。

约 4800 行 Python，没有框架，依赖只有 `anthropic`、`pillow`、`prompt_toolkit` 三个。

![demo](docs/demo.gif)

## 安装

```
git clone https://github.com/feiyuceng06-prog/feiyu-agent.git && cd feiyu-agent && ./install.sh
```

脚本会建虚拟环境、装依赖，并往 `~/.local/bin` 放一个 `feiyu` 命令。需要 Python 3.10 以上。升级代码后再跑一次即可。

装完先加一个模型：运行 `feiyu`，输入 `/key add`，填接口地址、模型 id 和密钥。细节见[模型](#模型)一节。

两个可选的系统包，建议装上：

```
sudo pacman -S bubblewrap ripgrep      # Debian/Ubuntu: apt install bubblewrap ripgrep
```

`bubblewrap` 给 bash 加沙箱，`ripgrep` 让搜索快很多。

<details>
<summary>手动安装</summary>

```
python -m venv .venv
source .venv/bin/activate
pip install -e ".[openai]"
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

`[openai]` 装的是 `openai` 包，DeepSeek、Kimi 和其他 OpenAI 兼容接口都靠它。只用 Claude 的话 `pip install -e .` 就够。

</details>

## 使用

```
feiyu                             # 在当前目录开始对话
feiyu -w ~/code/my-project        # 指定工作目录
feiyu -m ds-flash                 # 用 /key 里的某一条模型（默认上次用的）
feiyu -r                          # 接着上次在这个目录里的对话
feiyu "给 CLI 加一个 --json 参数"    # 跑完一个任务就退出
```

其他参数：

- `--mode default|auto|plan|readonly`：权限模式。一次性任务和管道里没人能点确认，要它自己动手就加 `--mode auto`。
- `--json-schema <文件>`：按 schema 输出 JSON，见[接进别的程序](#接进别的程序)。
- `--review`：跑完让另一个模型给改动打分。

## 界面

```
╭─────────────────────────────────── deepseek  ·  上下文 1.4k  ·  ¥0.0014 ─╮
│ > 把 tools.py 里的 search 改成用 rg
╰──────────────────────────────────────────────────────────────────────────╯

⏺ Read(feiyu/tools.py)
  ⎿  读取 217 行

⏺ Update(feiyu/tools.py)
  ⎿  已修改 1 处
       47  -  rx = re.compile(args["regex"])
       47  +  if shutil.which("rg"):

  改完了，search 现在优先走 rg，找不到就退回原来的正则实现。
```

按键：`⏎` 发送，`\⏎` 换行，`shift+tab` 切权限模式，`esc` 中断，`ctrl-d` 退出，`↑` `↓` 翻历史。

顶边框显示上下文用了多少和这次会话花了多少钱。上下文是百分比，没配价格的模型不显示金额。回答里的 Markdown 会渲染（粗体、代码、列表、表格、链接），斜体除外，因为 `*` 在命令行里多半是通配符。

`esc` 打断是安全的：说到一半的话留在历史里，没跑完的调用会补一条「被打断」的结果，对话不会坏。

输入 `/` 弹出命令补全：

| 命令 | 作用 |
|---|---|
| `/help` | 显示帮助 |
| `/model` | 列出模型；`/model <名字>` 切换，对话保留 |
| `/key` | 添加、查看、删除模型配置 |
| `/mode` | 权限模式 |
| `/allow` | 查看或添加权限规则 |
| `/sandbox` | bash 沙箱：`off` / `workspace` / `strict` |
| `/jobs` | 查看或停掉后台命令 |
| `/mcp` | 查看接入的 MCP 服务器 |
| `/undo` | 撤销它最近一次写文件，可以连着撤 |
| `/hooks` | 查看钩子 |
| `/verify` | 查看或设置改完自动跑的命令 |
| `/review` | 让另一个模型给这次改动打分 |
| `/memory` | 查看、添加、删除这个目录的长期记忆 |
| `/context` | 重新加载项目约定文件 |
| `/cost` | 按任务看最近的花费 |
| `/compact` | 把历史压成摘要 |
| `/helper <名字>` | 摘要、子 agent、网页提炼改用一条便宜的模型；`/helper off` 改回 |
| `/style` | 回答风格：`default` / `concise` / `explanatory` / `teaching` |
| `/theme` | 界面主题：`default` / `swiss` |
| `/skills` | 看有哪些技能和自定义命令 |
| `/clear` | 清空对话和存盘 |
| `/exit` | 退出 |

## 工具

十七个工具，每次调用都打在屏幕上：

| 工具 | 说明 |
|---|---|
| `bash` | 跑 shell 命令。`cd` 在多次调用之间保持；长命令可以挂后台 |
| `read_file` `write_file` `edit_file` | 读、写、精确替换。路径相对工作区根目录。`edit_file` 能一次改同一文件的多处，对不上时会指出最接近的位置；`write_file` 可以追加 |
| `list_files` `search` | 按通配符列文件、按正则搜内容 |
| `symbol` | 走语言服务器找定义和引用（pyright、clangd、rust-analyzer、typescript-language-server、gopls、lua-language-server，装了才能用） |
| `git` | 看状态、diff、log，提交。不让它提交它不会提交 |
| `web_search` `web_fetch` | 搜网页、抓网页 |
| `task` | 派一个子 agent 去查一件事，只带结论回来 |
| `todo` | 任务清单 |
| `remember` | 记一条跨会话的记忆 |
| `job` | 看后台命令的输出，或停掉它 |
| `exit_plan` | 计划模式下把方案交给你 |
| `ask_user` | 拿不准时问你一句 |
| `skill` | 把一份技能的完整说明读进来 |

几件日常会碰到的事：

**截图可以直接给它看。** `read_file` 读到 png、jpg、gif、webp 会把图交给模型。模型能不能看图在 `/key add` 时填，没标的模型会明说看不了。

**跑很久的命令挂后台。** dev server、watch、长构建让它用 `bash` 的 `background` 参数起，之后用 `job` 取新输出。命令结束时它会收到通知。退出 feiyu 时这些进程会一起被杀掉。

**改文件前必须先读过。** 读过之后文件又被别人改了（你在编辑器里改的，或者格式化工具改的），写操作会被拦下让它重新读。这一道闸挡的是它拿旧内容整份覆盖、把你的改动抹掉的情况。

**只读的调用会并行。** 一批调用全是读文件、搜索、抓网页这类时最多四个一起跑；掺进一个写操作就整批串行。

**连续调满 25 轮工具会停下来问你。** 大重构本来就要几十轮，选「接着做」就继续。一次性任务里没人能回答，它会直接停，进度存着，`feiyu -r` 能接上。

### 子 agent

「这个项目里中断是怎么处理的」这种问题要翻十几个文件，全进主对话会把上下文撑爆。它会派子 agent 去看，只把结论带回来。子 agent 只能读，改不了东西也上不了网，用量算进 `/cost`。

### 任务清单和计划模式

事情多的时候它会先列清单，做完一步改一步。想收尾时清单上还有没做完的，会被拉回去继续。没做完就退出的话，进度留到下次在同一目录开工时带上，七天后作废。

改动大的时候切到计划模式（`shift+tab` 或 `/mode plan`）：它只能读和想，写和执行一律拒绝。方案交给你过目，你点头后才动手。

### 拿不准就问你

答案会影响它接下来怎么做的时候，它会停下来问一句，摆两到四个选项：

```
  │ 这两处的断句逻辑要一起改吗？
  │ [1] 一起改，保持三处同源（推荐）
  │ [2] 只改 writer，nvim 那边我自己来
    [1-2] 选  [t] 自己打字  [esc] 你定
```

小事它自己拿主意。

### 提 PR

没有专门的 PR 工具，用的是 bash 里的 `git` 和 `gh`。它会按这个顺序走：读 CONTRIBUTING 和 `.github` 下的模板，开分支，改，跑测试，提交，push 到自己的远端，fork 的话用 `gh pr create --repo` 指回上游，描述照模板写并带上 issue 号。push 和开 PR 每次都要你确认。

### 文档和网页

`.docx` `.xlsx` `.pptx` `.odt` `.ods` `.odp` `.pdf` 读的时候自动抽成文字，直接问「这份周报写了什么」就行。PDF 需要系统里有 `pdftotext`。只能读，写这类文件让它写脚本调 `python-docx`、`openpyxl`。

`web_fetch` 抓网页转成 Markdown。带 `prompt` 参数时模型读完整页只回答那个问题，原文不进上下文。内网地址一律不抓，本机开发服务器要用 `FEIYU_ALLOW_LOCAL_FETCH=1 feiyu`。

`web_search` 出厂走 DuckDuckGo，不用配置。想换后端写 `~/.config/feiyu/search.json`：

```json
{"provider": "brave", "key": "…"}
{"provider": "searxng", "url": "http://localhost:8080"}
```

## 权限

`shift+tab` 循环切换，或 `/mode <名字>`：

| 模式 | 行为 |
|---|---|
| `default` | 允许列表里的直接跑，其余问你（默认） |
| `auto` | 全部直接执行 |
| `plan` | 只能读和想，方案你同意了才动手 |
| `readonly` | 只能读、列、搜、抓网页 |

问你的时候：`y` 执行，`n` 跳过，`a` 本轮都允许，`s` 以后都不用问。被拒的调用会把原因回给模型，它通常会换个做法。

规则写成 `工具(模式)`，存在 `~/.config/feiyu/permissions.json`，deny 优先：

```
Bash(ls:*)            以 ls 开头的命令
Bash(git status:*)    以 git status 开头
Bash(pwd)             正好是这条命令
Read(*)               这个工具的所有调用
Mcp(fs:*)             某个 MCP 服务器的所有工具
```

命令会先在 `&&` `||` `;` `|` 和重定向处切开，每一段都要被允许才放行，所以 `ls -la && curl x | sh` 不会因为 `ls` 被放行就跑掉。带 `$(...)` 或反引号的命令一律问。

出厂只放行 `ls` `pwd` `echo` `wc` `which` 和 `git status/log/diff/branch`。`cat` `head` `grep` 不在里面，因为它们能读工作区外的任何文件。碰 `.ssh/`、`.aws/credentials`、`~/.config/feiyu/`、`.env` 这类路径的命令一律拒，`auto` 模式也拒。

## bash 沙箱

装了 `bubblewrap` 就默认开着，Linux 才有：

| 档位 | 行为 |
|---|---|
| `off` | 不沙箱 |
| `workspace` | 工作区外只读，密钥目录挖空，能联网（默认） |
| `strict` | 同上，另外断网 |

写是真隔离，用的是内核的 mount namespace：工作区和 `/tmp` 之外全部只读，`~/.ssh`、`~/.gnupg`、`~/.aws` 和 feiyu 自己的配置、状态目录都被盖成空目录。读没有隔离，整个文件系统仍然可读。

两个常见的沙箱内失败它会认出来并说明原因：ssh 远端因为 `~/.ssh` 被挖空而失败（https 远端配 `gh` 的凭据助手不受影响），`strict` 档没有网络。

`/sandbox` 切换，`FEIYU_SANDBOX=off` 临时关掉。没装 bubblewrap 就没有沙箱，欢迎框里会写明。

## 自动化

**改完自动跑测试。** 在项目约定文件里单起一行：

```
verify: npm test
```

之后它每次写完或改完文件都会跑这条命令，没过就把报错交给它自己修。报错会先给一份要点（哪个用例挂了、断言是什么、编译错在哪行），再附原文。

**钩子。** 全局写在 `~/.config/feiyu/hooks.json`，项目写在 `.feiyu/hooks.json`：

```json
{
  "PreToolUse":       [{"matcher": "Bash(git push:*)", "command": "echo '请开 PR' >&2; exit 1"}],
  "PostToolUse":      [{"matcher": "Write|Update", "command": "ruff check ."}],
  "UserPromptSubmit": [{"command": "git branch --show-current"}],
  "Stop":             [{"command": "notify-send 'feiyu 干完了'"}]
}
```

退出码 0 放行、非 0 拦下。`PreToolUse` 拦下这次调用，`PostToolUse` 把输出交给模型自己修，`UserPromptSubmit` 放行时把 stdout 当背景带上，`Stop` 只记日志。`matcher` 用权限规则的语法。钩子从 stdin 收一份 JSON。

项目里的钩子第一次见到要你点头才启用，文件改了会重新问。仓库里带下来的命令不该一 clone 就自动执行。

**独立评分。** `/review` 或 `--review` 另开一个模型，只给它看任务和最终 diff，按完成度、有没有 bug、有没有多做没要求的事打 0 到 10 分。要多花一次 API 调用，所以手动触发。

## 技能和自定义命令

给它一套写好的做法，不用改代码。都是一个 `.md` 文件，开头可以写 frontmatter：

```markdown
---
name: 发版
description: 这个项目怎么发一个新版本
---

1. 跑 `.venv/bin/python -m unittest discover -s tests -t .`
2. bump `feiyu/__init__.py` 的 `__version__`
3. 在 CHANGELOG 顶部写一节
```

放的位置决定谁来触发：

| 放哪 | 谁触发 |
|---|---|
| `~/.config/feiyu/skills/` `.feiyu/skills/` | **它自己**。系统提示里只有名字和用途，它判断这次用得上才把正文读进来 |
| `~/.config/feiyu/commands/` `.feiyu/commands/` | **你**。敲 `/发版` 触发，正文当成你说的话送进对话 |

命令的正文里写 `$ARGUMENTS`，会换成你跟在命令后面的东西：

```markdown
查一下 $ARGUMENTS 在这个仓库里是怎么实现的，只看代码，别改任何文件。
```

敲 `/查 上下文压缩` 就等于把整句话打了一遍。没写这个占位符的话参数附在末尾。

**为什么分成两种。** 系统提示每一轮请求都要重发一次。一份用得上一次的做法要是常驻在里面，一百轮就付了一百次钱。技能只有名字和用途常驻（一份约一行），正文等真用上那次才进上下文。

`/skills` 看有哪些、各自是哪来的。同名时项目那份盖过全局那份。

**项目技能第一次见到要你点头**，目录里的内容改了会重新问 —— 技能是它自己决定加载的，仓库里带下来的一段指令不该一 clone 就进它的上下文。**项目命令不用点头**：你不敲就不会生效，敲了等于你自己把那段字打了一遍。

## 它记得什么

**项目约定。** 工作区放一个 `FEIYU.md`（也认 `AGENTS.md`、`CLAUDE.md`，取第一个），启动时读进系统提示。`/context` 重新加载。

**长期记忆。** 值得留到下次的事它会自己记一句，比如构建怎么跑、某个坑的原因、你说过的偏好。记之前要你点头。下次在同一个目录开工自动带上，`/memory` 查看和删除。存在 `~/.local/state/feiyu/memory/`，不会写进你的项目。

```
⏺ Remember(这个项目的测试必须用 pytest -q 跑，conftest 里有 fixture)
  ⎿  记住了
```

**会话。** 每轮自动存盘，`feiyu -r` 接着聊。存盘是完整对话原文，在 `~/.local/state/feiyu/sessions/`，权限 600。`/clear` 连存盘一起删。

**上下文管理。** 每轮请求都要重发全部历史，所以它会自动收着：同一个文件又读了一次或被改过，旧的那份换成一行说明；很久以前的长输出只留头尾；实在超过上限才压成一段摘要继续。上下文上限在 `/key add` 时填，没填的话撞过一次「输入太长」就会自动记住那个模型的真实上限。

anthropic 接口会打缓存断点，重发的历史按缓存价计费。OpenAI 兼容接口是服务端自动缓存。`/cost` 里能看到命中率。

## 接进别的程序

给一份 JSON schema，跑完按它输出：

```
feiyu --json-schema review.schema.json "检查 calc.py 有没有 bug" > out.json
```

stdout 里只有 JSON，界面和日志全走 stderr。不合 schema 会让它重出，最多两次，还不行就非零退出。支持 `type` `required` `properties` `items` `enum` `additionalProperties`。

**日志。** 每件事一行 JSONL 写到 `~/.local/state/feiyu/logs/<日期>.jsonl`：每次工具调用、参数、权限判定、耗时、token 用量。排查「它为什么干了那件怪事」按 `run` 字段过滤。`FEIYU_NO_LOG=1` 关掉。

**花费。** `/cost` 按任务汇总最近七天：

```
   08-27 00:14  修好 parser.py 的错误处理         5轮    8.2k   71%    ¥0.031
   08-27 09:02  给 CLI 加 --json 参数            12轮   31.5k   84%    ¥0.118

   合计  39.7k  ¥0.149  缓存命中 81%
```

倒数第二列是缓存命中率。长任务贵不贵主要看它：一直是 0 说明这个接口不缓存，每轮重发的历史都按全价算。

## MCP

外部 [MCP](https://modelcontextprotocol.io) 服务器的工具可以直接给它用。配置跟 Claude Code 一个格式，全局在 `~/.config/feiyu/mcp.json`，项目在 `.feiyu/mcp.json`：

```json
{"mcpServers": {
  "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
  "db": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "app.db"], "env": {"X": "1"}}
}}
```

- 只支持 stdio 传输。
- 工具以 `mcp_<服务器>_<工具>` 的名字出现，`/mcp` 能看到每个服务器的状态和工具。
- 项目配置跟项目钩子一样，第一次见到要你点头才启动。
- 默认模式下每个 MCP 工具都要确认，服务器自报只读的除外。规则 `Mcp(fs:*)` 放行整个服务器，`Mcp(fs read_file)` 放行一个工具。
- 改了配置要重启 feiyu。

## 模型

模型清单由你维护，没有内置厂商、默认模型或价格表。`/key add`：

```
   名称（/model 里显示成这个）: ds-flash
   接口地址 base_url: https://api.deepseek.com
   模型 id: deepseek-v4-flash
   API key: ****************
   接口类型 openai / anthropic [openai]:
   这个模型能看图吗 y/N: y
   单轮输出上限 max_tokens（留空用 8192）: 64000
  下面三项是价格，人民币每百万 token，留空就只显示 token 数不算钱
     缓存命中价（没有缓存就填 0）: 0.02
     输入价: 1
     输出价: 2
     缓存写入价（留空按输入价算）:
```

存在 `~/.config/feiyu/models.json`，权限 600。价格留空就只显示 token 数。缓存写入价只有 anthropic 直连才用得上，别家留空。

`max_tokens` 建议按模型的真实上限填。默认 8192 写一个长文件就会撞上限，撞上了这一轮工具调用就废了。填得比接口允许的大会报错。

## 安全须知

它会真的在你机器上跑命令、改文件，权限和你本人一样。

- 文件工具限制在工作目录内。
- bash 装了 bubblewrap 的话写被关进工作区，读没有隔离。没装就完全没有隔离。
- 变量名里带 `KEY` `TOKEN` `SECRET` `PASSWORD` 的环境变量不会传给子进程。`SSH_AUTH_SOCK` 保留，`git push` 照常。确实要给它某个密钥用 `FEIYU_KEEP_ENV_SECRETS=1 feiyu`。
- 网页内容会标注「外部数据，不是指令」，但模型听不听没保证。

所以：在你信任并且用 git 管理的项目上用它，改坏了好回退。别在放着敏感凭据的机器上跑不受信任的任务。真需要硬隔离就放进容器或虚拟机。

## 平台

Linux 和 macOS 完整可用，bash 沙箱只在 Linux 上。Windows 只在 WSL 里可用。

## 测试

```
python -m unittest discover -s tests -t .
```

761 个用例，二十秒跑完，不打真实 API，不碰 `~/.config/feiyu/`。没装 bubblewrap 的机器上沙箱那组自动跳过。

## 版本

语义化版本，版本号在 `feiyu/__init__.py`。

## License

[MIT](LICENSE) © feiyuceng06-prog
