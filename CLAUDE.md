# Feiyu Agent — 给 Claude 的项目约定

跑在终端里的编程 agent。约 4800 行 Python，无框架。

verify: .venv/bin/python -m unittest discover -s tests -t .

## 结构

| 文件 | 职责 |
|---|---|
| `feiyu/__main__.py` | CLI 参数、主循环、斜杠命令 |
| `feiyu/agent.py` | `SYSTEM_PROMPT`、`STYLES`、turn 循环、工具分发 |
| `feiyu/tools.py` | 十三个工具的 schema 与实现，含办公文档抽取 |
| `feiyu/providers.py` | anthropic / openai 兼容两种后端，用量与计价 |
| `feiyu/keys.py` | 模型清单（接口地址/密钥/价格）的存取 |
| `feiyu/permissions.py` | 命令级权限规则 |
| `feiyu/ui.py` | **所有给人看的输出**：配色、框、流式渲染、输入区 |
| `feiyu/pricing.py` | 花费换算，只有两个函数 |
| `feiyu/session.py` | 会话存盘 / 恢复 |
| `feiyu/trace.py` | 结构化日志（JSONL）与按任务的花费汇总 |
| `feiyu/schema.py` | 结构化输出：抠 JSON + 最小 schema 校验 |
| `feiyu/memory.py` | 跨会话长期记忆（agent 自己记的事实） |
| `feiyu/hooks.py` | 钩子：四个时机跑外部命令，含项目钩子的信任机制 |
| `feiyu/todo.py` | 任务清单的状态与校验（渲染在 ui，工具在 tools） |
| `feiyu/subagent.py` | 子 agent：独立后端 + 只读工具集，只带结论回来 |
| `feiyu/sandbox.py` | bash 沙箱（bubblewrap），三档 off / workspace / strict |
| `feiyu/files.py` | 记住看过的文件版本，写之前对一次，别盖掉没看见的改动 |
| `feiyu/history.py` | 历史瘦身：把过期或过长的工具结果换成一行说明 |
| `feiyu/progress.py` | 上次在这个目录干到哪了（跟 memory 分开，会过期） |
| `feiyu/jobs.py` | 挂在后台跑的 bash 命令：收输出、报结束、杀进程组 |
| `feiyu/images.py` | 图片缩到上限之内再 base64，只有 read_file 用 |
| `ui.MarkdownStream` | 流式 markdown → (文本, 样式) 片段，Stream 上色 |

**分层原则：`agent.py` 和 `tools.py` 只产出结构化事实**（`ToolResult.summary` / `.detail`），
怎么画一律由 `ui.py` 决定。加新工具时给它填好 `summary`，不要在业务代码里拼 ANSI。

## 硬性约定

- **版本号每次改完就 bump**。单一真源是 `feiyu/__init__.py` 的 `__version__`，
  `pyproject.toml` 用 `[tool.setuptools.dynamic]` 读过来。小改动进 patch，加功能进 minor。
- **README 有中英两份**（`README.md` / `README.en.md`），互相链接，改一份必须改另一份。
- 不引新依赖来解决小问题。现有依赖只有 `anthropic`、`pillow`、`prompt_toolkit`，
  `openai` 是可选 extra。
- 注释只写代码本身表达不了的约束，不要解释「这段在干什么」。

## 改 ui.py 前必读

- **量显示宽度前先剥 ANSI**：`ui.visible()` 会先 `_ANSI.sub("", s)`，
  因为 `get_cwidth()` 会把转义序列算成可见字符。截断同理，用 `ui.clip()` 不要用 `len()`。
- **中断后必须 `finally: stream.finish()`**。spinner 是 daemon 线程，不叫停会继续往屏幕糊帧。
- 输入区是自绘的 `Application`（不是 `PromptSession`）。原因：`PromptSession` 画不了输入行
  下方的底边框；`bottom_toolbar` 会钉在屏幕底部把框撑成空壳；`rprompt` 落在被顶边框占满的首行。
  补全菜单是布局的一节，做成 `Float` 会压住底边框。
- `accept_handler` 必须 `return True`，否则 buffer 被 reset，scrollback 里留下空框。
- 自定义 up/down 键要加 `filter=~has_completions`，不然补全菜单开着时选不动。

## 测试（0.12.0 起）

`tests/`，标准库 `unittest`，**不引测试框架**：

```bash
python -m unittest discover -s tests -t .
```

`tests/fakes.py` 提供 `FakeBackend`（按脚本逐轮返回，不打真实 API）和
`unpaired(messages)`——**这个是主力断言**：返回历史里没配上结果的 `tool_use` id，
非空就是接口会 400 的形状。

写新用例时：

- `permissions` / `keys` / `session` 的存储路径都是 **import 时算好的**，
  测试里要换掉模块上的 `STORE` / `STATE` / `DIR`，不然会读写用户真正的密钥和规则。
- `ui._COLOR` 按 `isatty` 定，测试里不是终端就一律不上色，
  颜色相关的断言会**空转**——要断言宽度先把 `ui._COLOR` 设成 True。
- agent 会往屏幕上画东西，用 `contextlib.redirect_stdout` 挡掉。
- 改完跑一遍「回退验证」：`git stash push feiyu/` 之后再跑，
  该失败的用例得真的失败，否则这条用例只是在描述现状，抓不住回归。

## 题库（`evals/`，跟 `tests/` 分开）

`tests/` 测的是代码对不对（不打接口、十几秒跑完）；`evals/` 测的是**它干活干得好不好**
（打真实接口、要花钱、有随机性）。两者不要混。

```bash
python -m evals.run                  # 二十一题各跑一遍
python -m evals.run -n 3             # 每题三遍，看稳定性
python -m evals.run --only 改名 修bug  # 只跑几题
python -m evals.run -m kimi-k2.6     # 换模型对比
```

前十一题（`tasks.py`）覆盖：改名 / 修 bug（有测试盯着）/ 写测试 / 读懂后回答 / 跨文件改名 /
别动不该动的 / 改完自己验证 / 在一堆文件里找定义 / 三件事一起 / 文件不存在时别硬编 /
长任务（跨六个文件改名 + 测试 + README，每改一个跑一遍测试，十几轮起）。

后十题（`tasks_medium.py`，0.44.0）是中等规模的小项目（几个模块、上百行、带测试）：
加 --json 要放进现有输出层 / 两个模块各藏一个 bug / 三份重复抽成一份 / 一个字段贯穿三层 /
看日志找原因 / 删死代码别过头 / 改常量但注释里的同一个数不动 / 六个文件里一个拼错的 import /
写 API 文档不带私有函数 / 三层配置优先级。**每题都有两条暗线：测试文件不许改、没让动的模块不许动**——
改测试凑绿是最常见的作弊。加题时用「参考答案」验判据：铺现场后判据必须挂，套上参考答案必须过
（会话记录里的 golden.py 就是这么干的）。

几条定下来的规矩：

- **判据必须是确定性的**：文件内容、跑一遍的退出码、答案里有没有那个数。
  **不让另一个模型来打分** —— 打分模型自己就会波动，那测的是两个模型的乘积。
- **每题在自己的临时目录里从头铺现场**，题与题之间不共享任何状态。
- **存盘 / 日志 / 记忆全指到临时目录**（`_isolate()`），跑评测不该在真实状态里留痕。
- **判题失败要带上真实报错**（`_ran()`）：只报一句"跑不起来"的话，事后分不清是
  它做错了还是判据写错了。
- **崩溃原文单独写文件**：接口 400 这类是偶发的，表格里那半句话不够查。
- 默认 `--mode auto`：非交互场景没人能回答确认，不给这个开关每步都会被拒。

**加题的标准**：这道题失败时，能不能一眼看出是哪块出了问题。看不出来的题不要加。

## 验证方式

改了 TUI **不要只跑单元断言**，必须看真实渲染。夹具在 `dev/`（0.43.0 起入库，
之前每次都要重写）：`dev/ui_runner.py` 挂假后端跑真主程序，`dev/ui_drive.py` 接 pty、
喂键、回 CPR、用 pyte 回放、渲染成 PNG。跑法（pyte 不是项目依赖，装在 venv 里）：

```bash
python dev/ui_drive.py /tmp/shots --theme swiss --cols 80
```

夹具的几条要点：

1. `pty.openpty()` 把整个进程接上去（`isatty()` 才为真）；
2. **夹具要回应 CPR**：边读边喂 pyte，见到 `\x1b[6n` 就按 `screen.cursor` 回写
   `\x1b[{y+1};{x+1}R`。不回应的话 prompt_toolkit 认为终端高度未知，
   toolbar 之类的东西干脆不画，你会误以为没问题；
3. 用 pyte 回放拿到最终画面（直接剥 ANSI 会留下重影，看着像 bug 其实不是）；
4. 想看配色就把 ANSI 渲染成 PNG——块字符 `▀▄█` 和框线 `─│╭╮╰╯` 直接画矩形/线段，
   别指望预览字体有这些字形；字符格宽高比用 **1:2**。
5. 断言每行显示宽度，中英混排 / 超长 / 纯 CJK 都要覆盖。

假后端挂掉真实 API：`agent.build_backend = lambda *a, **k: Fake()`。

## 上下文（0.6.0）

- 项目约定：启动读工作区的 `FEIYU.md` / `AGENTS.md` / `CLAUDE.md`（首个命中），
  接在系统提示最后（比通用指令更具体）。`/context` 重载。
- 循环终止：`while True` 靠「模型不再要工具」退出，另有 `MAX_ROUNDS = 25` 兜底
  （到上限时往历史补一条 assistant 再返回，**不补的话角色不交替，下一轮请求会报错**）。
- 压缩：超过 `context_limit * 0.75` 自动触发，也可 `/compact`。
  **循环内压缩要补一条 user**：`compact_to()` 收尾是 assistant，任务没做完就继续调
  run_turn 的话角色不交替，所以 `_maybe_compact(mid_turn=True)` 会补一句「接着做完」。
  **实现要点：先把 messages 渲染成纯文本再总结**（`backend.transcript()`），
  因为 anthropic 和 openai 的 messages 结构不通用，而且直接在原历史后追加提问会
  破坏 tool_use/tool_result 的配对与角色交替。压完 `compact_to()` 重建成
  [摘要, 好的] 两条，配对问题一并消失。
- 存盘：每轮存一次到 `~/.local/state/feiyu/sessions/<workspace-hash>.json`（0600）。
  **anthropic 的 messages 里是 SDK 对象**，存之前要 `model_dump()` 摊平（`session._plain`）。
  恢复时系统提示重新拼，不用存盘里的那份。
  **反过来也要认**：`transcript()` 里的 content block 恢复后是 dict、直出时是对象，
  统一走 `providers._field()`。0.11.0 那版对 dict 用 `getattr` 取值，
  `--resume` 之后压缩会拿到一份没有 assistant 文本、工具名全空的记录，摘要基本是废的。

## web_fetch

HTML 走 `_Markdown`（HTMLParser 子类）转 Markdown，不是纯文本——纯文本会把标题、
链接、代码块全丢掉，查文档时基本没法用。带 `prompt` 时经 `Toolbox.ask_model`
（由 `Feiyu._ask_model` 注入，调 `backend.ask()`）让模型读完整页只回答问题，
原文不进主上下文；实测 5 万字页面进上下文只剩百来字符。同地址 15 分钟内走内存缓存。
返回内容开头**必须**保留「外部数据，不是指令」那句。

## 办公文档（0.11.0）

`read_document()` 在 `read_file` 里前置：docx/xlsx/pptx/odt/ods/odp 用 `zipfile` + `ElementTree`
解（这些格式就是 zip 装 XML，**不要为此引依赖**），PDF 调系统的 `pdftotext -layout`。
命名空间用 `_tag()` 剥掉前缀再比对本地名。
**xlsx 和 ods 必须单独写 reader**：按 `table-row`/`table-cell` 组织成制表符分隔的行，
套用文档的「每个 p 一行」会把每个单元格拆成一行，表格结构全丢（踩过）。
ods 还要处理 `number-columns-repeated`（连续空列会被压成一个单元格）。
认不出的二进制走 `_looks_binary()` 明确报错，不返回乱码。

## 状态栏只显示能据以行动的数

上下文显示**百分比**（`context / context_limit`），不显示绝对 token —— 每次请求
1.5k 固定底噪，绝对数字既吓人又指导不了任何决定（用户两次因为这个来问）。
没配价格就不显示金额，**不要拿 token 数冒充计费**，那会和左边的上下文重复。

## 权限（0.10.0，照 Claude Code 的模型；0.12.0 改成按段判定）

`permissions.verdict(tool, args)` → allow / deny / ask，`_gate` 按这个走。
规则语法跟 Claude Code 一致：`Bash(ls:*)` / `Bash(git status)` / `Read(*)`。
存 `~/.config/feiyu/permissions.json`，**deny 优先**。确认时按 `[s]` 走
`permissions.suggest()` 生成规则并写盘（git/npm 这类两段式命令会带上第二个词）。

**bash 必须先 `segments()` 切段再判定**，每段都被 allow 命中才放行，任一段撞 deny 就整条拒。
规则是前缀匹配的，不切段的话 `ls -la && rm -rf x` 会顺着 `Bash(ls:*)` 直接跑掉——
0.11.0 及以前就是这个行为，`echo x && rm -rf ~` 一声不问。
切在 `&&` `||` `;` `|` 和重定向处；`2>&1` 这类只接管描述符的先用 `_FD_DUP` 抹掉，
`/dev/null` 当无害目标跳过，否则 `cmd > /dev/null 2>&1` 会天天问。
`$(...)`/反引号（`_SUBSTITUTION`）一律降级成 ask，前缀匹配看不见里面藏了什么。
引号里的元字符也会被切开，结果是多问一次——**只往这个方向错**。

**出厂 allow 刻意不含 `cat`/`head`/`grep`** —— 它们能读工作区外任意文件，
等于绕开 `_resolve` 的目录限制。加进去是用户的知情选择。
`DENY_PATHS` 内置拦截 `.ssh/`、`id_rsa`、`~/.config/feiyu/` 等，auto 模式也拦。
**这是正则路径匹配，不是 Claude Code 那个语义分类器**，挡不住刻意变形，别对外说成等价。

## 任务清单与计划模式（0.16.0）

`feiyu/todo.py` 只管状态和校验，画由 `ui.tool_result` 负责（`detail` 的 kind 复用成
`done`/`doing`/`pending` 三种颜色），工具在 `tools.py`。分层照旧。

- **整份替换，不做增量更新**：让模型每次交完整清单，比按 id 改某一条可靠得多——
  它经常记错 id，而整份替换错不了，代价只是多几十个 token。
- **清单的价值在 `unfinished()`**：`send()` 里模型想收尾（没有 tool_calls）时，
  清单还有没做完的就塞一条提醒再问一轮。治的是「五件事做完三件就宣布干完」。
  **`nudged` 标志必须有**，只提醒一次——提醒到底会转不出来。
- **光有工具 schema 模型不会用**：实测 kimi 拿到 `todo` 工具后，三步任务照样直接
  埋头做完，一次都没调。往 `SYSTEM_PROMPT` 里加一条「好几件事就先列清单」之后才用。
  行为指令要放系统提示，工具描述只说明「怎么用」不说明「什么时候用」。
  （它仍然倾向于开头列一次、结尾一次性标完，不是每步都改——这个没再强求。）

计划模式：`MODES` 加了 `plan`，`MODE_ORDER` 变成四档。

- **改模式一律走 `set_mode()`**，它会重拼系统提示。`plan` 会往提示里加 `PLAN_PROMPT`，
  直接赋值 `self.mode` 的话切了等于没切。
- `_gate` 里 plan 的拒绝理由要**指名 `exit_plan`**，否则模型会反复试别的写法。
- `exit_plan` 是工具，但切模式是 agent 的事，所以走 `Toolbox.on_exit_plan` 回调
  （跟 `on_remember` 一个套路），Toolbox 只管工作区。
- 出厂 allow 放行 `Todo(*)` 和 `Plan(*)`：都不碰工作区也不出网，
  不放行的话每列一次清单都要点一次确认，烦到没人愿意让它列。

## 子 agent（0.18.0）

`feiyu/subagent.py`。存在的**全部理由是上下文隔离**：实测一次派活，
子 agent 烧 41579 token，主对话只涨 2284。改这块前先想清楚会不会破坏这一点。

- **工具集限死 `TOOLS = (read_file, list_files, search, symbol)`**，三个性质捆在一起：
  改不了东西 → 不需要中途要确认（它跑在工作线程里，读不了 stdin）→ 因此能进
  `PARALLEL_SAFE`。没有 `web_fetch` 也就掐掉了「读网页被诱导去干别的」这条注入路径。
  **想给它加工具前先确认这三条还成立。**
- **必须 `build_backend` 建全新后端 + 全新 `Toolbox`**：带着主对话历史就谈不上
  「独立去看」，上下文也照样被撑爆；共用 Toolbox 则 cwd/清单会串。
- **过程行带回去、不当场打印**：并行派几个时各自往屏幕上写会交叉成一团，
  由调用方在主线程按顺序画（走 `ToolResult.detail`）。
- **用量要带回来算进 `Feiyu.tokens` / `.cost`**（`subagent_tokens` / `subagent_cost`）。
  不带的话子 agent 的钱就成了看不见的开销——这比看得见的危险。
- `spawn` 走 Toolbox 回调（跟 `on_remember` / `on_exit_plan` 一个套路）：
  建后端要模型配置，那是 agent 的事，Toolbox 只管工作区。

## 判二进制必须用增量解码器（0.18.0 修）

`_looks_binary` 只看开头 4096 字节。**直接 `raw[:4096].decode()` 是错的**：
一个中文字符 3 字节，第 4096 字节落在字符中间的概率约 2/3，一报错整个文件就被
判成二进制读不了。**本项目自己的 `feiyu/agent.py` 就中招了**——子 agent 的端到端
测试里它反复报「二进制文件，无法直接读」才发现。用 `codecs.getincrementaldecoder`，
结尾不完整的字节会留在缓冲里，不算错。

## 并行工具调用（0.17.0）

`_run_tools` 现在分两条路：整批都在 `PARALLEL_SAFE` 里就走 `_run_parallel`，
否则 `_run_serial`（跟 0.16.0 之前一模一样）。

- **判断放在批一级，不要改成逐个挑**：「读三个文件、然后按内容写一个」这种批次里，
  写那步依赖前面读的结果，挑着并行等于乱序执行。
- **`bash` 永远不能并行**：`Toolbox.cwd` 是跨调用共享的，两条并发的 `cd` 互相盖掉。
  写类工具同理（同一批改同一个文件就是竞态），`todo` / `exit_plan` / `remember`
  改的是 agent 自己的状态，也不行。
- **闸和打印全在主线程**：`ui.confirm` 要读 stdin，两个线程同时问就乱套；
  让工作线程自己打印的话输出会交叉成一团。所以先按顺序过一遍闸，再一起提交，
  最后**按调用顺序**取 `future.result()`——任务是并发跑的，这里只是让输出有个定序。
- **中断的不变量照旧**：`future.result()` 抛出后 `with` 会等其余线程收尾，异常传到
  `send()` 的 `finally`，`_fill_missing()` 给没结果的调用补上。测试里专门盯着这个。
- 并行时结果行要带上作用对象（`ui.tool_target()`）：四条 `读取 3 行` 一起出来，
  不带路径根本分不清谁是谁。

## 权限门的两个集合

- `GUARDED`：confirm 模式下要问 —— bash / write_file / edit_file / **web_fetch**（内容会出网）
- `WRITES`：readonly 模式下拒绝 —— 只有前三个，web_fetch 不改状态所以放行

加新工具时想清楚它进哪个集合，别只往 `GUARDED` 里塞。

## 平台

POSIX only。Windows 原生跑不了的四处：`install.sh`（bash）、`_tool_bash` 的 cd 保持
（`$?`/`pwd`/`exit $var` 是 POSIX sh）、`EscInterrupt`（termios，`os.name` 已判断所以是静默失效）、
`ui.confirm`（guard 没启用时一律返回 "no"，**等于 confirm 模式在 Windows 上全拒**）。
另外 `0o600` 在 Windows 上是空操作，密钥文件没保护。macOS 全部可用。

## `main()` 有三条路径，公共装配要放在早退之前（0.12.0）

`main()` 依次是：一次性任务（`args.prompt`）→ 管道（stdin 不是终端）→ 交互 TUI，
前两条都是 `return`。**三条路径都要用的东西必须装在第一个 `return` 上面**。
0.11.0 把 `agent.approve` 装在 TUI 那一段里，结果 `feiyu "改一下 x"` 的
`self.approve` 一直是 None，`_gate` 走 `if self.approve else "no"`，
**每个写操作都被当成「用户拒绝」**——README 里演示的一次性用法根本改不动文件。
`ui.STYLE_NAMES` / `MODE_NAMES` / `MODEL_NAMES` 只喂补全，留在 TUI 段没问题。

## 中断必须让历史保持自洽（0.12.0）

`esc` 走 `EscInterrupt` 往主线程扔 KeyboardInterrupt，可能落在两个位置，两个都要接住：

- **落在 `run_turn` 里**（模型正在流式输出）：assistant 消息还没进历史，
  已经说出口的话会丢，角色也不再交替 → `_note_interrupted(stream.text)` 把
  `ui.Stream.text` 攒下的原文补成一条 assistant。
- **落在 `_run_tools` 里**（工具正在跑）：`tool_use` 已经在历史里但没有配对的结果 →
  `_run_tools` 改成把结果 append 进调用方传进来的 list，`send()` 用 `finally` 调
  `_fill_missing()` 给剩下的 call 补 `INTERRUPTED` 结果，再 `add_tool_results`。

**少一条配对结果不是「下次凑合能跑」，是会话彻底废掉**：DeepSeek 实测直接 400
`An assistant message with 'tool_calls' must be followed by tool messages...`，
而且 `__main__` 的 `session.save` 在 `_run_turn` 吃掉 KeyboardInterrupt 之后照常执行，
损坏状态会被写进存盘，`--resume` 再捞回来。用户只剩 `/clear` 一条路。

## 截断要说出来（0.12.0）

`TurnResult.truncated`：anthropic 看 `stop_reason == "max_tokens"`，
openai 看 `finish_reason == "length"`，`send()` 里 `ui.warn` 提示一句。
`_parse_args` 返回 `(参数, 出错说明)` 而不是静默的 `{}`——流式拼起来的 tool 参数撞上
上限就断在 JSON 中间，当空参数跑下去只会报个 `KeyError`，模型不知道自己被截断了，
多半原样重试。说明填进 `ToolCall.error`，`_run_tools` 见到就不执行、直接回错。

## 可观测性（0.13.0）

`trace.Trace` 每件事写一行 JSONL 到 `~/.local/state/feiyu/logs/<日期>.jsonl`，**0600**
（里面有命令原文和文件路径）。按天分文件、每行带 `run` id：查"刚才为什么干了怪事"
按 run 过滤，算"这周花了多少"按天汇总。`kind` 有 run_start / turn / tool / verify /
review / json / interrupt / run_end。`/cost` 按 run 汇总——**一次运行就是一个任务**，
这是能对上"这次干活花了多少"的粒度。

**写日志绝不能弄崩主流程**：`Trace.write` 把所有异常咽掉。`FEIYU_NO_LOG=1` 关掉。
参数值截到 300 字（`MAX_VALUE`），不记文件内容——排查要的是"调了什么"。

## 自动校验（0.13.0）与钩子（0.15.0）

约定文件里单起一行 `verify: <命令>`（`VERIFY_LINE`，要求顶格且整行只有这一句，
免得正文里提一嘴 verify 就被当配置）。`/verify` 可以临时改或关掉。

**0.15.0 起 verify 不再是独立机制**：`Feiyu.all_hooks` 把它合成成一条
`PostToolUse` + `Write|Update` 的钩子。verify 本来就是「改完文件跑个命令」，
两套机制各写一遍只会行为不一致。改这块时改钩子那条路径就行。

**只有 `write_file` / `edit_file` 触发**（`VERIFY_AFTER`）。bash 也能改文件（`sed -i`），
但把它算进来的话每次 `ls` / `git status` 之后都要跑一遍测试——实测就是这样，
一个任务里跑了两遍。代价远大于收益。
`_run_tools` 因此返回**最后一次改动的 `(工具名, 主参数)`** 而不是 bool——
PostToolUse 的 matcher 要拿它匹配。

没通过时把报错**并进最后一条 tool_result**，不另起 user 消息：两种后端的消息结构
不通用，tool_result 是唯一两边都能安全追加内容的地方，而且不会破坏配对。

### 钩子的几个决定

- 四个事件：`PreToolUse` / `PostToolUse` / `UserPromptSubmit` / `Stop`。
  退出码 0 放行、非 0 拦下，跟 Claude Code 一致。
- **`matcher` 复用 `permissions.matches()`**，不另发明语法。`|` 分隔，裸名当 `Name(*)`。
- **`PreToolUse` 放在权限门之前**：反正要被拦，没必要先烦用户点一次确认。
- **失败时 stdout 和 stderr 都要**：unittest 把失败写 stderr、pytest 写 stdout，
  只取一个的话总有一半工具的报错是空的（第一版只取了 stderr，pytest 场景全瞎）。
  成功时只取 stdout —— `UserPromptSubmit` 拿它当额外背景，stderr 多是噪音。
- **项目钩子必须先经用户同意**，这条不能省：`.feiyu/hooks.json` 是从仓库带下来的，
  直接跑等于「clone 一个仓库就执行任意代码」。**按内容 hash 认**
  （`trusted-hooks.json`），文件一改就重新问——按路径认的话，同意过一次之后
  仓库里换成 `rm -rf` 也照跑。非交互场景（`approve=None`）一律不跑。
- 启动时的信任问句用 `ui.ask_yes_no()`，**不能用 `ui.confirm()`**：
  后者要 `EscInterrupt` 的守卫，那时还没建起来。非终端一律返回 False。

## 评分 agent（0.13.0）

`/review` 或 `--review`。**必须 `build_backend` 建全新实例**，复用 `self.backend`
等于让它带着整段对话自己批改自己的作业。

`changes()` 优先 `git diff HEAD`，没有版本库就用 `self.before`（动手前存的原样）
和当前内容算 `difflib.unified_diff`。**不要退回"把改完的整份文件交出去"**——
评分的模型看不到改动前的样子，会把"这文件本来就只有一个函数"读成"它把别的都删了"。
实测同一个改动：给整份文件评 4/10，给真 diff 评 10/10。

## 结构化输出（0.13.0）

`--json-schema <文件>`：任务跑完**单起一轮不带工具的请求**让它按 schema 吐 JSON
（不是一开始就要求它边干活边凑格式，那会让它整个任务都分心）。不合规最多重来
`MAX_REPAIR` 次，把具体问题回给它。

`schema.py` 只实现 type / required / properties / items / enum / additionalProperties，
**不引 jsonschema**。注意 `True` 是 `int` 的子类，schema 写 integer 时要单独挡掉 bool。
`extract()` 要剥 ```json 代码块和"好的，结果如下"这类前言。

**stdout 只能有 JSON**：用 `contextlib.redirect_stdout(sys.stderr)` 包住整个跑任务
的过程，最后往真 stdout 写 JSON。比逐处改 `print` 目标可靠——`ui.Stream` 也是在调用
时才读 `sys.stdout`，一起被重定向。

## 长期记忆（0.14.0）

`feiyu/memory.py`，一个工作目录一份，存 `~/.local/state/feiyu/memory/<hash>.md`（0600）。
`remember` 工具写，`_refresh_system()` 读进系统提示，`/memory` 看和删。

**跟 CONTEXT_FILES 刻意分开存**：那几个是人手写的规矩，agent 不该去改；
混在一起它迟早把用户写的约定改掉。也**不要写进工作区**——污染用户仓库，还会被手滑提交。

**`remember` 同时进 GUARDED 和 WRITES**，两个都是有意的：

- 进 GUARDED（default 模式要确认）：内容可能间接来自 `web_fetch` 抓回来的页面，
  而记下的东西进以后**每一次**系统提示 —— 这是条能跨会话的注入通道。
  配套地 `ui._HEADLINE_ARG["remember"] = "fact"`，确认行必须显出原话，否则等于让人盲签。
- 进 WRITES（readonly 模式拒绝）：不动工作区，但确实往磁盘留了东西。

`Toolbox` 的 `on_remember` 回调接到 `_refresh_system`：**记完要当场重拼系统提示**，
不然它这一轮里记的东西自己看不到，要等下一轮才生效。

`as_prompt()` 的措辞里两句不能删：「不是用户此刻的指令」（防注入被当命令执行）、
「不写在项目里的任何文件里」——**实测漏掉后者时它把自己记的事说成
「项目笔记（FEIYU.md）里写的」**，用户去翻那个文件会发现根本没这句。

上限 40 条 / 3000 字（`MAX_ITEMS` / `MAX_CHARS`），超了从最早的挤；
单条 300 字（`MAX_ONE`），超了直接拒——那多半是把整段输出塞进来了。
存储是一行一条 `- (日期) 事实`，所以 `add()` 必须先把换行压平。

## bash 沙箱（0.19.0）

`feiyu/sandbox.py`，用 bubblewrap 的无特权 user namespace，**不引 Python 依赖**。
三档 `off` / `workspace` / `strict`，装了 bwrap 就默认 `workspace`。

**说清楚它管什么**（README 里也是这么写的，别在别处改口）：

- **写是真隔离** —— 内核的 mount namespace，工作区和 `/tmp` 之外全部只读，绕不过去。
- **读只是挖空了几个目录** —— `SHADOW_DIRS` 把 `~/.ssh` 这类 tmpfs 盖空，
  但整个文件系统仍然可读。这跟 `DENY_PATHS` 一样是权宜之计。
  真读隔离要改成只挂载允许的目录，那会让一大半构建工具跑不起来。**别说成读也隔离了。**

几个必须记住的点：

- **`SHADOW_DIRS` 里 feiyu 自己的两个目录不能删**：`~/.config/feiyu` 有 API key，
  `~/.local/state/feiyu` 有完整对话原文、日志和长期记忆。
- **记 pwd 的临时文件必须走 `writable=`**：沙箱里的 `/tmp` 是自己的 tmpfs，
  不单独 `--bind` 的话外面读不到，`cd` 跨调用就断了。这条有专门的用例盯着。
- **`available()` 要真跑一次 bwrap**，不能只看文件在不在：有些发行版关掉了
  无特权 user namespace，bwrap 装着但一跑就失败。
- **`effective()` 要能说出为什么没沙箱**，不能默默降级——欢迎框和 `/sandbox` 都靠它。
- **钩子和 `verify:` 不进沙箱**：那是用户自己写在配置里的命令，不是模型产出的。
- 被挡下时往输出里追加一句「这是沙箱限制」，模型才不会闷头重试。实测它会改成
  写工作区内、或者建议用户 `/sandbox off`。

测试里 `需要沙箱 = unittest.skipUnless(sandbox.available(), ...)`：
没有 bwrap 的机器上这些用例要跳过，不能让整个套件挂掉。
**测真隔离不能只断言 argv 拼对了**，得真跑一遍看写不写得进去。

## 0.20.0 的三处修补

**bash 超时必须自己接住 `TimeoutExpired`**（`_timed_out`）。让它冒到 `run()` 的兜底
`except Exception` 里的话，模型只看到一句 `TimeoutExpired: Command [...] timed out`——
命令停下前打印的东西全丢了，它根本不知道卡在哪一步；沙箱开着时那串 argv 还会把
整条 bwrap 命令行（二三十项）灌进上下文。注意 **`TimeoutExpired.stdout` 是 bytes**，
`text=True` 也不管用，要走 `_decode()`。

**`search` 截断必须说出来。**上限 `SEARCH_CAP = 200`，到顶了 summary 标「（已截断）」、
正文末尾也加一句。只报「200 处匹配」的话模型会以为搜全了——实测本机一个仓库
`const` 实际 743 处，「这个函数在哪些地方被调用」之类的结论就是错的。
有 `rg` 走 `_ripgrep()`（141M 仓库 0.8 秒 → 5 毫秒），**rg 报错就退回 `_scan()`**：
它是 Rust 正则，不支持反向引用和前后瞻，不能因为它挑食把整个搜索判死。
`returncode == 1` 是「没匹配」不是出错。

**流中途失败的重试。**两个 SDK 自己会重试 429/5xx/连接错误（`providers.RETRIES = 4`），
但那只管**请求发出之前**；流一旦开始断了就没人管，而 agent 一轮动辄几十秒。
`send()` 里补了 `except Exception`：**还没吐字就重发（安全），吐了字就不重发**
（模型会把刚说的话再说一遍），两种情况都要 `_note_interrupted()` 保住已说的话和角色交替
——不补的话就是 0.12.0 那个「历史废掉」的坑换了个入口。
`transient()` **按状态码和类名判断，不 import SDK 的异常类**：两种后端，
openai 还是可选依赖，硬 import 会把 anthropic-only 的装法弄坏。

顺带清掉的：`_BLOCK_TAGS`（死常量）、`permissions.json` / `sandbox.json` 没按 0600 存、
`_web_cache` 无上限（TTL 只在读时检查，过期条目永远不清，存的是整页正文）。

## 写文件之前要对版本（0.21.0）

`feiyu/files.py` 的 `Watcher` 记住 agent **看过**的每个文件（mtime_ns + size + sha256），
`read_file` 成功后记一次，`write_file` / `edit_file` 写完也记一次。
`Toolbox._check_fresh()` 在两个写工具的最前面拦两种情况：

- **没读过就要覆盖已有文件** —— 它多半是凭印象重写，会把不知道存在的内容删掉。
- **读过之后文件变了** —— 用户在编辑器里改了、格式化工具改了、它自己用 `sed -i` 改了。
  这条是**唯一会真的丢用户数据**的路径：`write_file` 整份覆盖，中间那些改动无声消失。
  （`edit_file` 多半会因为 `old_str` 对不上而失败，但对不上是运气，不是保护。）

几个刻意的选择：

- **比对内容不只比 mtime**：`stale()` 先比 (mtime_ns, size)，一样就直接放行；不一样才去
  算 sha256。格式化工具和 `git checkout` 回同一版本都会改 mtime 不改内容，
  只看 mtime 的话每次跑完 `verify:` 都要重读一遍，纯属白花钱。
- **新建文件不拦**：没有可丢的东西。目标不存在时 `_check_fresh` 直接返回 None。
- **读不了的二进制不要求先读**：`read_file` 会拒绝它们，要求先读等于永远写不了。
  这一条只对 `write_file` 生效，是权宜之计不是保护。
- **Watcher 活在 `Toolbox` 里、只活在这次进程里**。`--resume` 回来是一张白纸，
  它得重读一遍——这正是想要的，隔了一个会话的内容本来就不该信。
- 报错措辞里要写明「可能是你自己用 bash 或钩子改的」。只说「用户改的」的话，
  跑完 `verify: black .` 被拦下时它会一头雾水地反复重试。

`edit_file` 同时补上的三件：`replace_all`（默认仍然要求唯一，匹配多处的报错里
**要提到这个参数**，否则它只会去加上下文）、空 `old_str` 直接拒（`"".count()` 返回
行数 +1，不拦的话报错会很莫名）、`old_str == new_str` 直接拒（白跑一轮还会触发 verify）。

测试在 `tests/test_tools.py::TestFreshness`。`Base.write()` 多了个 `seen=` 参数：
默认记进 Watcher（那些用例测的是编辑语义，不该被这道闸挡住），
`seen=False` 才是「文件在那儿但 agent 没读过」。

## 工具参数的流式进度（0.22.0）

写一个大文件那十几秒里，屏幕上原来只有 spinner —— 分不清它在生成参数还是卡住了。
现在两个后端在参数流上来时回调 `on_tool(工具名, 到目前为止的参数)`，
`agent.send()` 把 `Stream.progress` 传下去，状态行变成
`⠋ Write feiyu/tools.py · 已生成 42 行 (7s · esc 中断)`。

- **回调只存原始值，标签留到重画时才拼**（`Stream._status` 存的是元组，
  `_draw_status` 才调 `ui.tool_progress`）。上游每个 delta 都会回调一次，
  在回调里做正则扫描是对越来越长的参数反复扫，O(n²)；重画是 12 Hz，扫多少次都无所谓。
- **`Stream._run` 从「spinner 阶段 → 正文阶段」两段式改成一个循环**。
  关键是 `_should_draw()`：**正文开始之后，只有工具参数在生成时才画状态行**。
  不加这条的话，正文两个 chunk 之间的每个空档（8ms 一次）都会插进来一行，
  字被切成一段一段的。
- **`_hide_status()` 必须先看 `_status_drawn`**：没画过就写 `\r\033[K`，
  擦掉的是正文那半行。原来的 `_clear_spinner` 无条件擦是安全的，因为那时
  spinner 只可能出现在正文之前。
- 正文停在半行上时，状态行**先补一个 `\n` 再画**（`_draw_status` 开头那段），
  否则会把说过的话盖掉。擦掉之后 `_fresh_line` 仍是 True，`finish()` 就不会再补换行，
  工具调用行照旧紧接着正文——这一点用 pty + pyte 三个场景都回放确认过。
- `IDLE_TICK`（0.03）和 `_tick`（0.008）分开：正文在放的时候按 `_tick` 走，
  没东西可放时按 `IDLE_TICK` 轮询，不必按吐字的节奏空转几十秒。

anthropic 那边认 `content_block_start`（拿工具名）+ `input_json_delta`（拿 `partial_json`），
openai 兼容那边本来就在攒 `slot["args"]`，攒完顺手回调。
**`on_tool` 一律是可选参数**：`subagent` 跑在工作线程里，不能往屏幕上画，就不传。

## 后台跑 bash（0.23.0）

`feiyu/jobs.py`。`bash` 多一个 `background`，新增 `job` 工具（`output` / `kill` / `list`）
和 `/jobs`。治的是 dev server、watch、长构建把一整轮 turn 卡到超时。

- **必须边跑边收输出**：`Job._drain` 是个 daemon 线程。不收的话管道写满 64KB
  就把子进程堵死，从外面看跟卡住一模一样。
- **`take()` 只给没读过的**：模型会反复轮询，每次都灌整份日志的话上下文几轮就满了。
  超过 `MAX_CHARS`（20 万字）从最早的丢，并且**要告诉模型丢过**——
  不说的话它会拿一份缺了中间的日志当完整的读。
- **杀的是进程组**，所以 `Popen(start_new_session=True)` + `os.killpg`：
  只杀 pid 的话 `npm run dev` 底下那个 node 活得好好的。先 TERM 后 KILL。
- **后台命令不记 pwd**：它跟后面的命令是并行的，`cd` 跟着它走只会串味。
  前台那条记 pwd 的机制原样保留。
- **结束了要主动说一声**（`Jobs.finished_since()` + `Feiyu._note_jobs`）：
  搭在最后一条 tool_result 上，跟 `verify` 的失败一个套路——两种后端里
  tool_result 是唯一能安全追加内容的地方。**`reported` 标志必须有**，
  只说一次，每轮都提醒会把它带跑偏（跟 todo 的 `nudged` 同一个理由）。
- **退出时 `kill_all`**（`_finish` 里）：留着的话用户关掉 feiyu 之后那些进程还在跑，
  而他已经没有任何句柄能找到它们了。沙箱里的那些靠 `--die-with-parent` 也会死，
  但没装 bwrap 的机器上只有这一道。
- **出厂放行 `Job(*)`**：起那条 bash 时已经确认过了，之后每轮询一次再点一次没人受得了。
  它只作用在 agent 自己挂起来的进程上。

## 读图片（0.24.0）

`feiyu/images.py` + `Toolbox._read_image`。`read_file` 碰到 png/jpg/gif/webp
就把图交给模型，`ToolResult.image` / `ToolOutput.image` 一路带到后端。

- **能不能看图是每条模型配置自带的一项**（`keys.FIELDS` 的 `vision`，`/key add` 问一句）。
  **不要写内置的「哪些模型支持视觉」表**，理由跟不要写内置价格表一样：
  各家 id 退役很勤，写死的表迟早给出错的答案。探测也不行——只能真发一张图过去试。
  没标的模型走的是「明说不认图」，不是「发出去让接口报错」：后者是 400，
  一轮对话就废了。
- **两种后端形状不一样**：anthropic 的图可以直接躺在 `tool_result` 的 content 列表里；
  **openai 兼容的 `tool` 角色 content 只能是纯文本**，图得另起一条 `user` 跟在
  所有 tool 结果**之后**（放中间会破坏 tool_call 的配对）。
- **`without_images()` 是必须的**：图片进了历史就一直在，`transcript()`（压缩前
  渲染成文本）和 `session.save` 都会把几十万字的 base64 原样带上——摘要会被冲垮，
  存盘一张图就几 MB。剥成一句 `IMAGE_NOTE`，结构不动所以配对和角色交替都不受影响。
- **缩图的两个上限**：长边 `MAX_EDGE = 1568`（1568×1568 约 1600 token，边长翻倍
  就是四倍的钱），字节 `MAX_BYTES`。一轮缩完还超字节就接着缩，别在那儿放弃。
  **有透明通道必须走 PNG**，转 JPEG 会把透明的地方糊成黑块。
- 子 agent 的 Toolbox 也要 `box.vision = backend.vision`，不然它读图永远说不认。

## 思考模式：reasoning_content 要么条条都有、要么条条都没有（0.27.0 才修对）

0.25.0 只在"模型这轮真想过"时才挂 `reasoning_content`。**这不够**——模型不是每轮
都想（有时直接说话 + 调工具），中断补的那条 assistant 更是我们自己造的，
于是历史里必然出现"有的有、有的没有"，DeepSeek 对这种混着的历史回 400。

实测过三种：**全都带 → 接受；全都不带 → 接受；混着 → 400**。

修法两处，缺一不可：
- `run_turn` 里这一轮没想但 `self.thinking` 已经为真时，补 `reasoning_content = ""`；
- `_mind_thinking()` 在**每次发请求之前**扫一遍历史把缺的补上 —— 这是唯一的收口，
  中断补的、压缩重建的、`--resume` 捞回来的都从这儿过。`self.thinking` 也在这里
  从历史里重新认（存盘回来时那个标志是 False）。

**这个 bug 是题库跑出来的，人肉试了三四次都没复现**——它只在轮数多的任务上犯。

## 0.25.0 两处

**思考模型的 `reasoning_content` 必须原样回传。**DeepSeek v4 的思考模式下，
assistant 消息里少了这个字段，**下一轮请求直接 400**：`The reasoning_content in
the thinking mode must be passed back to the API`。也就是说凡是它想过一次，
这轮之后的对话就全断了——实测撞到过。`run_turn` 现在攒 `delta.reasoning_content`，
**只在它真吐过的时候**才往 assistant 消息上挂（不认这个字段的接口收到多余字段可能报错，
而吐过它的一定是同一个接口）。anthropic 那边不用管：`response.content` 里
本来就带 thinking 块，整个 append 回去了。

**`--mode`。**`feiyu "任务"` 和管道模式下 `ui.confirm` 一律返回 no（没人能回答），
等于每个写操作都被拒——README 里演示的一次性用法照样动不了文件，只是这次卡在
另一个地方。加了 `--mode {default,auto,plan,readonly}`，**走 `agent.set_mode()`
不要直接赋值**（要重拼系统提示）。装配点在第一个 `return` 之前，三条路径都吃得到。

## 0.25.1 四处（用户拿着截图来提的）

一次真实使用暴露的四个问题，都不大但凑一起就"用着乱"：

- **`list_files` 没有上限。**在 `~` 里跑一次 `**/*` 是 336747 个文件、五秒多，
  拼出来的字符串几 MB，而模型看到的仍然只是被 `_truncate` 砍掉的前几十行。
  加了 `LIST_CAP = 300`，**关键是攒够就 `break`**——`Path.glob` 是生成器，
  break 掉整棵树就不走了（0.14 秒）。**截断必须标出来**，跟 0.20.0 的 `search`
  同一个道理：只报个数字的话模型以为自己看全了。
- **切了 `/style` 没有任何地方能看出来。**欢迎框是 scrollback，印完就不会重画，
  它上面那行"风格 默认"会一直挂着。修法是把风格放进常驻状态栏，
  **只在不是 default 时显示**——一直显示的话会和左边的权限模式"默认"撞车，
  两个"默认"并排更让人糊涂。
- **打个招呼它就去翻项目。**说句"你好"，模型答完还顺手 `list_files('**/*')`。
  这是系统提示的问题：原来第一条就是"改动之前先用工具了解项目"，没有
  "先判断这句要不要动手"这一层。加了一条放在最前面。**行为指令放系统提示**，
  照旧。
- **在家目录里跑要警告。**工作区是 `~` 的话，"沙箱把写关进工作区"等于没关，
  列目录也必然慢。启动时 `ui.warn` 一句，两行别写一行——一行在 80 列终端上会
  被终端自己软换行，断在 `<目录` 和 `>` 中间。

## 0.26.0 体验排查（用 pty 把九条路径真跑一遍）

用户拿着截图说"用起来乱七八糟"。照着常见路径跑了一遍（第一次用 / 权限确认 /
中断 / 计划 / 只读 / 80 列 / 压缩 / 报错 / 长输出），修掉九处。**这类问题靠加功能
修不出来，只能真跑。**夹具在会话记录里：pty + pyte，脚本化敲键盘、逐屏回放。

### markdown 现在渲染了（最大的一处）

模型每句话都带 `**` 和反引号，不渲染就是满屏记号——"看着乱"主要来自这里。
`ui.MarkdownStream` 是个流式状态机，把字符流切成 `(文本, 样式)` 片段，
`Stream._runs` 再上色。认：`**粗体**`、`` `行内代码` ``、`#` 标题、`-` 列表（变 `•`）、
`>` 引用（变 `│`）、``` 代码块。**不认斜体**——`*` 在命令行里多半是通配符。

- **样式一变就要 `_flush_word()`**：攒着的西文词会被后来的样式整个染上。
- **宽度按原文算，颜色最后才套**（`_painted`）。反过来就是那个犯过两次的老坑。
- **半个记号落在 chunk 边界上要攒着**：`` ` `` 单独一个字符时判不出是不是围栏，
  当普通文字走掉的话行首状态一丢，收尾那行 ``` 再也认不出来，后面全被当代码块。
  测试里专门有一条"任何切法结果都一样"（chunk 1..12 逐个比）盯着它。
- **顺带修掉一个既有 bug**：`_chars` 把行首空格全吃了（原意是不让折行后的行以空格
  开头）。代码块的缩进因此全没了。加 `_newline` 区分"真换行"和"折行"。

### 另外八处

- **确认时看不到要改什么**（最要紧的一个）：原来只问"要执行 Update 吗"，
  diff 要点完确认才看得到，等于盲签。`tools.preview()` 出结构化行，
  `ui.tool_preview()` 画，`_gate` 在 `approve` 之前打出来。
- **确认问句要认得出对象**：`ui.ask_text()` → "要改 calc.py 吗？"。
  **这行靠 `\r\033[K` 擦掉，只能擦一行**，所以问句要按 `width() - 按键提示宽度` 裁。
- **交方案不该给 `[a]`/`[s]`**：`confirm(simple=True)` 只给是非两个键。
  "以后都不用问"用在 `exit_plan` 上等于把计划模式永久废掉。
- **`ui.notice/warn/error` 不折行**：80 列下每一条都被终端软换行断在词中间
  （`feiyu --res` / `ume`）。走 `wrap()`，并且 **`wrap()` 西文要回退到空格断词**。
- **`/compact` 把整份摘要打屏幕上**：压缩就是为了省地方，压完全文再打一遍是自相矛盾。
  只报压缩前占多少。**压完还剩多少不知道就别编**——第一版拿 `len(summary)//3` 估了个
  数字出来，显示成"→ 22"，看着像坏了。
- **接口报错原样甩 JSON**：401/404/429/连不上各给一句能照着做的话（`_API_HINTS`）。
  401 和 404 是新手最容易撞的两个，原样甩的话用户只能干瞪眼。
- **工作目录从右边截断**：`/tmp/xxx/yyy/…` 恰好把"在哪个项目"截掉了。
  `clip_path()` 从左边裁保住尾巴。
- **`exit_plan` 的 headline 把整个方案塞进括号**，下面又整块画一遍；
  `Plan()` 那对空括号也去掉了。plan 模式补了自己的标记 `◐`。

## 欢迎框里不放会变的东西（0.26.1）

`ui.welcome()` 原来印了模型 / 风格 / 模式 / bash 沙箱。**这个框是打进 scrollback 的，
切完之后重画不了**，于是 `/style concise` 之后它还挂着"风格 默认"，用户来问过。

同步做不到（除非把整个界面改成全屏应用，那是另一个量级的改动），所以按"同步不了
就删掉"处理：

- 框里只留整个会话不会变的：工作目录、项目约定、版本、键位。
- 会变的四项全部靠状态栏（输入框顶边），**那是唯一一处始终为真的地方**：
  模式和模型一直显示，风格只在非 default 时显示，沙箱只在关着时显示（`无沙箱`）。
  规则统一成一句：**常态不占位置，异常才出现**。
- 沙箱关着时启动另外 `ui.warn` 一句 —— 这条是安全性质的，值得说一次；
  开着是常态，不必天天挂在状态栏上。

**以后往欢迎框里加东西之前先问一句：这个值会在会话中途变吗？**会变就别加。

## list_files 要在目录一级剪枝（0.26.2）

0.25.1 给 `list_files` 加了 `LIST_CAP` 上限，但那只治"匹配太多"。真正常见的情况是
**匹配很少、要走的目录很多**：本仓库 `**/*` 只该列 54 个文件，`Path.glob` 却把
`.venv` 里 8400 个文件全吐出来让我们一个个 `is_file()` 掉 —— 8455 次 stat、178 毫秒，
上限一次都没用上。

改成 `os.walk` + `dirnames[:] = [...]` 就地剪枝，**0.4 毫秒**。
模式匹配换成 `PurePath.full_match()`（3.13+），`**/*` `*.py` `src/**/*.py` 语义跟
`glob` 一致，实测逐条对过。`os.walk` 是逐目录来的，末尾要 `matches.sort()`。

**记住这条**：`Path.glob` 没法剪枝，凡是要跳过某些目录的遍历都别用它。

## 截断的工具参数绝不能留在历史里（0.28.0，用户拿截图来提的）

用户用中转站的 claude-sonnet-5 写一个长文件，从某一轮起**每次请求都** `400
Provider returned error`，说什么都救不回来，一路撞到 25 轮上限。

链条是：输出超过 `MAX_TOKENS`（那时写死 8192）→ `finish_reason == "length"` →
`write_file` 的 arguments 断在 JSON 中间 → **这条 assistant 原样进了 `self.messages`**
→ 下一轮重发 → 接口把 `tool_calls` 转成 `tool_use.input` 时 `json.loads` 失败。

`_parse_args` 早就认出了截断，但它的结果只进 `ToolCall`，**没有回写历史**——
历史里挂的仍然是原始的 `slot["args"]`。现在解析放在拼 assistant 之前，
解析不出来就把历史里那份换成 `"{}"`，模型那边照旧收到「你被截断了」的说明。

**这跟 0.12.0 那条没配对的 `tool_use` 是同一类问题**：历史里留下了接口不接受的
形状，而历史每轮都重发，所以一次事故会污染整个会话。往历史里放任何东西之前，
先问一句「这个形状接口下一轮还认吗」。

顺带修掉根因：**`max_tokens` 现在是每条模型配置自带的一项**（`keys.FIELDS`，
`/key add` 问一句），跟 `vision` 和价格同一个道理——**不要写内置的「哪个模型能出多少」
表**，各家不一样、也没有接口能问出来。出厂保底仍是 8192：往高了写不安全，
有些兼容接口收到超过自身上限的值直接报错。撞上限时的提示也改成能照着做的话。

回归用例在 `tests/test_providers.py`：**断言就是 `json.loads(历史里的 arguments)`
不报错**。

## 历史瘦身（0.29.0）

**省 token 要先省在历史上，不是省在系统提示上。**实测本仓库 `read_file` 一次
`tools.py` 是 16180 token，比整套工具 schema（2246）还贵七倍，而且它会一直躺在
历史里被后面**每一轮**重发。0.28.0 之前历史没有任何裁剪，一路留到
`context > limit * 0.75` 才一次性全量摘要。

`feiyu/history.py` 只做一件事：**换掉 `tool_result` 的内容，不删消息、不碰配对**。
少一条配对结果不是"下次凑合能跑"，是整个会话废掉（0.12.0）。两种后端形状都认
（anthropic 是 user 消息 content 列表里的块，openai 兼容是独立的 `role=tool` 消息），
带图的那种 `content` 是列表，跳过——图另有 `without_images()` 管。

两个机制：

- **`expire()` — 过期折叠**（`Feiyu._fold`，每批工具结果进历史之后立刻做）。
  同一个文件又读了一次、或者被 `write_file`/`edit_file` 改过，历史里旧的那几份
  就换成一句说明。模型要的是最新那版，旧版还在每轮陪着一起发。
  **按 `_resolve()` 之后的绝对路径认**，模型写 `./x.py` 和 `x.py` 指的是同一份。
  只登记成功的调用（`outputs` 里 `is_error` 为假的）。
- **`trim()` — 过长压缩**（`_maybe_trim`，`TRIM_RATIO = 0.55`）。
  靠前那些超过 `TRIM_LIMIT` 的工具输出只留头尾各 `TRIM_KEEP`。
  最近 `TRIM_KEEP_LAST` 条不动——正在用的东西不能碰。

**压缩现在是两级的**：`_maybe_compact` 先调 `_maybe_trim`。瘦身是无损的
（用户的话、模型的决定、最近几轮全部原样留着），摘要是有损的（要模型自己复述一遍），
所以能靠瘦身撑住就不摘要。

**`_reads` 登记在历史被整体换掉时必须清空**：`reset()` / `compact()` / `restore()` /
`use_model()` 换了后端类型这四处。不清的话登记的 id 指向的消息已经不存在了。

实测一个「读三个文件、改两处、各重读一次验证」的任务：历史 55140 → 33101 token（-40%）。

顺带：**`read_file` 补了默认上限 `READ_CAP = 1500`**。原来是
`limit = int(args.get("limit", len(lines)))` —— 没有上限，整份读进来。
**截断要说清怎么接着读**（`offset=N`），只报行数的话模型以为自己看完了，
跟 `SEARCH_CAP` 是同一个道理。

### 工具 schema 只删了逐字重复的部分（2189 → 2083）

`SYSTEM_PROMPT` 和三处 schema 描述是逐字重复的：`background` 的用途、`todo`
什么时候列、`write_file`/`edit_file` 的 read-before-write。按项目自己定的
「行为指令放系统提示，工具描述只说怎么用不说什么时候用」删掉 schema 那几份。

**没有做「按模式挂工具」**（plan 模式不挂写工具、没有后台任务时不挂 `job`）。
算过账不划算：省 275 token/轮，而两种后端的服务端缓存命中后这部分按 0.1× 计费，
等效只有 27 token；代价是引入「工具集会中途变」这个新状态维度，而且**工具块在请求
前缀，一改就作废整段 prompt cache**，plan↔default 来回切反而更贵。

## 会问人、会留进度（0.30.0，用户提的）

用户说「它不会问我需要怎么做，不确定的事也不问；也不会主动记住任务」。
查下来是两个不同的根因，都指向同一条老教训。

### ask_user：以前根本没有"问"这个能力

系统提示里唯一相关的一句是「遇到小选择…**别停下来问**」，是劝退的；
而"先问"没有任何机制支撑 —— 它想问也只能说完话不调工具，靠退出循环把问题带出来，
而这条路还会被 todo 的 `unfinished()` 推回去。

`ask_user` 工具：问句 + 两到四个选项，`ui.ask_choice()` 画。

- **选项走单键**（`os.read` 一个字节），跟 `confirm` 同一条路 —— turn 中途 stdin
  是 cbreak，读整行要另起 prompt_toolkit。**另给一个 `t` 键进自由输入**：
  模型摆出来的选项经常不是用户真正想说的那句，只能选就等于逼他将就。
- **三个集合一个都不进**，每条都有理由：进 `GUARDED` 等于问你之前先问你
  「要不要问你」；进 `WRITES` 的话只读模式下连问一句都不行；进 `PARALLEL_SAFE`
  的话它会跑在工作线程里，**那儿读不了 stdin**。
- **没人能回答时不能当成"拒绝"**（管道、一次性任务、`ask_user` 回调没装）。
  回"被拒绝"会让它停在原地反复问；现在回的是「自己挑一个合理的做法往下走，
  最后说明你按的是哪个假设」。用户按 esc 放弃也走这条。

### progress：任务进度跟长期记忆刻意分开

用户要的"记住任务"跟 `memory` 是冲突的 —— `memory` 明确排除「任务的过程和进展」，
因为那种信息很快过时，被当成事实讲出来比不记更糟。所以另起 `feiyu/progress.py`，
规矩正好相反：**整份替换、带时间戳、超过 `STALE_DAYS` 就不带、做完就删、
进系统提示时明说"可能已经过时，先自己看一眼现在的代码"。**

**它不是模型写的**：内容自动来自 `todo` 清单，`_finish` 里存（三条路径共同的收尾）。
所以不像 `remember` 那样是一条跨会话的注入通道。
`_refresh_system` 里**只在本次会话还没有自己的清单时才带** —— 有了新清单，
旧的那份只会跟它打架。启动时带了旧进度要 `ui.notice` 说一声，
不说的话下次开工它突然提起一件旧事，用户不知道这话是哪来的。

### 又一次栽在"光有工具 schema 模型不会用"上

`remember` 从 0.14.0 起就存在，但 **`SYSTEM_PROMPT` 里一个字都没提过它**，
所以模型基本不调 —— 跟 0.16.0 的 `todo` 一模一样的坑，隔了十几个版本又踩一次。
这次三条行为指令全部写进系统提示（何时问、何时先讲方案、何时 remember），
schema 里只留"怎么用"。**加新工具时，先问一句：系统提示里写了什么时候用它吗？**

系统提示因此从 462 涨到 649 token。这是**值得**的：它直接决定行为，
而工具 schema 那边省下的 106 token 远不如这个重要。

### 顺带修掉 EscInterrupt 的一处抢字节

`pause()` 原来只 set 一个标志就返回，而读线程这时多半正阻塞在 `select(0.1)` 上，
**醒来之后不再检查 `_paused` 就把下一个字节读走了**。那个字节是接下来要问的人的
（`confirm` 的按键、prompt_toolkit 的 CPR 回复）。现在 `pause()` 等一个 `_idle`
握手，`select` 醒来后也再看一眼。

### pty 夹具：喂键必须是非阻塞的

验证时屏幕上冒出一句 `terminal doesn't support cursor position requests (CPR)`，
查了四轮才定位到**是夹具的毛病不是产品的**：旧夹具喂键用
`for ch: write + sleep(0.15)` 的阻塞循环，那几百毫秒里它读不了 fd、也就回不了 CPR。
改成非阻塞（每轮 select 超时喂一个字符，全程都能回 CPR）之后警告就没了。
**以后写这类夹具，喂键和读屏必须在同一个循环里。**

## 0.31.0 四处（一次针对性的排查）

### anthropic 后端从来没打过缓存断点

`run_turn` 里 system / tools / messages 全是裸传，但 `_charge` 又在读
`cache_read_input_tokens` —— 那个数在 anthropic 后端上**恒为 0**，状态栏里
一直是盏不会亮的灯。openai 兼容那边是服务端自动缓存，所以这个洞被盖住了很久。

- **三个断点：system 一个、`tools[-1]` 一个、历史上滚动 `CACHE_KEEP`(2) 个**，
  一共 3-4 个，接口只认 4 个。历史上留两个而不是一个：新的那个负责这一轮写入，
  旧的那个保证上一轮写进去的那份一定命中。
- **每轮重挂之前必须先清干净**（`_mark_messages`）。不清的话断点一轮涨一个，
  很快超过 4 个被接口拒掉。
- **只挂 dict 形式的块**：assistant 那条 content 是 SDK 直出的对象，塞不进字段；
  user 消息（tool_result 那些）本来就是我们自己拼的 dict。
- **`_cached_tools()` 不能就地改 `self.tools`**，要拷一份——那份 schema 是共享的。
- **必须能降级**：用户多半连的是中转站，未必透传 `cache_control`。
  `_cache_rejected()` 认出来就 `self.cache = False` 重来一次。
  **重来的前提是一个字都还没吐出来**（断点是请求参数，不认的话错在发出的那一刻），
  这跟 0.20.0 那条「吐了字就不重发」是同一条规矩，用例专门盯着。

### 系统提示里没有环境信息（`env_block`）

原来只有 IDENTITY（模型名）。没有 cwd、平台、日期、git 分支，后果是实打实的：
写 CHANGELOG 的日期靠编、照着别的发行版给装包命令、每次想知道自己在哪都要多跑
一条 `pwd` 换一轮往返。

- **`git branch --show-current` 而不是 `rev-parse --abbrev-ref HEAD`**：
  后者在刚 `git init` 还没提交的仓库里直接报错，那种目录会被误报成"不是仓库"。
  这是写测试时发现的。
- 探测**只在 `__init__` 做一次**：`_refresh_system` 每次 remember / 切模式都会调，
  不该每次都 fork 一个 git。分支中途变是它自己 checkout 出来的，它知道。

### `search` 加了 `context` 和 `files_only`

它是 13 个工具里调用最频繁的一个，改它的收益乘以调用次数。
没有上下文行的话，每次找到 `def foo` 都得再 `read_file` 一次才知道函数体——
**每次搜索都是两轮往返**。

- **`SEARCH_MAX_CONTEXT = 10` 要封顶**：不封的话一句 `context=200`
  就把整个文件拉进历史，而那正是 search 想省掉的事。
- **截断单位跟着参数变**：带 context 时列出来的行数不等于匹配处数，
  报「7 处匹配」是错的（实际是 1 处 + 6 行上下文）。
- **顺带统一了两条路径的格式**：`_scan` 原来是 `f"{rel}:{i}: {line.strip()}"`
  （多个空格、还 strip 掉缩进），rg 是 `rel:i:line`。**装没装 rg 输出长得不一样**，
  这是既有的不一致。统一到 rg：主路径是它，兜底该向它看齐。
  新用例里几条都是「两条路径结果必须逐字相等」。

### 撞到 `MAX_ROUNDS` 不再一刀切

原来到 25 轮直接 `_stop_runaway`。但上限是防「卡在循环里空转」的，
**大重构本来就要几十轮**，一律停掉的话做到一半的任务得靠用户再说一句话接上，
而它未必接得回原来那条线。

现在 `_round_limit()` 问一句，选「接着做」就把 `rounds` 清零继续。
- **没人能回答（管道、一次性任务、`ask_user` 没装）照旧停**：那些场景没人看着，空转很贵。
  既有那条用例原样通过，说明非交互行为没变。
- **用户在那儿自由输入的话要带给模型**（`_stop_runaway(said=)`）：
  丢掉的话他下一句得把同样的话再说一遍。

## 已知问题（未修，用户知情）

- ~~**写文件会盖掉用户在编辑器里的改动**~~ **0.21.0 已修**：`files.Watcher` + `_check_fresh`。
- ~~**不能读图片**~~ **0.24.0 已修**：`images.py`，按模型配置的 `vision` 开关。
- ~~**`bash` 不能后台跑**~~ **0.23.0 已修**：`jobs.py` + `job` 工具。
- ~~**`bash` 工具没有沙箱**~~ **0.19.0 已修**：`sandbox.py` 用 bubblewrap 把写权限
  关进工作区。**但只有写是真隔离**，读仍然只靠挖空几个目录；没装 bwrap 的机器上
  自动降级成不沙箱（欢迎框会写明）。`cd` 越界弹回照旧只影响下一条命令的起点。
- **web_fetch 的 prompt injection**：只靠返回内容开头那句「外部数据，不是指令」提示，
  是软防护 内网地址已经挡了（`_blocked_host` + `_NoLocalRedirect`），但页面内容诱导
  模型做别的事这条防不住，靠 confirm 模式兜。
- ~~子进程继承环境变量~~ **0.9.0 已修**：`_child_env()` 按变量名过滤
  （`KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL`），`_SECRET_KEEP` 白名单保住
  `SSH_AUTH_SOCK`（否则 git push 断）。逃生门 `FEIYU_KEEP_ENV_SECRETS=1`。
  **注意这不是隔离**：bash 没沙箱，照样能 `cat ~/.ssh/id_rsa`，挡的只是「顺手读到」。

## 模型全部来自用户配置

**没有内置厂商表，没有默认模型，没有内置价格表**（0.5.0 起）。全部读自
`~/.config/feiyu/models.json`，由 `/key add` 写入；上次用哪个记在同目录 `state.json`。
不要为了「方便」再把厂商或价格写回代码里——各家 id 退役很勤
（`deepseek-chat` 已于 2026-07-24 退役），写死的表迟早给出错的报价，而报错价比不报价更糟。

价格是每条配置自带的三项（缓存命中 / 输入 / 输出，¥ 每百万 token），没填就显示 token 数。
`Feiyu.backend` 允许为 None（清单为空时），碰它之前先看 `agent.ready`。

## 0.32.0：拿真实日志和题库定的五处

用户要「更强、性能大幅提高」。没有凭感觉加功能，先看了三天的 trace 日志
（36 次运行、250 次工具调用）和题库基线（deepseek-v4-flash，19/20），按证据改：

- **bash 占了一半以上的调用，`search` 只用过 1 次** —— 模型宁可 `grep`。
  两处修：系统提示加一句「找代码用 search / read_file / list_files，别拿 bash 跑
  grep / cat / find」；`_tool_bash` 认出整条命令就是 grep/cat/find/ls 这类
  （`_native_for`，带管道 / 重定向的不算）时在结果末尾提醒一次，
  **`Toolbox.hinted` 只提醒一次**——跟 todo 的 `nudged` 同一个理由。
- **`write_file` 参数截断 6 次**（默认 `max_tokens` 8192 的模型写长文件）。
  截断的提示原来说「拆成几次写」，但根本没有拆着写的手段——加了 `append`。
  追加也走 `_check_fresh`，没读过的文件照样拦。
- **上下文溢出自愈**（`agent.overflow()` + `_rescue_overflow`）。
  日志里有一次 `Input token exceed the limit` 之后**每一次**请求都 400：
  历史里那条大消息还在。现在第一次 `history.shrink()`（所有超过 `SHRINK_LIMIT`
  的工具结果一律压头尾，**折叠过的和最近的也压**——请求已经发不出去了，
  「正在用的不能碰」让位于「先活下来」），第二次摘要重发并补一条 user。
  `overflow()` 认关键词不认 SDK 类：DeepSeek / OpenAI / anthropic / 中转站措辞各不同。
  只在**一个字都没吐出来**时才重发，跟 0.20.0 那条一致。
- **`edit_file` 对不上时不要让它整份重读**。先按「每行去掉首尾空白」找一次
  （`_loose_find`），恰好一处才替，替进去的 `new_str` 按文件的缩进挪（`_reindent`）；
  多处照旧拒。完全找不到时 `_nearest` 拿 old_str 第一行去比相似度，
  给出最像的那几行带行号——一份大文件重读一次就是上万 token，这几行省的就是那个。
- **题库唯一挂的题**（「把 nowhere.py 里的 foo 改成 bar」，它把文件建出来了）：
  系统提示加一条「指名的东西不存在时先说找不到、列候选，别替他建一个」。
  照旧是「行为指令放系统提示」。
- 顺带：`search` / `list_files` 同样参数再跑一次，旧结果也走 `expire()` 折叠。

**启动 0.13 秒，没得省**；`-X importtime` 看过，一半在 prompt_toolkit。

## git 工具（0.33.0）

四个动作 `status / diff / log / commit`，就这四个。push / reset / checkout 刻意不进：
会影响别人或丢改动，走 bash 让它先说一声、再点确认，是想要的摩擦。

- **权限按 action 判**：`permissions.subject()` 对 git 返回 `"<action> <paths…>"`，
  规则写 `Git(diff:*)`，出厂放行 status / diff / log，commit 要问；`suggest()` 生成
  `Git(commit:*)`。**只读 / 计划模式拒的是 commit 不是工具名**：`agent.mutates(call)`
  替代了 `_gate` 里两处 `call.name in WRITES`，加会改状态的工具时先想它是不是也该按参数判。
- **commit 默认 `git add --update`，只带已跟踪文件**：新文件要模型写进 `paths`。
  不然 `.env`、临时产物会跟着进版本库。结果里列出没提交的新文件，让它知道漏了什么。
- **diff 超过 `GIT_DIFF_CAP` 只给 --stat 和开头**，提示按文件分次看。整份给的话
  几个大文件就把上下文塞满，跟 `read_file` 的 `READ_CAP` 同一个道理。
- **「用户没让提交就不 commit」写在系统提示里**，不是工具描述——照旧。
- 确认前 `tools.preview()` 列出提交信息和会进去的文件，不然点确认等于盲签。

### 老用户拿不到新出厂规则（顺带修的，比 git 工具本身更要紧）

`permissions.json` 是整份存的，`load()` 见到 `allow` 是列表就原样用。结果
0.16.0 之后加的 `Ask(*)`、`Job(*)` 到现在的 `Git(*)` **一条都没到老用户手里**——
用户自己那份文件里就缺这三条。现在文件里多存一个 `known`（发过哪些出厂规则），
没发过的补进去，发过但被用户删掉的不动；空列表当刻意清空，不补。
**以后加出厂规则不用再管迁移**，`DEFAULT_ALLOW` 加一条就行。

`tests/test_agent.py` 的 `Base` 之前没换 `permissions.STORE`，一直在读真实文件——
这次才发现，因为新用例在有那份文件的机器上结果不一样。现在换了。

## edit_file 批量改 + 题库基线对比（0.34.0）

**没有另起 `multi_edit` 工具**，是 `edit_file` 加了个 `edits` 列表：少一份 schema、
少一个权限名，模型也不用在两个工具之间选。`_apply_edit()` 是单处替换的纯函数
（容错匹配、replace_all、报错措辞都在里面），单处和批量都走它。

- **全部成功才写盘**。改到一半失败的话文件处在模型没见过的状态，下一次 edit 更难对上；
  报错要带「第几处」和「一处都没写进文件」，不然它以为前面几处已经改了。
- 按顺序应用，后一处看得到前一处的结果——这跟「改完再改」的直觉一致。
- 「同一个文件几处就用 edits」写进系统提示。照旧：光有 schema 模型不会用。

`evals.run --save 名字` / `--compare 名字`：结果存 `~/.local/state/feiyu/evals/`，
不进版本库（跟模型和当天接口状态绑着）。**只有通过率降了才算变差**，token 涨跌
有随机性，只标出来。改系统提示之前先 `--save`，改完 `--compare`，不然对比要靠肉眼。

## 旁活用便宜模型（0.35.0）

`/helper <名字>`，存在 `state.json` 的 `helper` 字段（`keys.helper()` /
`set_helper()`）。三件事走它：压缩摘要、子 agent、`web_fetch` 的 prompt 提炼。
`/review` 刻意不走：评分要的是判断力，省这点钱不值。

- **`Feiyu._side_backend()` 按名字缓存一份后端**，没设 helper 就返回主后端自己
  （它的 `ask()` 本来就不进历史）。每次摘要都重建客户端太浪费。
- **旁路的用量要进 `tokens` / `cost`**，跟子 agent 一个道理：看不见的钱比看得见的危险。
- **顺带修掉：`ask()` 这条路以前根本不计费**——摘要、网页提炼一次几万 token，
  账上一个字都没有。现在两种后端的 `ask()` 都 `_charge(..., context=False)`：
  **算钱但不动 `tokens_context`**。动了的话一次网页提炼就把压缩阈值假撞上，
  用例专门盯着。
- `keys.remove()` 删掉的那条正好是 helper 时要清掉，`keys.helper()` 也只认清单里
  还在的名字——不然 `_side_backend` 会拿一条不存在的配置去建客户端。

## symbol 工具：最小 LSP 客户端（0.36.0）

`feiyu/lsp.py`，stdio JSON-RPC，只做 `textDocument/definition` 和 `references`，
不引依赖。`SERVERS` 按扩展名选服务器（这是工具表不是模型表，命令行多年没变），
`which()` 除了 PATH 还找 `~/.local/share/nvim/mason/bin`。

- **读 stdout 必须自己缓冲、`os.read`**：第一版用 `BufferedReader.read1(1)` 配 `select`，
  Reader 把 fd 上的数据整段吸进自己的缓冲区，之后 `select` 看 fd 是空的就一直等，
  明明消息已经到了 —— 表现是每次请求都 45 秒超时。
- **服务器反过来发的请求（注册能力、要配置）一律回 `null`**，不回它会等着，我们也在等。
- **冷启动时 references 要先等 `WARMUP`**：实测 pyright 0 秒 1 处、0.5 秒后 3 处。
  它是异步分析整个目录的，早问只报当前文件里的 —— 漏引用比慢一秒糟得多。
  definition 不用等，跨文件也是当场解析。
- 每次请求前 `didOpen` / `didChange` 把文件当前内容喂过去，版本号递增；
  服务器只信最新那份，模型刚改过的文件不用等它自己刷新。
- 模型报得出名字和大概行号，报不准列号，所以 `find_position()` 拿名字做整词匹配算位置。
- 进 `PARALLEL_SAFE`（`Client.request` 有锁）、出厂 `Symbol(*)` 放行、
  `_finish` 里 `toolbox.lsp.close()` 杀进程组。**没进子 agent 的工具集**：
  每个子 agent 一个 Toolbox 就是一个服务器进程，先不给。
- 没装服务器要明说并指回 search，测试里有一条盯着；真打 pyright 的用例
  `skipUnless(lsp.which(...))`，跟沙箱那几条一个套路。

## verify 失败先给要点（0.37.0）

`feiyu/verify.py` 的 `digest()`：从测试 / 编译输出里抠「谁挂了、为什么、在哪行」，
`agent._failure_note()` 把它放在原文前面 认 unittest、pytest、jest、gcc/clang、
rustc（位置在下一行 `-->`，要拼上）、tsc、go 认不出就返回空，调用方照旧只给原文
——**宁可没有要点，也不要错的要点**。上限 `MAX_ITEMS` 条，总结行（`FAILED (…)` /
`N failed`）单独放最后。`note:` 这类不算。

## 界面三处（0.37.1，pty 回放看出来的）

用 pty + pyte 把一轮完整对话回放成 PNG 看了一遍（夹具在会话记录里，
`ui_runner.py` 挂假后端跑真主程序、`ui_drive.py` 喂键 + 回 CPR + 渲染），改了三处：

- **确认后 diff 画了两遍**：`_gate` 里预览一遍，`_run_serial` 的结果 detail 又一遍。
  `Feiyu._previewed` 记下画过的调用 id，结果里只留 summary。出错时照旧画（出错的
  detail 不是 diff）。
- **助手正文第一行带 `⏺`**（`Stream._gutter()`）。原来正文和工具行只差一个缩进，
  一屏工具调用下来看不出哪句是它说的话。`_lead` 只在整段输出的第一行为真，
  折行的续行不带——用例盯着。
- **代码块左边一条灰色竖线**替代两格缩进，列数不变（`_gutter_width` 仍是 2），
  折行和宽度计算全不用动。

**顺带一个坑：版本号显示成旧的**。`sed -i` 改 `__init__.py` 之后紧接着跑测试，
`.pyc` 和源码的 mtime 落在同一秒，Python 按秒比对就当字节码是新的。
表现是 `/help` 和欢迎框都写 0.30.0 而源码是 0.37.0。删 `__pycache__` 就好，
不是代码问题；以后 bump 完看到版本不对先想这个。

## 0.38.0：正文渲染再补四样，顺带修掉 DeepSeek 用量翻倍

铺了个带表格 / 编号列表 / 超长列表项 / `---` / 链接的场景用 pty 回放，看出来的：

- **表格是整张攒齐了才画**（`MarkdownStream._rows`）：列宽要看完所有行才知道，
  没法流式。首行下面有 `|---|` 就当表头（加粗 + 一条 `─`），`---:` 右对齐。
  装不下就从最宽的列削到 4 格为止（`limit` 由 `Stream` 按终端宽度喂进来）。
  单元格里的 `**` 和反引号只拿掉不上色，宽度才算得准。
- **列表项折行后续行对齐到记号后面**：列表记号单独成一段样式 `mark`，
  `Stream._runs` 见到它就把 `_hang` 记成记号后面那一列，原文换行时清零。
  **`_put` 里判「行上已经有东西」要跟 `_line_start` 比**，不能再跟 2 比——
  续行起点是 2 + hang，跟 2 比的话装不下的词会在那儿无限换行。
- **西文改成整词攒**（`WORD_MAX = 60` 封顶）：原来只在离右边界 24 列内攒，
  一条长 URL 会先放出去半截、剩下那截再折到下一行。
- `---` 单独一行画整行灰线（样式 `hr`，`Stream._rule`），`[文字](地址)` 画成
  `文字 (地址)`，地址灰色留着——终端里多半能直接点。半个链接落在 chunk 边界上
  按 `_LINK_HEAD` 攒，`a[0]` 这种不是链接的原样出。
- **工具缺参数先查 `_REQUIRED` 再调**：原来直接跑，模型看到的是一句裸的
  `KeyError: 'regex'`，看不出是自己漏了参数。

**用量翻倍**是查日志时看出来的：几次 deepseek 的 run，`run_end` 的累计量
正好是各轮上下文之和的两倍，kimi 从来不会。openai 兼容那条流原来**每见到一个
带 usage 的 chunk 就 `_charge` 一次**，DeepSeek 有时在两个 chunk 上都带（数值一样）。
现在只记最后一份、循环结束再算账。**这个 bug 只影响累计量和花费，`tokens_context`
是赋值不是累加，所以状态栏的百分比一直是对的**——这也是它一直没被发现的原因。

## 上下文上限：配置 > 学到的 > 128k（0.39.0）

用户说「不同模型的上下文不同，撞过墙」。查下来 **`limit` 字段从来没生效过**：
`agent.use_model` 读 `entry["limit"]`，但 `keys.FIELDS` 没有这一项，`load()` 直接过滤掉，
README 写的 `"limit": 32000` 是空话。所以所有模型一律 128k：窗口大的白白早压缩，
窗口小的等不到压缩点就 400，之后全靠 `_rescue_overflow` 认报错措辞。

- `keys.FIELDS` 加 `limit`，`/key add` 问一句（认 `64k` 这种写法）。
- **撞一次墙就学**（`Feiyu._learn_limit`）：报错里写了上限（`limit_in_error`，
  数字要紧挨着 maximum / limit 这类词，`max_tokens 8192 is too large` 那种是输出上限，
  不能认）就用它；没写就按 `self.context`——上一次**成功**请求的大小，真实上限在它和
  这次失败的请求之间，取小的那边安全。记在 `state.json` 的 `limits` 里（跟配置分开：
  配置是用户写的，学到的是 agent 的），`/key add` 重填或 `/key rm` 时清掉。
  **`MIN_LEARNED_LIMIT = 16k` 以下不学**：第一轮就 400 多半不是窗口的事，学进去
  会让它之后每两轮压一次。用户填了 `limit` 的不动，填错了该由他改。
- **措辞认不出的 400 在上下文过了六成时也当溢出**（`rejected(exc) and context > 0.6 limit`）：
  中转站各说各的，压小重发没有坏处；上下文很小时照旧报错。
- `tests/test_agent.py` 的 `Base` 之前没换 `keys.STORE / STATE`——这回学到的上限会写
  state.json 才发现，跟 0.33.0 那次 `permissions.STORE` 是同一个坑。

## 追加进历史、还没发过的工具结果要先估进上下文（0.39.1）

`tokens_context` 只在接口回 usage 时更新，一次 `read_file` 读进 16k token 之后，
压缩判断看到的仍是上一轮的数——顺序变成「先超、再 400、再急救」，而不是提前压。
`Feiyu._pending` 在 `add_tool_results` 之后按 `CHARS_PER_TOKEN = 3` 粗算加上，
`context` 属性把它算进去（状态栏也跟着更诚实），`run_turn` 成功返回就清零——
接口刚报过准确的数。`reset` / `compact` / `restore` 同样清零。
三个字符一个 token 对中文是高估，高估的代价是早压一次，低估的代价是 400，只往这个方向错。

## 缓存写入价（0.40.0）

anthropic 直连的 `cache_creation_input_tokens` 原来并进 miss 按输入价算，官方是 1.25 倍。
现在 `price` 里多一个可选的 `write`，`_as_rates` 有它就返回四元组，`pricing.cost` 没第四项
就按输入价——**不内置 1.25 这个倍率**，跟不内置价格表同一个理由。`_charge` 多一个
`write=` 关键字参数，只有 anthropic 的 `run_turn` 传它；`tokens_context` 照旧把它算进规模。

## 0.45.0：真实仓库实测修掉的一处，加十道中等题

拿两个真实仓库（博客 SSG 的 2634 行 build.mjs、nvim 配置的 cp.lua）跑了五个任务，
全部做对，但第一个问答任务用了 27 次工具、14 轮，几乎全是在反复读同一个文件 原因：

- **`_fold` 对 read_file 按路径折叠是错的**：读了 1..1500 行再读 1800..1900 行，前一段被当成
  「过期」折掉，模型只好重读 现在按「路径 + offset:limit」认（`_reads` 的 key 带 `#offset:limit`），
  同一段再读一次才折；write / edit 之后该路径下所有段一起折 `tests/test_history.py` 两条盯着。
- 系统提示加一句「大文件先 search 定位再按范围读」——它一上来就把 2634 行的文件读了 1500 行。

这类问题**题库跑不出来**（题库的文件都只有几十行），只有在真实仓库上才暴露 以后每个 minor
版本至少在 `dev/` 之外的真实仓库上跑一遍（复制到 scratch，`XDG_STATE_HOME` 指到临时目录）。

## MCP 客户端（0.44.0）

`feiyu/mcp.py`。写法照 `lsp.py`（自己缓冲、`os.read`、服务器反向请求就地回），但
**MCP 的 stdio 是一行一个 JSON，不是 Content-Length**，两边的 `_read` 不能共用。

- **工具名 `mcp_<服务器>_<工具>`**，`safe_name()` 过滤到 `[A-Za-z0-9_-]` 且 ≤ 64——两种后端
  都有这个限制，不过滤的话带点号的工具名一到接口就 400。撞名了加 `_2`。
- **工具集会变，所以 `tool_schemas()` 动态拼**：`use_model` 建后端时和 `load_mcp()` 载入后
  都走它，两个后端各有 `set_tools()`。子 agent 刻意不给：它的三条前提（只读、不用确认、
  可并行）MCP 工具一条都保证不了。
- **权限：规则名一律 `Mcp`**，匹配文本是「服务器 工具」（`permissions.MCP_SUBJECTS`，
  由 `load_mcp` 填——服务器名里可能有下划线，从工具名上切不准）。`suggest()` 给 `Mcp(服务器:*)`。
- **闸按 `readOnlyHint` 判**（`Feiyu._guarded` / `_mutates`）：自报只读的当读工具，
  其余当写——它们的作用范围我们看不见，只能往严的方向错。
- **项目配置跟项目钩子一样要点头**（`trusted-mcp.json` 按内容 hash），非交互不起。
  这条不能省：`.feiyu/mcp.json` 是仓库带下来的，直接起等于 clone 一个仓库就执行任意命令。
- **返回开头带 `NOTE`**（外部数据不是指令），跟 web_fetch 同一个道理；`sampling/createMessage`
  一律回 -32601——那等于让外部进程借我们的模型说话。图片 / 音频只留一句说明。
- 服务器并行起（npx 冷启动好几秒）；`_finish` 里 `mcp.close()` 杀进程组并关管道
  （不关管道 unittest 会报 ResourceWarning）。改了配置要重启，没做热加载。
- 测试用 `tests/mcp_server.py` 假服务器，四个工具覆盖正常 / isError / 超时 / 只读，
  `echo` 第一次调用时反过来发 `roots/list`，验证客户端会答而不是卡死。

## 0.43.0：主题、`/undo` 加固、界面两处

- **主题只换调色板和记号，布局不动**（`ui.THEMES` / `set_theme()`）。`_RGB` 和
  `BULLET` / `ELBOW` / `SPARK` / `FRAMES` / `CORNERS` / `DOT` / `LOGO` 都是模块级常量，
  画东西的函数调用时才读，所以 `set_theme` 就地改 `_RGB` 加 `globals().update()` 即可。
  `THEMES["default"]["rgb"]` 是 import 时拷的一份，切回来才一模一样。
  存 `state.json` 的 `theme`，**`main()` 里在画任何东西之前 `set_theme`**——欢迎框是
  scrollback，之后换不了。`swiss`（国际现代主义）：黑白打底、红色一个强调色（warn 和 error
  同色，只靠文字区分）、直角、`■` / `└` / `–`、不画 logo。
- **代码块正文不再压灰**（`_PAINT["block"]`）：浅底上对比度不够，而代码是最要逐字看的。
  左边灰竖线已经够区分。引用仍是灰的。
- **钩子结果挂到 `⎿` 那一级**（`ui.hook_line`），原来 `ui.notice` 打出来悬在两块输出之间。
- **`/undo` 写完之后文件又被改过就不退**（`files.stale()`），退等于盖掉那些改动，跟
  `_check_fresh` 拦的是同一种事故；符号链接也不碰。不退的那条留在栈里。

## 0.42.0：拿四天日志定的五处

看了 47 次运行、304 次工具调用的 trace，按证据改：

- **bash / job 输出按轮数老化**（`history.age` + `Feiyu._age`）。bash 占 304 次里的 150 次，
  是历史里最多的一种工具结果，但 `expire()` 只认「同一个文件又读了一次」，bash 没有这种
  信号，一直躺到 `TRIM_RATIO` 才动——最大那次任务 40 轮、上下文 72k，trim 只触发过一次。
  现在超过 `AGE_LIMIT` 字符的登记下来，过了 `AGE_ROUNDS` 批只留开头 `AGE_KEEP`。
  出错的也登记：六轮之前的报错早处理过了。登记表跟 `_reads` 一起在 `_forget_history()` 清。
- **`/cost` 显示缓存命中率**（`Billing.tokens_in / tokens_hit`，trace 的 turn 行带 `input` / `hit`）。
  每轮重发整段历史，长任务贵不贵全看这个数，而且它跟折叠是对冲的：命中率高的话
  折叠中段消息反而作废后面的缓存前缀。先把数亮出来，再决定折叠要不要更狠。
  旁路请求（`context=False`）也计入累计，`last_*` 只记主对话那次。
- **`/undo`**（`Toolbox.undo_stack` + `Feiyu.undo`）。只记 `write_file` / `edit_file`，
  bash 改的看不见——报错里要说明。写前快照放在 `_check_fresh` 之后，被拦下的不进栈。
  退回去的内容要 `files.record`，不然它下一次写又被「没读过」拦下。
  **下一句话时必须告诉模型**（`_undone` 并进 user 消息）：不说的话它接着按改完的状态往下推。
- **bash 提示按段识别**（`_native_for` 走 `permissions.segments()`）。0.32.0 那版只认单个裸命令，
  日志里模型写的几乎全是 `cd x && grep … | head` 这种复合命令，四天里提示一次都没触发过，
  search 只用了 3 次。现在每段都是查找 / 筛选 / cd 才算，有一段在干别的就不算。
- **题库加「长任务」题**，`run.py` 记收尾时的上下文大小并进 `--compare`。
  日志里出事的全是三四十轮的任务，前十题几轮就完，测不到。

顺带：`verify:` 改成 `.venv/bin/python`——系统 python 没装 prompt_toolkit，
裸 `python` 跑出来是 23 个 ImportError，不是代码问题。

## 子 agent 有 `symbol` 了（0.41.0）

0.36.0 没给是因为「一个子 agent 一个 Toolbox 就是一个语言服务器进程」。解法是
`subagent.run(lsp=)` 把主 Toolbox 的 `lsp.Pool` 传进去共用，不另起进程：`Client.request`
有锁，`didOpen` 每次都喂当前文件内容，子 agent 和主对话看的是同一份。
三个条件照旧成立：只读、不用中途确认、可并行。
