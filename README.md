# Feiyu Agent

简体中文 | [English](README.en.md)

Feiyu Agent 是一个跑在终端里的编程助手。你把任务交给它，它会自己读代码、改文件、执行命令，一步步把事情做完，整个过程你都看得到它在做什么。它可以用 Claude、DeepSeek 或 Kimi。

## 安装

需要 Python 3.10 以上。建议先建一个虚拟环境再装，会干净很多；有些系统（比如 Arch、较新的 Debian/Ubuntu）也不允许直接往系统 Python 里装包：

```
python -m venv .venv
source .venv/bin/activate       # Windows 用 .venv\Scripts\activate
```

激活之后再安装：

```
pip install -e .
```

以后每次用之前，先 source 一下这个 .venv 把它激活。嫌麻烦的话，在 PATH 上放个 shim 就能到处直接敲 `feiyu`：

```
printf '#!/usr/bin/env bash\nexec %s/.venv/bin/feiyu "$@"\n' "$PWD" > ~/.local/bin/feiyu
chmod +x ~/.local/bin/feiyu
```

如果要用 DeepSeek 或 Kimi，再装上 openai 依赖：

```
pip install -e ".[openai]"
```

然后按你要用的模型，设置对应的密钥：

```
export ANTHROPIC_API_KEY=...    # Claude
export DEEPSEEK_API_KEY=...     # DeepSeek
export MOONSHOT_API_KEY=...     # Kimi
```

## 使用

直接运行 feiyu 就进入交互模式，工作目录是当前目录：

```
feiyu
```

用哪家模型不用指定——它会挑第一个设了密钥的厂商。想固定用某一家，加 `-p`：

```
feiyu -p deepseek
feiyu -p kimi
```

想让它在别的项目里工作，加 `-w` 指定目录：

```
feiyu -w ~/code/my-project
```

只想跑一个任务、跑完就退出的话，把任务直接写在后面：

```
feiyu "给命令行加一个 --json 参数，并更新测试"
```

还有几个不常用的参数：`-m` 换具体的模型名，`--base-url` 换接口地址（比如 Kimi 的国内站 https://api.moonshot.cn/v1），`-V` 看版本号。

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

按键：`⏎` 发送，`\⏎` 换行，`esc` 中断当前回答，`ctrl-d` 退出，`↑` `↓` 翻历史。

斜杠命令（打 `/` 会弹补全）：

| 命令 | 作用 |
|---|---|
| `/help` | 显示帮助 |
| `/model` | 列出当前厂商可用的模型；`/model <名字>` 切换，对话历史保留 |
| `/style` | 切换回答风格：`default` / `concise` / `explanatory` / `teaching` |
| `/clear` | 清空对话历史，重新开始 |
| `/exit` | 退出 |

顶边框右侧那两个数字，**上下文**是最近一次请求的规模，**花费**是这次会话累计花了多少钱。两个要分开看：agent 每轮都要把完整历史重发一遍，所以累计的 token 数会比对话本身大好几倍，只看一个数容易误判。

价钱按 `feiyu/pricing.py` 里的价格表算。模型改名调价都很频繁，想改的话优先写 `~/.config/feiyu/pricing.json`，它会覆盖内置表：

```json
{"deepseek-v4-flash": {"hit": 0.02, "input": 1.0, "output": 2.0}}
```

单位是人民币每百万 token，三项分别是缓存命中、输入未命中、输出。表里没有的模型不会瞎猜价钱，界面上退回显示 token 数。

## 它能做什么

给它一个任务后，它不会闷头乱改，而是先弄清情况再动手：先读相关文件、用关键词或正则搜一搜代码，摸清结构，然后再新建或修改文件。改完之后一般会跑一遍测试或相关命令，确认没搞坏。

这些都是靠几个基本能力完成的——执行 shell 命令、读文件、写文件、精确替换文件里的某一段、按通配符列目录、用正则搜内容。它每用一个都会在屏幕上打出来，所以它做了什么你一直看得见。

跑 shell 命令时 `cd` 会在多次调用之间保持：它一旦 `cd` 进子目录，后续命令都从那里执行，不用每次都写 `cd x && …`。读写文件的路径则始终相对工作区根目录，不受 `cd` 影响。

## 支持的模型

DeepSeek 和 Kimi 都提供了兼容 OpenAI 的接口，所以能和 Claude 一样直接用。各家的模型名换得挺勤，仓库里写的默认值随时可能过期——进去之后打 `/model` 就能看到你的密钥当前实际能用哪些，再 `/model <名字>` 切过去。

## 安全须知

Feiyu 会真的在你机器上跑命令、改文件。它只能动它的工作目录，但在这个目录里，它的权限和你本人一样。所以尽量在你信任、并且用 git 管理的项目上用它，万一改坏了也方便回退。

另外它执行 shell 命令时会继承你的环境变量，也就是说它读得到 `$DEEPSEEK_API_KEY` 这类密钥。别在放着敏感凭据的目录里用它。

## 版本

遵循语义化版本，版本号写在 `feiyu/__init__.py`，`pyproject.toml` 从那里读。

## License

[MIT](LICENSE) © feiyuceng06-prog
