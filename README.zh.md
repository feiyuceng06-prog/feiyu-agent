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

想要窗口就敲 `feiyu-desktop`，见[桌面端](#桌面端)。

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

按键：`⏎` 发送，`\⏎` 换行，`@` 带上文件，`shift+tab` 切权限模式，`esc` 中断，`ctrl-d` 退出，`↑` `↓` 翻历史。

顶边框显示上下文用了多少和这次会话花了多少钱。上下文是百分比，没配价格的模型不显示金额。回答里的 Markdown 会渲染（粗体、代码、列表、表格、链接），斜体除外，因为 `*` 在命令行里多半是通配符。

`esc` 打断是安全的：说到一半的话留在历史里，没跑完的调用会补一条「被打断」的结果，对话不会坏。

### `@` 指一个文件

句子里任何位置写 `@路径`，这份文件在消息发出去**之前**就读好附上 —— 你已经知道
要看哪个文件了，不该再花一整轮往返让它去调 `read_file`。`Tab` 补全路径，一次一层。

```
> 照着 @feiyu/serve.py 把 @feiyu/tools.py 里的抓取那段重写一下
  已读好并附上：feiyu/tools.py（400/2331 行）· feiyu/serve.py（400/920 行）
```

`@` 一个目录附的是那一层的清单。图片和二进制不贴正文 —— 路径确认了，让它自己去读。
只有工作区里真的存在的路径才展开，所以 `@property` 和 `user@example.com` 照旧是普通文字。

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
| `/cost` | 按任务看最近的花费和最慢的那一轮 |
| `/compact` | 把历史压成摘要 |
| `/helper <名字>` | 摘要、子 agent、网页提炼改用一条便宜的模型；`/helper off` 改回 |
| `/style` | 回答风格：`default` / `concise` / `explanatory` / `teaching` |
| `/theme` | 终端配色和记号：`default` / `swiss` / `retro` / `anthropic` |
| `/skills` | 看有哪些技能和自定义命令；`/skills <名字>` 开关一份 |
| `/profile` | 配置档案（人格）：看有哪些、换一份、`/profile off` 摘掉 |
| `/bot` | 机器人：看有哪些、`/bot <名字>` 用那套人格开一段新对话 |
| `/clear` | 清空对话和存盘 |
| `/exit` | 退出 |

## 工具

十八个工具，每次调用都打在屏幕上：

| 工具 | 说明 |
|---|---|
| `bash` | 跑 shell 命令。`cd`、export 的变量、source 进来的环境（venv）和定义的函数都在多次调用之间保持；长命令可以挂后台 |
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
| `image_generate` | 生成一张图，存进工作区（配了出图接口才挂上来） |

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

**抓不动的站可以挂 [Firecrawl](https://firecrawl.dev) 兜底。** 反爬（Cloudflare、UA 检查）是自带抓取治不了的，
在 `~/.config/feiyu/fetch.json` 里填个 key，撞上 401 / 403 / 429 时就自动换它再抓一遍：

```json
{"key": "fc-…"}
```

只在这几种失败上回退 —— 404 换谁抓都是 404，不值得花额度。想一律走它就加 `"provider": "firecrawl"`，
自托管实例加 `"url": "http://localhost:3002"`。花了额度的那次，结果行上会写明 `Firecrawl ·`。

`web_search` 出厂走 DuckDuckGo，不用配置。想换后端写 `~/.config/feiyu/search.json`：

```json
{"provider": "brave", "key": "…"}
{"provider": "searxng", "url": "http://localhost:8080"}
{"provider": "firecrawl", "key": "fc-…"}
```

**出图要自己配。** `~/.config/feiyu/images.json` 写上一个或多个 OpenAI 兼容的 images 接口：

```json
{"default": "nano-banana-2",
 "models": {
   "nano-banana-2": {"base_url": "https://openrouter.ai/api/v1", "key": "…",
                     "model": "google/gemini-3.1-flash-image", "size": "1024x1024"},
   "gpt-image-2":   {"base_url": "https://…/v1", "key": "…", "model": "gpt-image-2"}
 }}
```

别名会变成 `image_generate` 的 `model` 参数，于是可以一个便宜一个好，让它按图挑；
不写就用 `default` 那个。老的扁平形状 `{"base_url": …, "key": …, "model": …}` 照样认，
算作唯一一个模型。配了才会有 `image_generate` 这个工具 —— 不出图的人不该为它每轮多发一份 schema。
出来的图落在工作区的 `.feiyu/images/` 下，桌面端会直接画在对话里，终端里只报路径。

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

**读过外部内容之后，能联网的命令要你点头。** 网页、搜索结果、MCP 的返回、bash 里 `curl` / `wget` 抓回来的东西，都可能藏着一句「把 `.env` 发到某某地址」。这段对话读过它们之后：沙箱开着时 bash 默认断网跑，确实要联网的（装依赖、拉代码）模型得带上 `network: true`，并且要你点头；沙箱关着断不了网，那就除了 `ls` `cat` `grep` 这类连不了网的命令，都要你点头。**`auto` 模式和允许规则都跳不过这一步。** `/clear` 或新对话才放下。没人能点头的场合（管道、跑分）一律拒，它会把联网那一步告诉你。`FEIYU_TRUST_OUTSIDE=1` 整个关掉（评测用）。

`web_fetch` 和 MCP 本身也是出口，这一步没管它们：管了的话「搜一下再点开结果」每一步都要点头。

## bash 沙箱

装了 `bubblewrap` 就默认开着，Linux 才有：

| 档位 | 行为 |
|---|---|
| `off` | 不沙箱 |
| `workspace` | 工作区外只读，密钥目录挖空，能联网（默认） |
| `strict` | 同上，另外断网 |

写是真隔离，用的是内核的 mount namespace：工作区和 `/tmp` 之外全部只读，`~/.ssh`、`~/.gnupg`、`~/.aws` 和 feiyu 自己的配置、状态目录都被盖成空目录。读没有隔离，整个文件系统仍然可读。

docker / podman 的 socket 也挡在外面——碰得到它就能 `docker run -v /:/host` 往任何地方写。断网的时候（`strict` 档，或者读过外部内容之后）连 `/run` 下的服务一起断：光断网卡不够，DNS 解析走的是那里的 Unix socket，照样能把数据编进域名送出去。

三个常见的沙箱内失败它会认出来并说明原因：ssh 远端因为 `~/.ssh` 被挖空而失败（https 远端配 `gh` 的凭据助手不受影响），docker 连不上，断网时联不了网。

`/sandbox` 切换，`FEIYU_SANDBOX=off` 临时关掉。没装 bubblewrap 就没有沙箱，欢迎框里会写明。

## 自动化

**改完自动跑测试。** 在项目约定文件里单起一行：

```
verify: npm test
```

之后它每次写完或改完文件都会跑这条命令，没过就把报错交给它自己修。报错会先给一份要点（哪个用例挂了、断言是什么、编译错在哪行），再附原文。

**长任务的三道闸。** 同一份约定文件里，另起一行：

```
max-rounds: 0
budget: 5
time-budget: 30m
```

`max-rounds` 是连续调多少轮工具之后停下来问一句（出厂 25，**0 是不限**）。出厂那个数是给交互用的：改一行、编译、看报错、再改这种循环几百轮起步，事先给不出一个对的数字。

**放开轮数就得把 `budget` 填上。** 非交互场景（管道、一次性任务）没人回答那句问话，轮数不设闸时钱是唯一那道闸；而没填过价格的模型算不出花了多少，那时两道闸一道都不在。

`time-budget` 是这一句话最多跑多久（不写单位按分钟算，也可以写 `90s` / `45m` / `1.5h`，**0 是不限**）。它按**每一句话**计，不是整个会话——交互里你想事情的那几分钟不算在内。**快到点时它会收到一句「该收口了」**：把做成的那部分弄到能用、改到一半的别留在半路。被外面掐掉时，改到一半的文件比什么都没做更糟。

命令行的 `--max-rounds` / `--budget` / `--time-budget` 比约定文件优先，`/context` 重载也不会把它盖回去。

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

## 编辑器（nvim / VS Code）

在编辑器里开着的项目下跑 feiyu 时，`write_file` / `edit_file` 的确认就从终端里
那十四行 +/- 变成编辑器原生的左右对照。接受、拒绝，或者**先把右边改成你想要的样子
再接受** —— 写进文件的是你看到的那份，不是模型原来那份。

```
nvim       ~/.config/nvim/lua/feiyu_ide.lua     ga 接受 · gA 以后都接受 · gr 拒绝
VS Code    「Feiyu IDE Bridge」扩展              alt+y  · alt+shift+y   · alt+n
```

编辑器在 unix socket 上监听，并在 `$XDG_RUNTIME_DIR/feiyu/ide/` 下放一个几百字节的
lock 文件报到；feiyu 按**工作区完全相同**找到它，在上面说行分隔 JSON。
不引新依赖，也不监听任何 TCP 端口。

**谁先起来都行。** feiyu 先跑着、回头才打开编辑器也接得上：每次要确认写操作时看一眼，
最多五秒试一次连接，中途编辑器重启同理。接上和断开各说一句，你不用猜现在走的是哪条路。

降级只往一个方向：没有编辑器、编辑器崩了、回的东西认不出、答得太慢 —— 一律退回
原来终端里那套确认。**这座桥不可能挡住 agent。** `FEIYU_NO_IDE=1` 整个关掉；
`FEIYU_IDE_TIMEOUT`（默认 600 秒）是它等你多久之后放手、回终端问。

只管 diff。bash、`git commit`、`web_fetch` 这些照旧在终端确认 —— 它们没有 diff 可开。

## 桌面端

```
feiyu-desktop                     # 在当前目录开一个窗口
feiyu-desktop -w ~/code/project   # 指定工作目录
feiyu-desktop -r                  # 接着上次在这个目录里的对话
```

装完在应用列表里也能搜到 **Feiyu**。

**窗口里的东西不会跑到终端上。** agent 那半边人看的输出（还有桥接自己那几行 `→ op`）写进 `~/.local/state/feiyu/desktop.log`，跟对话存盘一样按 0600 存，攒到 4 MB 从头写。要它回到终端上看：`FEIYU_DESKTOP_STDERR=1 feiyu-desktop`。

左边是侧栏：新建会话、技能与工具，底下是历史对话——点一条切过去（agent 的上下文跟着换，不只是换画面），`shift+点` 置顶，划上去点 `×` 删掉（会先问一句），搜索框只筛本地那份列表。`Ctrl B` 收起侧栏，`Ctrl N` 开新会话。最上面那行是工作台，点一下换目录。

**设置**（`Ctrl K`）是五个页签：通用、技能、工具集、MCP、档案。「通用」里是外观、权限模式、回答风格、模型、旁活模型、bash 沙箱、校验命令、权限规则、长期记忆——按一下就生效，跟在终端敲那条斜杠命令是同一回事。

模型那一项可以直接加、改、删配置（接口地址、模型 id、API key、上下文上限、价格）。**已经存下的密钥不会发回界面**，所以改价格、改上限时 key 那栏留空就是「不动它」。这只在桌面端窗口里开着——开发用的浏览器入口听在回环地址上，同机任何进程都能连，那边一律拒。其余四个页签：每一项一个开关，关掉的东西既不进它的上下文，也调不出来——技能不在系统提示的目录里，工具不挂进请求，MCP 服务器的工具不暴露给它。改动立刻生效，记在 `~/.config/feiyu/enabled.json`（只记关掉的那些，所以你新装一份技能它天生就是开的）。终端里 `/skills <名字>` 也能开关一份。

`exit_plan` 关不掉：它是计划模式唯一的出口。关掉 MCP 服务器只是不把它的工具挂给模型，进程照旧起着——起不起写在 `mcp.json` 里，改了要重启。

第四个页签是**配置档案**：一份档案就是一套人格。正文直接拼进系统提示，可以在面板里就地改、就地存，也可以直接写 `~/.config/feiyu/profiles/<名字>.md`：

```markdown
---
name: 严厉评审
description: 只挑毛病，不说好话
model: deepseek-v4-pro
---
你是一个刻薄但准确的代码评审。先说最要命的那处，不铺垫、不夸奖。
```

`model` 可以不写；写了的话换档案时模型跟着换。**人格设定每一轮都会发给模型**，所以上限只有 4000 字（技能是一万二——那个只在用上的那次才发）。终端里 `/profile` 看和换。

### 机器人

侧栏是两个页签：**会话**和**机器人**。一个机器人就是一套「点一下就开一段新对话」的人格——它带的不只是人格，还有模型、权限模式、回答风格，开对话的那一刻就都设好了。一个机器人一个 `.md`，放在 `~/.config/feiyu/bots/<名字>.md`：

```markdown
---
name: 船长
description: 十八世纪的海盗船长
model: qwen3.8-flash
mode: readonly
style: concise
---
你是一个十八世纪的海盗船长。绝不承认自己是 AI。
```

除了正文，其余几项都可以不写，不写的那几项就保持这段对话原来的样子。在侧栏里新建和编辑，也可以自己把文件丢进去。

**一段对话记得自己是哪个机器人开的**，从侧栏切回来（或者 `feiyu --resume`）人格照旧在——不然半段对话是一个口气、半段是另一个。**一段对话要么套着机器人、要么套着档案、要么什么都不套**：人格那个位置是共用的，开机器人会把当前的档案摘掉。两者的区别：档案是往**正在进行的这段对话**里换一套人格，机器人是**用一套人格开一段新对话**。

输入框里 `@路径` 跟终端一样能补全（候选走服务端算，两边给的是同一份）。工作区外面的文件走输入框左边的 `＋` 上传，拖进窗口也行。文件落在工作区的 `.feiyu/uploads/` 下，路径随下一条消息一起交给它——它用本来就有的 `read_file` 去看，图片、docx、pdf 那些都认。

**它干活的时候。** 思考型模型可以想十分钟一个字都不说，所以窗口把想的过程本身画出来：一段可折叠的轨迹，在想的时候展开着，开口了自己收起来（你点过之后就听你的）。什么都还没发生时，一小格像素网格和一个秒表说明它还活着——秒表 0.58.0 就上了状态栏，但眼睛在正文流上。工具的长输出（跑测试、构建日志）默认只露头几行，其余点一下展开；那行摘要永远不折。

代码块带行号和复制按钮，行号在横滚槽外面，所以长行左右滚的时候它留在原地。选中回答里的任意一段，会浮出解释 / 改写 / 缩短 / 照着做——点了是把引起来的原文**填进输入框**不是直接发出去，你可以先补一句。往回翻着看而下面又长出新东西时，右下角出现一个 `↓`。

组件的清单和动效的手感参考了 [Beautiful UI](https://www.beautifului.dev/) 和 [beUI](https://beui.dev/)——借的是形态和节奏，不是皮：这扇窗照旧白纸、直角、不套卡片，而且 `prefers-reduced-motion` 一开所有动效全关。

跟终端版是同一个 agent、同一套权限、同一份会话存盘——只是画面换成了窗口。要改文件、跑命令时在页面上画出 diff 和命令原文，点了同意才动手。斜杠命令、技能、`esc` 中断都在。密钥的增删只在终端做（`feiyu` 里 `/key add`），桌面端连读都不给。

实现上是一个 Tauri 窗口，底下监管一个 `feiyu --serve` 子进程，两边一行一个 JSON。**它不监听任何端口**——前端和 agent 之间走进程内通道，外面够不着。

界面的信息架构参考了 [Hermes Desktop](https://github.com/NousResearch/hermes-agent)（MIT）的 `DESIGN.md`；配色、字体、间距是 [feiyuz123.com](https://feiyuz123.com) 那一套。没有框架，没有 npm 依赖。

桌面端要 `cargo` 和 `webkit2gtk`。`install.sh` 里是可选的：没有就跳过，终端版照常。

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

**三套外观。** 默认是纯白纸配 IBM Blue；「复古」是泛黄的纸、暖墨、赭色，只换配色；**Anthropic 是一整套 claude.ai 的观感**：暖象牙纸、陶土色、衬线正文，你说的话是气泡，输入区是一张浮起来的卡片，空对话时是一句衬线大字的问候。终端那边另有 `/theme`（换 ANSI 配色和记号，可选 `default` / `swiss` / `retro` / `anthropic`），两边互不相干。

**技能可以是一个包。** 一个 `.md` 是一份，一个带 `SKILL.md` 的目录也是一份，后者旁边可以放附属文件：

```
~/.config/feiyu/skills/
├── 发版.md              ← 一个文件一份
└── 教/                  ← 一个目录一份
    ├── SKILL.md         ← 正文
    └── MISSION-FORMAT.md  ← 附属文件，它按需读
```

附属文件**不进正文**——读正文时只会看到有哪些，要哪份它自己再取一次。网上下的技能包多半是 zip，直接丢进 `skills/` 就行，会自动解开（原 zip 留着，解过一次就不再解）。

**项目技能第一次见到要你点头**，目录里的内容改了会重新问 —— 技能是它自己决定加载的，仓库里带下来的一段指令不该一 clone 就进它的上下文。**项目命令不用点头**：你不敲就不会生效，敲了等于你自己把那段字打了一遍。

## 它记得什么

**项目约定。** 工作区放一个 `FEIYU.md`（也认 `AGENTS.md`、`CLAUDE.md`，取第一个），启动时读进系统提示。`/context` 重新加载。

**长期记忆。** 值得留到下次的事它会自己记一句，比如构建怎么跑、某个坑的原因、你说过的偏好。记之前要你点头。下次在同一个目录开工自动带上，`/memory` 查看和删除。存在 `~/.local/state/feiyu/memory/`，不会写进你的项目。

```
⏺ Remember(这个项目的测试必须用 pytest -q 跑，conftest 里有 fixture)
  ⎿  记住了
```

**同一件事栽第二次会被提醒。** 同一条命令报同样的错、同一个文件同样的替换失败，第二次时结果里会多一句「别再原样重试，先弄清楚为什么」，第三次叫它停下来问你。缓过来之后它会被提示：原因要是这个项目特有的，就用 remember 记一句（照样要你点头），下次别再踩。

**会话。** 每轮自动存盘，`feiyu -r` 接着聊。存盘是完整对话原文，在 `~/.local/state/feiyu/sessions/`，权限 600。`/clear` 连存盘一起删。

**上下文管理。** 每轮请求都要重发全部历史，所以它会自动收着：同一个文件又读了一次或被改过，旧的那份换成一行说明；很久以前的长输出只留头尾；实在超过上限才压成一段摘要继续。上下文上限在 `/key add` 时填，没填的话撞过一次「输入太长」就会自动记住那个模型的真实上限。

anthropic 接口会打缓存断点，重发的历史按缓存价计费。**走 OpenAI 兼容格式的 Claude（OpenRouter、中转站）同样得打**，它自己认得出来——不打的话每一轮都是全价，实测贵十倍。其余 OpenAI 兼容接口是服务端自动缓存。`/cost` 里能看到命中率。

## 接进别的程序

给一份 JSON schema，跑完按它输出：

```
feiyu --json-schema review.schema.json "检查 calc.py 有没有 bug" > out.json
```

stdout 里只有 JSON，界面和日志全走 stderr。不合 schema 会让它重出，最多两次，还不行就非零退出。支持 `type` `required` `properties` `items` `enum` `additionalProperties`。

**历史瘦身会看缓存。** 接口对重发的历史是按前缀缓存的——改了中间某一条，它后面的全部从缓存价掉回原价。所以「把旧的工具输出折叠掉」这件事只在两种时候做：这个接口压根不缓存（那折叠纯赚），或者上下文已经过半（快撞窗口了，省地方比省钱要紧）。`/cost` 里的命中率就是判断依据。

**日志。** 每件事一行 JSONL 写到 `~/.local/state/feiyu/logs/<日期>.jsonl`：每次工具调用、参数、权限判定、耗时、token 用量。排查「它为什么干了那件怪事」按 `run` 字段过滤。`FEIYU_NO_LOG=1` 关掉。

**额度。** 状态栏右边显示接口那边还剩多少（`余 $9.78`），点一下现查一次，每轮跑完自动同步。这跟左边那个 `¥` 不是一回事——那个是按你填的价格估的这一趟花了多少，这个是人家账上的余额。

认得出的接口：OpenRouter、DeepSeek、Moonshot、SiliconFlow，以及一堆抄 OpenAI 早年计费接口的中转站。**OpenAI 和 anthropic 的普通 key 查不到余额**（那是账号后台的事），这时候就整个不显示，不会编一个数给你。密钥只会发给你自己填的那个 `base_url` 的 host。

**花费。** `/cost` 按任务汇总最近七天：

```
   08-27 00:14  修好 parser.py 的错误处理       5轮    8.2k   71%   最慢12s   ¥0.031
   08-27 09:02  给 CLI 加 --json 参数         12轮   31.5k   84%   最慢38s   ¥0.118

   合计  39.7k  ¥0.149  缓存命中 81%
```

百分比是缓存命中率。长任务贵不贵主要看它：一直是 0 说明这个接口不缓存，每轮重发的历史都按全价算。

「最慢」是这个任务里**最慢的那一轮**，不是平均——九轮五秒、一轮十分钟的话，平均会把那十分钟摊没，而你要查的正是它。这个数大得离谱多半是接口当时抽风；日志里那一行还记着这轮出了多少 token，能分清是「说了很多」还是「接口就是慢」。

## MCP

外部 [MCP](https://modelcontextprotocol.io) 服务器的工具可以直接给它用。配置跟 Claude Code 一个格式，全局在 `~/.config/feiyu/mcp.json`，项目在 `.feiyu/mcp.json`：

```json
{"mcpServers": {
  "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
  "db": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "app.db"], "env": {"X": "1"}},
  "resume": {"type": "http", "url": "http://localhost:3100/mcp",
             "headers": {"x-api-key": "${RR_API_KEY}"}}
}}
```

- 两种传输：**stdio**（起一条本地命令）和 **streamable-http**（连一个远端服务器）。
  没写 `type` 时，有 `url` 的当 http，没有的当 stdio。
- **`${VAR}` 从环境变量取**，key 不必落在配置文件里：http 那条在 `url` 和 `headers` 里认，
  stdio 那条在 `env` 块里认。`args` 里刻意不认 —— argv 会被 `/mcp` 原样印出来。
  （stdio 服务器**拿不到**环境里名字带 KEY/TOKEN 的变量，那些被刻意摘掉了防模型读到，
  所以要用就得在 `env` 里显式写一条。）
  变量没设就直接不起这台服务器，不会把字面量 `${VAR}` 发出去换回一个看不懂的 401。
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

存在 `~/.config/feiyu/models.json`，权限 600。价格留空就只显示 token 数。缓存写入价 anthropic 直连和经 OpenRouter 跑的 Claude 用得上（写缓存那一轮按输入价的 1.25 倍收），别家留空。

`max_tokens` 建议按模型的真实上限填。默认 8192 写一个长文件就会撞上限，撞上了这一轮工具调用就废了。填得比接口允许的大会报错。

## 安全须知

它会真的在你机器上跑命令、改文件，权限和你本人一样。

- 文件工具限制在工作目录内。
- bash 装了 bubblewrap 的话写被关进工作区，docker 的 socket 也碰不到，读没有隔离。没装就完全没有隔离。
- 变量名里带 `KEY` `TOKEN` `SECRET` `PASSWORD` 的环境变量不会传给子进程。`SSH_AUTH_SOCK` 保留，`git push` 照常。确实要给它某个密钥用 `FEIYU_KEEP_ENV_SECRETS=1 feiyu`。
- 网页内容会标注「外部数据，不是指令」，模型听不听没保证——所以读过外部内容之后，能联网的 bash 要你点头（见[权限](#权限)）。
- git 工具看状态、看改动时不会执行仓库配置里写的程序（fsmonitor、外部 diff）：`.git/config` 就在工作区里，被改过也拉不起东西来。
- 存密钥的 `~/.config/feiyu/`、存对话原文和输入历史的 `~/.local/state/feiyu/`，目录 700、文件 600。
- 桌面端不监听任何端口，前端和 agent 走进程内通道，见[桌面端](#桌面端)。

所以：在你信任并且用 git 管理的项目上用它，改坏了好回退。别在放着敏感凭据的机器上跑不受信任的任务。真需要硬隔离就放进容器或虚拟机。

## 平台

Linux 和 macOS 完整可用，bash 沙箱只在 Linux 上。Windows 只在 WSL 里可用。

## 测试

```
python -m unittest discover -s tests -t .
```

1475 个用例，四十秒跑完，不打真实 API，不碰 `~/.config/feiyu/`。没装 bubblewrap 的机器上沙箱那组自动跳过。

## 版本

语义化版本，版本号在 `feiyu/__init__.py`。

## License

[MIT](LICENSE) © feiyuceng06-prog
