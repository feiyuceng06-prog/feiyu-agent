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

以后每次用之前，先 source 一下这个 .venv 把它激活。

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

直接运行 feiyu 就进入交互模式，默认用 Claude，工作目录是当前目录：

```
feiyu
```

进去以后像聊天一样把任务说出来就行，比如「把 utils.py 里的 bug 修一下」。输入 /exit 退出。

想换模型，加 -p：

```
feiyu -p deepseek
feiyu -p kimi
```

想让它在别的项目里工作，加 -w 指定目录：

```
feiyu -w ~/code/my-project
```

只想跑一个任务、跑完就退出的话，把任务直接写在后面：

```
feiyu "给命令行加一个 --json 参数，并更新测试"
```

还有两个不常用的参数：-m 换具体的模型名，--base-url 换接口地址（比如 Kimi 的国内站 https://api.moonshot.cn/v1）。

## 它能做什么

给它一个任务后，它不会闷头乱改，而是先弄清情况再动手：先读相关文件、用关键词或正则搜一搜代码，摸清结构，然后再新建或修改文件。改完之后一般会跑一遍测试或相关命令，确认没搞坏。

这些都是靠几个基本能力完成的——执行 shell 命令、读文件、写文件、精确替换文件里的某一段、按通配符列目录、用正则搜内容。它每用一个都会在屏幕上打出来，所以它做了什么你一直看得见。

## 支持的模型

默认是 Claude（claude-opus-4-8）。DeepSeek 和 Kimi 都提供了兼容 OpenAI 的接口，所以也能直接用，默认分别是 deepseek-chat 和 kimi-k2.6。各家的模型名偶尔会变，需要时用 -m 指定就好。

## 安全须知

Feiyu 会真的在你机器上跑命令、改文件。它只能动它的工作目录，但在这个目录里，它的权限和你本人一样。所以尽量在你信任、并且用 git 管理的项目上用它，万一改坏了也方便回退。
