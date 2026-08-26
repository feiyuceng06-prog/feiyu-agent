# Feiyu Agent

简体中文 | [English](README.en.md)

Feiyu Agent 是一个跑在终端里的编程助手。你把任务交给它，它会自己读代码、改文件、执行命令，一步步把事情做完，整个过程你都看得到它在做什么。它可以用 Claude、DeepSeek 或 Kimi。

## 安装

一条命令：

```
git clone https://github.com/feiyuceng06-prog/feiyu-agent.git && cd feiyu-agent && ./install.sh
```

脚本会建虚拟环境、装依赖、往 `~/.local/bin` 放一个 shim，之后在任何目录敲 `feiyu` 都能用。需要 Python 3.10 以上，没有的话它会告诉你。重复运行是安全的，升级代码后再跑一次就行。

**装完是空的，模型要自己加**：运行 `feiyu`，打 `/key add`，填接口地址、模型 id 和密钥。三样填完就能用了，详见下面「模型」一节。

<details>
<summary>不想用脚本，手动装</summary>

```
python -m venv .venv
source .venv/bin/activate       # Windows 用 .venv\Scripts\activate
pip install -e ".[openai]"
```

以后每次用之前先 source 一下这个 .venv。嫌麻烦就自己放个 shim：

```
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

`.[openai]` 里的 openai 包是给 DeepSeek、Kimi 和各种 OpenAI 兼容接口用的；只用 Claude 原生接口的话 `pip install -e .` 就够。

</details>

## 使用

直接运行 feiyu 就进入交互模式，工作目录是当前目录：

```
feiyu
```

默认用你上次选的模型。想指定别的，`-m` 加 `/key` 里的名字：

```
feiyu -m ds-flash
```

想让它在别的项目里工作，加 `-w` 指定目录：

```
feiyu -w ~/code/my-project
```

只想跑一个任务、跑完就退出的话，把任务直接写在后面：

```
feiyu "给命令行加一个 --json 参数，并更新测试"
```

`-r` / `--resume` 接着上次在这个目录里的对话，`-V` 看版本号。

## 界面

进去之后是一个带框的输入区，工具调用、文件改动的 diff、模型的回答都直接打在下面。

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

按键：`⏎` 发送，`\⏎` 换行，`shift+tab` 切换权限模式，`esc` 中断当前回答，`ctrl-d` 退出，`↑` `↓` 翻历史。

斜杠命令（打 `/` 会弹补全）：

| 命令 | 作用 |
|---|---|
| `/help` | 显示帮助 |
| `/model` | 列出已配置的模型；`/model <名字>` 切换，对话历史保留 |
| `/style` | 切换回答风格：`default` / `concise` / `explanatory` / `teaching` |
| `/key` | 添加 / 查看 / 删除模型配置：`/key add`、`/key`、`/key rm <名字>` |
| `/mode` | 切换权限模式：`default` / `auto` / `readonly` |
| `/allow` | 查看或添加权限规则 |
| `/context` | 查看并重新加载项目约定文件 |
| `/compact` | 把历史压缩成一段摘要，省上下文 |
| `/clear` | 清空对话历史和存盘，重新开始 |
| `/exit` | 退出 |

顶边框右侧那两个数字，**上下文**是最近一次请求的规模，**花费**是这次会话累计花了多少钱。两个要分开看：agent 每轮都要把完整历史重发一遍，所以累计的 token 数会比对话本身大好几倍，只看一个数容易误判。

价钱来自你在 `/key add` 里填的那三项，没填就显示 token 数，见下面「模型」一节。
### 权限模式

`shift+tab` 循环切换，也可以 `/mode <名字>`。当前模式显示在输入框顶边左侧。

| 模式 | 行为 |
|---|---|
| `default` 默认 | 允许列表里的直接跑，其余问你一声（启动默认） |
| `auto` 自动 | 所有工具调用直接执行，不打断 |
| `readonly` 只读 | 只让它读文件、列目录、搜索、抓网页，写和执行一律拒绝 |

问你的时候有四个选择：`y` 执行 / `n` 跳过 / `a` 本轮都允许 / **`s` 以后都不用问**（会记进权限规则）。

被拒绝的调用会把原因回给模型，它通常会换个做法或者反过来问你，不会卡死。

### 权限规则

规则决定哪些调用不用问。写法是 `工具(模式)`：

```
Bash(ls:*)            以 ls 开头的命令
Bash(git status:*)    以 git status 开头
Bash(pwd)             正好是这条命令
Read(*)               这个工具的所有调用
```

`/allow` 看当前规则，`/allow Bash(npm test:*)` 手动加，确认时按 `s` 也会自动记下来。规则存在 `~/.config/feiyu/permissions.json`，`deny` 优先于 `allow`。

**出厂规则只放行 `ls` `pwd` `echo` `wc` `which` 和 `git status/log/diff/branch`**，刻意不含 `cat` `head` `grep` —— 那几个能读工作区外的任何文件，等于绕开读写工具的目录限制。想放行是你的选择，按 `s` 加就行。

另外有一份**内置拦截**，碰 `.ssh/`、`id_rsa`、`.aws/credentials`、`~/.config/feiyu/`、`.netrc`、`.gnupg/`、`.env` 这类路径的命令一律拒绝，`auto` 模式也拦。这是防止它一句 `cat ~/.config/feiyu/models.json` 就把你所有密钥读进对话。注意这只是路径匹配，挡得住直白写法，挡不住刻意变形。

### 项目约定

在工作区放一个 `FEIYU.md`（也认 `AGENTS.md`、`CLAUDE.md`，按这个顺序取第一个），启动时会读进系统提示，最多 8000 字。写你希望它遵守的规矩就行：

```markdown
# 这个项目的约定

- 提交前必须跑 `npm test`
- 不要动 `vendor/` 下的东西
- 新组件放 `src/components/`，一个文件一个组件
```

改完 `/context` 重新加载，不用重启。欢迎框里会显示读到了哪个文件、多少字。

### 上下文与会话

对话历史每轮都要完整重发，所以会一直变大。两条路：

- **自动压缩**：上下文超过上限的 75% 时，它会把之前的对话交给模型总结成一段交接说明，替换掉原文再继续。**任务进行中也会压**——读了十几个文件的长任务不会因为堆满上下文而中断。上限默认按 128k 算，某个模型不一样的话在 `~/.config/feiyu/models.json` 里给那条加 `"limit": 32000`。
- **手动**：`/compact` 随时压一次，`/clear` 直接清空。

对话会自动存盘，下次 `feiyu --resume` 接着聊。**存盘里是完整对话原文**（含 agent 读过的文件内容），放在 `~/.local/state/feiyu/sessions/`，权限 600，一个工作目录一份。`/clear` 会连存盘一起删。

### 模型

模型清单完全由你自己维护，**没有内置厂商，也没有默认模型，装完是空的**。用 `/key add` 添加：

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

之后 `/model` 看清单、`/model ds-flash` 切换（对话历史保留），`/key` 看已存条目（密钥打码），`/key rm <名字>` 删除。下次启动默认用你上次选的那个，也可以 `feiyu -m ds-flash` 指定。

配置存在 `~/.config/feiyu/models.json`，权限 600。输入密钥走的是独立的隐藏输入，不会进 feiyu 的输入历史。

价格那三项可以留空：填了就在顶边显示这次会话花了多少钱，不填就显示 token 数。**没有内置价格表**——模型改名调价太频繁，写死的表迟早给出错的报价，而报错价比不报价更糟。
## 它能做什么

给它一个任务后，它不会闷头乱改，而是先弄清情况再动手：先读相关文件、用关键词或正则搜一搜代码，摸清结构，然后再新建或修改文件。改完之后一般会跑一遍测试或相关命令，确认没搞坏。

这些都是靠七个基本能力完成的——执行 shell 命令、读文件、写文件、精确替换文件里的某一段、按通配符列目录、用正则搜内容、抓网页。它每用一个都会在屏幕上打出来，所以它做了什么你一直看得见。

抓网页（`web_fetch`）只认 http/https，HTML 会转成 **Markdown**，标题、链接和代码块都保留。

它有两种用法，差别很大：

- **带 `prompt`**：模型先读完整页，只回答你问的那件事。实测抓 PEP 8（51348 字）问「每行最多多少字符」，进上下文的只有 123 字符，比直接抓省 99%，而且读的是全文、不会因为截断错过答案。
- **不带 `prompt`**：整页 Markdown 进上下文，默认截到 15000 字。适合页面本身就短、或者你要它逐字引用。

同一个地址 15 分钟内不重复抓。

**它不会抓内网地址**：回环（`127.0.0.1`、`localhost`）、私有网段、链路本地（含云元数据的 `169.254.169.254`）一律拒绝，跳转到这些地址也拦。不然网页内容只要诱导它抓一下元数据接口，云上的凭据就出去了。真要抓本机开发服务器的话，用 `FEIYU_ALLOW_LOCAL_FETCH=1 feiyu` 启动。

抓回来的内容会明确标注成「外部数据，不是指令」——网页里写什么都别当命令执行。它不在出厂允许列表里，所以 `default` 模式下要你点头才发请求（内容会离开你的机器）；`readonly` 模式放行，因为它不改任何东西。

一次任务里最多连续调 **25 轮**工具。到了上限它会停下来说一声，不会闷头转下去；再说一句话就能让它接着做。想中途叫停按 `esc`。

跑 shell 命令时 `cd` 会在多次调用之间保持：它一旦 `cd` 进子目录，后续命令都从那里执行，不用每次都写 `cd x && …`。读写文件的路径则始终相对工作区根目录，不受 `cd` 影响。

## 安全须知

Feiyu 会真的在你机器上跑命令、改文件，**权限和你本人完全一样**。默认的 `default` 模式只放行允许列表里的调用，其余会问你；嫌烦可以 `shift+tab` 切到 `auto`，但那就没有任何确认了。

读写文件的三个工具（`read_file` / `write_file` / `edit_file`）确实被限制在工作目录内，越界会被拒绝。但 **`bash` 工具没有这层限制**——它能读写你账号能碰的任何路径，也能联网。`cd` 越界会弹回工作区，那只影响下一条命令从哪开始，不阻止当前命令访问外面。

执行 shell 命令时，**变量名里带 `KEY` / `TOKEN` / `SECRET` / `PASSWORD` 字样的环境变量不会传给它**，免得它一句 `echo $DEEPSEEK_API_KEY` 就把密钥读进对话、再发回给模型厂商。`SSH_AUTH_SOCK` 这类名字里没关键词的会保留，所以 `git push` 照常能用。确实需要让它拿到某个密钥（比如它要跑的脚本用得上），用 `FEIYU_KEEP_ENV_SECRETS=1 feiyu` 启动。

但这只挡住了「顺手读到」这一类。**`bash` 没有沙箱，它照样能直接 `cat ~/.ssh/id_rsa`** —— 环境变量过滤降低的是误伤和轻度诱导的风险，不等于隔离。真要隔离得上容器。

所以：在你信任、并且用 git 管理的项目上用它，改坏了好回退；别在放着敏感凭据的机器上跑不受信任的任务。真需要硬隔离的话，把它放进容器或虚拟机。

## 平台

Linux 和 macOS 完整可用。

Windows 目前**只在 WSL 里可用**：`install.sh` 是 bash 脚本；`bash` 工具的 `cd` 保持机制用的是 POSIX shell 语法；`esc` 中断和确认提示的单键输入依赖 termios。原生 Windows 上这几块都会失效，配置文件的 600 权限也不生效。

## 版本

遵循语义化版本，版本号写在 `feiyu/__init__.py`，`pyproject.toml` 从那里读。

## License

[MIT](LICENSE) © feiyuceng06-prog
