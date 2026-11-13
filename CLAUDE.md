# Feiyu Agent — 给 Claude 的项目约定

跑在终端里的编程 agent。约 14900 行 Python，无框架。
另有一个桌面端（`desktop/`，0.80.3 起是 Electron + 原生前端，旧 Tauri 源码还留着），入口是 `feiyu-desktop`。

verify: .venv/bin/python -m unittest discover -s tests -t .

## 结构

| 文件 | 职责 |
|---|---|
| `feiyu/__main__.py` | CLI 参数、主循环、斜杠命令 |
| `feiyu/agent.py` | `SYSTEM_PROMPT`、`STYLES`、turn 循环、工具分发 |
| `feiyu/tools.py` | 十八个工具的 schema 与实现，含办公文档抽取 |
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
| `feiyu/skills.py` | 技能（模型按需读）与自定义斜杠命令（用户敲），含项目技能的信任机制 |
| `feiyu/enabled.py` | 哪些技能 / 工具 / MCP 服务器是开着的（只存关掉的那些） |
| `feiyu/profiles.py` | 配置档案（人格）：一份 .md 一套人格，拼进系统提示 |
| `feiyu/events.py` | 结构化事件的 sink：ui 除了画终端，还能把同一件事报给别的前端 |
| `feiyu/serve.py` | `--serve`：把 agent 挂在 stdio 上说行 JSON，给网页端用 |
| `desktop/electron/` | feiyu-desktop（0.80.3 起）：Electron 主进程监管 `feiyu --serve`，preload 只暴露白名单 invoke / listen；文件读取和 pty 也在这儿 |
| `desktop/renderer/` | React 文件树 / 预览面板（`Ctrl Shift E`）；聊天界面仍是 `desktop/ui/app.js`，`scripts/build.mjs` 拼进 `dist/` |
| `desktop/src/main.rs` | 旧 Tauri 窗口，留作兼容，安装和打包已不走它 |
| `desktop/src/pathguard.rs` | 外面给的路径必须落在工作区里，两个二进制共用一份 |
| `desktop/src/devserver.rs` | **只给测试用**的静态服务（Tauri 没 CDP，夹具接不上去），不是产品 |
| `desktop/ui/` | 前端（index.html / app.css / app.js / logo.svg），无框架无依赖 |
| `feiyu/todo.py` | 任务清单的状态与校验（渲染在 ui，工具在 tools） |
| `dev/fuzz_serve.py` | 往 `--serve` 灌畸形消息和垃圾参数，看它活不活得下来 |
| `desktop/dev/fuzz_ui.mjs` | 往前端灌畸形事件，看 `handle()` 抛不抛、那道 catch 兜不兜得住 |
| `desktop/dev/webkit.py` | 在 **WebKitGTK**（产品那个引擎）里离屏量滚动 / 布局 / 过渡 —— Chrome 那套夹具量不出引擎差异（0.80.0） |
| `feiyu/subagent.py` | 子 agent：独立后端 + 只读工具集，只带结论回来 |
| `feiyu/sandbox.py` | bash 沙箱（bubblewrap），三档 off / workspace / strict |
| `feiyu/files.py` | 记住看过的文件版本，写之前对一次，别盖掉没看见的改动；`IGNORE_DIRS` 也在这儿 |
| `feiyu/history.py` | 历史瘦身：把过期或过长的工具结果换成一行说明 |
| `feiyu/progress.py` | 上次在这个目录干到哪了（跟 memory 分开，会过期） |
| `feiyu/jobs.py` | 挂在后台跑的 bash 命令：收输出、报结束、杀进程组 |
| `feiyu/images.py` | 图片缩到上限之内再 base64，只有 read_file 用 |
| `feiyu/mentions.py` | `@路径`：认哪些、贴什么、补全给什么（终端和桌面端共用） |
| `feiyu/lessons.py` | 同一件事栽第二次时当场提醒换做法，缓过来之后引导 remember |
| `ui.MarkdownStream` | 流式 markdown → (文本, 样式) 片段，Stream 上色 |

**分层原则：`agent.py` 和 `tools.py` 只产出结构化事实**（`ToolResult.summary` / `.detail`），
怎么画一律由 `ui.py` 决定。加新工具时给它填好 `summary`，不要在业务代码里拼 ANSI。

## 硬性约定

- **版本号每次改完就 bump**。单一真源是 `feiyu/__init__.py` 的 `__version__`，
  `pyproject.toml` 用 `[tool.setuptools.dynamic]` 读过来。小改动进 patch，加功能进 minor。
- **桌面端那三处版本号手抄的，要跟着一起改**：`desktop/Cargo.toml`、
  `desktop/Cargo.lock`（`feiyu-desktop` 那条）、`desktop/tauri.conf.json`。
  漂了不报错，只是窗口状态栏显示错版本 —— 0.69.1 发现它们已经漂了两代，
  现在 `tests/test_cli.py::TestVersionSync` 盯着。
- **README 有中英两份**（英文 `README.md` 是默认展示的那份，中文 `README.zh.md`），互相链接，改一份必须改另一份。
- 不引新依赖来解决小问题。现有依赖只有 `anthropic`、`pillow`、`prompt_toolkit`，
  `openai` 是可选 extra。
- **最低 Python 是 3.10**（`pyproject` / `install.sh` / README 三处都这么写）。
  本机是 3.14，所以**用了新版本才有的 stdlib API，测试照样全绿** —— 0.26.2 的
  `PurePath.full_match`（3.13+）就这么把 `list_files` 在 3.12 上坏了一年多。
  拿不准就 `uv venv --python 3.11 /tmp/p311` 建一个跑一遍，二十秒的事。
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
python -m evals.run                  # 二十二题各跑一遍
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

`dev/demo_gif.py docs/demo.gif` 用同一套夹具录 README 顶部的演示 GIF（假后端，不花钱）：按屏幕变化抓帧、
每帧停留时间取到下一帧的间隔、magick 拼成 GIF。`FEIYU_UI_LANG=en` 让 ui_runner 走英文脚本。
⏺ ⎿ ⏎ 这几个记号 JetBrains Mono 没有，要用 Adwaita Mono 画，否则是豆腐块。

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

### Firecrawl 兜底（0.75.0）

`fetch.json` 里有 key 时，builtin 撞上 **401 / 403 / 429** 才回退到 Firecrawl 重抓一遍
（`_fetch_page` 里那段）。几个决定：

- **默认是「回退」不是「一律」**。日志里 web_fetch 54 次失败 16 次，一半是反爬——
  那一半才是它值钱的地方。`raw.githubusercontent` 和 JSON 接口 builtin 本来就抓得到，
  一律走 Firecrawl 只是每次慢几秒、还白烧额度。要一律走就写 `"provider": "firecrawl"`。
- **回退白名单只有那三个状态码**（`FIRECRAWL_RETRY`）。404 换谁抓都是 404，500 是对面的事，
  花额度也救不回来。这条有用例盯着（`test_404_不花额度重抓`）。
- **走 Firecrawl 也要过 `_blocked_fetch`**。内网拦截原来长在 `_fetch_raw` 里，
  只拦「我们自己去抓」；自托管实例就在本机，让**它**替我们抓 `169.254.169.254`
  跟自己抓没有区别。所以那段拦截从 `_fetch_raw` 里抽出来，两条路都过一遍。
- **它回的已经是 Markdown，`ctype` 给 `text/markdown` 就不会再进 `_Markdown`**。
  再转一遍的话代码块里的 `<div>` 会被当标签吃掉。
- **`_fetch_page` 返回值多了第四项 `via`**，`summary` 上标 `Firecrawl ·`。
  花没花钱必须看得见——这跟「状态栏只显示能据以行动的数」是同一条。
- 回退也失败时保留 builtin 的原始报错，末尾补一句 Firecrawl 为什么也没成。
  额度用完（402）和 key 不对（401）只看状态码分不清，所以 `_firecrawl_post`
  把响应正文一起带回来。

`web_search` 也多了 `firecrawl` 这个 provider（结果在 `data.web` 那组，还有 images / news
两组不要）。两处共用 `_firecrawl_post`。

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

POSIX only。Windows 原生跑不了的四处：`install.sh`（bash）、`_tool_bash` 的状态保持
（0.69.0 起要的是**真 bash**：`export -p` / `declare -f` / `unset -v`，不再只是 POSIX sh；
`sandbox.SHELL` 找不到 bash 会退回 `/bin/sh`，那时候 `source 相对路径` 和状态保持都打折）、
`EscInterrupt`（termios，`os.name` 已判断所以是静默失效）、
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

### 0.68.2：桌面端卡死的真凶是 `md()` 的死循环

0.68.1 修的重绘开销是真的（78.5% → 6.0%），**但用户装了修复之后还是卡死**。
这次是当场逮的：`WebKitWebProcess` 瞬时 CPU 100%+、RSS 从 250 MB 一路涨到
**24 GB**（机器一共 31 G），而 agent 那侧日志停在一条 `calls` 字段为空的
`turn`（纯文本收尾）之后一片安静。**这个形状是死循环，不是渲染慢**：
渲染慢不会漏内存，也不会在 agent 静止时继续烧 CPU。

`md()` 的外层 `while (i < lines.length)` 靠每个分支自己推进 `i`，而**段落兜底那个
分支在一次循环都跑不动时不推进**：

```js
const para = [];
while (… && !bullet.test(lines[i]) && !isRow(lines[i])) para.push(lines[i++]);
out.push(`<p>…</p>`);          // para 为空时 i 没动 → 原地转圈
```

进得到这里、又过不了 while 条件的输入有两类，**都是天天出现的**：

1. **没有分隔行的表格行**（`| a | b |`）。表格分支要求下一行是 `|---|`，
   而模型流式吐一张表时，表头落地、分隔行还没到的那一瞬间就是这个形状 ——
   也就是说**每画一张表都会撞一次**，只是有时下一个 delta 来得快、有时不。
   `> | a | b |` 同样中招（blockquote 递归调 `md()`）。
2. **不是整行的 `---`**（`--- 分隔`）。顺带把 hr 的正则从 `---` 放宽成 `-{3,}`，
   `----` 本来就该是分隔线。

修法是一行：`if (!para.length) para.push(lines[i++]);` —— 兜成一段普通正文，
跟 GitHub 对孤立表格行的处理一致。

**新夹具 `desktop/dev/md_check.mjs`**（12 条真实形状，`node dev/md_check.mjs <页面地址>`）：

- 为什么 `fuzz_ui.mjs` 抓不到：它灌的是畸形**事件**，而这里坏的是一条合法事件里的
  一段**合法 markdown**。两个夹具都要跑。
- 判据靠 `Runtime.evaluate` 的 `timeout` —— 它能打断同步死循环（V8 的
  TerminateExecution），所以死循环在夹具里是「超时」而不是「夹具自己也挂住」。
- **CDP 超时回的是 `{error}` 不是 `{result}`**。第一版的 `call()` 只取 `m.result`，
  于是真撞上死循环时夹具自己在 `out.exceptionDetails` 上 TypeError 崩掉 ——
  判据在最该报警的那一刻反而废了。收 `m` 整条，`m.error` 和
  `m.result.exceptionDetails` 都算「转不出来」。
- 报了失败之后要 `Page.navigate` 重载：被 terminate 过的 JS 上下文未必还能用，
  不重载的话后面每条都跟着假失败。
- 回退验证：撤掉那一行之后 4 条真的转不出来（其中「四个连字符」因为 hr 正则那处
  独立的修改仍然通过——两处修的是两个触发点）。

### 0.68.1：流式重绘吃掉 78.5% 的主线程（**但这不是「卡死」的原因，见 0.68.2**）

用户报窗口会卡死。**不是死锁，是前端把主线程占满了。** `case "text"` 每来一个 delta
就把整条消息重跑一遍 `md()` 再整块 `innerHTML` 重写，而这条链上从头到尾没有合并：
provider 一个 token 一个 chunk → `ui.py` 的 `events.emit("text", delta=…)` →
stdio 一行 JSON → 前端一次全量重渲染。

怎么量的（headless Chrome 接 devserver，灌真实 `handle()`，200 个工具块当背景、
1734 个 DOM 节点，6700 个 delta / 16400 字按三十秒的真实速度喂）：

| | 主线程忙 | 占比 |
|---|---|---|
| 改之前 | 23537 ms | **78.5%** |
| 现在 | 1799 ms | 6.0% |

6700 token 那一轮是日志里真实出现过的（09-12 04:16，`out=6696`）。
`md()` 本身不是瓶颈——同样的量累计只有 526 ms，开销全在 `innerHTML` 和重排上，
而重排代价还跟整个 transcript 的 DOM 体积成正比（transcript 里的块从来不删）。

- **按固定间隔节流，不是按帧。** 一次重绘的代价随消息变长线性上涨，`requestAnimationFrame`
  按帧画的话，一条流一分钟的长消息要画几千次，加起来照样十几秒。`SAYING_GAP = 80`
  把重绘钉死在 12.5 次/秒之后，总开销只跟「流了多久」有关，跟 delta 有多少无关。
- **`closeSaying` 必须先 `clearTimeout`**：不撤的话那次攒着的重绘会在 `saying` 被置空
  之后才跑，要么白跑，要么把光标又加回一块已经收尾的正文上。
- **滚动语义一个字没动**：`case "text"` 本来就不跟着滚（只有 `block()` 创建时跟），
  顺手加上等于改行为，不在这次范围里。
- 改完按规矩跑了 `dev/fuzz_ui.mjs`（102 条畸形事件全接住）。
  **`devserver` 用 `include_str!` 把 `ui/` 编进二进制**，改前端之后要
  `cargo build --release --bin feiyu-devserver` 才能用夹具看到新代码。

> **改完 `desktop/ui/` 要编两个二进制，不是一个。**
>
> ```bash
> cargo build --release --bin feiyu-devserver   # 夹具看的那个
> cargo build --release --bin feiyu-desktop     # 你自己在用的那个
> ```
>
> 产品那个也是把前端**编进二进制**的（`tauri.conf.json` 的 `frontendDist: "ui"`，
> Tauri 打包时嵌进去），`~/.local/bin/feiyu-desktop` 只是个 shim，指向
> `desktop/target/release/feiyu-desktop`。
>
> **这条上面那句只写了 devserver，于是 0.74.0 我自己照着漏了**：夹具里截图全是新的、
> 断言全绿、commit 也推了，而用户打开桌面端说「好像没变动？」—— 他跑的是五天前
> 编的那个二进制。**夹具绿不等于产品变了**，这两件事之间隔着一次编译。
> 编完还要**关掉正在跑的窗口再开**，热重载是没有的。

顺带记一条排查手法：`serve.py` 的 `REPLY_TIMEOUT = 600.0` 会造成**另一种**「看着像死了」——
agent 要问话时阻塞整整十分钟，超时后静默停掉。trace 日志里认它很容易：
空档正好是 600.0 秒，而那条 `round_limit` 没有 `answer` 字段。两周日志里只有 4 次。

### 0.68.0：两道闸都能写进约定文件，`max-rounds: 0` 是不限

`verify:` 已经在约定文件里了，闸却只能从命令行给——意味着「这个项目的活本来就要几百轮」
这件事没地方记，每次起会话都得重敲 `--max-rounds`。现在 `max-rounds:` / `budget:`
跟 `verify:` 同一个机制（`ROUNDS_LINE` / `BUDGET_LINE` + `find_limits`）。

- **`find_limits` 返回的 dict 里没写的键不出现，不要用 0 表示"没配"**：0 是"不限"这个
  明确的选择。混起来的话，约定文件里只写了 `max-rounds` 就会把命令行给的 `budget` 清成 0。
- **`__main__` 里判的是 `is not None` 不是真值**：默认值从 0 改成了 None，
  否则 `--max-rounds 0`（显式要求不限）会被当成没给，约定文件里那个数原样留着。
- **`limit_cli` 记住命令行显式给过哪几项**，`/context` 重载时跳过：
  用户敲了 `--max-rounds` 就是要这个数，不该被仓库里的一行改掉。
- **`max_rounds == 0` 时闸整个不判**（`if self.max_rounds and rounds >= ...`）。
  这一条要跟预算那道一起看：`_over_budget` 在**没填价格时返回空串**（`self.cost` 是 None），
  所以「`max-rounds: 0` + 没填价格的模型 + 非交互」是**一道闸都没有**的组合。
  README 两份都写明了这一点，代码里不拦——拦的话就等于替用户决定他不能跑无人值守的长任务。

判据这次是这么定的：新用例在把改动撤掉之后必须真的挂（撤掉后 8 条挂了），
而 `test_不设闸也还有预算那道` 要断言**预算那道**的措辞（"到了你设的上限"）——
轮数那道的话里也有"我先停下"，只断这三个字的话，闸没关它照样绿。

## 已知问题（未修，用户知情）

- ~~**写文件会盖掉用户在编辑器里的改动**~~ **0.21.0 已修**：`files.Watcher` + `_check_fresh`。
- ~~**不能读图片**~~ **0.24.0 已修**：`images.py`，按模型配置的 `vision` 开关。
- ~~**`bash` 不能后台跑**~~ **0.23.0 已修**：`jobs.py` + `job` 工具。
- ~~**`bash` 工具没有沙箱**~~ **0.19.0 已修**：`sandbox.py` 用 bubblewrap 把写权限
  关进工作区。**但只有写是真隔离**，读仍然只靠挖空几个目录；没装 bwrap 的机器上
  自动降级成不沙箱（欢迎框会写明）。`cd` 越界弹回照旧只影响下一条命令的起点。
- **web_fetch 的 prompt injection**：（0.79.0 起读过外部内容之后，能出网的 bash 要人点头，
  见那一节；web_fetch / MCP 本身这个出口仍没管）只靠返回内容开头那句「外部数据，不是指令」提示，
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

## web_search（0.46.0）

用户发现 web_fetch 抓搜索引擎结果页本来就能用，只是糙：一次 12k 字（开头一千多字是筛选菜单）、
链接是 DDG 的跳转地址、系统提示没提所以它不会主动搜 `web_search` 就是把这条路收拾干净。

- `_fetch_page` 拆出 `_fetch_raw`（不转 Markdown、不进缓存、可放行内网），搜索走它拿原始 HTML
  或 JSON；结果按 `search:<后端>:<词>:<条数>` 进同一个 `_web_cache`。
- 出厂 DuckDuckGo 的 HTML 端点，`_DuckParser` 只认 `result__a` / `result__snippet` 两种 a，
  `_unwrap_ddg` 从 `uddg=` 解出真地址；页面上有 anomaly / captcha 字样就明说要验证码，
  **别回空结果**——空结果模型会换个词一直试。
- 后端配置 `~/.config/feiyu/search.json`（`SEARCH_CONFIG`）：brave（JSON 接口、要 key）、
  searxng（本机自建，`allow_local=True` 绕过内网拦截——那是用户自己配的地址）。
  provider 认不出来直接报错，不静默退回 DDG。
- 进 `GUARDED` 和 `PARALLEL_SAFE`（跟 web_fetch 一样），规则名 `WebSearch`，`subject` 认 `query`；
  同一个词再搜一次旧结果折叠（跟 search / list_files 一路）。
- 系统提示加「不认识的库、看不懂的报错先 web_search」——照旧：光有 schema 模型不会用。

## 提 PR（0.47.0）

用户问「它能不能做好 PR、做开源贡献」。查下来 push 和 `gh pr create` 在 workspace 沙箱里本来就通
（`gh` 的凭据走 keyring，`GNOME_KEYRING_CONTROL` 在 `_SECRET_KEEP` 里；https 远端走 gh 的凭据助手），
但那是「碰巧能」：系统提示对 PR 一个字没提，它不会读 CONTRIBUTING、会直接在 main 上提交、fork 流程全靠摸。

- **没加 PR 工具**，加的是系统提示里一段流程（读模板 → 开分支 → 测试 → commit → push → `gh pr create --repo`
  指回上游 → 描述带 issue 号）。跟 todo / remember 一个教训：行为指令放系统提示。
  push 和开 PR 仍然走 bash、每次确认，那个摩擦是要的。
- **`_sandbox_note()`**：沙箱里挂掉的命令按报错原文认三种（只读 / ssh 远端 / strict 断网）追加说明。
  `~/.ssh` 被 tmpfs 盖空，known_hosts 和 ssh 配置都没了，ssh 远端在沙箱里必挂，原来只有只读那一种有说明，
  模型拿到 `Host key verification failed` 只会换着法重试。**认不出就一个字不加**，加错了比不加更误导；
  workspace 档的 DNS 失败不算沙箱的事。
- **题库「提 PR」题**：`upstream.git` 铺在工作区里当远端（沙箱外只读，放外面 push 不进去），
  `.git/info/exclude` 把它和 PR.md 排掉。判据全是 git 事实：main 本地和远端都没动、分支名 `fix/12*`、
  提交信息 `fix: … (#12)`、远端有那条分支、PR.md 三节齐全且 issue 号在「为什么」里。
  `gh pr create` 那一步本地没法判，不在范围内。查 `money/` 有没有没提交的改动要 `-uno`，
  不然跑测试留下的 `__pycache__` 会被当成脏改动（参考答案验的时候撞到的）。
  `--only` 匹配时题名里的空格会被去掉，所以 `--only PR` 而不是 `--only "提 PR"`。

### 真实 PR 跑出来的四处（0.47.1）

拿 elarmuzik1993/llm-parliament#19 真开了一条 PR（#28），feiyu 做对了，但日志里四个毛病：

- **`--resume` 回来忘了用 `.venv/bin/python`**，裸 python 报 ImportError 后翻了五轮环境。
  没压缩过（trace 里没有 compact），提示词就在历史里它也没看——所以不靠提示词，
  `tools._venv_hint()` 认出「裸 python / pytest 缺模块 + 工作区有 .venv」就指路，确定性的事不让它猜。
  顺带系统提示的 remember 那条改成「用户交代了解释器 / 测试命令 / 不能动的东西当场记」，
  `SUMMARY_PROMPT` 要求把测试命令连解释器路径原样抄进摘要。
- **`ruff --fix` 整文件跑**，把三个无关测试的 import 顺序也换了。系统提示加一条：自动修复只处理自己改的行。
- **每条 bash 都 `cd <绝对路径> &&`**，系统提示加一句「bash 本来就在工作区里、cd 跨调用保持」。
- **非交互撞 25 轮上限**只说「再说一句话」，可一次性任务没人能说——`_stop_runaway` 按 `said` 是否为空
  区分，空的就提示 `feiyu --resume "接着做"`。

## 改了配置，活着的那个实例得跟上（0.47.5）

用户拿 OpenRouter 的 key 配模型，base_url 写成了 `https://openrouter.ai/api/v1/responses`
（SDK 会往后接 `/chat/completions`，那是 Responses API 的路径，404）。发现后重新 `/key add`
同名改对，**还是 404** —— `_key_add()` 只调 `keys.add()` 写盘，一个字都没碰 `agent.backend`，
而客户端的 `base_url` 是 `OpenAI(...)` 建实例时定死的。更糟的是 `_model()` 里
`if arg == agent.name: return 已经在用了` 会早退，同名切不回来，用户只剩重启一条路。

现在 `_key_add(agent)` 保存后判一句：改的是正在用的那条、或者清单原来是空的，
就 `agent.use_model(name)` 重建（顺带解决「加完第一条还要再 /model 一次」）。
**加的是别的那条不能重建**——那会把人从正在用的模型上踢走，用例专门盯着。

**这类问题的形状是「配置写盘了，但活着的实例没跟上」**，`/key add` 改价格、
改 `max_tokens`、改 `limit` 全都撞得上，只是没有 404 那么显眼。以后往
`~/.config/feiyu/` 里写东西时先问一句：这一轮正在用它的对象，需要重建吗？

顺带 404 的提示原来咬定是「模型 id 退役了」，把人往错方向指了两轮，
现在两种可能一起说（id 不对 / base_url 多写了一段路径）。

## 0.48.0：提示要放在运行时，不要只往系统提示里加句子

用户问「现在有哪些能力、能不能更强」拿 `~/.local/state/feiyu/logs/` 的 trace 盘了一遍
（16 次运行、207 次工具调用），三条证据，按证据改的三处：

### `web_search` 上线三个 minor，一次都没被调用过

同期 `web_fetch` 54 次、**失败 16 次（30%）**，其中一次运行是 21 次 web_fetch + 0 次 web_search，
模型在自己拼 `google.com/search?q=` 和 `bing.com/search?q=`0.46.0 已经往系统提示里加过
「不认识的库、看不懂的报错先 web_search」那句了，**没用**。

这是第四次栽在同一件事上（0.16.0 todo、0.30.0 remember、0.46.0 web_search，加这次），
但结论要改一个字：**"行为指令放系统提示"是必要条件，不是充分条件**。真正见效的是
0.32.0 那套——**在它正在用笨办法的当口，在工具结果里说一句**。所以这版加的全是运行时提示：

- `_is_search_url()` 认出 web_fetch 的地址是搜索引擎结果页（十二个引擎 × 六种搜索词参数名，
  **要真带着搜索词才算**，抓 google 首页不算），在结果里提一句改用 `web_search`。
  提示挂在成功和失败**两条**返回路径上——失败那次尤其要，不然它接着换个引擎再猜一个。
- 实测：现在抓 `google.com/search?q=python+asyncio` 能通了，但 Google 回的是
  274 字的「If you're having trouble accessing Google Search」，正好是这条提示要挡的那种浪费。

### `_blocked_host` 把公网域名误杀了

日志里 `www.google.com` 被「内网地址已拦」拦过两次。根因：原来是**任意一条**解析记录
落在私有段就拦，而 `www.google.com` 的 A 记录是正常公网地址、AAAA 记录被 DNS 污染成
`2001::1`——Teredo 前缀，Python 的 `is_private` 算它内网。

改成**全部解析结果都是内网才拦**（`_internal_ip` + `all(...)`）。要挡的那几种
（`169.254.169.254`、`localhost`、内网主机名）解析出来全是内网，新口径照样拦得住，
用例把两个方向都盯着。**口径别再说成"防 DNS rebinding"**：这个检查在 urllib 自己解析之前
跑，本来就有 TOCTOU，它防的是「模型被网页内容诱导去抓内网地址」这一种，不是恶意 DNS。

### `symbol` 一次都没被调用过

全时段 0 次（同期 `search` 7 次、`bash` 187 次）。`_symbol_hint()`：`search` 的 regex 是个
**光秃秃的标识符**（`_BARE_NAME`，没有任何正则记号、三字符起）且有命中、且命中的第一个文件
**装了对应的语言服务器**时，提一句 symbol——三个条件缺一不可，没装服务器提了也是白提。

`_first_hit_path()` **只认匹配行**（`路径:行号:`），不认带 context 时的上下文行
（`路径-行号-`）：路径里本来就可能有 `-`，按 `-` 切 `my-file.py` 会切成 `my`。

### 顺带

- `Toolbox.hinted` 从 `bool` 变成 `set[str]`，走 `_hint_once(kind)`。提示不止一种了，
  各提各的一次；共用一个 bool 的话第一条提示会把后面所有提示都吃掉。
- `_fetch_note()`：401 / 403 / 429 / 404 各补一句人话（要登录 / 反爬 / 限流 / 地址不存在，
  都指向 web_search）。**认不出的状态码一个字不加**，跟 0.47.0 的 `_sandbox_note()` 同一个规矩。
  日志里 web_fetch 的失败一半是 401/403，模型只看到 "HTTP 403" 就换个地址再猜。

## 技能与自定义斜杠命令（0.49.0）

0.48.0 的结论说「行为指令放系统提示是必要条件」，但系统提示是**每一轮都要重发**的东西，
到 0.48.0 已经 649 token。为了一百次任务里用得上一次的做法给每一轮都加钱，加到第三条就不划算。
`feiyu/skills.py` 把这笔开销挪到「真用上的那一次」。

**两件事共用一份加载器，区别只在谁来触发**：

- **技能**（`skills/`）由**模型自己**按需加载：系统提示里只放一行名字加用途（`catalog()`），
  它判断用得上就调 `skill` 工具把正文读进来。实测一份技能的目录行 124 字，正文可以上千字。
- **命令**（`commands/`）由**用户**敲 `/名字` 触发，正文当成用户自己说的话送进对话，
  `$ARGUMENTS` 换成跟在后面的东西；**正文里没写这个占位符就把参数附在末尾**——
  不然用户敲了参数却石沉大海。

两边都是全局一份（`~/.config/feiyu/`）加项目一份（`.feiyu/`），**同名的项目那份赢**
（跟约定文件一个道理：具体的压过通用的）。

### 项目技能要同意，项目命令不用

**这条不能省，但两边的口径不一样，别抄混了**：`.feiyu/skills/` 是仓库带下来的，
而技能是**模型自己**决定加载的——clone 一个仓库，里面的技能就能往它的上下文里塞指令，
跟钩子、MCP 是同一类注入通道，所以照 0.15.0 那套**按内容指纹认**
（`trusted-skills.json`，`digest()` 摘整个目录：加一份、删一份、改一个字都要重新问）。

**命令不用认**：用户不敲就不会生效，敲了等于他自己把那段字打了一遍。这跟钩子的区别在
「谁按下了那个按钮」，不在内容危不危险。

### 几个决定

- **`catalog()` 里绝对不能放正文**，放了这个功能就白做了（用例专门盯着）。
- **目录里要写清楚"什么时候用"**：「跟哪一份对得上就先用 skill 读进来」那句是必需的——
  0.16.0 / 0.30.0 / 0.46.0 栽过三次，光列出来模型不会调。这里能走提示词那条路
  是因为**目录行直接点名了触发条件**，比 0.46.0 那种泛泛的「先 web_search」强得多。
- **名字用 `^[\w-]{1,40}$`**，`\w` 在 Python 里认 Unicode，所以中文名字照用
  （这个项目的技能多半是中文写的）；排掉的是空格和斜杠——它要当斜杠命令敲。
- **`load_skills()` 之后必须 `_refresh_system()`**，目录是拼在系统提示里的。
  这是 0.47.5 那条「配置写盘了，活着的实例没跟上」的同一个形状。
- **`skill` 进 `PARALLEL_SAFE`、出厂 `Skill(*)` 放行**：只是把一份本地说明书读进来，
  而项目那份在载入时已经过用户同意了。不进 `GUARDED` / `WRITES`。
- **名字错了要把有哪些列出来**：只说「没有」的话它会接着换个名字猜。
- **不给子 agent**：它的三条前提里「不需要中途确认」是靠只读四件工具撑住的，
  技能正文可以写任何东西，给了就得重新论证。
- **`ui.pad()`**：`ljust` 数的是字符数，CJK 一个字占两格，技能名可以是中文，
  `/skills` 和 `/help` 用 ljust 会对不齐。这是那个犯过三次的老坑的第四次。

## 网页端（0.50.0）

用户要一个网页端，样式照他的博客（feiyuz123.com）再往国际现代主义推一档，
并且「用 Rust 优化性能」。

### Rust 放在哪：**服务层，不是工具层**

先把这件事说清楚，别有人回头照着「用 Rust 提性能」去重写 tools.py：
实测本地工具加起来不到 20ms（启动 0.13 秒、read_file 0.6ms、search 5ms 走 rg、
list_files 0.4ms），而一轮要等模型 3 到 10 秒 —— **重写工具层是把 20ms 优化成 5ms**，
这话跟用户当面说过。

Rust 在这儿的理由只有一条，但很硬：Python 侧的硬性约定是「不引新依赖」，
而网页端要 HTTP、WebSocket、静态资源、并发连接。放 Rust 里，**Python 一个依赖都不加**，
用户拿到的是一个 900K 的静态二进制。

### 接缝在 ui.py，不在 agent.py

`agent.py` 里有三十处 `print(ui.xxx(...))`。**一处都没动。**
`feiyu/events.py` 是个 sink：装上之后 ui 的那几个函数照旧返回 ANSI（终端那条路一个字没变），
同时把同一件事按结构报一份。这正是 CLAUDE.md 开头那条分层原则的兑现——
「ui.py 是唯一给人看的输出层」，所以换前端就该在这儿换。

- **报的是原始 markdown 和原始参数，不是画好的字符串**。把 ANSI 发给浏览器再解析回去
  是最糟的做法；怎么画是前端的事。
- **`Stream.progress` 要按 `PROGRESS_STEP` 攒着报**：上游一个 delta 一次回调，
  一个长文件就是几千条事件，全推给浏览器会把它卡住。终端那条路不受影响（它按 12Hz 重画）。
- **`events.paused()` 是必需的**：斜杠命令那些函数（`__main__._mode` 之类）内部就是拿
  `ui.notice()` 拼的，不挡的话一条 `/mode` 会报两遍（内部一条、调用方把返回值发出去又一条）。
  第一版就是这样，实测截图里看到的。
- 事件 sink **绝不能因为前端出问题把 agent 弄崩**，`emit` 咽掉所有异常，理由同 `trace.write`。

### 协议：stdout 只放 JSON

`feiyu --serve` 一行一个 JSON 出去、一行一个进来。人看的那些 ANSI 整体
`redirect_stdout(sys.stderr)` —— **跟 `--json-schema` 一模一样的做法**，
不要逐处去改 print 的目标。

- **确认走的是本来就有的回调**（`agent.approve` / `ask_user` / `approve_plan` /
  `approve_hooks` / `approve_mcp` / `approve_skills`）。`__main__` 塞的是 `ui.confirm`，
  serve 塞的是「发一条 ask 事件、在队列上等回话」。所以确认语义跟终端完全一致，
  包括「本次会话都允许」和「永久允许」写规则。
- **对面断了要 `fail_all()`**：不放掉那些等着的请求，agent 会卡到 `REPLY_TIMEOUT`（10 分钟），
  而网页早关了。
- **中断走 `_thread.interrupt_main()`**，跟 `EscInterrupt` 落在同一处，
  0.12.0 那套「中断要让历史保持自洽」的不变量原样成立，不用另写一套。
- `--serve` 的早退分支要放在 `__main__` 里那些 `ui.confirm` 版回调**之前**：
  那些在没有终端的地方一律返回「拒绝」，而 `load_hooks` / `load_mcp` / `load_skills`
  要用 serve 自己那套回调去问人。

### 前端：博客的设计系统 + 国际现代主义

继承自 feiyuz123.com（改之前先回去核对 `~/Myproject/src/styles.css`）：纯白纸、
墨色全是 `rgba(0,0,0,α)`、IBM Blue **只做线和面永远不做字**、24px 基线网格
（line-height 是长度不是比例）、8pt 间距阶、只有 400/700 两档字重、除图片外全是直角、
没有暗色模式、浮现是 8px/500ms/ease-out。

这一版加的：**meta-rail** —— 每块内容左边一列大写小标签加一条竖线，这是整页的骨架；
规线承担全部分区职责（没有卡片、阴影、圆角、底色块）；功能性大写只给标签不给内容。

**第二色 `--alert` 只给需要你动手的东西**：确认卡、报错、diff 删除行。
取自终端 swiss 主题那个红。蓝管结构、红管「停下来看我」，别扩大用途。

几个踩到的：

- **`.note` 是 `<p>`，浏览器默认给它 1em 边距**，不清掉整条 meta-rail 就歪了。
- **按钮上的权限规则是文件路径，不能吃 `text-transform: uppercase`**
  （会变成 `UPDATE(FEIYU/TOOLS.PY)`）。功能性大写只给标签，这条整份设计里到处成立。
- **确认卡插进来时要把当前那块工具行收掉**（`liveTool = null`），
  结果回来时照 `lastTool` 另起一块。不这么做的话结果会画在授权它的那张卡**上面**，
  读起来像是先改了再问的。
- 工作目录**从左边裁**保住尾巴，跟终端的 `ui.clip_path` 同一个道理。

### 验证：两个夹具，都要跑

跟终端那半边的 `dev/ui_drive.py` 是同一个思路 —— **改完前端不要只看 CSS 觉得对**。

```bash
node web/dev/shoot.mjs http://127.0.0.1:7777/ /tmp/shots   # 灌编好的事件流，不花钱
node web/dev/live.mjs  http://127.0.0.1:7777/ /tmp/live "任务"  # 打真接口，验整条链
```

`shoot.mjs` 除了截图还**量网格**：每块的大写标签必须跟正文第一行顶对齐，差 2px 就报。
不引 puppeteer，Node 自带 WebSocket，CDP 本来就是 JSON over WS。

**两个夹具自己的坑**（都真的骗过我一轮）：
- 拍完 `3_narrow`（390px）之后**必须把视口还原**再去量对齐 —— 那个宽度下 meta-rail
  是故意塌成上下两行的，不还原就会量到 17px 的「偏移」，然后去修一个不存在的问题。
- **滚到某处再拍要 `captureBeyondViewport: false`**：页面本身不滚（头尾钉住，滚的是
  `.sheet`），带 beyond 会重新从文档顶部铺开，把刚滚到的位置丢掉。

### 三道门，一道都不能少（0.50.1 补的，头一版只有第一道）

连上这个端口的人可以让 agent 在这台机器上写文件、跑命令。

1. **只听 127.0.0.1**，不要改成可配置的。远程用就开 ssh 隧道。
2. **一次性令牌**（`?t=`，每次启动从 `/dev/urandom` 取 16 字节）。
   只靠「只听回环」不够：**这台机器上任何一个进程、任何一个用户都能连回环地址**。
   令牌只出现在开浏览器的那个地址里，不进 argv（`ps` 看不到），只印一遍。
3. **Origin + Host 校验**。这一条治的是最要命的那种，头一版漏掉了：

> **WebSocket 不受同源策略保护。** 用户在浏览器里打开任何一个网站，那个网站的脚本
> 就能 `new WebSocket("ws://127.0.0.1:7777/ws")` 连上来 —— 浏览器照连不误，
> 没有 CORS 预检，令牌也拦不住（浏览器会带上，攻击者不需要知道它……
> 其实攻击者拿不到令牌，但**头一版连令牌都没有**）。
> **实测过**：这样连上之后可以 `/mode auto` 关掉确认、让 agent 读任意文件并把内容
> 从同一条连接送回去、跑任意命令。也就是说，没有这道门，「用户访问了一个网页」
> 就等于「在他机器上执行任意代码」。

Origin 缺失或为 `null` 一律拒（浏览器发 WS 时**一定**带 Origin，不带的是脚本，
而脚本没有正当理由连这里）。Host 另外挡 DNS rebinding。
三道门的判定抽成了自由函数 `token_ok` / `origin_ok`，`cargo test` 里五条用例盯着 ——
**安全检查没有测试等于没有检查**。

### ctrl-c 和那只狗（0.50.2）

**ctrl-c 会甩一页 traceback。** SIGINT 是发给整个前台进程组的，`feiyu --serve`
这个子进程也吃到一份，那一下正落在 `run()` 的 `inbox.get()` 上 —— 0.50.1 只把
循环**体**包在 try 里，没包取消息那一行。而且它直接冲出 `serve()`，
`_finish` 里那些清理（杀后台命令、关语言服务器和 MCP 进程）全跳过了。

两边都改了，因为这是个竞态、只改一边不保险：
- Python：`inbox.get()` 也接住 KeyboardInterrupt，静静返回 0 走正常收尾。
- Rust：`with_graceful_shutdown` 接 ctrl-c，先按协议发 `{"op":"stop"}`，
  等它自己退（最多 5 秒）再动手杀。**这条是确定性的，不靠信号落在哪一行。**

**pty 夹具复现不了这个**（`scratchpad/ctrlc_drive.py` 那种）：Rust 要是先死，
Python 走的是 stdin EOF 那条干净路径，看不到 traceback。非交互 shell 起的后台任务
更是把 SIGINT 继承成忽略，根本测不出来。所以这里靠的是「两边都堵上」，不是「测过了」。

### 那只狗

`feiyu/logo.png` 是纯剪影（白狗黑底、无 alpha），`potrace` 描成 911 字节的 SVG，
`fill="currentColor"`，走 `<img>` 加载时解析成黑色，CSS 压 `opacity: .8` 正好是
`--text` 那档墨色。生成方式：`ui._trim()` 裁黑边 → 二值化取反 → `potrace -O 1.0`。

**位置是这版设计里最要紧的一处对齐**：刊头用跟 `.block` 一模一样的两列网格，
狗落在 meta-rail 那一列，刊名和元信息落在正文列。于是从最上面的狗到最下面每一块的
大写标签，整页只有一条纵线。

**窄屏下 `--rail` 是 0，`.mark { width: 100% }` 就是宽 0，记号会整个消失** ——
真这么坏过一次。媒体查询里让它自己站一行。`shoot.mjs` 现在两档宽度各量一次记号的
盒子，小于 24px 就报错：**零尺寸的元素在截图里就是「不存在」，眼睛扫过去不会发现
少了东西**，这类 bug 只能靠量。

### 命令补全（0.50.3 修的三处）

用户说「输入方面自动补全有问题」。拿 `web/dev/input.mjs` 敲**真按键**回放一遍
（`Input.dispatchKeyEvent`，不是直接改 `input.value` —— 毛病全在 keydown 那一层，
绕过按键等于没测），19 条里挂了 3 条：

- **打完 `/mode auto` 一回车，参数被吃掉了。** `showPalette` 原来按
  `typed.split(" ")[0]` 筛，于是 `/mode auto` 的 head 是 `/mode`，而
  `"/model".startsWith("/mode")` 为真 —— 菜单开着且高亮在 `/model` 上，
  回车走了「补全」那条路：输入框变成 `/model `，`auto` 无声丢掉，什么也没发出去。
  改成按**整串**筛，外加打了空格就收起。
- **回车的含义要看用户有没有自己挑过**（`palMoved`）。没挑过 = 发送我打的这句，
  用 ↑↓ 挑过 = 用我选的那条。不分这两种的话，`/undo` 打全了还得按两次回车。
  Tab 永远是补全，这条不变。输入一改 `palMoved` 就作废。
- **esc 关菜单顺手把这一轮打断了。** 输入框那个 handler 只 `preventDefault()`，
  事件照样冒到 document 上，那边看到菜单已经关了就发 `{"op":"interrupt"}`。
  要 `stopPropagation()`。
- 顺带：**完全相同的那条排最前**。打全 `/mode` 再按 Tab，原来会补成 `/model`。

**这三条 `shoot.mjs` 一条都测不出来** —— 它灌的是事件流，碰不到键盘。
按键这一层要单独的夹具，改输入区之前先跑 `node web/dev/input.mjs <地址>`。

### 网页端的命令面（0.50.4，三处都是用户拿截图来提的）

- **启动那三句是环境说明，不是对话。** `load_hooks` / `load_mcp` / `load_skills`
  的返回原来当 `note` 发，于是在正文流里占一整块（「提示 · MCP：fin（29 个工具）」），
  看着像 agent 开口说了句什么，还把「把要做的事写在下面」那条空态提示顶掉了。
  改成单独一种事件 `boot`，前端摆进刊头、跟元信息一档。
  照 0.26.1 那条：**常态不占位置，异常才出现**（未启用 / 失败才染成 alert 色）。
  `/clear` 不清它 —— 它说的是环境，跟聊了什么无关。
- **`/help` 一直是「发得出去但什么都不显示」。** 前端只拿 help 事件填补全清单，
  从来没画过。现在敲 `/help` 会画一张命令表；**连上时自动发的那次不画**
  （`wantHelp` 标志），不然每次开页面都白多一块。
- **不该在补全里提供一个点了就报错的东西。** `/key` 是刻意不给网页端的
  （密钥增删只在终端），但补全把它列出来了，选了才说「未知命令 /key」。
  现在 `TERMINAL_ONLY` 那张表同时干两件事：从 `/help` 的清单里剔掉（补全也就没有了），
  以及用户硬打进来时给一句人话说明为什么。
- 顺带补上 `/verify` `/review` `/helper` —— 这三个漏在外面纯属疏忽，没有理由不给。

`web/dev/input.mjs` 现在 27 条，覆盖到这些。**改命令面之前跑它**。

### 对话那一屏的排版（0.50.6，用户说「不太好看」）

原来人说的话是「加粗 + 左边一条蓝线」，三个毛病叠在一起：

1. **蓝线正好挨着 meta-rail 那条竖线**，两条竖线隔几个像素并排，看着像画错了；
2. 标签「你」已经说明了是谁在说话，再加一条线、再加一次加粗，同一件事说了三遍；
3. 短句子加粗等于在喊。

改成**靠字号和位置分层，不靠装饰** —— 这是这套系统一贯的做法。人说的那句是一段
对话的小标题（`--text-lg`、常规字重、上面一条 `--line-strong`），它说的是正文。
`.block--you + .block` 的上边线抹掉：一问一答是一体的，中间不该再切一刀。

**竖线改成整条贯通**（画在 `.stream::before` 上，不是每块 `.block__label` 各画一段）。
逐块画的话块与块之间那点内边距会把它切成一节一节，看着是毛的。
**它是整页的纵向骨架，就得是一条线。**

`shoot.mjs` 加了第四张 `4_talk`：纯对话、没有工具调用没有 diff。
**这是最常见的一屏，也是最容易做丑的一屏**——排版全靠字号和留白撑着，
前三张（都带工具调用和 diff）盖不到它。

### 侧边栏、多份会话、上传（0.51.0）

**会话存储从「一个工作目录一份」改成「一份 id 一份」**（`sessions/<hash>/<id>.json`，
id 是时间戳所以天生按时间排序）。0.50.x 那个形状装不下「历史对话列表」。
**老位置那份要照样读得到**（`_legacy_path`，`listing()` 把它当 id 为 `legacy` 的一条）——
升级之后 `feiyu --resume` 不能突然说没有上次的对话。写只往新目录写。

- `/clear` 语义不变（清空 + 删掉当前这份）；**「新对话」是另一件事**：存住当前那份、
  开一个新 id，旧的留在盘上。这两个别混。
- **id 会拼进路径，`_ID_OK` 必须挡住 `../`**，用例专门盯着。
- 顺带修掉一个既有 bug：**一次性任务和管道模式既不 `--resume` 也不存盘**，
  而撞轮数上限时代码自己印的提示正是「`feiyu --resume` 接着做」，指向一个不存在的东西。
  会话装配挪到三条路径共同的上游了 —— 跟 0.12.0 那条 `main()` 的规矩一样。

**上传**走 Rust 那边的 `POST /upload`（令牌 + Origin 两道门都要过，Origin 这道比读资源
更要紧：它是往磁盘写）。文件落在 `<工作区>/.feiyu/uploads/`，**必须在工作区内**，
否则 agent 的文件工具够不着。`safe_name()` 只留最后一段并换掉路径分隔符，
`cargo test` 里拿 `../../.ssh/authorized_keys` 之类盯着。前端拿到的是相对路径，
随下一条消息一起交给 agent，它用本来就有的 `read_file` 去看 —— **没加新工具**。

### 删对话（0.51.1）

- **删的是正在看的那段时，agent 也要归零**（`reset()` + 换新 id）。
  盘上没了内存里还在的话，下一轮存盘会照着旧 id 写回去，等于没删。
- **问一句再删，但不用 `confirm()` 弹窗**：一段对话是真数据，值得问；
  可那个弹窗跟这套排版格格不入，还会挡住整页。就地把那一行换成
  「删掉「…」？删掉 / 取消」，跟确认工具调用是同一个路子。
- **叉按钮必须 `stopPropagation()`**：不然点叉的同时把这段对话也打开了 ——
  跟 0.50.3 那个「esc 关菜单顺手把这一轮打断了」是同一类错。
- 叉平时 `opacity: 0`，划上去或键盘聚焦才显形。侧栏是索引，常态不该被一排叉占着。
- **取消要把那一行接回去**：`cloneNode` 不带事件，直接换回去的话它就不能点了。

顺带一个测试自己的坑：`test_serve.py` 里替换 `session_mod.drop` 忘了还回去，
**它跟着模块活到别的测试文件**，`test_session` 那几条删除用例莫名其妙地挂。
换模块级的东西一律配 `addCleanup`。

### 两个夹具自己骗了我两轮（0.51.0）

- **`ask()` 只等「不忙了」是错的**：刚点下去的那一刻本来就是不忙的，第一次轮询就返回，
  后面所有断言都跑在对话开始之前，报出来一片红。**要先等它真的忙起来，再等它闲下来。**
- 我一度以为是 `.pyc` 那个老坑（源码和字节码 mtime 同秒），清了 `__pycache__` 之后
  一样 —— **不是**。真因是上面那条，以及中转站当时严重降速（一个字的回复要 20 秒）。
  记在这里是因为「先怀疑那个记过的坑」本身会带偏方向。

**协议层是验过的**：`--serve` 直接喂一条 prompt，收尾能看到 `sessions rows=1`。
**UI 层是验过的**：`input.mjs` 39 条。**浏览器里两段真对话来回切没验成**，
卡在中转站降速上，不是代码。

## 从网页端改成桌面端（0.52.0）

用户要一个桌面应用，框架照 Hermes Desktop（NousResearch/hermes-agent，MIT），
样式保留博客那一套。

**没有 fork 它。** `apps/desktop` 是 **524,089 行 TS/TSX、2183 个文件、33MB**
（Electron 40 + React 19 + Vite + Tailwind 4 + @assistant-ui/react + xterm +
Mermaid + CodeMirror）。真正值钱的是它那份 360 行的 `DESIGN.md`——信息架构和
几条原则。照着它在我们这份 1500 行的前端上实现，一天，代码还是能读的。
数字摆给用户看过，是他拍板的。

### 借来的框架

标题栏（自绘）/ 侧栏（导航项 + 搜索 + 已置顶/会话分组）/ 底部状态栏 /
输入区左附件右模型选择器。加上它两条跟我们本来就一致的原则：
**Flat, not boxed**（不许卡片套卡片）、**Tokens, not literals**（组件里不许有裸色值）。
还有一条直接照抄的规矩：**One action, one home** —— 侧栏那几个导航项和标题栏那几个钮
**都走斜杠命令**，不给它们另开一套逻辑。

### Tauri 那半边

- **不再监听端口**，所以 0.51 那一整套安全面（一次性令牌、Origin、Host、
  DNS rebinding）**全都不需要了**。前端和 agent 之间是 `invoke` / `emit`，
  走进程内通道，外面够不着。这是桌面端相对网页端唯一实打实的收益。
- **`WEBKIT_DISABLE_DMABUF_RENDERER=1` 必须在 GTK 初始化之前设**，
  NVIDIA + WebKitGTK 在 Wayland 上不设就直接协议错误退出（Writer 里也有这条）。
- **`#[tauri::command]` 的函数名不要跟 `main` 里的局部变量撞**：
  `generate_handler!` 展开出来的代码在那个作用域里按名字取函数，会被变量遮住。
  `workspace` 撞过一次，改名 `where_am_i`。
- `frontendDist` 是**相对 `tauri.conf.json` 所在目录**的。配置不在 `src-tauri/` 时
  写 `"ui"` 而不是 `"../ui"`。

### 为什么还留着一个 devserver

**Tauri 在 Linux 上用的是 webkit2gtk，没有 CDP**，`dev/shoot.mjs` / `dev/input.mjs`
那套 headless Chrome 夹具接不上去。`src/devserver.rs` 把同一份 `ui/` 用 HTTP 端出来，
夹具照旧能跑；前端靠 `window.__TAURI__` 在不在自己决定走哪条传输。
**它是测试夹具，不是产品**，install.sh 不装它。`/ws` 和 `/upload` 那两道门照旧全在。

**代价是夹具跑的引擎跟产品不是同一个。** 0.80.0 就栽在这儿：一条 `justify-content: flex-end` 在 Chrome
里是「开头够不着」、在 WebKitGTK 里是「滚动原点翻到底边」，夹具绿了三个版本而产品里「回到最新」一直挂着。
滚动、布局、过渡这类引擎行为，改完再跑一遍 `python3 desktop/dev/webkit.py <地址>`（系统的 python，不是 .venv）。

### 这一轮踩到的

- **`[hidden]` 压不住 `display`**。`[hidden]` 的 UA 样式特指度跟 `.chip { display: inline-flex }`
  打平，后写的作者样式赢 —— `el.hidden = true` 对任何设了 display 的类都失效。
  全局补一条 `[hidden] { display: none !important }`。附件条、补全菜单、窗口按钮都中过。
- **裸的 `find` 是 `window.find`**（浏览器自带的查找函数），不是 id 为 find 的那个输入框。
  夹具里写 `find.value = "x"` 会静默无效。
- **夹具的 `ev()` 必须把页面异常报出来**。抛了却返回 `undefined` 的话，断言会挂在一个
  根本没执行的动作上 —— 顶层 `const` 跨 eval 重复声明就是这么骗了我一轮。
- **Chrome 的 profile 目录跨次保留 localStorage**，上一轮置顶过的会话会留到下一轮把
  列表顺序改掉。夹具开头 `localStorage.clear()`。
- **夹具灌合成事件时会跟服务端自己推的事件抢**：它盘上有真会话，`sessions` 事件会在
  合成列表之后到把 DOM 覆盖掉。灌完、点完，**再灌一遍**才断言。

### 权限和密钥在 serve 这条路上

- **`/key` 刻意不在 `serve.run_command` 的分支里**，落到「未知命令」。密钥的增删只在终端做。
  加新的斜杠命令进来时先想一遍：它会不会印出配置里的东西。
- `/model` 只印名字和模型 id，不印 base_url 和 key（核过）。
- `_mcp` 会印 MCP 服务器的 argv —— 用户要是把 key 写在那儿，终端和网页都看得到。
  这是既有行为，不是网页端引入的。
- 事件流里没有任何密钥；trace 日志里也没有（拿正则扫过全部日志，0 命中）。

### 0.50.1 顺带修掉的三处

- **一条畸形消息会把读 stdin 的线程弄死**（`{"op":"reply","id":"不是数字"}` → `int()`
  抛 ValueError → 线程没了 → stdin 再也没人读）。页面上表现是「不响应了」，
  而 traceback 在 stderr 上没人看。现在 `_dispatch_in` 整个包在 try 里。
- **`interrupt_main()` 有竞态**：reader 线程按 `self.busy` 决定扔不扔，而 busy 是在
  `handle_prompt` 的 finally 里清的，中间那个窗口里 KeyboardInterrupt 会落在收尾代码
  或者 `inbox.get()` 上，把整个服务带走 —— 而用户只是按了一下中断。`run()` 现在接住它。
- **`~/.local/state/feiyu/history` 一直是 0644**：那是用户在输入框里打过的**每一行原文**，
  prompt_toolkit 的 FileHistory 按 umask 建的。现在 `_history_file()` 先建出来收成 0600。
  顺带 `files.private_dir()`：feiyu 自己那几个目录统一 0700（文件本来就是 0600，
  但目录是 0755，同机的别人能列出有哪些工作区）。

`install.sh` 里网页端是**可选**的：没有 cargo 就跳过并说明，不要让整个安装失败——
网页端是加分项，终端版才是这个工具本身。

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

（0.76.0 起还有一条 streamable-http 传输，见文末那节。下面这些跟传输无关，两种都适用。）

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
- 测试用 `tests/mcp_server.py` 假服务器，五个工具覆盖正常 / isError / 超时 / 只读，
  外加 `getenv` 验 `env` 块里的 `${VAR}` 真展开并传进了子进程，
  `echo` 第一次调用时反过来发 `roots/list`，验证客户端会答而不是卡死。
  HTTP 那套另有 `tests/mcp_http_server.py`，绑 127.0.0.1 的临时端口，一样不出网。

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

## 出图（0.55.0）

用户问「生成图片的模型能不能直接在对话里出图」形状照 Hermes 的 `image_generate`：
工具把图存进工作区、只把**路径**告诉模型，界面自己去读那个文件画出来。

- **没配就不挂这个工具**（`agent.tool_schemas()` 看 `~/.config/feiyu/images.json` 在不在）。
  0.29.0 算过「按模式挂工具」不划算，理由是**工具集会中途变**、一改就作废整段 prompt cache；
  这里判的是一份**整个会话里不会变**的配置（改了要重启，跟 MCP 一个规矩），
  所以那条反对意见不成立。不出图的人不该每轮多发一份 schema。
  代价是没配的时候模型只会说「我不能出图」，所以 README 两份都写清了怎么配。
- **`ToolResult.shows` 跟 `.image` 是两回事**，别混：`image` 是 (media_type, base64)，
  喂给**模型**看的，会进历史（所以有 `without_images()` 那一套）；`shows` 是一个工作区
  相对路径，给**人**看的，走前端自己的读图通道，一个字节都不进对话历史。
  `read_file` 读图时两个都填 —— 模型看到图，人也看到同一张。
- **接口只回 URL 时，那个地址要照旧过 `_blocked_host`**：它是**接口**给的，不是用户给的。
- **同一秒出两张图不能互相覆盖**：文件名是 `%Y%m%d-%H%M%S.png`，撞了往后缀加序号。
- 权限规则名 `Image`，进 `GUARDED`（出网 + 花钱）和 `WRITES`（往磁盘写），
  **不进 `PARALLEL_SAFE`**。`ui._HEADLINE_ARG["image_generate"] = "prompt"` —— 确认那行
  必须显出描述，否则等于让人给一张看不见的图付钱（跟 `remember` 显 `fact` 同一个理由）。
- **没往系统提示里加句子。** 前面栽过四次（0.16.0 todo / 0.30.0 remember / 0.46.0
  web_search / 0.48.0），但那四次的共同点是**模型手上有个它更顺手的笨办法**
  （bash grep 代替 search、埋头做代替列清单）。出图没有替代品，而且 schema 的第一句
  就点名了触发条件 —— 跟 0.49.0 技能目录行能靠提示词那条路是同一个道理。

### 前端

- **路径不可信**，两条传输各自在**服务端**比工作区前缀：桌面端 `invoke("read_image")`、
  夹具那条 `GET /file`，共用 `desktop/src/pathguard.rs`（六条用例，回退验证过）。
  `Path::starts_with` 是按分量比的，所以 `ws-secrets` 不会被当成 `ws` 的子目录。
- **`read_image` 回 `tauri::ipc::Response`（原始字节 → ArrayBuffer），不要回 `Vec<u8>`** ——
  后者会被序列化成一个个数字的 JSON 数组，一张 2MB 的图变成 8MB 文本。
  CSP 因此要放 `blob:`（页面自己造的同源 URL）。
- **`![]()` 要在链接规则之前处理**：`![x](y)` 的后半截本身就是合法的链接语法，
  反过来会渲染成一个前面挂着感叹号的链接。远程地址**不当图画**（CSP 挡着，画出来必然是
  破图标），渲染成链接。
- **同一轮里画过的路径，正文里再 `![]()` 就不画第二遍**（`shownShots`）。
  Hermes 那份 `generated-images.ts` 里也有这条：模型生成完极爱把路径再重述一遍，
  不挡的话同一张图上下挨着出现两次。`turn_start` 时清空登记。
- `fillShots()` 只在 `closeSaying()` 里跑，**不在每个 delta 上跑** —— 那会边流边反复读文件。

`shoot.mjs` 加了第五张 `5_image`，并且**量图的盒子**：零尺寸的图在截图里跟"没有这块"
长得一模一样（跟窄屏那只狗是同一类坑）。同时验"重述那次没画第二遍"——
这条只能在 DOM 里验，单元测试碰不到。

## 技能与工具面板（0.56.0）

用户要一个 Hermes Desktop 那样的「技能与工具」模块：三个页签（技能 / 工具集 / MCP），
左边清单带开关，右边详情。

### 开关表只存关掉的那些

`feiyu/enabled.py`，`~/.config/feiyu/enabled.json`，0600。**默认全开，文件里记的是关掉的名字。**
反过来（记开着的）有两个毛病：新装一份技能得先想起来去开它；升级加了新工具，
老用户表里没有于是拿不到 —— 那正是 0.33.0「老用户拿不到新出厂规则」的形状，这次一开始就避开。
所以这份文件通常根本不存在，或者只有一两行。

- **不缓存，每次现读。** 面板上一按就写盘，而正在跑的那个 agent 得当场跟上
  （0.47.5 那条「配置写盘了，活着的实例没跟上」）。文件几十字节，省这一次读没意义。
- **`exit_plan` 关不掉**（`enabled.LOCKED`）。它不是一项能力，是计划模式**唯一的出口** ——
  关掉之后 `_gate` 还会指名让模型调它，而它不在工具表里，那一轮就转不出来。
  这是这个功能里唯一真正的脚枪，一行常量就堵上了，别为了"什么都能关"把它放开。
- **`agent.apply_toggles()` 是必须的**：写盘之后要 `set_tools()` 重挂工具表 + `_refresh_system()`
  重拼系统提示。少这一步就是"按了没反应"。
- 终端也吃这份表（它是这台机器上的配置，不是桌面端的私有状态），
  并且 **`/skills <名字>` 能在终端里开关一份** —— 只在图形界面里能开的话，
  没有图形界面的机器上那份技能就永远回不来了。

### 关 MCP 关的是"挂不挂工具"，不是"起不起进程"

起不起写在 `mcp.json` 里、改了要重启。面板上关掉只是不把它的工具挂给模型
（顺带系统提示里那段介绍也不提它）。这句话要写在详情栏里 ——
不说清楚的话，关了之后进程还在，人会以为没生效。

### 协议

三条新 op：`catalog`（要三份清单）/ `detail`（要一份技能的正文）/ `toggle`（开关一项）。
`toggle` 走 inbox 到主线程做 —— 它动工具表和系统提示，跟跑一轮是同一份状态。

- **清单里没有技能正文。** 五十份技能各上万字，一次全推过去几百 KB，而面板一次只看得见一份。
  跟 `skills.catalog()` 不放正文是同一个道理，只是省的东西不一样：那边省每轮的 token，
  这边省一次推送的体积。
- **`Protocol.send()` 的载荷里不能有 `kind`。** 它拼的是 `{"kind": 事件名, **载荷}`，
  重名的话事件名被整个盖掉，前端那条 case 永远不匹配。`detail` 第一版就写成
  `send("detail", kind="skill", ...)`，发出去是 `{"kind":"skill"}`。
  现在 `send()` 自己 `payload.pop("kind")` 兜底，字段改叫 `what`。
  **这条是协议测试跑出来的，肉眼看代码看不出来。**
- **没配出图接口时清单里不放 `image_generate`** —— 摆一个永远打不开的开关只会让人以为坏了。

### 前端

面板跟 `.sheet` 占同一格（`grid-row: 2 / 5`），开着的时候把对话那一屏和输入区整个收起来。
它是配置不是对话，浮在上面或者挤在旁边都会让人分不清自己在哪儿。

- **开关是方的。** Hermes 那个是圆角胶囊，这套系统里除图片外全是直角。蓝只做面：
  开着是蓝底白块，关着是描边空块。
- **开关必须 `stopPropagation()`**，不然按一下开关的同时把这一项也选中了 ——
  跟 0.51.1 那个删对话的叉是同一类错。
- **"还没收到清单" ≠ "这台机器上没有"**，两种情况长得一模一样，得分开说（`shelf.got`）。
  连上（含断线重连）时面板要是开着就把清单重新要一遍 —— 不然它会一直空着。

夹具：`shoot.mjs` 多了 `6_shelf` / `7_shelf_tools` / `8_shelf_mcp` 三张，
并且量开关的盒子（零尺寸的开关在截图里就是"不存在"，跟那只狗是同一类坑）；
`input.mjs` 补了 Ctrl K 开、esc 关、开着时对话那屏收起来。

**跑这几张要让夹具连到一份备好技能的 `XDG_CONFIG_HOME` 上**，因为面板一打开就向服务端
要 catalog，灌合成事件会被真推送盖掉（CLAUDE.md 里早记过这个坑）。另外两条：
- **要先等真的连上再开面板**（看状态灯 `#dot`，不要看 `online` —— 那是顶层 `let`，
  在 classic script 里落在 script scope 上，CDP 的 eval 按裸名字取不到，会抛 ReferenceError）。
- 出图那一屏不再依赖"夹具跑在仓库根上"：走 `/upload` 把 logo 传进工作区，
  拿回来的相对路径正好就是 agent 出图时给的那种，顺带也验了一次上传。

## 0.56.1 / 0.57.0：四个用户拿截图来问的 bug，和配置档案

### `legacy` 既是别名又是文件名 → 一行删不掉的会话

`session.LEGACY` 本来只是"读 0.51.0 之前那个位置"的别名。但 `save()` 会把
`agent.session_id` 直接当文件名写，而 `--resume` / `open_session` 会把 id 设成
`legacy` —— 于是新目录里出现 `legacy.json`。`listing()` 从目录里扫出来给它的 id
还是 `legacy`，`drop()` 见到这个 id 却去删**旧位置**那份，`os.remove` 的 OSError
被咽掉。表现：点多少次「删掉」都回一句「删掉了一段对话」，那行永远在。

两处一起改：`save()` 见到 `legacy` 写回旧位置（这个 id 永远只对应那一个文件），
`drop()` 碰到它两个位置一起删（修已经产生的那份）。
**教训：一个值同时当"别名"和"真名"用，读写删三条路早晚对不齐。**

### 桌面端按了「都允许」反而全被拒

`serve.Server.approve()` 返回 `yes/no/all/save`，而 `Feiyu._gate` 只认
`yes/always/remember` —— `all` 和 `save` 两个都落到最后那句 `return 用户拒绝`。
它的 docstring 还写着"语义跟 ui.confirm 一致"，其实不一致，所以一直没人发现。
现在有一张 `_ANSWERS` 翻译表，**并且有一条用例断言表里的值全落在 `_gate` 的分支上**。

顺带：`dict.get(answer)` 在对面回一个列表时会抛 unhashable，而这行跑在一轮中途。
先判 `isinstance(str)`。

### 并行那批把「本轮都允许」丢了

`_run_parallel` 走的是 `_deny_reason`，那里 `allow_all` 写死 False。四条 web_fetch
你在第一条上按了「都允许」，后三条照样问、照样拒。现在跟串行同一套：
在批内累加 `allow_all`。`_deny_reason` 删掉了。
**顺带把界面上那句「本次会话都允许」改成「本轮都允许」** —— 它是 `_run_tools`
那一批的局部标志，下一轮照样问，原来的说法是假话。

### 并行的结果挂错了块

事件里没有调用 id，前端只能按到达顺序猜。而并行是「四条 tool_start 全来完，
四条 tool_result 才开始来」，于是后三条结果全挂到最后那条的标题下面 ——
屏幕上是四行一模一样的 `Fetch(同一个地址)` 配着四个不同的结果。
`ui.tool_call` / `ui.tool_result` 加了 `cid=`，前端按 id 存块（`toolBlocks`）
和存标题（`toolSeen`）。确认卡插进来时**把所有开着的块一起摘掉**，
不是只摘一条 —— 并行那批是先问完再跑。

### 命令补全两处

- **↑↓ 走到看不见的地方**：菜单最高 16rem，命令十几条。
  `scrollIntoView({block:"nearest"})`，**不要 "center"** —— 那样每按一下整个列表都在跳。
- **`/命令 空格` 之后没有子选项**：原来一见空格就 `hidePalette()`。现在接着补参数，
  参数清单由服务端随 `help` 事件给（`push_help`）—— 模型清单、档案、技能都会变，
  写死在前端迟早对不上。命令名补完顺手把参数菜单打开，省一次回车。

### shift+tab 在桌面端没反应

终端那边是 prompt_toolkit 的键位，直接调 `agent.cycle_mode()`。桌面端补了一个
`cycle_mode` op，**两边调的是同一个方法**，不各写一套模式顺序。
挂在输入框上而不是 document 上：Tab 在别处是移焦点，别抢。

### 配置档案（0.57.0）

`feiyu/profiles.py`，`~/.config/feiyu/profiles/<名字>.md`，frontmatter 认
`name` / `description` / `model`，正文直接拼进系统提示。

- **位置在风格之后、项目约定之前**：风格管"话说多长"，档案管"你是谁"，
  项目约定最具体。同一句话，项目里写的赢。
- **`MAX_BODY` 只有 4000**，比 `skills.MAX_BODY`（12000）小一个数量级 ——
  技能是"用上那次才发"，人格是**每一轮都发**。
- **名字以文件名为准，不认 frontmatter 里的 `name`**：切换、保存、删除全按文件找，
  两个来源不一致就会出现"切得过去但存不回来"。
- **一律走 `agent.set_profile()`**，别直接赋值 —— 要重拼系统提示（跟 `set_mode` 同理）。
  档案里写了 `model` 就连模型一起换。
- 面板里是第四个页签。**档案那一栏不给开关**：它是"用哪一份"不是"开不开"，
  单选套上双态开关的皮，人会以为可以两份一起开。当前那份用 ● 标，换用在详情栏按。
- 新建走**就地的一行输入**，不用 `window.prompt` —— 那在 webview 里是模态框，
  一弹起来整个界面就不响应了。
- **没保存的稿子要留住**（`shelf.draft`）：点开另一份再点回来、或者服务端推了新清单
  重画，都不该把你打的字冲掉。

### 夹具两条

- **`case "catalog"` 不能整个赋值 `shelf.data`** —— 档案是另一条事件推的，
  整个赋值会把它抹成 undefined，下一次 `drawShelf` 就在 `.length` 上抛。
  这个是 `input.mjs` 报"页面 JS 报错"抓出来的，画面上看不出来。
- **并行那条断言要只看自己灌进去的那几块**：夹具连的是一个真 agent，
  上一轮 `input.mjs` 起的活会把自己的工具行推过来。

## 0.58.0：「为什么卡死了」—— 查不出来才是那个 bug

用户说桌面端卡死了。查下来**它没卡**：那一轮 622 秒之后正常跑完了。
但整个排查过程暴露了真问题 —— 手上没有任何能定位它的数据。

### 结论先写在前面（别再重查一遍）

- **不是超时**：trace 里那一轮没有 `api_error`，一个请求真的跑了 622 秒。
  数据一直在断断续续地来，所以两个 SDK 默认的 600 秒 read 超时压根没响。
- **不是代理慢**：六条配置各量一遍小请求，4～31 秒，出字 69～279 字/秒。
- **不是输入大**：16k 输入、缓存没命中，qwen3.8-flash 5.6 秒答完。
- **不是"在想"**：同一个模型灌一道推理题，`thinking` 事件 0 条。
- **同样规模复现不了**：16.7k 输入 + 2670 输出 token = **36.6 秒**。
  所以那 622 秒是中转站当时抽风，一次性的。

**真正该修的是"事后查不出来"**，不是那 622 秒。

### 三处改动，都为了下次一眼能看出来

1. **turn 记 `ms` 和 `out`。** 原来只有 input/hit —— 一轮跑了十分钟，
   事后分不清是"说了很多"还是"接口就是慢"。
2. **`/cost` 显示每个任务里最慢的那一轮。** 不显示平均：一个任务九轮五秒、
   一轮十分钟，平均会把那十分钟摊没，而要查的正是它。
3. **桌面端等待时显示秒表。** 终端的 spinner 本来就带计时
   （`⠋ 思考中… (622s · esc 中断)`），桌面端只写"干活中" ——
   慢和死长得一模一样。超过 90 秒还多说一句"比平时慢，还在等"。

### 顺带：想的过程也报出来了

`run_turn` 多一个 `on_think`：openai 兼容那边接 `reasoning_content`，
anthropic 那边接 `thinking_delta`，都只报**累计字数**（跟 `on_tool` 一样按
`PROGRESS_STEP` 攒着报，一个 delta 一条事件会把前端灌死）。
状态行变成 `在想… 已经想了 3200 字`。

这次的模型没走这条路（0 条事件），但 DeepSeek 那类思考型模型会 ——
**想十分钟一个字不说**的时候，这是唯一的活体证明：数据一直在流，
连超时都不会响，而屏幕上什么都没有。

### `STREAM_TIMEOUT`

两个后端原来一个 `timeout` 都没传，吃 SDK 默认的 600 秒。现在写明白了，
并且能用 `FEIYU_STREAM_TIMEOUT` 改。**默认值原样保持 600，别想当然往小了调** ——
它量的是"两个 chunk 之间最多能静多久"，而实测过一轮 622 秒的请求
一直在吐字，调到三分钟会把本来会成功的请求掐死。

### 加 `on_think` 时踩到的

`run_turn` 的签名一变，**测试里所有手写的假 run_turn 全挂**（`tests/fakes.py`
和 `test_agent.py` 里四处 `def boom(on_text, on_tool=None)`）。
108 个 error，全是 `unexpected keyword argument`。
改后端接口之前先 `grep -rn "def run_turn"`。

## 0.58.1：一次专门的找 bug，五处

用户让"查一下有没有 bug"。ruff 只开会挑毛病的那几条规则（`F,B,E9,PLE`，
不开 UP031 那种风格项 —— 这个项目是刻意用 `%` 的），加上拿边角输入捅一遍新加的模块。

### 1. 一条单测被同名的另一条盖掉了（ruff F811）

`tests/test_tools.py` 里两个 `test_到上限就停`。后定义的把先定义的整个盖掉，
而**先定义那条里"截断必须标出来"的断言从来没跑过**（0.25.1 定的规矩）。
产品是对的，只是那条测试是死的。**同名的测试方法是静默失效**，
`unittest` 一声不吭 —— 值得把 F811 加进例行检查。

### 2. 档案的名字有两个来源 → 一行删不掉的东西

`profiles.load()` 的注释写着"名字以文件名为准"，代码却在走
`skills.parse()`（那个认 frontmatter 里的 `name`）。后果：
`我的档案.md` 里写 `name: 严厉评审`，面板上就出现一行「严厉评审」，
而 `remove("严厉评审")` 去删 `严厉评审.md`（不存在）→ **删不掉**；
`write("严厉评审", …)` 写进另一个文件 → **存到别处去了**；
两个文件写同一个 name 还会在清单里变成两行同名的。

**跟 0.56.1 那条删不掉的会话是同一类错**：一个东西有两个名字来源，
读写删三条路早晚对不齐。修法一样 —— 砍掉一个来源。

### 3. shift+tab 白做了一版

主循环有一道 `if not text.strip(): continue`（拦空 prompt 用的），而
`cycle_mode` 是不带正文的 op，**被静默丢掉**。前端发得好好的、后端一句话没说，
两边的单测各自还都是绿的（前端只验"发出去了"，后端根本没被调到）。
现在有 `NO_TEXT_OPS`，并且有一条用例反过来查：
凡是往 inbox 塞空正文的 op 都得在表里。**加不带参数的 op 时先看这张表。**

### 4. `/profile` 刷新了错的那份清单

`push_catalog()`（技能/工具/MCP）写成了 `push_profiles()` 该干的活，
档案那一页照旧显示切换前的状态。两个方法名长得像，推错了界面只是"没反应"。
加了一条按命令查"推哪份"的用例。

### 5. `/review` `/compact` 在桌面端是彻底静默的

`run_command` 不置 `busy`，而这两个会**真的调一次模型**（十几秒起）：
秒表不转、停止键不出现、那句"正在压缩…"又只走 stderr ——
用户看到的就是点了一下什么都没发生。现在置 busy（`finally` 清），
顺带 esc 也能打断它们了（`run_command` 跑在主线程上，`interrupt_main` 正好落在这儿）。

### 假警报两处，记下来别再查

- `ui.py` 的 `zip(cells, widths, aligns)`（B905）：三个序列上面都补齐到 `ncol` 了，安全。
- `providers.py` 没设 timeout 不是"没有超时"：两个 SDK 默认 600 秒。见 0.58.0。

### 夹具

`shoot.mjs` 加了档案的增删改一整圈（新建 → 改正文 → 存 → 删）。
**这条只能在 DOM 里验**：面板那几个动作各自要发对 op、发对名字，
而"存到了另一个文件上"这种错单测看不出来 —— 清单和详情用的是同一个名字来源才行。

## 技能包（0.59.0）

用户把一个 zip 丢进 `~/.config/feiyu/skills/`，里面是 `教/SKILL.md` 加四个
`XXX-FORMAT.md`。原来只扫 `skills/*.md` 一层，包一律看不见。

现在两种形式都认，**单文件那种一个字没改**：

- `skills/发版.md` —— 一个文件一份（原来就有的）
- `skills/教/SKILL.md` —— 一个目录一份"包"，旁边可以带附属文件

`SKILL.md` 这个名字跟 Anthropic 那套技能包一致，网上下的包直接能用。

### 附属文件不进正文

包里那些 `XXX-FORMAT.md` **只在读正文时报个名字**，要哪份走
`skill(name, file=…)` 单独取。全塞进正文的话，0.49.0 那个"不用的时候
只占一行目录"的好处就没了 —— 这份包的正文 9325 字，四个附属文件加起来还有 8 千多。

`skills.extra()` **必须把路径关在这个包自己的目录里**（realpath 之后比前缀）：
文件名是**模型**给的，而模型会读网页、会照着技能正文里写的东西走，
一个 `../../../.ssh/id_rsa` 就能把"读一份说明书"变成"读任意文件"。
跟桌面端那个 `pathguard.rs` 是同一条规矩，用例两个方向都盯着。

### zip 自动解开

技能包在网上多半以 zip 分发，丢进去就该能用 ——「先自己 unzip 一下」是个
没必要的台阶。`_unpack_zips()` 就地解成目录，**zip 原样留着**（不删用户的文件），
**目录已经在就不再解**（不覆盖你后来改的东西）。

三道闸：成员个数 `MAX_ZIP_FILES`、解压后总大小 `MAX_ZIP_BYTES`、
以及 **zip slip** —— 成员名写 `../../.bashrc` 就写到目录外面去了，
所以每个成员都要先算 realpath 再确认它落在目标目录里。用例专门盯着这条。
包里常见的那层同名壳（`教/SKILL.md`）会被剥掉。

### `digest()` 必须连子目录一起摘

**这条差点漏掉。** 包的正文在 `技能名/SKILL.md` 里，只摘顶层的话，
项目技能同意过一次之后包里的内容随便改都不会重新问 —— 0.49.0 那道信任闸就白设了。

## 0.60.0：三处优化，都是先量出来的

用户说"优化，可以用别的语言"。**跟 0.50.0 那次一样，Rust 不是答案** ——
本地工具在这个仓库上：list_files 4.8ms、search 9.9ms、read_file 1.5ms、
git status 6.7ms、bash 12.9ms，加起来还不到一次请求的零头。
拿三十天的 trace（525 轮、286 万 input token）和真实存盘量了一遍，
真正的大头在别处。

### 一、折叠是在赔钱（这条最值钱）

**账**：525 轮里重发了 286 万 token，缓存只命中 61%，剩下 110 万按全价算。
按模型分：kimi 82%、qwen 79%，而花钱最多的 deepseek-v4-pro 只有 46%。

**为什么**：前缀缓存的规矩是"改了第 k 条消息，k 之后的全部作废"，
而 `expire` / `age` 改的都是**靠前**的消息。拿一份 40 轮的历史实测：

| 动作 | 省下 | 下一轮花费 |
|---|---|---|
| `age` 折 30 条 | 10.9k token | **4.4 倍** |
| `expire` 折 3 条 | 4.5k token | **7.6 倍** |

也就是说 0.29.0 那句"省 token 要先省在历史上"，在会缓存的后端上是反的。
更糟的是每轮折一点 = 每轮把前缀作废一次。

**修法**（`_should_fold`）：登记照旧每轮做（免费），动手要满足任一条：
1. 这个后端**根本不缓存**（观察到的命中率 < `CACHING_HIT`）—— 那折叠纯赚
2. 上下文过了 `FOLD_AFTER`（0.5）—— 快撞窗口了，省地方比省钱要紧

**样本太少时先当它会缓存**：头一两轮本来就没有可命中的前缀，
按"不缓存"办的话会在会话最开头折得最狠，正好是最不该折的时候。
不折的时候 `_reads` 的登记**留着别 pop**，等紧张了一起折 —— 前缀只作废一次。

### 二、侧边栏解析 96 MB 只为了拿 40 行标题

`listing()` 把每一份存盘整个 `json.loads` 一遍。40 份长会话实测 **131 ms**，
而它在每次新建 / 切换 / 删除会话时都要跑。

每份存盘旁边写一张 `<id>.card.json`（几百字节，只有标题/模型/条数/时间）。
**131 ms → 0.5 ms。**

卡片是**派生数据不是第二份真相**：没有、坏了、比存盘旧，都退回读整份。
两个坑：`_prune` 数上限时要把卡片排掉（不排的话一份顶两个，实际只留 20 份），
删存盘要把卡片一并带走别留孤儿。

### 三、冷启动一半花在 prompt_toolkit 上

`ui.py` 顶上一句 `from prompt_toolkit.utils import get_cwidth` 把整个包拉进来，
53ms。而只有交互 TUI 用得上它 —— `feiyu "任务"`、管道、`--serve`（桌面端起的就是它）
全程可能一次都不画输入框。改成用到时再 import：**0.13s → 0.09s**。

`get_cwidth` 没有自己实现：算显示宽度要处理组合字符和零宽，
而这是这个项目栽过三次的地方（CLAUDE.md 开头那条）。

### 量过但**没动**的

- **两个 SDK 的 import**：`openai` 319ms、`anthropic` 703ms，是启动最大的一块。
  但它们**已经是按需 import 的**（只 import 你实际配的那种）。再往下就得把
  客户端构造推迟到第一条消息，代价是配置写错了要等到那时才报 —— 不划算。
- **工具层**：全部加起来 20ms 级，重写成 Rust 是把 20ms 优化成 5ms。这话第二次写了。

## 0.60.1：界面四处，全是量出来的

用户说"界面也可以优化一下"。**先量再改** —— 眼睛看"还行"的地方量出来是错的，
看着"有点乱"的地方往往说不清哪儿乱。

### 一、窗口缩到最小就看不见字了

桌面端 `minWidth: 720`，而窄屏断点在 `46rem = 736px` —— **720 到 736 这一档
是坏的**：侧栏变成 `position: fixed` 浮在内容上面，而 `.sheet` 照旧从 x=0 铺开。
量出来正文起点 x=24，正好压在侧栏（0–240）底下，而且没有任何地方能把它关掉。

修法两条：进入这一档时**自动把侧栏收起来**（`watchNarrow()`，跟 CSS 那个断点
同一个数），拉回来时恢复成你原本的选择（**这次自动收起不进 localStorage**）；
浮着的时候加一层遮罩，点旁边就收起。

### 二、代码块画了第二条竖线

meta-rail 那条纵线在 x=416，`<pre>` 自己的 `border-left` 在 432 —— 两条发丝线
隔 16px 并排。**0.50.6 给「你」那一块修过一模一样的毛病**，代码块这儿漏了。
去掉 `<pre>` 的边，留缩进，靠 rail 撑结构：一页只有一条纵线。

窄屏那档顺带把 `.stream::before` 关掉 —— 那时候标签是堆在正文上面的，
没有 rail 那一列了，再画就是一条贴着左边、什么也不标的边框。

### 三、确认卡四个按钮一样重，而且蓝色做了字

`.btn--primary` 原来是蓝边蓝**字**，这违反这套系统自己的头一条规矩
（「IBM Blue 只做线和面，永远不做字」）。改成蓝底白字 —— 蓝当面，合规，
顺带这张"停下来看我"的卡终于有了主次。

`拒绝` 从红框改成中性框：**红留给真的会丢东西的动作**，而"拒绝"是最安全的
那个选项，染成红的会让人以为按了要出事。现在卡里的红只剩左边那条杠和 diff 的减号行。

### 量过之后**没改**的

- 正文列没有偏：sheet 240/1040，stream 328/864，左右各 88，是居中的。
- 表格有收尾线（`td` 上的 `border-bottom`），我第一眼看错了 —— `table` 元素
  自己没有边不代表表格没有收尾。
- 空态没加文案：0.50.5 用户明确要求删掉过。

`shoot.mjs` 补了两条：720/760 两档量侧栏宽度和正文起点；量 `<pre>` 有没有
`border-left`。**这两条都是"看着还行"但量出来是错的那种**。

## 设置面板（0.61.0）

原来一堆东西只有斜杠命令碰得到：风格、旁活模型、沙箱、校验命令、权限规则、
长期记忆 —— 界面上完全够不着。

「技能与工具」改名成**「设置」**，多一个「通用」页签排在最前，八项：
权限模式 / 回答风格 / 模型 / 旁活模型 / bash 沙箱 / 校验命令 / 权限规则 / 长期记忆。
沿用面板已有的两栏：左边选哪一项，右边改。

### 改动一律走斜杠命令

界面上按一下 = 发一条 `/mode auto`，**跟在终端敲是同一条代码**。
不另写一套"设置怎么保存"，也不用再想"面板改了要不要重拼系统提示"——
那些 `_mode` / `_style` / `_helper` 早就处理好了。
服务端跑完把 `settings` 重推一遍，界面不自己猜结果。

`push_settings()` 只出**当前值和可选项**，不出任何"怎么改"的逻辑。

### 加完之后查出来的三个 bug

1. **`/memory rm 1` 不是真语法**（真的是 `forget`）。记忆那一栏的「删掉」
   按下去只回一句"未知用法"，界面上毫无反应 —— 而两边各自的测试都看不见对方：
   前端只验"点了发得出去"，后端根本没被调到。

   所以有了 `TestSettingsCommands`：**直接从 `app.js` 里正则扒出所有
   `tune("…")`，一条条真喂给 `_dispatch`**，落到"未知命令 / 未知用法"就算挂。
   加新设置项时不用改那条用例，扒出来的自动多一条。回退验证过。

2. **斜杠命令的报错在桌面端一律显示成普通提示。** `run_command` 把 ui 的事件
   整个 `events.paused()` 掉，再一律按 `level="notice"` 发出去 ——
   `/mode 没这个模式` 跟切换成功长得一模一样。等级本来就报过，只是被扔了。
   新增 `events.capturing`（挡住往外报，但把最高那一档留下），
   `Server._say()` 拿它发。**跑完要把捕获器摘掉**，不然下一轮的 ui 输出
   会被这个过期的收集器接走（用例盯着）。

3. **面板底下那两行说明说错了话**：设置页上写着"开关记在 enabled.json"、
   "关掉的东西既不进它的上下文" —— 那一页一个开关都没有。改成跟着页签走。

顺带：模型清单为空时不再摆一排点不动的选项，直接说去终端 `/key add`
（密钥的增删刻意不给桌面端，见 0.52.0）。

## 复古主题（0.62.0）

**主题只换调色板和记号，布局和字体一个不动** —— 这是这套系统对"主题"的定义
（终端 0.43.0 定的，桌面端照搬）。换字体会动到 24px 基线和所有量宽度的地方，
那不叫换主题，叫换一套设计。

配色取自用户自己那套 Anthropic 暖色（`#D87756` 系），不另造一套：
泛黄的纸 `#fbf6ea`、暖墨 `rgba(43,32,22,α)`、赭色 `#c0562c`。
**墨色照旧只有一个色相构造式**，所以"只有一个色相"那条在这个主题里也成立。
`--accent` 换成赭色之后，「只做线和面、永远不做字」照旧管用 ——
那条约束的是用法，不是具体哪个颜色。

### `--plus`：新加的一个 token

diff 的新增行原来直接吃 `--accent`。默认主题下是 IBM Blue，看着没问题，
但复古主题里 accent 是赭色、alert 是砖红 —— **加号减号糊成一片**。

拆出 `--plus`（默认还是那个蓝，light 主题一个像素没变；复古里是墨绿 `#3f6b5f`）。
顺带这也把一处既有的违规修了：拿 `--accent` 给 diff **上色**本来就是"蓝色做字"。

`shoot.mjs` 现在**每个主题都量一次加减号的 RGB 距离**（阈值 90）——
眼睛看"都是红的"说不清差多少，量得清。`tests/test_ui.py` 那边有同一条不变量的
终端版，另外加了一条"每套主题的键都得配齐"：`set_theme` 是 `_RGB.clear()`
之后整份换的，少一个键要等真画到那种东西时才 KeyError。

### 桌面端的主题存在哪

localStorage，跟侧栏收放、置顶一样是**这个窗口自己的偏好**，不进协议。
**跟终端的 `/theme` 刻意不共用一个值** —— 同一个名字在两处指的不是同一套东西
（那边换的是 ANSI 配色和记号），共用只会互相踩。设置面板里那一项标了 `local: true`，
不依赖服务端的 `conf`，所以连不上的时候也能改。

### 夹具又骗了我一轮

`shoot.mjs` 没清 localStorage（`input.mjs` 早就清了，这边漏了），
于是上一轮切成复古会留到下一轮，报"换了主题颜色却没变"。
**Chrome 的 profile 跨次保留**这条 CLAUDE.md 里早写过，只是当时只改了一个夹具。

## 在设置里加改模型配置（0.63.0）

用户要在设置的「模型」里能加 key。**这跟 0.50.4 那条"密钥的增删只在终端做"是冲突的**，
所以先说清楚为什么现在可以：

那条规矩是给**网页端**定的 —— 它 HTTP + WebSocket **听在 127.0.0.1 上**，
同机任何进程、任何用户都能连（0.50.1 那三道门就是为它设的）。
而 0.52.0 换成桌面端之后，前后端走的是 Tauri 的 `invoke`/`emit`，
**根本不监听端口**，外面够不着。那条理由对它不成立。

### 两条闸，一条都不能少

**一、`--own-window`。** 桌面端起子进程时传这个标志，devserver 不传；
`Server.trusted` 认它。写密钥的 op 只对 trusted 开，devserver 那条路一律拒 ——
它还是听在回环上的，0.50.1 的威胁模型对它照旧成立。
**这个标志是"我们之间没有网络"的凭据，别给别的东西复用。**

**二、key 只进不出。** `push_models()` 只发一个 `has_key` 布尔，
**已经存下的密钥一个字节都不出去**。所以前端就算被人拿下也读不走 ——
放开的只是"写"，没有放开"读"。用例直接断言整段输出里不出现那个 key，
夹具那边也断言清单 JSON 里没有 `key` 这个字段。

### 「没带 = 不动，带了空 = 清掉」

只想改个模型 id 的请求把价格和上限一起抹掉过一次。现在 `merged()` 分这两种：
字段不在 payload 里就保留旧值，在（哪怕是空）才算改。
`key` 是同一条规矩的特例说明版：服务端不发它，表单没法原样送回来，
**所以留空一律等于不动**。

顺带：加完第一条就直接 `use_model` 用上（不然还要再去 /model 选一次，
0.47.5 那条同样的形状）；删掉正在用的那条要换一条还在的，
删光了把 `backend` 撤掉（`ready` 是从 backend 推出来的只读属性，赋值会 AttributeError）。

`/key` 这条**斜杠命令**照旧在 `TERMINAL_ONLY` 里，但理由换了：不是安全，
是它逐项问的那套 prompt_toolkit 在 `--serve` 里根本跑不起来。

## 额度（0.64.0）

用户要在底下看到"还剩多少钱、用了多少"。**能做，但只对认得出来的接口能做。**

`/cost` 算的是**我们自己按你填的价格估的**花费；`feiyu/credits.py` 问的是
**接口那边的账**。两个数不是一回事，界面上也分开显示（状态栏 `¥花费 · 余 $9.78`）。

### 只查认得出来的，认不出就明说

`_BY_HOST` 按 host 认：openrouter / deepseek / moonshot / siliconflow，
其余试一次 one-api 那套（一堆中转站抄的是 OpenAI 早年的计费接口）。
**OpenAI 和 anthropic 的普通 key 本来就查不到余额**，那是账号后台的事 ——
认不出就返回一句能照着做的话，**绝不猜，绝不显示一个编的数**。

这跟"不要写内置价格表"不冲突：那条禁的是**会过期的事实**（模型 id、单价），
这张表记的是**接口形状**，跟 `lsp.SERVERS` 一类。真变了是整条失效显示"查不到"，
不会像过期价格那样给一个看着对其实错的数。

### 三条硬规矩

- **key 只发给配置里那个 host**（`_same_host`，闸放在 `_get` 里，
  不指望每个查法自己记得）。查个余额把密钥发到第三方去，比查不到糟糕得多。
- **绝不拖慢一轮对话**：`agent.refresh_credits()` 丢后台线程，
  查不到就当没有。**要带 `then` 回调重推一次状态** —— 不带的话调用方紧接着
  推的那份状态里还是上一次的数，界面永远慢一拍。
- **同步的地方只准读缓存**（`look(cached=True)`）：`/cost` 是同步的，
  实测走代理偶尔要满八秒（跟 0.58.0 那次 622 秒同源，这个代理会抽）。
  让终端为一个锦上添花的数字卡八秒是本末倒置。

### 失败要另算，而且短得多

一开始成功失败共用 TTL=60s，结果**一次网络抖动让余额消失一分钟** ——
而且同一个账号下六条配置共用一个缓存位，头一条抖了后面五条全吃那份失败。
`FAIL_TTL = 8s`。用例断言 `FAIL_TTL < TTL / 4`。

### 什么时候刷

开局一次、每轮跑完一次、状态栏那个数点一下强刷一次（`fresh=True` 绕过缓存）。
查不到时状态栏**整个不显示** —— 摆一个"—"在那儿只会让人以为坏了，
理由留在 title 上。

## 机器人（0.65.0）

用户要一个 Hermes / Grok bots 那样的机器人模块，**明确说了不要放在设置里**。
所以它在侧栏跟「会话」平级（两个页签），不是设置面板的第五页 ——
这个位置本身就是需求的一部分：机器人是你**每天用来开对话**的东西，
不是你配一次就忘的东西。

`feiyu/bots.py`，`~/.config/feiyu/bots/<名字>.md`，frontmatter 认
`description` / `model` / `mode` / `style`，正文是人格设定。

### 跟配置档案的界限（这一节是这个功能最要紧的部分）

两个都是"往系统提示里塞一段人格"，**没合并，因为用法完全不同**：

| | 档案（0.57.0） | 机器人 |
|---|---|---|
| 是什么 | 手动切的人格 | 一键开一段新对话 |
| 带什么 | 人格 + 可选的模型 | 人格 + 模型 + 权限模式 + 风格 |
| 在哪儿 | 设置里的一项 | 侧栏跟"会话"平级 |
| 什么时候生效 | 当场换掉现在这段 | **开一段新的** |

**人格那个位置是共用的**（`agent._persona()`）：一段对话要么套着机器人、
要么套着档案、要么什么都不套。同时套两个人格只会互相打架，
而"你是谁"这个问题只该有一个答案。所以 `start_bot()` 里开机器人要
**把档案清掉**，`set_profile()` 反过来也一样。

### 一段对话记得自己是哪个机器人开的

存进 session 的 `bot` 字段，`--resume` 或者从侧栏切回来时照旧是那个机器人。
不存的话切回去人格就没了，**而那段对话前面的每一句都是按那个人格说的** ——
半段海盗船长半段普通助手，比一开始就没有人格更糟。
机器人被删掉了就当没有（跟 `profiles.current()` 认不出名字回空串同一条）。

### 名字一律用文件名

跟 0.58.1 那条档案的教训一模一样，这次一开始就按对的做：`_parse(text, stem)`
强制 `name = stem`，frontmatter 里的 `name` 只当注释。切换、保存、删除全按文件找。

### 「开一个机器人」= 新会话 + 套人格，顺序不能反

`serve.start_bot` 先 `new_session()` 再 `agent.start_bot()`。反过来的话
`new_session` 里的 `reset()` 会把刚设上的 bot 连同系统提示一起冲掉 ——
表现是"点了机器人，开了新对话，但人格没生效"。

### 版本号显示成旧的：这次是 `install.sh` 自己造出来的

0.37.1 记过「`sed -i` 改完 `__init__.py` 紧接着跑，`.pyc` 和源码 mtime 同秒
→ Python 当字节码是新的」。这次踩的是它的变体：bump 完**已经**删过一次
`__pycache__` 了，可后来跑的 `install.sh`（`pip install -e`）又把它编了一遍，
而 pip 会碰源文件的 mtime —— 于是同秒又撞上，界面上和 `feiyu --version` 全是旧版本号。

**`__pycache__` 要在 `install.sh` 之后清，不是之前。** 顺序反了等于没清。

### 夹具这轮踩到的两条

- **`JSON.stringify(数组).includes('"start"')` 永远不匹配** ——
  序列化之后引号是转义的（`\"start\"`）。要先 parse 再看字段。
  这条骗了我一轮"事件没发出去"。
- **合成事件要在点击之后灌**：点机器人会让服务端真推一份 `bots` 回来，
  先灌的话会被真推送盖掉（CLAUDE.md 里记过两次的老坑，这次是第三次）。
- **机器人那栏复用了会话的 `.talk` 行样式**，于是 `input.mjs` 里那些全局
  `document.querySelectorAll(".talk__title")` 一下多数出三行来，三条既有断言
  同时变红 —— 看着像"侧栏坏了"，其实是选择器太宽。侧栏现在有两份列表，
  **量会话那栏一律带上 `#talks` 前缀**。
- 跑 `shoot.mjs` 的那份 `XDG_CONFIG_HOME` 要**同时**备好技能（含一份关掉的）、
  两份以上的档案、`enabled.json` 里关掉一个工具 —— 少哪样哪一屏就报"不对"，
  而那不是产品的毛病。`enabled.json` 的形状是 `{"skills": [...], "tools": [...]}`
  （按类分，记的是**关掉**的那些）。

## 0.65.1：一次专门的找 bug + 按证据的优化

用户让"查一下有没有 bug，再优化一下"。查法：拿畸形消息灌协议、把每条斜杠命令
配五种垃圾参数跑一遍、再翻三十天的 trace（565 轮、544 次工具调用）。

### 1. 一条畸形消息能带走整个进程

`{"op":"bot","do":"save","row":[1,2,3]}` → `{**row.get("row")}` TypeError →
**整个 agent 没了**，连同后台命令、语言服务器、MCP 进程，用户那边只看到窗口不响应。

0.50.1 把 reader 线程整个包起来过，但 `run()` 那个循环没有 —— 而它跑的才是
真正干活的那几支。现在循环里除了 `KeyboardInterrupt` 还接 `Exception`：
报出去、清 `busy`、接着转，理由同 `trace.write` / `events.emit`。

**新加一支 op 时不要指望自己写对**：那道 `except` 才是保证。

### 2. read_file 读不到的文件，说的是别的错（占它全部失败的 85%）

日志里 `read_file` 失败 27 次，其中 **23 次是同一个不存在的 `.png` 被反复读**。
根因：`_tool_read_file` **一个存在性判断都没有**——
文本文件靠 `read_bytes()` 抛出去（模型看到一句裸的 `FileNotFoundError`），
图片更糟，先走进 `_read_image` 才失败，于是被归成「图片打不开」。
模型读不出"这文件根本不在"，就只会换着参数接着试。

`_missing_note()` 放在最前面：不在就说不在、是目录就说是目录，
并且**列出同一个目录里最像的几个名字**（`difflib.get_close_matches`）——
只说"没有"的话它会接着猜（跟 `edit_file` 的 `_nearest` 是同一条）。

**一次白跑的工具调用 = 一个整轮往返**（本仓库中位 4.1 秒 + 整段历史重发）。
这类修补比任何毫秒级优化都值钱。

### 3. `/compact` 在空对话上照样花钱

`compact()` 不判空，一敲就真发一次请求，**还拿"关于空无一物的摘要"把历史换掉**，
之后每轮都带着那句废话。现在按**消息**判空（不按 `transcript()` 的渲染结果判——
thinking 和纯图片那几种块不落字，拿它的空当"没东西"会把真该压的放过去）。

### 4. 接口报错的提示会指错方向

`_API_HINTS` 是拿 `"401" in text` 认的，而报错里带着 request id、余额、时间戳 ——
`request id: 20260828401547313` 里那个 401 就让它说"key 过期了"。
改成认状态码本身（先问 SDK 的 `status_code`，再退回 `Error code: (\d{3})`），
措辞类的（余额不足、超上下文）单独一张表、排在前面。

**顺带：这套提示桌面端一直没有**，`handle_prompt` 的兜底原样甩 JSON。
0.26.0 只修了终端那一半 —— 加前端时记得问一句：这条路终端有的东西它有吗。

### 优化：量完之后只动了一处

- **每轮的 Python 开销全在毫秒以下**（170 KB 历史上：`transcript()` 0.13ms、
  `tool_schemas()` 0.012ms、`_refresh_system()` 0.006ms、`without_images()` 0.09ms）。
  跟 0.50.0 / 0.60.0 两次的结论一样：**这里没有可优化的东西**，别再查第四遍。
- **启动 60.6ms → 55ms**：`concurrent.futures` 改成用到时才 import（它会把
  `logging` 一起拉进来）。544 次工具调用里只有 37 次走并行，多数运行一次都用不上。
- **量过但没动**：`urllib.request` 是剩下最大的一块（9.2ms，`credits` 和 `tools`
  都要），但 `tools._opener` / `_NoLocalRedirect` 是 SSRF 那道闸、而且测试直接
  patch 它 —— 为 9ms 把安全代码改成惰性构造、再改一批测试，不划算。
- 缓存命中 65%（0.60.0 时 61%）。按轮次看：第 0 轮 52%、第 2 轮 87%、第 6 轮往后
  掉回 53%。掉回去那段**不是折叠造成的**（三十天里 `age` 只触发 11 次、`trim` 3 次），
  多半是接口那边的缓存 TTL，我们这边没有可动的旋钮。

### 找 bug 的夹具进库了：`dev/fuzz_serve.py`

```bash
python dev/fuzz_serve.py                 # 两组都灌
python dev/fuzz_serve.py --only ops      # 只灌畸形 op
```

两组输入：27 条畸形 op（类型不对、少字段、`../` 路径、半截 JSON），
以及**每条斜杠命令 × 五种垃圾参数**（空、不存在的名字、`../`、多参数、500 字）共 110 条。
判据只有一条但很硬：**跑完还答得上 `status`，退出码为 0**。
回退验证过 —— 把 `serve.py` 那道 `except` 去掉，它立刻报"死在这条上"。

**改协议、加 op 之后跑它一次**，跟改前端跑 `input.mjs` 是同一个位置的事。
它把 `XDG_CONFIG_HOME` 指到临时目录，所以没有模型配置、`/compact` 和 `/review`
会早退 —— 别改成连真配置，那两条会**真的调模型**。

## `@路径`（0.66.0）

用户在正文里写 `@feiyu/ui.py`，这份文件在**消息发出去之前**就读好、附在这句话后面。

起因是日志：`read_file` 是三十天里第二高频的工具（120 次），而**用户自己已经知道
要看哪个文件**的那一部分，本来不该再花一整轮往返（本仓库一轮的中位数 4.1 秒 +
整段历史重发）。跟 0.65.1 那条「一次白跑的工具调用比任何毫秒级优化都贵」是同一笔账，
只是这次省的是**本来就不该发生的**那一次。

### 认不认，只问文件系统

`@property`、`@media`、`a@b.com`、`@2x.png` 在正文里到处都是。想用正则分辨
「这看着像不像路径」是分辨不出来的 —— 判据只有一条：**它在工作区里真的存在吗**。
不存在就原样留着当普通文字，一个字都不动。

两个非要写下来不可的坑：

- **不能只剥结尾的标点。** 中文里没人打空格：`@feiyu/ui.py，第 200 行` 一路吃到
  「行」字，结尾那个字不是标点，剥不动。改成在原文里找每一个断点、按长到短挨个试。
- **断点这条件同时挡住了误判。** 光按前缀试的话，仓库里只要有个文件叫 `p`，
  `@property` 就会匹配成 `p`。要求「匹配到的那一截后面必须是断点」才算数。
- **`..` 和结尾是 `.` 的一律不认。** 按断点剥前缀会剥出 `.` / `feiyu/.` 这种残片，
  而 `@../../etc/passwd` 剥到只剩 `.` 的时候，那个 `.` 解析出来正好是工作区根目录
  —— **越界的写法反而"匹配上了"**。越界本身由「解析完必须落在工作区里」挡住，
  跟 `Toolbox._resolve` 同一道闸。

### 贴过去的必须记进 `files.Watcher`

不记的话模型下一步 `edit_file` 会被「你还没读过这个文件」挡回去 —— 它确实看过了，
只是没经过工具。用户把内容摆在它面前，反而让它做不了事，这是这个功能最容易
自伤的一处。

### 预算按份分，不是先到先得

一句话里指三个文件是常事。先到先得的话第一个（`serve.py` 981 行）就把
`MAX_CHARS` 吃光，后两个一个是零头、一个一个字都没有 —— 而这三个是用户
**一起**指出来的。现在每个拿 `left // (keep - i)`：小的用不完，余下的自然滚给后面。

**贴不下几个要先算清楚，不能边贴边判。** 边判的话前几个因为份小被跳过，
分母跟着变小，后几个反而贴上了 —— 用户先说的那个没了、最后说的那个在。
所以先算 `keep = MAX_CHARS // MIN_SHARE`，砍的一律是**后面**的。

### 补全一次只扫一层

给整棵树建索引再模糊匹配当然更聪明，但那要么每按一个键走一遍 `os.walk`
（大仓库上几百毫秒），要么维护一份会过期的缓存。按目录 `scandir` 永远是常数时间，
手感也跟 shell 补全一样。大小写不敏感，点开头的要自己先打个点才出来。

### 两个前端共用同一份候选

`mentions.complete()` 一份，终端的 `ui._Input` 直接调，桌面端走 `op: "paths"`
问服务端要。前端**不自己列目录** —— 「哪些目录跳过」「点开头要不要出现」这种规则
只该有一处，两边各写一套的结果就是两边补出来的东西不一样。

`paths` 走 reader 线程、不进 inbox：只读一层、不碰 agent 状态，排进 inbox 的话
补全菜单就成了「打完一句才弹出来」，等于没有。

桌面端那边还要听 `selectionchange`（挂在 `document` 上，元素级的 WebKitGTK 未必有）：
`@` 补全看的是"光标之前那一段"，而点一下、按左右键都不触发 `input`。

### 顺带：pty 夹具从 0.58.0 起就没跑通过

`dev/ui_runner.py` 的假后端 `run_turn` 没跟上 0.58.0 加的 `on_think`，
于是夹具**一句话都跑不完**（"got an unexpected keyword argument"）。中间七个版本
都没发现，因为夹具挂了只是少几张图，没有任何一条断言会红。

现在有一条了：`tests/test_ui.py::TestFixtureKeepsUp` 用 `ast` 读夹具里那个
`run_turn` 的参数名，跟真后端的签名对一下。**给 `run_turn` 加参数时它会红。**

夹具本身也改了两处：

- 打正题之前先敲 `@c` 探一次补全菜单，拍完 ctrl-u 清掉。
- **正题那一串必须 gate 在探完之后**（`elif sent_prompt and probed and keys`）。
  不 gate 的话 `idle` 落在 0.05~0.6 之间时走的是打字那条，菜单还没拍就被冲掉了 ——
  第一版就是这么拍出一张"已经提交完"的图的。
- `FEIYU_UI_PROBE` 换探什么，用来跟斜杠菜单对照（两者共用同一个 `CompletionsMenu`，
  都是盖住底边框的，这是布局本来的样子，不是 `@` 引进的毛病）。

### 三个夹具都跑了

`dev/fuzz_serve.py` 加了五条 `paths` 的畸形输入；`desktop/dev/input.mjs` 加了
七条 `@` 的按键回放（85/85）；`dev/ui_drive.py` 现在四张图。

## 0.67.0：桌面端量一遍 + 一次专门的找 bug

用户让"优化美化一下桌面端，再整体检查有没有 bug"。查法跟 0.65.1 同一套，
但换了没覆盖过的面：**前端灌畸形事件**、**把每个能点的东西点一遍**、
**把每份持久化配置写坏**。所有视觉改动都先量了再动。

### 美化那半边：四处，全是量出来的

拿 CDP 在真页面上量宽度、行高、点击目标和对比度（`/tmp` 里的一次性脚本，
判据都记在下面了，重量一遍就行）。

**一、两档灰都在 AA 之下。** `--muted` 是 `rgba(0,0,0,.5)`，压在纯白上是
`rgb(128,128,128)`，**3.98:1**；`--faint` 是 `.32`，**2.23:1**。而这半边把它们
用在了 9–10px 的时间戳、条数、`Ctrl N`、图说里的路径、还有会话行上那个 ×。

**博客那边的注释是错的**：`~/Myproject/src/styles.css` 写着「--muted 对白纸是
10.4:1，远在 AA 之上」—— 那个数不知道从哪来的，量出来就是 3.98。这套配色是
1:1 从博客继承的，所以这次是**刻意的偏离**，按量出来的数往下压一档：

| | 原来 | 现在 | 白纸 | panel 上 |
|---|---|---|---|---|
| `--muted` | .50 | **.56** | 4.94:1 | 4.77:1 |
| `--faint` | .32 | **.44** | 3.24:1 | 3.18:1 |
| 复古 `--muted` | .54 | **.65** | 4.86:1 | 4.59:1 |
| 复古 `--faint` | .34 | **.52** | 3.30:1 | 3.18:1 |

四档墨色的结构、「只有一个色相构造式」那条规矩一个没动，改的只有两个 α。
另外把**要读的数**从 `--faint` 挪到 `--muted`（会话行的日期条数、图说那条路径）：
10px 的字在 3.18:1 上过不了正文的 4.5，而它们是数不是记号。
**再动先把这几个数重新量一遍，别凭手感调。**

**二、设置里一条长值把行高撑到 2.2 倍。** 「校验命令」那格是
`overflow-wrap: anywhere`，值折成三行，行高 41px → **89px**，整张清单的节奏塌了。
改成一行封顶加省略号（完整的值在右栏那个输入框里，鼠标停上去也有）。

**三、删对话那个叉量出来 17×15。** 低于 24px 的最小可点边，触控板上要瞄准。
记号大小不动，靠 `min-width/height` 把盒子撑到 24。

**四、390px 下状态栏溢出 40px，版本号被剪掉。** 两边都能压缩时
`overflow: hidden` 剪的是最右那格 —— 而右边一排（模式/上下文/花费/余额/版本）
每格都是要看的数，工作目录才是那个截了也认得出来的。所以 `.status__left`
`flex: 1 1 auto`、`.status__right` `flex: 0 0 auto`。
顺带：**flex 子项默认 `min-width: auto`**，不写 `min-width: 0` 它就不肯缩到
内容宽度以下，`text-overflow` 一辈子用不上 —— `#s-cwd` 原来就是这样。

**量过但没改的**：前端渲染没有可优化的东西 —— 400 条流式分片 68ms、
200 次工具调用 64ms、侧栏 200 条会话 10.7ms、搜索每敲一个字 3.2ms、
10 万字的一段 markdown 0.4ms。
`mentions.complete()` 在真实目录上 0.01–0.04ms（5 万个文件的目录里 22ms，
那是病态情形，不值得为它把按目录 scandir 改复杂）。

### 找 bug 那半边：七处

**1. 命名管道能把整个进程挂死，而且一个字都不吐。**（最要紧的一处）

`read_file` / `write_file` 打开一个没有另一端的 FIFO 会**永远阻塞**：界面上什么
都没有，`STREAM_TIMEOUT` 也不响（那条管的是模型的流，这儿还没发出去）。
`edit_file` 更糟 —— `is_file()` 对 FIFO 是 False，于是它说"不存在，用 write_file"，
把模型引到那条一样会挂住的路上（跟 0.65.1 那条 read_file 说错了错是同一个毛病）。
0.66.0 的 `@路径` 也走这条，而它跑在**发消息之前**，表现是"按了回车什么都没发生"。

`files.odd_kind()` 一处判完，四个入口都接上：目录 / 命名管道 / socket / 设备文件。
用例跑在**子进程里带 20 秒超时** —— 没修好的话它是卡死不是失败，会把整个测试跑挂。

**2. 前端一条畸形事件能把那一条处理到一半就断。** `inbound()` 只 catch 了
`JSON.parse`，`handle(ev)` 抛出去没人接。灌 102 条畸形事件量出来**十处**
（`ev.rows || []` 挡不住 `{}`，数组里混一个 null 也抛）。
两层都补：`rowsOf` / `textsOf` 把清单coerce 干净，`inbound` 外面加一道 catch ——
**那道 catch 才是保证**，理由跟 0.65.1 服务端那道 `except` 一模一样。

**3. 一份写坏的存盘能把整份侧边栏带走。** `saved_at` 是字符串的话
`listing()` 在 `time.localtime()` 上抛 TypeError，**一条历史都列不出来**。
`_num()` 兜住类型和量级（`1e300` 是 OverflowError）；`_read()` 现在要求
`messages` 真的是个列表 —— 只判真值的话 `{"messages":"坏了"}` 会被
`restore` 直接装进 `backend.messages`，下一次请求就是 400，而那时候已经离得很远了。

**4. 合法 JSON 不等于你要的那个形状。** `json.load` 只在语法坏掉时抛 ValueError，
`null` / `[]` / `123` 都读得出来，然后 `.items()` / `.get()` 抛 AttributeError ——
而各家的 `except` 只接了 OSError 和 ValueError。三处：`keys.load()`（**在启动
路径上**，表现是一敲就崩，跟"密钥没了"分不出来）、`permissions.load()`、
`sandbox.level()`。`tests/test_broken_config.py` 把这条规矩钉住了。

**5. 会话标题全长成一个样。** 侧边栏里连着十五条
「看看这张图 我上传了这些文件（相对工作区）： -...」，认不出是哪一次。
`_first_words` 把整条消息拍平了截 60 字，而发出去的那条后面常常挂着机器拼的东西
（桌面端的附件说明、`@路径` 读好附上的整份正文，都是空行隔开接在后面的）。
改成**只取第一段，遇空行就停**。老存盘里已经存下的标题不会变（卡片是派生数据，
下次那段对话再存一次就好了）。

**6. pty 夹具从 0.58.0 起就没跑通过。** 见 0.66.0 那一节。

**7. 出图那屏的断言是 `sleep(900)`。** 图是异步读回来的，机器一忙就不够 ——
表现是断言拿到 `[]`，看着像"出图坏了"。改成轮询等它落地。
`input.mjs` 里那句「要等 agent 回话，不是等 DOM」是同一条。

### 新夹具：`desktop/dev/fuzz_ui.mjs`

```bash
node desktop/dev/fuzz_ui.mjs <页面地址>
```

`dev/fuzz_serve.py` 的另一半：那边灌服务端的 op，这边灌前端的事件。
102 条畸形事件（每种 kind 各来一条光秃秃的、一条全 null 的、一条类型全反的，
外加数组里混垃圾的十几条），判据三条：**没有一条把 `handle()` 抛出来**、
**`inbound` 那道 catch 自己也要验**（把 handle 换成必抛的走一遍真实入口）、
**灌完还画得动状态栏且没有未捕获异常**。**改前端、加事件之后跑它一次。**

顺带记一条查法：把界面上每个能点的东西点一遍（190 个，`button / [role=button] /
.talk / .shelf__row / .side__tab / .opt / .act / .sw / a[href]`），这轮一个都没抛。
值得再用，但没必要进库 —— 它跟 `input.mjs` 覆盖的是同一批元素。

### `@` 的一处已知取舍（不是 bug，别再查一遍）

`@` 附上的正文在**用户消息**里，而 `history.slots()` 只认工具结果 ——
所以它不会被 `age` / `trim` / `shrink` 折叠，会跟着每一轮一起重发。
两条理由让它不值得改：一是总量封在 `MAX_CHARS = 20000`，比单条工具结果的
`_MAX_OUTPUT = 30000` 还小；二是它落在历史很靠前的位置，正好在**接口缓存的
前缀里**（0.65.1 量过：第 2 轮命中率 87%），重发的是缓存价。
要给 history 加第二种 slot 的话，先把这两条重新量一遍。

## 0.67.1：窗口里的东西不该跑到终端上

用户拿截图来提的：「我在 desktop 的对话，终端也跟着同步显示」。

根因在 `start_agent`：`.stderr(Stdio::inherit())`。而 `feiyu --serve` 把**人看的
那一整套 ANSI 输出**重定向到了 stderr（stdout 只放协议，见 `serve.py` 顶上那段），
于是从终端起 `feiyu-desktop` 的话，窗口里聊的每一句终端里跟着又演一遍。

**不是吞掉，是落到文件里**：`$XDG_STATE_HOME/feiyu/desktop.log`，跟 trace 那份
JSONL 同一个目录、同一档权限（0600 —— 里面有命令原文和文件路径）。攒到 4 MB
从头写：换一次工作台就重起一个 agent，一直追加下去这份文件不封顶。
逃生门 `FEIYU_DESKTOP_STDERR=1` 让它照旧回终端。

**devserver 那条路不改**：它本来就是在终端里跑给夹具用的，那儿要看得见。

### 桥接自己那几行也得一起走

`send()` 里每收一条前端指令打一行 `→ 某个 op`。原来一次用户动作一行，还好；
**0.66.0 的 `@` 补全每敲一个键就发一条 `paths`**，于是它变成了逐键刷屏 ——
agent 那半边修好之后，剩下的就是这半边。`bridge_log()` 把它和「换到 …」一起
写进同一份日志。

留在终端上的只剩两样：启动时那一行「日志写在哪儿」，和「起不来 feiyu」那种
必须当场看见的。实测从终端起一次，终端上**只有一行**。

### 用例踩到的一条

头一版两条用例各自 `set_var("XDG_STATE_HOME", …)` 再调 `log_path()` ——
**环境变量是进程全局的，而 cargo 的用例是并行跑的**，第二条去看了第一条的文件，
断言说"没截断"。改成 `open_log_at(path)` 收路径当参数，用例各写各的目录，
一个 `set_var` 都不用。

### 顺带逮到 `shoot.mjs` 一个间歇的假故障

跑五次挂一次，报的是 `getComputedStyle ... parameter 1 is not of type Element`
——看着像前端坏了。翻出那次的 `1_flow.png` 才明白：**拍到的是上一轮夹具留下的
真实对话**，不是灌进去的那份 FLOW。

原因是它 `Page.navigate` 之后只 `sleep(1500)` 就开灌，而连上时前端会发
`/help` / `status` / `sessions` / `bots`，agent 冷启动要载 MCP，几秒才回得来 ——
这几条回话（盘上真有会话时还跟着 `cleared` / `opened`）落在 `feed()` 之后，
把刚灌进去的那一屏整个换掉。

改成等 `commands.length > 0` 再清一次 `stream` 才开灌。`input.mjs` 里那句
「**要等 agent 回话，不是等 DOM**」早就写着了，这边一直漏着。连跑四次全干净。

## 0.67.2：一次专门的找 bug，六处

用户让"检查一下有没有 bug，有的话就修复"。这轮的查法跟 0.65.1 / 0.67.0 换了个面：
**把代码逐个模块读一遍**（lint 只报出 37 条，全是风格项，一个真 bug 都没有），
外加**换一个 Python 版本把整套用例跑一遍**——最值钱的那条就是这么掉出来的。

### 1. `list_files` 在 3.13 以下整个是坏的（最要紧的一处）

0.26.2 改成 `PurePath.full_match()` 剪枝，而**那是 3.13 才有的方法**。
`pyproject` 写 `requires-python = ">=3.10"`、`install.sh` 会挑 `python3.10`、
README 写「Needs Python 3.10 or newer」——三处都在说支持，而装在 3.12 上的话
`list_files` 每次都回一句 `'PosixPath' object has no attribute 'full_match'`
（被 `Toolbox.run` 那道兜底 except 接住，所以是**模型收到一句怪话**，不是崩）。

本机是 3.14，所以 1069 条用例全绿。**建个 3.11 的 venv 跑一遍，5 挂 5**。

现在是自己翻的 `glob_re()`：`**` 跨任意层且可以是零层、`*` / `?` 不跨 `/`、
整条路径全匹配。语义拿 3.14 的 `full_match` **逐条比对过**（30 个 pattern × 18 条
路径，零分歧），那张表原样进了 `test_pattern_的语义`。另有一条 `test_pattern_匹配不靠
_3_13_才有的_API` 直接拿替身盯着「不许再碰那个方法」——**语义对不对和用没用那个 API
是两件事，前者在新 Python 上永远绿**。

**教训：本机 Python 比 `requires-python` 新的时候，绿灯是假的。**
往后用任何 3.11 之后加的 stdlib API 之前，先想一遍这三处声明。
（`uv venv --python 3.11` 建一个跑一遍，二十秒的事。）

### 2. 并行那一批把图丢了

`read_file` 在 `PARALLEL_SAFE` 里，所以「读一张图 + 读一个文件」走并行 ——
而 `_run_parallel` 拼 `ToolOutput` 时**没抄 `image`**（串行那条抄了）。
模型收到的只有「（下面是 a.png，1024×768）」这句话，图根本没发出去。
同一次 `read_file`，**看不看得见图取决于它旁边有没有别的调用**，而且一个报错都没有。

同一个形状还有两处：`_note_jobs` 和 `_after_tools` 会**重建**最后那条结果
（往上面补一句「后台命令结束了」或者「校验没过」），重建时字段也没抄全。
后台命令刚好在读图那轮结束，图就没了。

**重建 dataclass 时字段要抄全** —— 这类漏字段全是静默的。

### 3. 没填价格的模型能把整个 `--serve` 弄死

`serve.Server.status()` 里 `cost=round(agent.cost, 6)`，而 `agent.cost`
**会是 None**（`priced=False`，也就是这条配置没填价格 —— 界面上那句提示写的正是
"不填就只显示 token 数"，所以这是常态不是异常输入）。`round(None)` 抛 TypeError。

要命的是 `status()` 在**每一处** `push_status` 上，连主循环那道兜底 `except`
自己都要调它 —— 于是它抛出来不是"这条没处理成"，是 `run()` 整个退出、
桌面端窗口不响应。终端那半边一直是对的（`if spent:`），前端也早就判了
`typeof s.cost === "number"`，**就中间这一层没判**。

### 4. 面板里填的价格算不出钱，还会把终端填的价抹掉

`/key add` 把价格存成 `{"hit","input","output"}`，`providers._as_rates` **只认这个形状**；
而桌面端面板存的是 `[0.1, 1, 2]` 一个列表。后果两条：

- 面板里填了价照样 `priced=False`，一分钱不显示 —— 看着像坏了（顺带就是上面第 3 条）；
- 拿面板改一条**终端加的**配置时，表单读不出 dict 里的值、回来是三个空格子，
  于是**把原来的价抹掉** —— 正是 `merged()` 那段注释要防的事故。

存的形状定死成 dict（那是 `/key add` 和 `_as_rates` 的既有形状），
面板那三个格子在 `serve` 这条边界上来回换一次（`price_row` / `price_dict`）。
面板上没有的「缓存写入价」要留住，不然在面板上动一下别的字段就把它冲掉了。
用例从存盘一路断言到 `_as_rates` 的返回值 —— **两个前端各存各的形状，只能靠贯通的断言逮住**。

### 5. 一份写坏的存盘能让 feiyu 在这个目录里起不来

0.67.0 给 `listing()` 加了 `_num()` 兜住 `saved_at` 的类型和量级，**但漏了 `describe()`**。
而它比 `listing()` 更疼：它在**启动路径**上（`__main__` 里 `--resume` 和
「这个目录有上次的对话」那两句都不带兜底），一抛 TypeError 就是敲 `feiyu` 直接崩，
**连 `/clear` 都够不着**。

**加防御性兜底时，把同一份数据的所有读法都过一遍**，别只修报上来的那一处。

### 6. `@` 的 20000 字上限封不住

`_listing()` 只按 `MAX_ENTRIES`（80 项）数项数，**一个字都不数**。而文件名最长 255 字，
八十项就是两万字 —— 正好是 `MAX_CHARS` 的全部。于是 `@某目录 @某文件` 里后面那个
分到的是**负数预算**，`_numbered` 的 `first[:budget]` 变成从末尾切：
报出来的话是「只截了前 -509 个字符」，实际贴进去五万字。

两处一起改：`_listing` 也吃预算（截断照旧要说出来），`_one` 里预算不够
`MIN_SHARE` 就干脆不贴（跟 `expand` 里"一句话里指的东西太多"同一个说法），
share 那行加 `max(0, left)`。**预算绝不能变成负数** —— 负数会一路传到切片上，
切出来的东西比不贴还糟。

### 查过但**没改**的

- **lint 没有真 bug**：`ruff --select F,B,E7,PLE,…` 在 `feiyu/` 上 37 条，
  全是 `PLW0603`（模块级 global，那是 `ui.THEME` / `events._sink` 的设计）
  和测试里的 F401/F841。**别再指望 lint 找出这个项目的 bug**，第二次确认了。
- `_parse_args` 回一个非 dict（`"123"`）的话 `Toolbox.run` 的 `key not in args`
  会 TypeError —— 但那要模型吐出一个合法 JSON 却不是对象，实际不会发生，
  而且冒出去也只是这一轮报错，不污染历史。记在这儿，别再查第二遍。
- `session.new_id()` 的撞秒后缀在 `_prune` 的字典序里排在无后缀那份**前面**
  （`-` < `.`）。`_prune` 本来就是近似的，不值得为它引一个排序键。

### 验证

单测 1085 条（新加 16 条），**3.11 / 3.12 / 3.14 三个版本各跑一遍全绿**；
回退验证过（`git stash push feiyu/` 之后新加的用例全红，18 条）。
cargo 22 条、`fuzz_serve`（32 + 110）、`fuzz_ui`（102 条）、`input.mjs` 85/85、
`shoot.mjs` 13 张、`ui_drive.py` 4 张，全绿。

**`input.mjs` 的工作区要指到这个仓库自己**（`-w $PWD`）：`@` 那七条补的是
`feiyu/` 和 `README.md`，指到一个空目录会挂 5 条，而那不是产品的毛病。

## 桌面端的行长（0.67.2 顺带）

用户让"美化一下桌面端"。跟 0.67.0 同一套查法：拿 CDP 在真页面上量，再动。

### 一、正文一行 51 个汉字（这轮最实的一处）

`--content: 54rem` 下正文栏 760px，15px 中文是 **51 个字**、拉丁约 95 个字符，
两个都越过可读上限（中文 35–40、拉丁 45–75）。「你」那句 18px，**42 个字**。
长行读到行尾回不去，会丢行 —— 这是整个应用最常看的一屏上最贵的一个毛病。

`--content` 不能缩：它就是为 diff 和命令输出定的（"700px 下代码行会折得很碎"）。
所以**按内容类型分两档**，新加一个 `--measure: 34rem`：

| | 吃哪一档 | 1280px 下 |
|---|---|---|
| 段落 / 列表 / 引用 / 标题 / 「你」那句 / 设置右栏的 `.lead` | `--measure` | 544px |
| 代码块 / diff / 表格 / 图 / 工具输出 | `--content` | 760px |

量出来：正文 51 → **36 字**，「你」42 → **30 字**，代码块照旧 760。

两处用的是**同一个绝对长度**，所以 15px 的正文和 18px 的问句右边缘落在同一条
竖线上 —— 换成 `em` 会因为字号不同而错开。

### 二、敲进去的字和发出来的字差 64px

`.stream` 和 `.composer__row` 本来就都是 864 居中、左边缘同在 328，可输入框从
**368** 起（第一列是 `auto`，只有那个 ＋ 那么宽），而正文从 **432** 起。
同一句话，敲的时候在一处、发完在另一处。

输入区第一列改吃 `--rail`，＋ `justify-self: end` 贴着 rail 那条线（跟左边一列
的大写标签同一侧），textarea 的文字就落回 432。

**窄屏要把这一列退回 `auto`。** `--rail` 在 46rem 以下是 0，不退的话
`justify-self: end` 把 ＋ 的右边缘钉在 0 上，整个按钮溢到屏幕外 —— 390px 下
只剩半个加号。`3_narrow` 那张一眼看见的。

### 三、行高一律是长度，不是比例

设计文档第三条写着"line-height 是长度不是比例"，而代码里有四处比例值：
`.talk__title` 1.4（19.6px）、`.block--you .block__body` 1.5（27px）、
`.prose h1/h2/h3` 1.35、`.prose pre code` 1.55。全换成长度（20 / 28 / 24 / 20）。
`.block--you` 的标签行高要跟着一起改，不然标签浮在正文第一行上面。

### `ui/` 是编进二进制的，改完必须重新 build

devserver 走 `include_str!`，桌面端走 `frontendDist` + `generate_context!` ——
**两个都不是运行时从磁盘读**。改完 CSS 直接跑夹具会拍到一模一样的画面，
看着像"改了没生效"，其实是在跑旧二进制。这一轮白拍了一轮才想起来。

**依赖是自动跟踪的，`cargo build` 就够，不用 clean。** 量过：光 `touch ui/app.css`
或 `touch ui/vendor/xterm.css` 都会触发重编，整个 `frontendDist` 目录的每个文件
都在 desktop bin 的 fingerprint 里（`generate_context!` 展开成一串 `include_str!`，
rustc 的 dep-info 就记下了）。

**别拿 build script 的 `rerun-if-changed` 判断这件事**：它只列了
`tauri.conf.json` 和 `capabilities`，看着像"ui/ 没被跟踪"，结论是反的。
也**别拿 `strings | grep` 验二进制里的 CSS**：Tauri 把前端资源压缩存了，
连一直存在的 `--content: 54rem` 都搜不到，搜不到不等于没打进去
（devserver 那个 `include_str!` 的倒是明文，能搜）。

### 夹具：三条新断言，和一条一直在骗人的旧断言

新加的三条，都是这轮量出来的判据：
- 行长两档：正文 ≤ 40 字，**且代码块必须明显更宽**（后半条防的是有人把
  `--content` 一起缩了，那样 diff 会折得很碎）；
- 输入框的文字起点跟 `.block__body` 差 ≤ 1px；
- 390px 下 ＋ 的 `x ≥ 0`、宽 ≥ 24。

**顺带修掉一条假绿灯**：「代码块不许画自己的竖线」原来跑在 `9_parallel` 那一屏
上 —— 那屏全是工具调用，`.prose pre` 一个都没有，`[].some()` 永远是 false。
绿了很久，一次都没验过。挪到 `1_flow` 之后，并加一条"至少得有一个"的守卫。
**断言要跟它量的东西在同一屏上**，这条跟 0.51.0 那两个夹具的坑是同一类。

### 复查逮到的三处（头一版交出去之后自己再查一遍）

**1. 「行高换成长度」这一步，换的是比例，值要按各自的字号重算。**
窄屏那条 `.block__label { line-height: 1.4 }` 我顺手也换了，却套了
`.talk__title` 的 20px —— 那是给 14px 的字定的，而 `.block__label` 是 **9px**
（`--text-3xs`）。1.4 在 9px 上是 12.6，换成 20 等于在标签和正文之间凭空
塞了 8px。改成 **12px**。**比例值一处一个数，不要跨元素抄。**

顺手修掉一处既有的：`.block--you .block__label` 特指度比媒体查询里那条高，
所以窄屏下它一直吃 27/28px，同样是 9px 的字配一个大行高。也给 12px。

**2. `.clips { padding-left }` 从来没生效过 —— 它写在 `.clips` 主体之前。**
主体里有 `padding: 0`，两条特指度相同，**后写的赢**。加"补一个属性"的规则时
要看清它落在主体规则之前还是之后。这条干脆删了：附件条和补全菜单**都**贴
composer 那条 864 的左边缘（328），输入框的文字对齐正文（432）是另一条线，
各管各的 —— 两个辅助面板对齐的是这一整块，不是输入框。

**3. `clips` 是 app.js 里的全局变量（待上传的数组），遮住了 id 为 clips 的元素。**
夹具里写 `clips.innerHTML` 拿到的是那个数组。跟 CLAUDE.md 早记过的
「裸的 `find` 是 `window.find`」是同一类，**这是第二例** —— 夹具里取元素
一律 `document.getElementById(...)`，别指望 id 的全局兜底。

**4. 量具自己被 Chrome profile 骗了一次。** profile 目录跨次保留的不只是
localStorage，**窗口尺寸也留**：上一轮结尾把视口设成 390 就退出了，下一轮
不显式 `setDeviceMetricsOverride` 就一路在窄屏布局里量，`.clips` 量出 x=16
（该是 328），看着像"改动没生效"。**每次量之前先把视口设死。**

### 量过但**没改**的

- 8pt 间距阶只有两处不合：`.tab` / `.side__tab` 的 `margin-bottom: -1px`，
  那是刻意压在分隔线上的。
- 所有可点的东西都 ≥ 24px（0.67.0 修完之后没再退化）。
- 大屏下 `.stream` 居中，2560px 时左边空 728px。桌面窗口全屏时居中比左对齐
  好读，这是取舍不是毛病。
- 设置右栏 `.opt` 的两列是 `112px 484px`，「默认」两个字占 112 看着空 ——
  但那一列要装得下「只读」「计划」这类更长的名字，不动。

## 0.68.0–0.68.2：能量出好坏的尺子，和几样冲着解决率去的东西

用户要"超过 DeepSeek Harness"，口径定死在**任务解决率**。这一块当时按版本切成了
三段（0.68.0 / .1 / .2），但**changelog 一直没补**，0.69.1 才回头一次写清楚 ——
所以下面按内容分，不按那三个版本号分。

### 为什么先做尺子

原来的题库 21 题全过，**做任何改动都量不出好坏**。不先修这个，后面每条改动
都只是凭感觉。整套 `evals/` 就是干这个的：

- `swebench.py` 走 HF datasets-server 的 REST 接口拉数据集，**纯 urllib，不引依赖**。
  数据集要用 `SWE-bench/SWE-bench_Verified`，`princeton-nlp/` 那个没有 `image` 字段。
- `swedocker.py` 起每实例容器。**容器里 `bash -lc` 进的是 conda base 不是 testbed**，
  不 `activate testbed` 的话 `import astropy` 和 `pytest` 全失败，agent 会花好几轮
  去修一个不存在的问题。`_scrub()` 删掉 `.git/logs`，堵住从 reflog 里翻出答案那条路。
- `swegrade.py` **判分整个交给官方 `swebench.harness.run_evaluation`**（5.0.2）。
  自己写 FAIL_TO_PASS 解析等于自己给自己打分。`-p gold` 是验判据本身的后门。
- `report.py` 把"没解决 / 撞上限 / 判不了 / 铺不起来 / 崩了"分开。
  **「判不了」绝不能算进分母** —— 那是我们这边坏了，算进去等于让自己的 bug
  拉低分数。这个错犯过两次：`submitted_ids` 含基础设施失败，不等于判过。
- `run.py` 的 `_isolate()` 补上 `XDG_CONFIG_HOME`（原来只设了 `XDG_STATE_HOME`，
  **跑一次评测会写用户真实的 `~/.config/feiyu/state.json`**），并把拷过去的
  models.json 按 0700 目录 + 0600 文件放 —— `shutil.copyfile` 不带权限，
  第一版把明文 key 摊在 /tmp 的 0644 上。

`sandbox.wrap` 多一档 `FEIYU_EXEC_WRAPPER`：给了就整条走它、不再叠 bwrap，
SWE-bench 靠它把命令送进实例容器。**它不是安全机制**，`effective()` 会照实说。

### 量出来的结果：尺子饱和了

50 题跑出 **45/46 = 97.8%**。这不是我们厉害，是 SWE-bench Verified 全行业都在
96–97%。**所以它测不出任何改动的好坏。** 更硬的子集写死在
`swebench_hard.txt`（45 道 hard）和 `swebench_ab12.txt`（12 道 A/B 用）。
选题一律按 `difficulty` 分层 + `instance_id` 排序等距取，**不许随机**。

### 轮数和预算两道闸

`MAX_ROUNDS = 25` 原来是写死的，非交互下第 25 轮**硬停** —— SWE-bench 那种题
量出来的是上限不是能力。现在有 `--max-rounds` / `--budget`，也能写进约定文件
（`max-rounds: N` / `budget: 5`，跟 `verify:` 一个机制，要求顶格整行）。

**0 是"不限"不是"没写"**，所以命令行那侧用 `is not None` 判而不是真值判，
`limit_cli` 记住哪几项是命令行给的，`/context` 重载时不被约定文件盖掉。
放开轮数就必须有另一道闸，否则一道题能烧掉一整轮评测的钱 —— 两道闸在
`send()` 里同一处收口。`_stop_budget` **必须往历史补一条 assistant**，
跟 `_stop_runaway` 同理：不补的话角色不再交替，下一轮请求直接 400。

### 推理强度 `/effort`

anthropic 的 `output_config.effort` 五档（low/medium/high/xhigh/max）+
`thinking`。**xhigh / max 配上「不思考」接口会 400**，所以两个方向都要挡：
改档位时挡一次，"当前已是 xhigh、现在把思考关掉"是另一条路，也得挡。
`effort` / `thinking_on` 放**类属性**，因为测试里的替身不走 `__init__`。
openai 兼容那侧只在显式填过时才发 `reasoning_effort` —— 多数模型不认，乱发会 400。

### 复现优先

第一次写文件之前先跑一遍 `verify:`，把"现在什么是坏的"作为背景附在那次写的结果上，
一个会话只跑一次。挂在 tool_result 上而不是另起一条 user 消息：两种后端的消息结构
不通用，tool_result 是唯一两边都能安全追加的地方。

动机是实测的 `django__django-13212`：5 条失败测试全在 `forms_tests` 里，
而它 52 轮只盯着题面提到的 `core/validators.py`，方向对、改了一半，
**从头到尾没看见那几个测试名**。

### 写盘前语法检查 + `symbol outline`

语法检查走 `ast.parse`（标准库，零成本），查的是"它刚把这文件写坏了"。
`outline` 走 `documentSymbol` 出"类/函数 + 行号"，给 django / sympy 那种几千行的
文件当路标 —— `READ_CAP = 1500` 一次读不完还很贵。骨架里**只留结构，不要参数和
局部变量**：pyright 把它们也报成 symbol，几千行文件里那是几百条噪音。

`lsp.py` 的 `request()` 补了第三个分支收通知：通知有 `method` 没有 `id`，
原来两个分支都不 match，**被 `while True` 吃掉**。

### 同一个二进制跑 A/B 的开关

`FEIYU_ASSIST=off` 把上面这几样一起关掉。要证明它们有用就得同一批题跑两遍，
而"改动之前"那份代码在工作区里已经不存在了，靠 git 也分不干净
（同一批改动里混着 0.67.2 的修复）。**同一个二进制跑两遍、只翻一个开关**，
才是能说清的对照。这个开关只在 `evals/` 里出现。

### 桌面端：md() 的死循环和流式重绘

**一、`md()` 会原地死循环。** 外层 `while` 靠每个分支自己推进 `i`，而
**没有分隔行的表格行**（`| a | b |`，模型流式吐表格时表头落地、分隔行还没到的
那一瞬间）前面所有分支都不认，`para` 是空的、`i` 一步没动 —— 原地转圈往数组里塞
`<p></p>`：实测 **70 秒吃掉 16 G**，整个窗口卡死。兜底是 `if (!para.length) para.push(lines[i++]);`，
**那一行是唯一出口，别删**。顺带 `-{3,}` 也修了：四个以上连字符也是分隔线，
只认三个的话 `----` 会掉进这个死循环。

**二、流式正文不能每个 delta 画一次。** markdown 的记号跨 delta，追加 DOM 对不上，
所以每次都要整份重渲染 —— 实测约 4.6 ms 一次，日志里真出现过的 6696 token 那一轮
就是**三十秒主线程全占**。按固定间隔节流（80 ms，12.5 次/秒）而不是按帧：
按帧的话一条流了一分钟的长消息还是要画几千次。收尾时**必须先把攒着的那一帧撤掉**。

新夹具 `desktop/dev/md_check.mjs` 专盯这类：`fuzz_ui.mjs` 灌的是畸形**事件**，
而这里坏的是合法事件里的一段**合法 markdown**。关键是 `Runtime.evaluate` 的
`timeout` 能打断同步死循环（V8 的 TerminateExecution），所以死循环在夹具里
表现为"超时"而不是"夹具自己也挂住"。

## 0.69.0：shell 状态跨调用保持，以及把 0.68 的诊断撤掉

### 1. 撤掉「写完报整个文件的 LSP 诊断」——A/B 量出来是负的

0.68 在 `Toolbox._diagnose` 里把语言服务器的诊断并进了 tool_result。想法是
「早知道错 = 少走弯路」。**拿 12 道 SWE-bench 配对 A/B 量了一遍，方向是反的**：

| 中位 | 关 | 开 | |
|---|---|---|---|
| 轮数 | 42 | 64 | +53% |
| token | 90.5 万 | 155.8 万 | +72% |
| 花费 | | | +39% |

解决率两边都 12/12，**11 道里 12 道轮数上升**（最惨 `django-16631` 44→87）。

真实机制不是「早知道错 = 少走弯路」，是「**多告诉它一些它没问的事 = 多给它一些事做**」。
整文件的诊断把它引去修它没造成的问题——大文件里早就存在的类型错、未定义名。
当初特意设成「不标 `is_error`、只报文字」来防这个，**文字本身就足够带偏**。

留下的是本地语法检查（`ast.parse`，标准库，零成本，查的是「它刚把这文件写坏了」，
一定是它的锅）。取诊断那套（`diagnose` / `drain` / `self.diagnostics` / 两个常量）在 0.69.1 的
体检里一并删了——没人调、没用例盯，`self.diagnostics` 还在只写不读地攒东西。
**要重新接的话只报它改过的那几行**，那需要改动行号，跟原来那个整文件的签名不是一回事。
`request()` 收通知那个分支留着，它修的是另一件事（通知没有 `id`，两个分支都不 match，
会被 `while True` 吃掉）。
`tests/test_tools.py` 里有一条 `test_不报语言服务器的诊断` 守着这个结论。

**这轮最该记的教训不是诊断本身，是我动手前写下了「诊断是最大的一块」、
然后直接实现了一整轮，没先用小样本验这个前提。** 零成本的自检做得很勤
（判分链路那几个坑全是自检抓的），但对「这个改动有没有用」本身跳过了验证。

### 2. `export` / `source` / 函数跨调用保持

之前每条 bash 是个新进程，**只有 cwd** 靠「把 `pwd` 写进临时文件」跨了过去。
所以 `source .venv/bin/activate`、`export`、定义的函数全部不留。

这件事的代价是量过的：`_venv_hint` 那个提示就是为它加的，它的 docstring 记着
**裸 python 报 ImportError 之后花了五轮翻环境（连 `find /` 都用上了）**。
提示是绕，这里是修。

做法是把现成那条路扩一格：命令跑完
`( unset -v PWD OLDPWD SHLVL _; export -p; declare -f ) > 状态文件`，
下一条命令开头 `[ -s 状态文件 ] && . 状态文件`。

**不是常驻 shell 进程。** 常驻进程要自己做取帧、僵尸回收，而且超时时只能杀前台
不能杀 shell——杀了状态全丢。dump/restore 复用的是 cd 已经在走的机制，
超时、沙箱、kill 全部照旧，没新增失败面。代价是**进程状态不跨调用**：
shell 里挂的后台任务、开着的 REPL 都不留（后台任务有 `background`/`job` 那套管着）。

三个坑：

- **过滤那几个变量不能用 `grep -v`。** 值里带换行的变量会被按行切开，剩下的引号
  对不上，**整份 dump 就 source 不回来**——不是丢一个变量，是全部状态一起丢。
  要在子 shell 里 `unset -v` 再 `export -p`，引号交给 bash 自己管。
- **`PWD` / `OLDPWD` 必须排除。** `pwd` 内建读的就是 `$PWD`，带上旧值的话
  cwd 跟踪会跟着一起错。
- **状态文件要 `--bind` 进沙箱**（跟记 pwd 那个文件一样）。沙箱里 `/tmp` 是自己的
  tmpfs，不放行的话写回的东西外面读不到。

`bash` 工具多一个 `reset` 参数，`Toolbox.shell_reset()` 是它和退出收尾共用的出口。
**必须有这个出口**：状态跨调用保持的代价是一条命令能把后面全搞坏（`export PATH=`
这种），退不回去的话整个会话就废了。状态文件是 `mkstemp` 建的 0600——里头是整份
导出的环境，包括任务自己 `export` 的东西；`_child_env()` 的密钥过滤在它之前生效，
所以密钥类变量本来就不在里面（有用例盯着）。

### 3. 执行用的 shell 换成真 bash

`subprocess(shell=True)` 和 `sandbox.wrap` 里写死的都是 `/bin/sh`。这台机器上
`/bin/sh` 就是 bash，**但以 `sh` 的名字起来会进 POSIX 模式**，而模型写的是 bash：

- `source 相对路径` 在 POSIX 模式下**不搜当前目录**（只搜 PATH），直接 `file not found`
- `export -p` 的输出格式也不一样（`export FOO=` 而不是 `declare -x FOO=`）

工具叫 `bash`、说明里写的是 shell 命令，那就该真给它 bash。现在是
`sandbox.SHELL = shutil.which("bash") or "/bin/sh"`，两个执行入口都显式给 argv。

**第二条差异还顺手抓出我自己一个假绿的用例**：`test_dump_里没有_PWD` 只查了
`declare -x PWD=`，在 POSIX 模式下那个字符串永远不出现——**它当时是绿的，
但什么也没验**。现在两种格式都查。

## 0.69.1：一次专门的找 bug，五处

用户让"检查一下整个 agent 系统，有 bug 就修"。lint 照旧没抓到真 bug
（4498 条里全是中文全角标点的 RUF001-003），**五条全是读新代码 + 拿运行时探出来的**。
三个 Python 版本（3.10 声明下限 / 3.12 / 3.14 本机）各跑一遍，这次没有版本相关的。

### 1. `exit` 和 `set -e` 会让 cd 和 shell 状态静默丢掉（最要紧的一处）

收尾那几行（`pwd > 文件`、dump 状态）原来写在命令**后面**。命令自己 `exit`、
或者 `set -e` 中途失败时，那几行根本执行不到：

```
cd sub; exit 0        →  cwd 还是老的，而模型以为自己已经进去了
set -e; cd sub; false →  同上
```

**cd 那一半是 0.13.0 就有的老 bug**，一直没人发现——因为它不报错。
现在挂在 `trap __feiyu_save EXIT` 上，三种收尾（跑完 / 显式 exit / set -e 中断）
全覆盖，退出码照样原样带出去。信号杀掉那种收不到（超时走 SIGKILL），
那时候状态丢掉反而更安全。

### 2. 半份 dump 会被 source 一半，而且会传染

`> 文件` 是先截断再写。写到一半被 SIGKILL 就留下半份，半份照样 `-s` 非空，
`source` 时的语法错又被 `2>/dev/null` 吞掉 —— 结果是**一部分变量回来了、
函数静默没了**。更糟的是它会传染：下一条命令的 dump 是从这个已经缺了的环境里取的，
缺口就永久焊死，而谁都看不见。

现在 dump 写完才追加一行 `__feiyu_end=1`，restore 前用 `tail -n1` 验一下；
不完整就**整份丢掉**——宁可没有状态，也不要半份状态。

### 3. `reset` 撞上 `background` 会被默默吃掉

`if background: return _start_job(...)` 在 `if reset:` 上面，两个一起给的时候
直接从 background 那条路返回了。模型以为环境清干净了，其实没有。换个顺序。

### 4. 钩子和 `verify:` 跟 bash 工具用的不是同一个 shell

**这条是 0.69.0 我自己造的。** 那一版把 bash 工具换成真 bash，但
`hooks.run` 还是 `shell=True`（即 `/bin/sh`，POSIX 模式）。于是同一条命令
`source .venv/bin/activate && pytest` 在 bash 工具里跑得起来、写进 `verify:`
就 `file not found`。两边统一到 `sandbox.SHELL`。

### 5. 子 agent 把图片丢了——**同一个坑的第三处**

`subagent.py` 拼 `ToolOutput` 时没抄 `image`。而 `box.vision` 是按后端能力
打开的，所以 `read_file` 读图时 content 只有「（下面是 a.png，1×1）」这一句，
图在 `result.image` 里——子 agent 收到的就只有那句话。

前两处（`_run_parallel`、`_note_jobs`）在 0.68 修过。**修了两次还有第三处，
说明"手写 ToolOutput"这个动作本身就是坑**：字段一多，抄漏一个不会报错。
现在三处都有用例盯着。

### 顺带清掉的死代码

0.69.0 撤掉整文件诊断之后，`lsp.py` 里 `diagnose()` / `drain()` /
`self.diagnostics` / `DIAGNOSE_WAIT` / `MAX_DIAGNOSTICS` 全没了调用者，
而且没有任何用例盯着；`self.diagnostics` 还在只写不读地攒东西。一并删掉。
`request()` 收通知那个分支留着，它修的是另一件事：通知有 `method` 没有 `id`，
两个分支都不 match，会被 `while True` 吃掉。

### 一条方法上的教训

给第 2 条做回退验证时，我用 `sed` 去拆掉哨兵检查，跑出来是**绿的**——
我差点当成"这条用例没用"。实际是 `sed` 的模式压根没匹配上，改动没落地。
**回退验证本身也要验证它真的改到了东西**，不然它给的是假的安心。
后来改成 Python 里 `assert s.count(old) == 1` 再替换，立刻就红了。

## 编辑器桥（0.71.0）

`feiyu/ide.py`。agent 要改文件时，确认从终端搬到编辑器里的左右对照 diff。
nvim 那端是 `~/.config/nvim/lua/feiyu_ide.lua`，VS Code 那端是
`~/Projects/vscode-feiyu-ide`（扩展 `feiyu.feiyu-ide`）。**三边同一套协议，改一处要想到另两处。**

### 为什么不照抄 Claude Code 的 WebSocket

方向照抄了（**编辑器是 server、CLI 是 client**，lock 文件发现），协议没有：
本项目硬规矩是不引新依赖，手搓 RFC 6455 的帧和掩码换不来任何东西，
而 unix socket 两边都是原生的（nvim 有 `vim.uv`，VS Code 有 node 的 `net`）。
行分隔 JSON 同理，不用自己数长度。顺带**一个 TCP 端口都不听** ——
0.50.1 那三道门（令牌 / Origin / Host）在这儿一条都不需要。

### 接缝在 `agent.approve`，`agent.py` 一个字没动

`_gate` 里本来就有 `self.approve(call.name, call.input)` 这个回调，`__main__` 塞的是
`ui.confirm`。现在塞的是「先问编辑器，它不管就走 ui.confirm」。**`args` 就是
`call.input` 那个 dict 本身**（`_gate` 之后 `toolbox.run(call.name, call.input)` 拿的是同一个对象），
所以「人在 diff 里改过右边」只要就地改它就生效，不用给工具开特殊路径：

- `write_file` 换 `content`，**并且必须摘掉 `append`** —— 不摘会把整份内容再拼一遍；
- `edit_file` 换成一处「整份换整份」的 `edits`（old_str 是当前全文，必然唯一匹配）。
  盘上的内容在这中间被人改过的话 old_str 对不上、`edit_file` 照常报错，那正是该有的行为。

只接 `write_file` / `edit_file`。bash、`git commit` 没有 diff 可开，照旧在终端确认。
**`new == old` 或者 old_str 对不上时不开 diff** —— 那次调用本来就是空转或者会失败，
白占一次确认。

### 一条铁律：桥坏了不能影响 agent

连不上、超时、编辑器崩了、回的不是 JSON、action 认不出 —— `route()` 一律返回 `None`，
调用方退回终端确认。这类退路只在真出事时才走，最容易写完没人验过，所以
`tests/test_ide.py` 里 `TestDegrades` 把每种坏法都跑一遍，
**十五条承重行逐条做过回退验证**（拆掉哪一行，哪条用例红）。

### 四个真踩到的坑

**一、`sock.makefile()` 的缓冲读不能配 `settimeout`。** `_recv` 按秒分片等
（好在编辑器被 kill 时不用等满十分钟），而第一次分片超时之后那个文件对象就废了，
再 readline 直接抛 OSError → 判定成「编辑器断了」→ **每次都在 1.0 秒上返回 None**。
假编辑器立刻回话，所以 43 条单测一条都没抓到；是 nvim 那边真按了一次 `ga` 才暴露的。
现在自己按 `select` + `recv` 攒行。`TestSlowEditor` 那两条盯着它，
**假编辑器要延迟回话才测得到这条路**（而且 ping 不能跟着延迟，那道超时只有 1.5 秒）。

**二、nvim 侧 diff 的两块 buffer 不能都设 `acwrite`，而且 set_lines 之后要清 `modified`。**
用户的 `init.lua` 开着 `'confirm'`，切窗口时 nvim 会弹「Save changes to …?」——
**那个模态框把 libuv 的事件循环整个堵死**，表现是 feiyu 那边等到超时，而 headless
下连那句提示都看不见。只有右边设 acwrite（要接 `:w` 当接受），左边保持 nofile。
`settle` 关 tab 之前也要清一次 `modified`，不然人改过右边时 tabclose 又弹一次。

**三、编辑器被 kill 时 `deactivate` / `VimLeavePre` 都不跑**，socket 文件会留在
lock 目录里。lock 那份靠 `discover()` 的 pid 检查自愈，socket 那份现在一起清 ——
**但只删 lock 目录里面的**：那个路径是从文件内容里读出来的，照着它删别处是另一回事。

**四、VS Code 的面板不能拿裸命令名当 `shellPath`**（扩展 0.2.2 修）。
`createTerminal({shellPath: "feiyu"})` **不过 shell**，是拿 VS Code 自己进程的 PATH
去 execvp。从 GNOME 启动器点开的 VS Code 继承的是 systemd user 环境
（`systemctl --user show-environment`），里头**没有 `~/.local/bin`** —— 用户的 PATH
是在 `.zshrc` 里 export 的，那是交互式 shell 才读的文件。于是终端一创建就 ENOENT 死掉，
而 `feiyuIde.panel` 是开关，下次按看见的是个死终端于是又建一个新的，
**表现成「按了没反应」**。从终端敲 `code .` 起的窗口反倒是好的，所以它看着像时好时坏
（扩展日志里的判据很干脆：连着几行「起了面板」而中间没有一次关掉 —— 开关命令
每次都走了「建」那一支，说明每次终端都已经是死的）。
现在 `lib/which.js` 解析成绝对路径再 spawn（设置 `feiyuIde.command` → PATH →
`~/.local/bin/feiyu` → `.venv/bin/feiyu`），一份都找不到就弹报错**并且不建终端**。
nvim 那端没这个问题：它 `jobstart` 走的是自己那个继承了 `.zshrc` 的 shell。

### VS Code 面板的工作区（扩展 0.2.3）

`@路径` 相对**面板起来时那个工作区**算（`mentions.py` 只认工作区里的真实相对路径），
所以换项目就必须换一个 feiyu。越界时扩展重开面板到这个文件归属的工作区 ——
包含它的那个根，嵌套取最里面的；一个根都不包含就退回它所在目录（`code 单个文件`）。
**不静默重开**：面板里那个对话是有上下文的，杀掉它得人点一下；面板本来没活着就不问。
顺带一个好处：多根工作区里第一次按 `<space>Fb` 从此落在文件自己那个根，不再是 `roots[0]`。

lock 是按**工作区完全相同**匹配的，所以面板的工作区不在已登记的根里时要补一份，
缺了那个 feiyu 就找不到本窗口。补的时候得先清上一批 —— lock 文件名按下标编号，
数量变少时旧的会留成孤儿，**而它们指着同一个活 socket，别的 feiyu 照样连得上，
看起来一切正常**。

另外 `alt+y`/`alt+shift+y`/`alt+n` 要进用户 `settings.json` 的
`terminal.integrated.commandsToSkipShell`，否则焦点在 feiyu 面板里时这三个键会被
当字节灌进 prompt_toolkit 的输入区（而 `alt+space`(whichkey) 的 when 是
`!terminalFocus`，`<space>Fy` 那条路同时也是断的）。keybinding 自带
`when: feiyuIde.deciding`，只在真有 diff 等着决定时才截。

### 0.71.1：`connect()` 一次不够，换成 `Link`

0.71.0 在 `__main__` 里 `connect()` 一次就完事。**顺序反了整场就没有 diff**：
先在终端里跑 feiyu、回头才打开 nvim（很常见），这个会话永远连不上。
而且是**静默的** —— 「已连上 nvim」那句只在连上时打印，连不上什么都不说，
人以为 diff 会开、实际一直在终端里按 y。编辑器中途重启、被 kill 同理。

`ide.Link` 把「那一次连接」换成「每次要确认写操作时看一眼」：

- **不轮询、没有后台线程。** 一次确认之间什么都不发生。
- **节流 `RETRY`（5 秒）。** `discover()` 没有 lock 文件时立刻返回空，这条路几乎不花钱；
  节流兜的是另一种：lock 在、但那个 socket 没人 accept，`connect()` 要在 `CONNECT`
  上耗掉 1.5 秒。每次确认都撞一下这个的话，人能感觉到卡。
- **`name not in DIFFABLE` 要先判。** 不然每次 bash 确认都去试重连 —— 而 bash
  本来就没有 diff 可开，白花那 1.5 秒。
- **`route()` 之后要再看一眼 `bridge.dead`。** 就是这一次把它用死的（编辑器关了），
  等下一次确认再发现的话，「退回终端确认」那句就晚了一步。
- **状态真的变了才 `on_change`**（连上给编辑器名、断了给 None）。不然每次重连失败
  都要刷一句。`__main__` 拿它打印，所以 `ide.py` 不用 import `ui`。

铁律照旧：`_ensure()` 整段在 `try` 里，`connect()` 自己没接住的异常这层兜着；
`on_change` 炸了也不能把这次确认带走 —— 它只是打印一句提示。

`TestLink` 七条，**逐条做过回退验证**（拆掉节流 / DIFFABLE 闸 / dead 复查 /
两处 `except` / `__init__` 里那次连接 / 「已经连着就直接用」，各有一条用例变红）。
外加真 nvim 跑了一遍「feiyu 先起 → 后开 nvim → 接上 → nvim 退出 → 放手」八条。

**假编辑器夹具同时修了两处**（不修的话上面那条「编辑器重启」根本测不出来）：
`Server.stop()` 以前只关 listen 那个 socket，已经 accept 出来的连接还在照常答话，
于是「编辑器关了」的用例看到它若无其事地回 accept；而且**必须 `shutdown` 不能只
`close`** —— 服务线程那边 `conn.makefile()` 另持一份 fd，只 close socket 对象
底下的 fd 还开着，对面一点察觉不到。socket 文件名也改成带编号的，
否则同一个测试里第二个假编辑器 bind 会撞 EADDRINUSE。
顺带修了 `test_error_reply` 里那个内联假编辑器**会回 id 为 0 的通知**（协议说了别回），
客户端那时往往已经收摊，write 撞 BrokenPipe —— 跑在 daemon 线程里，
表现是测试全绿但屏幕上糊两条 traceback。

### 验证方式

单测（55 条）之外，这三样都跑过：

```bash
.venv/bin/python -m unittest tests.test_ide       # 55 条，3.11 / 3.12 / 3.14 各一遍
cd ~/Projects/vscode-feiyu-ide && npm test        # 14 条纯逻辑（lib/protocol.js）
```

**真编辑器必须真跑一遍**，单测碰不到 `vscode.diff` / `nvim_open_win` 这一层：

- nvim：`nvim --headless --listen <sock>` 起一个，`nvim --server … --remote-send ga`
  走真实 keymap，七条路径（ga / gr / gA / 改完再接受 / 关 tab / `:w` / 超时）。
- VS Code：`code --new-window --user-data-dir <临时> --extensions-dir ~/.vscode/extensions
  --disable-workspace-trust <工作区>`。判据很干脆：`open_diff` **超时**说明 diff
  真开起来了在等人，**立刻回 reject** 说明 `openDiff` 抛了。
  `FEIYU_IDE_DEBUG=<文件>` 让扩展把日志也写进文件 —— Output 面板脚本看不见。

跑 VS Code 判据时踩到的两条：**`--disable-workspace-trust` 不能省**（新 user-data-dir
打开 `/tmp` 下的目录会进 Restricted Mode，扩展整个不跑）；**`code --wait` 起的实例
不会自己退**，留着的话后面每次 `--new-window` 都被转发给它，看起来像「扩展没激活」——
我为这个查了四轮。

## 0.70.0：接进 Terminal-Bench（Harbor）

SWE-bench Verified 那把尺子饱和了（45/46 = 97.8%，全行业都在 96-97%），
量不出任何改动的好坏。Terminal-Bench 2.0 最强的才 60-83%，有区分度；
而且它测的正好是 **0.69.0 那个持久 shell**——SWE-bench 全是"改 Python 文件让测试变绿"，
几乎用不到 `export` / `source activate` / 管进程，那个改动在那儿一分贡献也看不出来。

`evals/tbench.py` 是一个 Harbor `BaseAgent` 子类，用
`-a evals.tbench:FeiyuAgent` 挂进去。

### 为什么是「装进容器」，不是「把命令送进容器」

SWE-bench 那条路（`swedocker.py`）是 feiyu 跑在宿主机、**只把 bash 送进容器**，
成立的前提是 `start()` 把工作区 bind-mount 进去，两边**看同一份文件**。

**Harbor 一个 volume 都不挂**（compose 模板里没有 `volumes:`）。照搬的话
`read_file` / `write_file` / `search` 在宿主机上对着一个**空目录**操作，
而 bash 在容器里改真文件——两边不是一个世界，**而且没有任何报错**。

所以反过来：`setup()` 把 feiyu 传进容器、装依赖、写配置，`run()` 在容器里本地跑。
这也正是 Harbor 自己的模型（claude-code 那些官方适配器都这么干）。

三条代价写在文件开头：要网（默认 `NetworkMode.PUBLIC`，断网的题 `setup()` 报
`NotUsable`，**跟"没解决"分开**，不进解决率的分母）；API key 要进容器（0600，
但容器里的 bash 就是模型自己，**拿额度有限的 key 跑**）；容器里没 bwrap，
feiyu 自己的沙箱是关的（无所谓，容器本身就是沙箱）。

### 先验判分，再花钱

跟 SWE-bench 那边 `-p gold` 同一个套路，两条都**不花钱**：

    harbor run -d terminal-bench@2.0 -a oracle -i <题名>   # 参考答案 → 1.0 ✓
    harbor run -d terminal-bench@2.0 -a nop    -i <题名>   # 什么都不做 → 0.0 ✓

两条都对了才轮到 feiyu。首跑 `openssl-selfsigned-cert`：**reward 1.0**，
5 分 3 秒，14.0 万 token，$0.0106。

### 路上踩的

- **机器上没有 `docker compose`。** Docker 29.7.2 装着，但 compose 是独立插件
  （Arch 上的 `docker-compose` 包）。没有它 89 道题在**起容器阶段**全挂，
  连 oracle 都跑不了，而报错是 `unknown flag: --project-name`，看不出是缺件。
- **我猜了三个名字，三个都错**：`environment.no_network`（真名是私有的
  `_network_disabled`，已改走公开的 `network_policy.network_mode`）、
  `-p` 传题面（feiyu 的题面是**位置参数**）、`run_end` 里的 `round`（没这个字段）。
  自己明明定了「不猜 API」，`exec` / `ExecResult` / `AgentContext` 全是翻源码确认的，
  偏偏这三处顺手写了看着合理的名字。**顺手的那几个才是会错的。**
- 结果在 `agent_result.metadata`，**不是 `agent_context`**。

### 成本实测

`openssl-selfsigned-cert` 花了 $0.0106（≈¥0.075），比按 SWE-bench 外推的
¥0.19–0.29 便宜不少——这题简单，别拿它当全量的单价。
另外 feiyu 自己记 ¥0.0548，账面扣 ¥0.075，**差 37%**，一道题的样本说明不了什么，
但跑全量之前值得回头对一次。

## 0.70.1：Terminal-Bench 十道冒烟，和一次终于对上的账

`0.70.0` 的适配器首跑十道只判成 3 道，量的基本是它自己的毛病。修完三处再跑，
判成 9/10。这一节记结论和踩的坑。

### 十道的结果（qwen3.8-flash，每题一遍）

| 分类 | 数 | 题 |
|---|---|---|
| 解出来 | 5 | chess-best-move / custom-memory-heap-crash / headless-terminal / nginx-request-logging / sqlite-with-gcov |
| 真跑完但错 | 1 | regex-chess |
| 墙钟没跑完 | 2 | adaptive-rejection-sampler / make-doom-for-mips（45 分钟都没做完） |
| 判不了 | 2 | protein-assembly 超时 / filter-js-from-html 上游 429 |

跑完的题里 **5/6**；按 `report.py` 含「撞上限」的口径 **5/8 = 62.5%**。
**十道一遍，两个数都不能引用**，但框住了区间，而且落在榜首 60-83% 的量级里。

### 「是做不出来还是钱不够」——证伪了

三道 0 分的题原本全撞在 ¥0.35 的预算闸上。把闸放到 ¥1.0、轮数 120、超时 ×3
重跑一遍：**一道都没翻盘**。而且 `regex-chess` 这次只花 ¥0.127（远没碰到闸）就
自己收敛了然后答错 —— 那是真做不出来。另外两道是**时间**不够，不是钱。

所以对这批题，限制因素不是预算。倒是"两道题 45 分钟还没收敛"本身是个信号。

### 适配器修的四处

1. **PEP 668**：Debian 12+ 上 `pip install` 直接拒绝。加 `--break-system-packages`，
   老 pip 不认这个参数，带不带各试一遍。
2. **镜像里没有 python3**（十道里两道）：用镜像自己的包管理器装。feiyu 是 Python
   程序，装运行时跟 claude-code 要 node 一回事。
3. **Harbor 自己那道 agent 超时**（题里带的，默认 900 秒）`FEIYU_BENCH_TIMEOUT`
   管不到，要 `--agent-timeout-multiplier`。十道里四道栽在这儿。
4. **超时那条路上用量没取到**（下面单说）。

### 对账：连着错三次，最后发现 feiyu 的账是准的

三次拿 feiyu 记的花费跟 openrouter 的账单比，得出过「少报 2.6 倍」「少报 128%」
这种结论，**全是错的**，而且每次都是同一个形状：**拿覆盖 N 道的记录去比覆盖
M 道的账单**。被 SIGKILL 的那些题钱在账面上、记录一个字没写。

第三轮 9/10 都有记录时终于能比：账面 ¥2.410、feiyu 记 ¥2.0405（缺的那道约 ¥0.3），
**差 ~3%**。逐条核对过 `models.json` 里十四条价格跟 openrouter 的公开价，
实际用到的两个分毫不差。**feiyu 的成本核算没问题。**

**根因在适配器不在 feiyu。** feiyu 每一轮都写 `turn` 行、每条都带累计 `cost`
（日志里 677 条），数据一直都在。是 `_fill_usage` 只认 `run_end`，而且写在
`exec` 后面 —— 超时时 `exec` 抛异常，它根本轮不到。现在改成 `finally` 里取，
并且 `run_end` 拿不到就退回最后一条 `turn`，metadata 里用 `feiyu_partial` 标出来。

**教训：一个数对不上时，先确认两边量的是不是同一批东西，再去怀疑其中一边算错了。**

### 为什么 `tbench.py` 没有单测

它 `import harbor`，而 harbor 不是本项目依赖（走一次性 venv）。主用例集里
import 它会直接失败。取用量那段的解析逻辑是拿假 environment 在 harbor 的 venv
里单独验的（三种情况：有 run_end / 只有 turn / 什么都没有）。

## 0.72.0：Terminal-Bench 有基线了，以及量尺子时挖出来的七个坑

0.70.1 那十道是**按题名等距取的、每题一遍、结果手工数的**，那一节自己写着
"两个数都不能引用"。这一版做出了能引用的基线，**没有动 agent 自己** ——
改的全是 `evals/`。

### 基线（2026-09-16，qwen3.8-flash，24 道分层子集，每题两遍）

**32/46 = 69.6%**（分母只算真判过的）。中位 24 轮 / 833 秒。
全程 ¥13.85、14.6 小时机器时间。存档在
`~/.local/state/feiyu/evals/tbench-0.72.0-baseline.jsonl`，
以后 `python -m evals.report --ab 它 新的.jsonl` 直接比。

榜首在 60-83%，这个数落在区间内，**但别拿它跟榜单直接比**：24 道子集不是全量 89 道。

三个比总数更该记住的读数：

| | 轮数 | 墙钟 | token | 花费 |
|---|---|---|---|---|
| 解出来（32 次） | 21 | 502 秒 | 300k | ¥0.13 |
| 没解出来（14 次） | 29 | 1800 秒 | 519k | ¥0.41 |

**四项全部指向同一件事：失败的那些跑得更久、烧得更多，然后还是没做出来。**
7 次撞上限**全是墙钟**（0 次预算、0 次轮数）—— 而 0.70.1 已经证伪过"给更多资源"
那条路（预算 ¥1.0、轮数 120、超时 ×3 重跑，一道都没翻盘）。所以收敛性是有数据
撑着的下一件事，而且不用再花钱就能开工。**（0.78.0 做了闸和尺子，A/B 还没跑。）**

**24 道里 4 道一过一不过**（`dna-insert` / `mcmc-sampling-stan` / `path-tracing` /
`protein-assembly`）。一道题 = 4 个百分点，所以**解决率分辨不出小改动**，
判断改动照旧要看配对 A/B 的机制指标（`report.py --ab` 那段注释早写明了）。
稳定的失败样本有 5 道（两遍都 ✗），验证改进时那几道才有用。

### 选题：分层、不随机、可复验

`evals/tbench_pick.py` + `tbench_base.txt`（24 道）。难度构成对齐全量 89 道
（4% / 62% / 34% → 4% / 62% / 33%），层内按题名排序等距取，选法直接 import
`swebench.pick` 而不是另写一份。`--check` 有用例盯着：改了 `COUNT` 或换了数据集
版本却忘了 `--write`，用例会红。**改 `tbench_base.txt` 等于换了尺子。**

难度读 `harbor download terminal-bench@2.0 --cache` 落地的 `task.toml`。
**没用 `tomllib`**（3.11 才有，下限是 3.10），十几行按 section 扫一遍就够，
还顺带能进主用例集。

### 分类：把手工数换成 `_row()` 里写死的规则

`evals/tbench_report.py` 把 Harbor 的 `result.json` 翻译成 `report.py` 认得的行，
「判不了不进分母」的口径和 `--ab` 全部原样复用。判据全是翻 harbor 源码确认的：
`exception_info.exception_type` 是类名；**墙钟超时不阻止判分**
（`single_step.py::_run_agent` 记完异常照样跑 verifier，所以一条记录可以同时有
超时和满分）；没跑到判分那步时整个 `verifier_result` 是 None。

**判定顺序就是这块的全部内容**：先看分数 → 接口报错 → 墙钟 → 我们这边坏了。

### 七个坑，四个是「静默地把分数算错」

前两个是适配器装不上，预检（`--install-only`，**不打接口所以不花钱**）8 分钟挖出来的：

1. **有 python3 不等于有 pip。** 两道镜像里 `python3` 好好的、`python3 -m pip` 是
   `No module named pip`，而原来只在「压根没有 python3」时才去装东西。
2. **`--break-system-packages` 不够。** `build-pmars` 里 `typing_extensions` 是 apt
   装的、没有 RECORD 文件，pip 卸不掉它，依赖升级整条失败。加 `--ignore-installed`。
   **报错还特别会骗人**：第三档回退撞 PEP 668，最后带出来的是
   "externally-managed-environment"，指着一个根本不是病因的地方。

这两道占 24 道的 8.3%，而 **NotUsable 不进分母** —— 不修的话它们不声不响地从尺子上
消失，剩下 22 道还显得一切正常。**预检那 8 分钟就是买这个的。**

3. **两道墙钟闸，互相不知道对方的值。** 适配器里写死 1800 秒，而 Harbor 那道是
   「题里声明的 × `--agent-timeout-multiplier`」。首轮把 multiplier 提到 2.0，
   声明 1800s 的题于是有 3600s，**却在 1800 秒上被我们自己掐掉**，抛的还是
   `RuntimeError` 而不是 `AgentTimeoutError`，于是被归进「判不了」**退出分母**。
   48 次里 7 次这样。适配器看不见 Harbor 解析出来的值（`BaseAgent` 拿不到），
   所以没法"设得松一点"，只能**只留一道闸**：`DEFAULT_TIMEOUT = 0`
   （`DockerEnvironment` 里是 `if timeout_sec:`）。分类那侧另加一层保险，
   认报错文本而不是类名（`RuntimeError` 太泛）。

4. **接口报错必须算「判不了」。** 跑到一半 openrouter 余额见底，**11 次 trial
   全被判成「没解决」**，直接把解决率压下去。401/402/403/429/5xx 没有一条是能力问题。
   这已经是第二次栽在这儿（0.70.1 是上游 429，当时靠人眼看出来），所以这次写进
   `_row()`。

5. **查这件事时我自己先被骗了一次。** `grep 'requires more credits'` 命中 0，
   我差点据此写下"首轮没被污染"。真因是 feiyu 的输出**按终端宽度折行**，
   那句话被换行拆开了。**在这类日志里找东西，先 `' '.join(text.split())`。**
   用例里那条夹具是折了行的，不是我编一句完整的塞进去。

6. **超时那次一个字都没留下。** `run()` 里写 stdout 文件排在 `exec` 之后，
   超时时 `exec` 抛异常，那几行根本执行不到 —— **最值得看的那几次跑恰恰没有痕迹**
   （`adaptive-rejection-sampler` 撞超时那遍 18 轮 1379 秒，`agent/` 是空目录）。
   跟 0.70.1 修 `_fill_usage` 是**同一个形状**，当时只把取用量挪进了 `finally`。
   现在输出重定向进容器里的文件、`finally` 里捞回来，**顺带把 trace 也捞出来** ——
   每条 `turn` 都带 `ms`（这一轮多久）和 `calls`（调了哪些工具），
   那正是"1379 秒耗在哪"唯一的答案，原来跟容器一起删了。
   **`python3 -u` 不能省**：输出到文件是块缓冲的，进程被杀时攒着那段会跟着没掉，
   而那段恰好是它最后在干什么。

7. **`tokens` 这个字段名在两种 trace 行里是两个量。**

       run_end 的 tokens = `agent.tokens`  —— 累计计费量
       turn    的 tokens = `self.context`  —— 当时的上下文大小

   `_fill_usage` 跑飞时退回拿 `turn`，于是把上下文大小填进了 `feiyu_total_tokens`。
   0.70.1 写过一句"trace 每行都是累计值"，**那话对 `cost` 成立、对 `tokens` 不成立**。
   后果是一张看着合理其实没意义的表：算出来「解出来 292k 花 ¥0.13，没解出来 68k
   却花 ¥0.41」。修正后是 300k vs **519k**，跟轮数、墙钟、花费终于全部自洽。
   好在 `feiyu_partial` 当时就记了，**靠它把已有基线追溯修对了，没有重跑**。

另外 `-n 6` 并发在这台 28 核 31G 的机器上把内存跑爆（harbor 整个进程被杀，
26 次 trial 停在 17 次）。题 toml 里那个 1 cpu / 2G 是任务自己要的，不含镜像里
构建、测试、qemu 那些真正吃内存的东西。`--cmd` 现在给 `-n 3`。

### 验证方式

`tests/test_tbench.py` 45 条，**两个模块都不 import harbor** 所以进主用例集
（`tbench.py` 那个适配器不行）。三个 Python 版本各跑一遍全绿。

**八处承重行逐条做过回退验证**（拆掉哪一行，哪条用例红）。其中一条第一次没守住，
值得单记：**「认 `Z` 结尾」拆掉之后用例还是绿的** —— `fromisoformat` 认 `Z` 是 3.11
起的事，本机 3.14 怎么写都对。改成把 `tbench_report.datetime` 换成一个"像 3.10 的"
子类才测得到。跟 `list_files` 的 `full_match` 同一个形状：**下限是 3.10 的项目里，
凡是碰版本差异的行，用例必须把解释器降级了测，光在本机跑是假绿。**

适配器那几处进不了主用例集，照 0.70.1 的老办法：拿假 environment 在 harbor 的 venv
里单独验 —— 取用量五种收尾、收日志两条路（正常 / exec 抛超时，且原异常要照样冒出去）、
token 两种来源各一遍。报告链路拿一次真的 `nop` 跑（不花钱）验到底：reward 0.0 → 「没解决」。

### 跑法

    python -m evals.tbench_pick --cmd                    # 打出带 24 个 -i 的整条命令
    harbor run ... --install-only                        # 先预检，不花钱
    python -m evals.tbench_report <目录> [<目录>…] --out 这次.jsonl
    python -m evals.report --ab 基线.jsonl 这次.jsonl

给多个目录会合并，**同名题以最后一个为准、而且整道顶掉**：补跑时不要把
「旧的第一遍 + 新的第二遍」凑一对，那两遍跑在不同配置下，凑出来的稳定性是假的。

## 0.73.0：退出码、以及三个被证伪的收敛性假设

拿 0.72.0 那把尺子去查「为什么没解出来的跑得更久」。**结论是没有明显的浪费可砍**，
三个假设逐个被自己的数据否掉。过程比结论值钱，所以这一节主要记过程。

### agent 那侧改了两处（这两处是确定有用的）

**一、非交互下「没做成」现在能从退出码看出来。** `_run_turn` 返回 0 / 1 / 130
（130 是 shell 惯例）。一次性任务和管道两条路都用它；管道里**记住最坏的一次但
照旧把剩下的跑完**（中途停掉是另一种行为，这里只负责让它可见）。交互 REPL 没动。

起因是 0.72.0 那轮基线：openrouter 余额见底，11 次 trial 当场退出、**退出码 0**，
判分那边把它们记成「没解决」，解决率被白白压低 —— 是事后翻 stdout 才发现的，
而 stdout 恰好是超时那几次没留下的东西。屏幕前有人时无所谓（他看得见那句红字），
**没人看屏幕的场景里这个区别就是全部**。顺带的好处：`_finish` 把这个码写进
`run_end`，而 bench 适配器已经在读 `feiyu_exit_code`。

**二、`_API_CODES` 里根本没有 402。** 撞到的是 openrouter，它说的是
"requires more credits"，**一个 `insufficient` 都没有**，所以措辞表也漏了 ——
最常见的计费错误只会甩一坨原始 JSON。现在按状态码认，措辞那条留着给
**把 402 塞进 400** 的接口兜底。回退验证时这条一开始没守住：拆掉
`_API_CODES[402]` 用例还是绿的，因为措辞表先命中了 —— 补了一条报错文本里
一个线索都没有的用例才测得到。

### 三个假设，全错

数据来自 6 道题 157 轮的 trace（0.72.0 补上的 `_collect` 才捞得出来）。

**先量出一个硬事实：墙钟的 91-98% 花在等模型**，工具执行只占 2-9%
（`polyglot-c-py` 是 1076 秒里工具只占 17 秒）。而每轮耗时跟**输出字数**的
相关系数是 **+0.98**，跟上下文 +0.59、跟输入 token 只有 +0.25。
`ms` 量的就是模型那一段（`asked` 设在 `run_turn` 之前，trace 写在流结束后、
工具执行之前）。

1. ~~**卡在某条 bash 上等**~~ —— 工具只占 2-9%，不成立。
2. ~~**整文件重写在浪费**~~ —— 基线里「覆盖 7 次共 3315 行、是新建的 3 倍」，
   看着像大量重复生成。**错在没看序列**：`scheduler.py` 是
   249→259→496→596→584，`main.py.c` 是 204→237→249 —— **文件在长大**，
   整写一个正在成形的实现，重新生成是必要的，不是抄旧内容。
   A/B 里 `FEIYU_REWRITE_HINT` 触发 **0 次**，而且不触发是对的：
   57→106 行的重合率上限就是 54%，低于门槛。
3. ~~**白跑轮次多**~~ —— bash 非零退出 38/223 = 17%，相邻两次高度相似的
   36/211 = 17%。对一个在陌生容器里摸索环境的 agent 这是正常的探路水平。
   读完整份 transcript 更清楚：它先在 `/tmp` 搭最小原型验 polyglot 技巧、
   确认 gcc 不报警告、再写真文件 —— 这是该有的样子。

**所以「没解出来的 29 轮 / 解出来的 21 轮」不是"卡住了在转"，
更可能就是"难题需要更多轮"。** 剩下的解释是最朴素的那个：这些题就是要生成
几百行代码，而生成得慢。**耗时 ∝ 输出字数是条物理定律，harness 绕不过去。**
真想让墙钟下来，杠杆在模型那侧，不在这儿。这个结论本身有用：它把
「优化 harness 能提升多少」的天花板量出来了。

### `FEIYU_REWRITE_HINT`：留着，默认关

整写一个已存在的文件、且新内容六成以上的行跟原文一模一样时，在工具结果里说一句
改用 `edit_file`。**没往工具描述里加句子** —— 那儿早就写着「如果只是改动已有文件的
一部分，优先用 edit_file」，它读到了照样整写，这正是 0.48.0 那条结论。

它现在是**证伪之后留下的东西**：代价为零（真出现冗余整写才说话），但没有证据说它
有用。**这次的便宜之处在于，我是用一个默认关的开关 + 一次 ¥3 的 A/B 证伪了前提，
而不是像 0.69.0 那样实现一整轮之后才发现方向是反的。**

按行的**多重集合**求交，不是集合：用 set 的话一份「全是空行」的新内容会被算成
100% 重合。有用例盯着。

### 评测那侧顺手修的三处

- **A/B 开关到不了容器。** 适配器的 env 字典是写死的，于是 `FEIYU_ASSIST`
  压根进不去 —— 它当初是给 SWE-bench 那条路加的（feiyu 跑在宿主机，环境变量
  天然就在），挪到 Harbor 这边就断了，**而且断得很安静**：开关照样能设，
  只是不起作用，两档跑出来一模一样，看着像"这个改动没影响"。
  Harbor 的 `--agent-env` 也不行，它进不到我们这次 `exec` 的 env 里。
- **A/B 表的花费列显示成 0。** 整张表都用 `%10.0f`，一道题几毛钱时 ¥0.365 和
  ¥0.912 双双显示成 0，而"变化"那列还写着 -18.3%，自相矛盾得很隐蔽。
- **选错了题。** 挑的三道有两道两档都撞满 1800 秒墙钟 —— 主指标是墙钟而所有样本
  都被上限截断时，**这个指标没有分辨力**，不管改动有没有用都是 1800。
  下次做墙钟相关的 A/B 要挑**能跑完**的题。

### 一条方法上的提醒

查这批数据时我先用 stdout 里的关键词数报错率，得出「102 次调用、1 次报错」——
而 trace 里光 bash 就有 99 轮。**feiyu 的工具结果在屏幕上是截断显示的**
（「… 还有 4 行」），拿它当数据源会静默漏掉大半。能用 trace 的时候用 trace。

## 0.74.0：桌面端以人为本的五处，和 Anthropic 主题

用户说「把 feiyu-desktop 做得以人为本一点，人性化一些，美化」。
**先跑起来真渲染看了一遍**（`dev/shoot.mjs`，不是只读 CSS），结论是这套视觉系统本身
没问题——对比度量过、24px 基线网格、token 规矩写得很死。不够「以人为本」的是行为。

### 一、确认卡分两档：日常是中性的，只有回不去的才见红

原来**每一次确认都是红底红杠**，包括改一行文件这种一天点几十次的事。
而 `--alert` 那条规矩自己写着：红的全部作用是「在一屏黑白里让需要你的东西一眼
能找到」—— **前提是它得稀少**。天天被红色叫住，红就退化成背景色，
真出现 `rm -rf` 那次反而认不出来。

`looksDangerous()` 认十来种「做了回不去」的：`rm -rf`、`git push` /
`reset --hard` / `clean -f`、`sudo`、`mkfs` / `dd` / `shred`、`chmod -R`、
`drop table`、写 `/dev/`、`kill -9`、`npm publish`。判定放前端，跟 `ui.py`
那条分层原则一致（后端只给结构化事实，怎么画归这边）。**宁可漏判不可错判**：
漏了只是少一次红，错了是天天见红、回到原点。

危险那档另加一行明说「回不去的操作」——**光靠颜色不够**，约 8% 的男性有红绿色觉
障碍，而这恰好是最不能看错的那一次。

### 二、按钮按「点错了会怎样」排，不按「多常用」排

原来是 `同意一次 | 拒绝 | 本轮都允许 | 永久允许(...)`，四个并排、宽度样式一样。
**点错「同意一次」最多白跑一步，点错「永久允许」是以后再也不问了** ——
代价差一个量级，视觉权重却一样。现在前两个（代价最小的）留在左边，
扩大授权的两个推到右边、字更淡，要点到得有意为之。

**`.btn--wide:first-of-type` 是个永不命中的选择器**：它问的是「父元素里同标签的
第一个」，而那是「同意一次」（没有 `--wide` 类）。要用相邻兄弟
`.btn:not(.btn--wide) + .btn--wide`。截图里看着像"没生效"，是夹具加了断言才逼出来的。

### 三、空对话时有话说

以前第一次打开是：侧栏「还没有历史对话」，中间纯白，**能干什么全靠猜**。
现在有一块「开始」：四个可点的例子 + `@` / `/` / `Ctrl N` 三个记号。
点例子是**填进输入框但不发出去** —— 让人能改，别替他做决定；光标落在末尾，
不是选中整句（多半是要接着改的）。

写成跟 `.block` 同一套两列网格，所以刊头那条纵线不会断。

### 四、状态栏说人话

`默认 · 23% · ¥0.0684 · v0.73.0` → `默认 · 上下文 23% · ¥0.07 · v0.73.0`。
23% 原来没说是什么的 23%；而四位小数是机器的精度不是人的——看一眼要判断的是
"贵不贵"不是"几厘几毫"。精确值留在 `title` 里。不到一分钱显示 `<¥0.01`，
别写一串 0（那串 0 看着像精度，其实是噪音）。

### 五、短对话贴着输入框

`.sheet` 改成 flex + `justify-content: flex-end`，`.stream` 加 `margin-top: auto`。
原来一问一答之后上面顶着天花板、下面空一大片到输入框，那片空洞比对话本身还高。
满屏之后这条不起作用，照常从上往下滚。

### 六、Anthropic 主题（第三套）

**跟「复古」不是一回事**，虽然都偏暖：复古是泛黄的纸（`#fbf6ea`）+ 赭色，做旧；
这套是近乎白的暖中性（`#fbf9f5`）+ 陶土橘（`#d87756`），干净。用户整台机器
（kitty / GTK / 壁纸 / nvim）都是这套色，桌面端跟上。

**α 是量出来的，不是照抄默认那套。** 这张纸比纯白暗一点，照搬 0.56 / 0.44 会掉到
3.69:1 / 2.66:1，两个都够不着。按 panel 那一侧（更难的那面）反解：
`--muted` 0.67 → 纸 5.19:1 / panel 4.63:1；`--faint` 0.53 → 3.40:1 / 3.03:1。

`--accent` 就是 `#d87756`，而它对这张纸只有 **2.98:1** —— 够不着任何文字标准。
这不是问题：这套系统里 accent「只做线和面，永远不做字」，那条规矩在这儿成了安全网。
`--alert` 压到 `#a4262c`（6.91:1）才跟陶土橘分得开，`--plus` 走墨绿（跟复古同理）。

### 夹具补了三处断言 + 三张图

**颜色和布局这类东西，光看图会放过去，得量。**

- 两档确认卡的**底色必须不一样**（一样就是危险分级没生效）、`rm -rf` 必须判成危险、
  扩大授权的按钮 `margin-left` 必须非 0
- 空对话必须出现「开始」那块，点一下必须填进输入框
- Anthropic 主题：**版面一格不能动**（跟复古同一道检查），而且**底色不能跟另外
  两套撞**（两套都偏暖，撞了就是白加一套）
- 新增 `0_hello` / `2b_danger` / `13_anthropic` 三张图

自己踩的一个夹具坑：`0_hello` 一开始拍不出来 —— 插在 FLOW 之后没重新导航，
流里还有东西，而「空对话」这个前提就不成立了。

## 0.74.1：一次专门的找 bug，三处

用户说「检查一下有没有 bug」。照老规矩：lint 一个真 bug 都没抓到（还是那 4498 条
全角标点的 RUF001-003），三条全是**读新代码 + 拿运行时探**出来的。

### 1. wiki MCP 的路径穿越（最严重，而且是同一天刚写的代码）

`wiki_read("../.config/feiyu/models.json")` **原样返回了用户的 API key**。
`_find()` 里 `ROOT / name` 那条捷径对 `..` 一点抵抗力都没有，而 `~/wiki` 的上一级
正好是家目录。

**根因是心态不是代码**：我把「页名」当成了内部标识符，而它是**模型给的，
也就是外部输入**。凡是 MCP 工具的参数，都是外部输入。
修成 `path.resolve().is_relative_to(ROOT.resolve())`。
回退验证：拆掉那行检查，key 立刻又漏出来。
（这个 server 在 `~/wiki/scripts/mcp_server.py`，不在本仓库。）

### 2. 管道里按 ctrl-c 停不下来

`cat 活儿.txt | feiyu --mode auto` 中途按 ctrl-c，只打断当前这一条，
**后面几条照样执行** —— auto 模式下那意味着它接着改文件、接着跑命令。
**人按 ctrl-c 的意思是"全停"。**

这条 0.73.0 之前就在，0.73.0 把退出码带出来之后才看得见。现在 130 就 `break`；
**出错（1）不停** —— 一条失败不代表后面几条做不了，那是另一回事。

### 3. 桌面端「开始」那块会自己删自己

`syncHello()` 把自己也算进「有没有内容」（它本来就是 `streamEl` 的孩子），
所以在空对话上连调两次就把它删掉，第三次又加回来，来回跳。
当时所有调用点恰好都是「先清空」或「先追加」，所以没露出来 ——
**加一个调用点就会静默失效，而空状态消失是不会报错的。**

### 不修的一处

`echo '注意不要 rm -rf'` 会被 `looksDangerous()` 判成危险 —— 字符串里提到也算。
代价只是多一次红，而要修得正经解析 shell 引号，那个复杂度换不来什么。
（注意这跟那个函数注释里「宁可漏判不可错判」是相反方向，属于明知故犯，记在这儿。）

### 查的时候自己被骗了一次

修完第 3 条去验，探针报「hello 不在、`syncHello` 是 undefined」，我差点以为修出了
新 bug —— **其实是 devserver 根本没起来**，探针连的是空页面（`curl` 一下 `app.js`
返回空才发现）。跟 0.70.1 对账那次是同一个形状：**一个结果不对时，
先确认测的是不是那个东西，再去怀疑那个东西坏了。**

顺带：起 devserver 要用 `nohup ... & disown`，`( ... &)` 那种写法在这个环境里
会跟着命令一起没掉，而它死了之后页面是空的、不报错。

## 0.80.0：以人为本的四个时刻，和一条在两个引擎里各错一半的 flex-end

用户说「以人为本为主题，继续雕刻前端，有一些动效动画，可以参考权威设计网站」。先真渲染审了一遍，
按「人用它的顺序」挑了四处（开口 / 点头 / 没看见的 / 手感）问用户，**四处全选**，另问了
「人不在时要不要发系统通知」（要）。依据查的是原文不是凭印象：Apple HIG · Motion（动效要有目的、
频繁的交互少加、别让人等动画）、NN/g（0.1 / 1 / 10 秒三条线；动效 100–500ms、出现比消失慢）、
Vercel Web Interface Guidelines（可打断、原点要对、只动 transform / opacity）、Material 3 的 emphasized 缓动。

### 一、开口：空对话时输入框在正中（只对 Anthropic 那套皮）

原来问候居中、输入框在最底下，中间空三百来像素 —— 一句「问它点什么」和能问的那个框隔着半屏。
现在问候 + 例子 + 输入框是一组，上下留白 42 : 58（视觉中心比几何中心高一点）；发出第一句时
输入框和你那句话一起用 460ms 落到底。跟 claude.ai 第一屏一个样子（Jakob 定律）。

- **只用 translate，不动布局**：问候照常贴在对话栏底部，对话流和输入框一起上抬同一个 `--lift`
  （`measureLift()` 量、写进变量）；落到底就是 translate 过渡回 0，合成层上走，不重排。
- **抬的是 `.stream` 不是 `.sheet`**：`.sheet` 抬上去会盖住标题栏，那几个钮就点不到了（夹具拿
  `elementFromPoint` 量）。
- **敲字换行时卡片往下长、顶边不动**：量的时候拿「空着时那一行的高度」（`rowBase`）当它的高度，
  多出来的算成往下长 —— 整组重新居中的话，每换一行输入框就往上窜半行，字在眼皮底下跳。抬到 0 之后才往上长。
- **「开始」的位置用 offsetTop 量，不用它自己的 rect**：它刚出现时在浮现（translate 8px），量进去就差 8px，
  浮现完没人再量。
- **只在「空对话里点了发送」之后滑**（`glideArm`，4 秒内有效）；开旧对话、机器人开场、别的主题直接撤，不演。
- 问候先淡出（`--pop-out`），你那句话**等它走了再出来**（`animation-delay`）：同时交接的那几帧两行字压在一起，截图看见的。
- 这套皮里补全和换模型那两张清单**改成浮层**（absolute），不再挤进输入区的流里 —— 在流里的话打一个 `/`
  对话栏就矮一截，正在看的那段被顶走。默认主题里照旧在流里。
- 那只狗点一下头、四个例子错开 40ms 递过来，都只在「开始」出现时一次。

### 二、点头：确认卡用键盘答

- 按钮上 `1 2 3 4`，跟摆的顺序一样。**卡片出来 600ms 之后才接键**（回不去的那档 1200ms），键帽到点才浮出来 ——
  人可能正在打字，卡片弹出来那一刻手指还在往下落，那个数字不该变成「同意」；之前按的数字照常落进输入框当字。
- 只在没打字时接：焦点在别的输入框、输入框里有字、补全 / 换模型 / 设置开着、`isComposing`（输入法在选词）都不接。
- 键盘没有 `:active`：先打 `data-pressed` 演 110ms 按下去，再 click —— 看得见按的是哪个。
- **回车不当「同意」**（刻意的）：空输入框上回车是最条件反射的一下，数字至少得有意为之。
- 「自己写」原来是 `prompt()` —— 系统对话框、整个窗口卡住，跟删对话那块「不用 confirm() 弹窗」自相矛盾。
  现在卡片里展开一行；esc 只收这一行（要 stopPropagation，全局 esc 在一轮跑着时会发 interrupt）。
- **发不出去就别装作答了**：原来 `send` 失败（断线）照样把按钮换成「已回答」。
- 答完收成一行回执：高度用 WAAPI 过渡（`settle()`，时长曲线从 CSS token 里读），前面一个勾是画出来的。
- 出现时叫一下再安静：默认主题左边那条线亮 accent 再退回灰（危险那张本来就红，不闪）；Anthropic 外面漾一圈陶土色（危险漾红）。
- **状态行在等你的时候说「等你点头 0:12」**：原来这时候秒表照样写「干活中」—— 它什么都没在干，这句一直在说反话；也不再算「慢」。

### 三、没看见的

- 往回翻着、或者窗口不在前台的时候长出来的块（`markUnseen`），第一块前面铺一条「以下是新的」（accent 的线，
  从右往左画出来）；「回到最新」从方钮长成「↓ N 条新」（第二列 0fr ↔ 1fr 过渡，不用 JS 量字宽），
  点它落到那条线上 —— 从第一条新的读起；线已经在上面了就到底。「在跑」那块不算。
- **看到底了只清数不撤线**；下一批没看见的来了就挪线；你开口说下一句时撤掉。
- `away` 只认真发生过的 blur / 隐藏，**初值不拿 `hasFocus()` 猜**（无头浏览器里恒为假，夹具里每块都会被当没看见）；
  按下鼠标也算回来了（捕获阶段听）。
- 数清零后药丸里的字留着（缩回去那一下里面还得有字），`aria-label` 跟当前的数走。

### 四、人不在时叫一声（系统通知）

- **只在人不在的时候叫**：要你点头（bash 带命令原文，危险的标出来）；一轮跑了 ≥10 秒结束（NN/g 那条线：
  过了 10 秒人多半去干别的了），带它最后说的第一句（`plainLine` 去掉 markdown 记号）。回放进来的 `turn_end`
  没有 `turnFrom`，不叫。
- Rust 加了个 `notify` 命令调 `notify-send`：**Wayland 上 tao 的 `request_user_attention` 走 GTK3 的 urgency hint，
  什么都不发生**，系统通知是唯一靠得住的路。带 `desktop-entry` hint（GNOME 认得是 Feiyu 的）；`--` 之后才放标题正文
  （以 `-` 开头的不会被当选项）；正文转义 `& < >`（GNOME 按 body-markup 解析）；**按字符截**（按字节会截在半个汉字上 panic）。
- 子进程 `reap()`（起线程 wait），顺带修了 `open_url`：原来 `spawn()` 完不 wait，每点一个链接留一个 xdg-open 僵尸。
- 真通知夹具看不见，只能数调用（夹具把 `notifyUser` 换掉）；**真弹出来长什么样得用户自己看一眼**。

### 五、手感：动效 token

- 新加一组，**进场和离场分开**：`--ease-enter / --ease-exit / --ease-move`（M3 emphasized 三条），`--press-dur 90`
  （<100ms 才算即时）、`--pop-in 200 / --pop-out 120`、`--side-dur 240`、`--glide-dur 460`。浮现那组（8px / 500ms）一个数没动。
- 按压两档：小方钮 0.92、带字的 0.97（原来都是 0.94，宽按钮两边各收进去十几像素，像整块塌下去）；
  按下走 `--press-dur`，松手才回弹 —— 原来按下松开共用 220ms，按下去的反馈要过 0.1 秒才出来，手感是黏的。
- 浮层从触发它的地方长出来（`transform-origin`），**收起时 `hidden` 立刻为真、画面上再淡出**（`data-leaving`）——
  `[hidden]` 那条带 !important，得用更高的特指度 + !important 压回显示。
- 侧栏：那一列的宽度过渡到 0，侧栏本身定宽、同一时长同一曲线往左平移 —— 右边缘一直贴着列线，整块滑出去，
  不挤不折；滑完才 `visibility: hidden`（Tab 走不进去）；`data-motion` 第一帧画完才挂（不然记住的「收起」启动时也要演一遍）。
- 「回到最新」露面时从右下角冒出来。`prefers-reduced-motion` 那段全补上了（勾在关了动画时要直接给终点，
  不然停在「没画」那一格）。

### 六、这一轮最要紧的一处：`.sheet { justify-content: flex-end }` 在两个引擎里各错一半

0.74.0「短对话贴着输入框」加的那条。内容超出一屏之后：

- **Chrome（夹具）**把开头放到滚动原点上面 —— `scrollHeight == clientHeight`，长对话的开头永远滚不上去；
  `atBottom()` 恒为真。
- **WebKitGTK（产品）**把它当 column-reverse：`scrollTop` 是 −max…0、0 在底边。于是 `atBottom()` **恒为假** ——
  长对话里「回到最新」一直挂着；往回翻着看时底下一长，正在读的那段就被往上推走（偏移从底边量）。
  这正是 `keepBottom` 注释里说「最烦人」的那件事，一直在产品里发生。
- 修：撤掉 flex-end，只留 `.stream` 的 `margin-top: auto`（有富余时推到底，超出时从顶上排）；`paintSaying` 补 `keepBottom`
  （以前是 WebKit 那个翻过来的原点白送的「跟着长」）。
- **怎么逮到的**：写「没看见的」时计数恒为 0；Chrome 里量出 streamTop −742 而 scrollHeight == clientHeight；
  再用 python-gobject + WebKit2 4.1（跟产品同一个引擎）离屏量出负的 scrollTop。回退后在 WebKit 里：到底了
  atBottom 为假、「回到最新」露着、往回翻时正读的那段被推走 40px、**24 块被当成「没看见」**—— 没这个修复，三就是个灾难。
- **教训：夹具跑 Chrome，产品跑 WebKitGTK，滚动 / 布局这类引擎行为两边都得量。** 新加 `desktop/dev/webkit.py`
  （离屏窗口，不上屏）专门量这一类：居中、落到底、滚动原点、往回翻不被推走、侧栏的过渡。两个坑：
  要 `WEBKIT_DISABLE_DMABUF_RENDERER=1 WEBKIT_DISABLE_COMPOSITING_MODE=1`（不然 GDK 起不来 GL，直接 core dump，
  脚本里 setdefault 了）；**要用一次性的 WebContext** —— 默认的把 localStorage 存进 `~/.local/share/<脚本名>/`，
  上一轮收起过侧栏，下一轮「收侧栏」那步什么都不发生（我还以为是 WebKit 不演过渡，查了半天）。

### 七、顺带：确认卡开着时 esc / 停止键没用（后端，`serve.py`）

测夹具时真 agent 卡在一张没人答的卡上、之后的命令全没人回，追下去是这个：中断是读线程
`_thread.interrupt_main()` 扔的，**那只是立一面旗、不是真信号** —— 主线程正阻塞在 `Protocol.request` 的
`box.get(timeout=600)` 上，要等满才回来看旗。量过：4 秒的 get、中断扔在 0.5 秒，被打断在 4.00 秒。
所以确认卡开着时按 esc 或 ■，十分钟里什么都不发生，等你终于点了同意，它才「刚好」停下。
修：中断时 `fail_all()` 把等着的放掉 —— 立刻醒、按没回话处理（拒绝），接着撞上那面旗。
用例要把线程摆得跟服务里一样（**主线程等回话**，中断从别的线程来），反过来摆的话中断落在 `_dispatch_in` 半路上。

### 夹具

- `shoot.mjs` 新一节九项 + 四张图（23–26）：居中比例、标题栏点得到、敲字往下长、默认主题不抬、落到底有中间态且归零、
  长对话滚得到头且到底时 atBottom 为真、线在第一块新的前面且药丸数对、人在不叫 / 不在时带命令和第一句、
  浮层浮着不挤且先淡出再藏、侧栏中途露一截且滑完藏起来、键帽刚出现不接到点才接、**关了动效一个都不演**
  （`Emulation.setEmulatedMedia` 模拟 reduced-motion 再重开页面：REDUCED 是开页面时读一次的）。
  720px 那条改量「露在屏幕上的宽」（侧栏不再压成 0 宽）。
- `input.mjs` +13 条，真按键：没到点按数字落进输入框、到点按 1 同意、框里有字时数字是字、危险档 0.8s 不接 1.2s 接、
  补全开着不接、「自己写」不弹 prompt、esc 只收那一行、写完回车当回答。这一段先把真 agent 的收件口堵上 ——
  附件那条真跑的一轮跑完推来的 turn_end 会（正确地）撤掉假卡的键，单独跑全过、串起来随机挂。
  附件那条之后立刻打断（0.80.0 起中断能放掉等着的确认），不然模型要是去 read 那张图就会留一张真卡。
- **划词那条浮条竖向没夹**：`showPick()` 注释写着「夹在视口里」，只夹了横向。对话栏真能滚之后，夹具选中的
  第一段落在视口上方，浮条跑到 y = −250（选区比一屏还长时也会这样）。竖向也夹住了。
- **`input.mjs` 开头和收尾那两处「打断一次」从来没发出去过**：写的是 `window.socket`，而 `socket` 是顶层 `let`，
  不在 `window` 上。改成 `socket`。
- **回退验证**（R1–R11，四批）：每条新断言都红过。两处夹具在回退时**崩了**而不是报红（`querySelector` 返回 null），
  改成 null 安全 —— 夹具该报红，不该崩；一条键帽断言看的是 opacity，没等待时量的那一刻淡入刚起步、恰好是 0，
  照样骗过去，改成看 `data-armed`。

### 不修的一处（跟这轮无关，记下来）

`tests/test_sandbox.py` 的 `test_真的断网_而且告诉它可以带_network` 在 shell 里设着 `http_proxy` 时挂：
沙箱里 curl 走去连 127.0.0.1 的代理，报的是 `Failed to connect ... over proxy`，识别断网的那段没认出来，
于是**不提示模型可以带 `network: true`**。去掉代理变量全过。这台机器平时就带着代理（见 supervpn），
所以终端里的 feiyu 真会碰到 —— 该修，但不在这轮的范围里，没动。

### 验证

`shoot.mjs` 全绿（含新一节）、`input.mjs` 114/114（连跑三次）、`fuzz_ui` 102 条、`md_check` 全过、`sessions.mjs` 8/8、
`webkit.py` 8/8（连跑三次）、Rust 24 条、Python 1476 条（上面那条要去掉代理变量才过，跟这轮无关）。
改完 `desktop/ui/` 两个二进制都编了；产品那个要**关掉窗口重开**才换得上（没有热重载），`serve.py` 那处也是重开生效。

## 0.79.1：右下角那个模型名终于点得动了

用户拿截图来说「希望点右下角可以切换模型」。查下来**那个钮从 0.52.0 起就摆在那儿、
有 `title="切换模型"`、悬停会变色，CSS 注释里还写着「点一下走 /model」—— 却从来没绑过点击**。
看着能点、其实是死的，就是 0.77.1 那条说的「比不给还糟」。

- 点一下在它头顶弹一张清单（跟补全菜单同一块地方，一次只开一张），按配置里的顺序排，
  在用的那条标出来。超过 8 条给筛选框（用户那台 33 条），上下键走、回车换、点外面收。
  **不加厂商小标题**：头一版按厂商分了组，用户看过说不要 —— 每行底下那行 id 本来就带着
  厂商（`anthropic/claude-sonnet-5`），小标题是同一件事说两遍。
- **换还是走 `/model <名字>`**，跟设置面板那张表同一条路；清单每次打开现取（`models` op），
  设置里刚加的那条得立刻出现在这儿。
- 没 key 的那条点不动（换过去必然 401）；点在用的那条只收起来，不白发一条命令；
  一轮正跑着时底下说一句「跑完才换过去」—— 命令会排在这一轮后面，不说的话点了没动静，跟以前一样像死的。
- esc 收清单**要 stopPropagation**，全局那个 esc 在一轮跑着时会发 interrupt（0.50.3 补全菜单踩过同一个坑）；
  清单开着、焦点却还在钮上时 esc 走全局那条，所以全局那条最前面先截一下。
- 钮后面加了个 ▾：光一个名字看不出能点，用户就是这么以为它是死的。三角绝对定位钉在右边，
  名字长了被省略号截断时它不跟着没。

**夹具逮到的一处**：高亮「先停在在用的那条」只在点开那一刻清单已经在手时成立 —— 清单是点开之后
才取回来的，第一次画的时候定成了第 0 条，后面不再动。改成「人没动过键盘就一直跟着在用那条」（`menu.moved`）。

**TDZ**：`setStatus` 要刷新清单底下那句话，而它定义在文件前面；`modelsEl` 要是在文件后面用 const 声明，
哪天有人在初始化时同步调一次 `setStatus` 就抛 —— 两个元素的 const 挪到了文件顶上跟 `paletteEl` 放一起。

`input.mjs` 加了 16 条（用一份编好的十条清单，**等真 agent 回的那份 models 落地再灌**，不然被它盖掉），
101/101；`shoot.mjs` 加三张图（默认 / anthropic / anthropic 390px）并量清单在视口里、不横出去。
回退验证：`ui/` 退回去，夹具在清单那段直接报错。

## 0.79.0：升级一轮、claude.ai 的皮，和一次全面检查

用户说「升级 feiyu-agent，UI 也美化一下，最后做个全面检查」。升级挑了四样（依赖 / 读侧边界 /
记教训 / 拆 `FEIYU_ASSIST`）并问还有什么方向；UI 挑了「往 claude.ai 的观感靠」，**明确同意推翻
「除图片外全是直角」和「主题只换调色板」两条**。

**这一轮是在 git worktree 里做的。** 上一个会话起的 A/B 还在跑，而 tbench 适配器每道题都把
**工作区里的 `feiyu/` 现场传进容器**（`upload_dir(_feiyu_src())`），用户日常的 `feiyu` /
`feiyu-desktop` 也直接从这个目录跑。**A/B 跑着的时候不要动主目录的 `feiyu/` 和 `evals/`**——
改一个字，后面那几道量的就是另一个版本，而且不报错。

### 一、OpenRouter 上的 Claude 一直在付全价（这轮最值钱的一处）

`providers.py` 那句「openai 兼容那边是服务端自动缓存」对 OpenAI / DeepSeek / Qwen 成立，
**对走 openai 兼容格式的 Claude 不成立**。实测（haiku-4.5 经 OpenRouter，1.5 万 token 前缀连发两次）：
不打断点两次命中都是 0；system 上打一个，第二次 $0.0156 → $0.0016。用户 33 条配置全走 OpenRouter，
其中 5 条 Claude —— **从来没吃到过缓存**。

- `_wants_marks`：模型 id 里带 `claude` 就打（`anthropic/claude-*` 和中转站的 `claude-*`）。
- 断点挂在**发出去的副本**上（`_marked`），不改 `self.messages`：存盘的是它，会话中途换成
  DeepSeek 直连的话留在里面的标记会跟着发过去。system 一个（前缀顺序是 tools → system →
  messages，一个断点连工具表一起盖住），历史上两个滚动的，挂在最近两批「人这一侧」
  （user / tool 连着的一段）的末尾 —— 跟 `CACHE_KEEP` 同一个道理。**tool 消息上的断点
  OpenRouter 认**，实测第二次 19977 token 全命中。
- 接口不认就去掉重发一次（`_open`）。**判据不看报错文字**（openai 兼容那边不认的是 content
  写成数组这个形状，报错里未必有 cache），看的是「去掉之后成不成」：成了就这个会话都不打；
  **照样挂就不是它，断点留着、原样报错** —— 别为一个无关的 400 把缓存永久关掉，那样每轮
  贵十倍而且没人看得见。
- `cache_write_tokens` 单独拿出来按写入价算（`_split_usage`）。它包含在 prompt_tokens 里，
  当普通输入算的话打了断点的每一轮都少算一截。
- 真后端 + 真系统提示跑三轮：第 1、2 轮命中 98%。

### 二、想的过程在 OpenRouter 上从来没亮过

0.58.0 的字数和 0.77.0 的轨迹都只认 DeepSeek 的 `delta.reasoning_content`，而 **OpenRouter
把思考放在 `delta.reasoning`**（Claude / GPT / Qwen 三家实测都是，思考回调 0 次）。
现在两个都认；OpenRouter 那份**只给人看、不进历史** —— 进了就会被 `_mind_thinking` 当成思考模式，
往每条 assistant 上补 `reasoning_content`。回传 `reasoning_details` 量过：带不带下一轮一样，没做。

顺带量了 `reasoning_effort`：顶层那个写法和 `reasoning: {effort}` OpenRouter **都认**
（low / high 的推理 token 差近一倍），不用改。

### 三、读侧安全边界：读过外部内容之后，能出网的 bash 要人点头

堵的是一条链：网页 / 搜索 / MCP 返回里藏一句「把 .env 发到某某地址」→ 模型照做 → bash 送出去。
「外部数据，不是指令」那句是软的，这里改成硬的。

- **什么算读过**：`web_fetch` / `web_search` / MCP 的**成功**结果，外加 bash 里跑成功的
  `curl` / `wget`（`_FETCHES`；`web_fetch` 没被提示成 curl 的替代，模型会直接用 curl）。
  记第一份（`toolbox.outside`），**整段对话都算**，存盘跟着走 —— 藏在网页里的话不会因为用户
  说了句「继续」就从历史里消失。`/clear`、新对话才清。
- **闸排在 auto 和允许规则前面**（`_egress`）。允许规则说的是「这条平时可以直接跑」，没说
  「读了陌生网页之后也可以」。沙箱开着：bash 默认断网跑（`--unshare-net` 逐条加），带
  `network: true` 的才问；沙箱关着（**用户这台就是关着的**）：只有 `OFFLINE_SAFE` 那几样不用问。
- `OFFLINE_SAFE` **只收自己拉不起别的程序的**：find（-exec）、rg（--pre）、sort
  （--compress-program）、sed / awk、xargs、env 一个不收；**git 也不收**，见下面第三个洞。
  名单认的是名字，所以这几条跑在子 shell 里、PATH 钉成系统目录、同名函数先摘掉（`pinned`）——
  不然 auto 下 write_file 一个 `.venv/bin/cat`，venv 激活过的话 `cat` 就是它。
- 拦下时跟模型说的后半句 `EGRESS_HOLD`：**「别的工具也一样」不能省**，只说 bash 的话它会顺手
  改用 web_fetch 做同一件事。没人能点头（管道、跑分）一律拒。评测两处（`run._isolate`、tbench
  适配器）设 `FEIYU_TRUST_OUTSIDE=1`，口径跟以前一样。
- **没管的**：web_fetch / MCP 本身也是出口（管了的话「搜一下再点开结果」每一步都要点头，
  这道闸会被嫌烦关掉）；git clone 拿回来的代码；MCP 报错里的文字。

**做这个时挖出来的三个洞，都是实测能打穿的：**

1. **沙箱里碰得到 docker。** 用户在 docker 组里，沙箱里 `docker ps` 照样列得出容器，于是
   `docker run -v /:/host` 一条就能往任何地方写 —— 「写：真隔离」**从 0.19.0 起就是假的**。
   workspace 档把 docker / podman 的 socket 盖成 /dev/null（socket 也是文件，绑得上）。
2. **断了网照样解析得了域名。** 只加 `--unshare-net` 时 `getent hosts example.com` 照样出 IP：
   nss-resolve 走的是 `/run/systemd/resolve` 下的 Unix socket，网络命名空间管不着 —— 把数据编进
   子域名就送出去了。断网的两档（strict、读过外部内容之后）连整个 `/run` 一起盖掉。
   **`--tmpfs /run` 要排在工作区那条 bind 前面**，工作区在 /run/media 下（U 盘）时才不会被遮住。
3. **git 工具会执行仓库配置里的程序。** `.git/config` 在工作区里、auto 下 write_file 不用问，
   写一句 `core.fsmonitor = …`，下一次 git 工具的 `status` 就把它跑了 —— git 工具在沙箱外、
   出厂放行，**沙箱开着也挡不住**。读的那几样一律 `-c core.fsmonitor=false`，diff 加
   `--no-ext-diff --no-textconv`。用例里真的配了一个会建文件的 fsmonitor，撤掉加固那个文件就出现。

### 四、记教训（`lessons.py`）

0.30.0 时定的边界：**只到「记教训」为止**，不碰自己的代码和系统提示。

- 同样的失败第二次：结果里说一句「别原样重试，先弄清楚为什么」；第三次：叫它停下来问人；
  之后不再唠叨。缓过来之后（同一个工具在同一个对象上成了；bash 看的是同一个程序，
  `pytest` 和 `.venv/bin/pytest` 算一个）提一句「项目特有的就 remember」，一段对话只说一次；
  只读 / 计划模式和关掉了 remember 时不提（它会被拒，白碰一次壁）。
- **「同一件事」认得很窄，宁可漏认不可错认**：bash 是同一条命令 + 输出的最后 600 字一样；
  别的工具是同一个对象 + 同样的报错；数字一律抹成 `#` 再比（耗时、临时目录编号、行号每次都变）。
  改完代码再跑测试、挂的换了一批，那是在往前走 —— 用例盯着这条不许说话。
- trace 里 `stumble` / `lesson_nudge` 两种事件，以后 A/B 量它有没有用就看这两个数。

### 五、`FEIYU_ASSIST` 拆成三个开关

`FEIYU_SYNTAX_CHECK` / `FEIYU_REPRODUCE` / `FEIYU_OUTLINE`，总闸照旧。**总闸以前多摘了一样**：
它把整个 symbol 工具拿掉了，连 0.36.0 就有的 definition / references 一起。现在总闸 = 三个分开关
都关，只摘 outline（enum 去掉、说明里一个字都不许提 —— 提了它会去调，白花一轮）。
**拿 FEIYU_ASSIST=off 跟 0.79.0 之前的存档比时记着这条。**

转发名单抽成 `tbench.AB_SWITCHES`，`test_tbench` 从 feiyu 的源码里抠出所有开关名、从适配器
源码里（`ast`，它 import harbor 进不了主用例集）抠出名单，漏一个就红 —— 0.73.0 那个坑的守卫。

### 六、依赖

anthropic 1.5 → 1.8、openai 3.14 → 3.19，同一份代码换依赖跑全套全绿；真请求验了三家。
`pyproject` 的下限原来是 `anthropic>=0.40` / `openai>=1.30`，而代码早就在用 `output_config` 和
adaptive thinking —— 老 SDK 装上去不报版本冲突，第一轮请求才 TypeError。改成跑过的那一版。

### 七、Anthropic 那套皮

全挂在 `:root[data-theme="anthropic"]` 下面，默认和复古一个像素不动。依据是
`~/.claude/awesome-design-md/design-md/claude/DESIGN.md`，颜色以用户那套为准
（#C96442 / #B3552F / #FBF9F5 / #141414 —— 2026-09-20 定案，0.74.0 写的 #D87756 是旧的一档）。

- **陶土色上放白字只有 3.90:1。** 带字的实心按钮用 `--accent-deep`（#B3552F，4.93:1），按下
  `--accent-deeper`；`--accent` 只给线、点、图标和发送键（图形只要 3:1）。链接字也走 deep。
  墨色 α 在纸 / 侧栏 / 卡片三个面上挨个量过，数写在 token 旁边。
- 字体全是本机的（CSP 只放 'self'）：界面 Adwaita Sans（GNOME 从 Inter 分出来的，DESIGN.md
  说 Inter 是 StyreneB 的替身），正文 IBM Plex Serif + 思源宋体，标题 Crimson Pro。
- 没有 meta-rail：你说的是气泡，它说的是衬线正文，它动手是小卡片，系统的话是一行小字 +
  药丸标签（只有这一种块留着标签）。块要分得开，所以 `block()` 的调用处补了
  `block--say` / `block--note` / `block--plan` 修饰类（默认主题没有规则挂在它们上）。
- 输入区换成卡片只动了 `grid-template-areas`，DOM 没动 —— 默认主题那条「敲的字和正文对齐」
  照旧成立。发送 / 停止键里的记号用 `::before` 画，**按钮得是 flex 不能是 grid**：藏起来的
  「→ / ■」字形是个匿名格子，会占掉一行把记号挤到上半截。
- 空对话那句问候：`greeting()` 按时段给，旁边那只狗是 `logo.svg` 当 mask 染成陶土色
  （<img> 里的 currentColor 染不上）。别的主题里 `display: none`。
- 分段页签复用 `slideInd()` 喂的那两个变量，只是把那条线画成一块会滑的白底。
- 代码块是深色的一扇窗（DESIGN.md 的 code-window-card），颜色全在 `--code-*` 那几个里。
- 终端也加了 `anthropic` 主题：记号照默认（Claude Code 那套），品牌色 #C96442；每个色按
  「kitty 浅底 #FAF9F5 和深底 #141414 上都 ≥3.7:1」挑，`test_ui` 盯着两种底。
- 夹具：`shoot.mjs` 那条「Anthropic 版面一格不动」只剩复古守着；文末新加一节量这套皮承诺的
  事（气泡圆角有底色、它说的话是衬线、输入卡片带阴影、没有竖线和标签、按钮 ≥4.5:1、
  390px 不横滚、代码行号对齐），最后切回默认确认**皮一点没漏到别的主题上**。

### 八、全面检查挖出来的

- **代码块的行号跟代码错开，0.77.0 起就这样。** 行号 20px 一行、代码 24px 一行（行盒由 `<pre>`
  的 strut 撑，`<code>` 的 20px 压不低它），四行错开半行；修了行高还剩恒定 4px，是 `<pre>` 自己的
  字（15px 浏览器默认等宽）跟 13px 的 `<code>` 按基线对齐推下来的。**夹具比的是两个元素的
  `line-height` 属性**，那俩一直相等，绿了三个版本。现在量最后一个行号和最后一行代码的顶边。
- `lsp.Client.close()` 只 kill 不 wait、不关管道：留僵尸，`Pool.get` 碰到「死了就重起」时旧的
  连 close 都没调 —— `--serve` 跑一整天，语言服务器死一次漏一对 fd。
- `ide.connect()` 连不上的那个 socket 没关（挨个试编辑器，每失败一个漏一个）。
- `HTTPError` 没关：tools.py 三处（抓网页、Firecrawl、出图）和 credits.py。抽了 `_error_body`，
  顺带让「读报错正文时连接断了」不再用一个 OSError 顶替掉真正的报错。
- 测试这边：ResourceWarning **从 74 条清到 0**（大头是上面那个 lsp；还有 HTTPServer 没
  `server_close`、中途重调 `setUp` 盖掉的临时目录、没 `with` 的 open）；SWE-bench 那两条在
  没有数据集缓存的新检出上直接 FileNotFoundError，改成跳过；一条用例往真 stdout 上打「再说」；
  `test_sandbox.py` 的 `if __name__` 夹在文件中间；测试里 12 个没用的 import。
- Python 3.10 全套过；`ruff --select E9,F` 在 `feiyu/` 和 `evals/` 上零条。

### 验证

Python 1475 条（3.14 和 3.10 都过）、Rust 22 条、`feiyu-desktop` 编得过；`shoot.mjs` 全绿
（含新的皮那一节）、`input.mjs` 85/85、`fuzz_ui` 102 条、`md_check` 全过、`sessions.mjs` 8/8、
`fuzz_serve` 32 + 115 条、pty 夹具默认和 anthropic 两个主题都拍过。每一样新东西都做了回退验证
（撤掉改动，新用例得真的红）：缓存断点 10 条、拆开关 9 条、沙箱加固 14 条、钉 PATH 1 条、
git 加固 2 条、记教训 8 条（另外 9 条是「不该说话」的护栏，本来就两头都绿）、夹具的行号断言
（撤掉修复后默认 15px / 皮里 29px 错开）。

`shoot.mjs` 跑之前 devserver 要重编（`include_str!` 把 ui/ 编进二进制）；这轮拍皮用的是
scratchpad 里一个只拍 anthropic 那几屏的小夹具，没进库。

## 0.78.1：那把尺子先量了自己一次（60 倍），然后说「量不出差别」

0.78.0 的 A/B 跑了六个半小时，**结果作废**：软闸从头到尾没咬住过一次。

### 单位在边界上丢了

`tbench_ab.txt` 里存的是**秒**（445），而 `--time-budget` 的裸数按**分钟**算。
适配器把 445 原样递过去 → 闸变成 445 分钟，**60 倍**。两档跑完一模一样，
表面上看是「这个改动没影响」。

**跟 0.73.0 那个「`FEIYU_ASSIST` 到不了容器」是同一个形状**：开关设了、命令也对、
就是不起作用，而不起作用的样子跟"没效果"长得完全一样。

抓到它靠的是 0.78.0 自己加的那条 trace：`on` 档 14 次 trial 里 `time_warn` **0 条**、
`time_stop` **0 条**。要是当时只看解决率，这轮数就直接写进文档了。
**「不说也记一条」那个决定，第一次用就回本了。**

修法是**让单位跟着值走**，而不是在边界上靠约定：

- `tbench_ab.txt` 现在写 `445s`
- `load()` 读到没单位的数**当场抛**（静默当分钟最糟：它照样跑得下去，只是闸永远不响）
- 适配器原样传，不补单位也不换算
- 两条用例钉住：文件里每行必须 `^\S+ \d+s$`、`load()` 遇到裸数必须抛

**验完才重跑**：单跑一道（pytorch-model-cli，296s），trace 里
`time_budget: 296.0` / `time_warn(left=64.7, said=true)` / `time_stop` 三样齐了才起整轮。
六小时的东西，先花八分钟验尺子。

### 两档真的不一样（这次先核了）

| | 软闸取值 | `time_warn` | `time_stop` |
|---|---|---|---|
| `on` | 7 种全对 | 11 次 `said: true`，0 次 false | 8 |
| `off` | 7 种全对 | 12 次 `said: false`，**0 次 true** | 11 |

### 数：量不出差别

| | 解决率 | 中位轮数 | 中位墙钟 | 花费 |
|---|---|---|---|---|
| `off`（不提前说） | **6/13 = 46.2%** | 18 | 486s | ¥2.08 |
| `on`（提前收口） | **7/14 = 50.0%** | 22 | 325s | ¥2.30 |

逐题（每题两遍，off → on）：`build-pmars` 0→2、`pytorch-model-cli` 0→1、
`custom-memory-heap-crash` 0→1，反过来 `chess-best-move` 2→0、`git-multibranch` 2→1，
`bn-fit-modify` 和 `fix-ocaml-gc` 打平。**符号检验 3 胜 2 负 2 平 —— 这是噪音的形状。**
7 道题的集子里一道就是 14 个百分点，这点差距连方向都说不准。

`off` 档那一次 429 被归进「判不了」不进分母（0.72.0 写下那条规则之后第一次在真数据上生效）。

两档存档在 `~/.local/state/feiyu/evals/tbench-0.78.1-ab-hint-{on,off}.jsonl`，
`python -m evals.report --ab 那两份` 随时复看。

### 比「没差别」值钱的是这条：**这把尺子看不见这个功能**

挑出来的 7 道**在基线里是 14/14**（「两遍都解出来」是入选条件）。掐掉三成时间之后
两档都掉到一半上下 —— 闸咬得很实，agent 确实做不完了。

但 Terminal-Bench 是 **pass/fail**：最终测试过就是 1、不过就是 0。
而「到点时手上的东西还能用」在这套判分里**根本没有得分的地方** ——
一个收了口、留下能跑的半成品的仓库，和一个停在半路、文件改到一半的仓库，
在 verifier 眼里都是 0。

**所以这轮 A/B 的正确结论不是「提前收口没用」，是「这把尺子量不了它」。**
真要量得换判据（到点时仓库还能不能构建、测试还能不能起来、改动是不是自洽），
那是另一套东西；不换的话，这个功能的收益只在人这边 —— 而那不是榜单能给的分。
**下次给这类改动挑尺子时，先问一句：它的收益在判据里有位置吗。**

### 开关保持默认开（判断，不是证据）

0.73.0 给 `FEIYU_REWRITE_HINT` 定的是「没证据就默认关」。这次不照搬，理由是它俩的
代价不一样：那个对所有人都生效，而这句提醒**只在用户自己设了 `--time-budget` 时才存在**，
一次任务多一句话。设了闸却不让它知道，等于只留下「被掐掉」那一半。
**这是个判断，不是这轮数据支持的结论**，写在这儿免得以后被当成量出来的。

### 这一轮踩到的

- **中途 harbor 拉数据集连不上**（httpx 连接错误），`off` 档最后三道 8 秒内连着秒退。
  补跑而不是直接出报告 —— 缺三道的话两档覆盖的不是同一批题，`--ab` 比出来的数是错的。
- **bash 是边读边执行的，不能改正在跑的脚本**。跑批脚本里 `echo "${SECS}s"` 现在会印成
  `254ss`（值自己带单位了），想顺手改，但改了会让 bash 的字节偏移错位、把正在跑的循环
  弄坏。只好等它跑完 —— 传进去的值是对的，丑的只有日志那一行。

### 账

作废那轮 ~¥4 + 这轮 ¥4.38（两档各 14 次 trial、各约 2 小时墙钟）。
**验尺子那八分钟是这笔账里最划算的一项。**

## 0.78.0：第三道闸是时间，以及一把量得了它的尺子

### 为什么是时间

0.72.0 那轮基线里，**7 次撞上限全是墙钟**（0 次预算、0 次轮数）。而撞上限的样子是
被外面一刀杀掉——改到一半的文件、没跑完的构建，全留在半路。

**这道闸不是为了让它更快。** 0.73.0 已经把这条路量死了：墙钟的 91-98% 花在等模型，
每轮耗时跟输出字数的相关系数 +0.98，"耗时 ∝ 输出字数"是条物理定律，harness 绕不过去。
0.70.1 更早证伪过"给更多资源"（预算 ×3、轮数 120、超时 ×3 重跑三道 0 分题，一道没翻盘）。
**所以它买的只有一件事：到点那一刻，手上的东西还能用。** 改到一半的文件是负分，不是零分。

### 闸

`--time-budget 30m` / 约定文件里 `time-budget: 30m`，跟另外两道同一个机制。
不写单位按**分钟**算（人说"跑二十分钟"，不会说"跑一千二百秒"）。

- **按每一句话计，不是整个会话。** 交互里人想事情的那几分钟不该算进来，而"这一问
  最多跑多久"才是他真正想设的那个数。轮数那道本来就是 per-send 的局部量，时间跟着它。
- **三道闸收在同一处，时间排在轮数前面。** 轮数那道在交互下会停下来问一句，
  到点了再问等于把最后那点时间也花在等人回话上。
- **提醒只说一次**（还剩 25% 时），搭在最后一条工具结果上——跟自动校验、后台命令结束
  同一个位置。每轮报一次时间只会把正事挤下去，那是 0.68.0 量出来的教训：
  「多告诉它一些它没问的事 = 多给它一些事做」。
- **看不懂的时长在 argparse 那一层就抛**，不静默当成"不限"：他以为设了闸而实际上
  一道都没有，是最糟的一种。
- `FEIYU_DEADLINE_HINT=off` 是 A/B 的另一档——**闸照旧，只是不提前说**。
  不说的那一档**也写一条 trace**：不写的话没法确认它真的没说，而 0.73.0 正是靠
  「`FEIYU_REWRITE_HINT` 触发 0 次」这个读数才知道那次 A/B 量的是空气。
- 重建最后那条 `ToolOutput` 时**带上 `image`**。这个项目已经漏过三处，每次都不报错。
- **提醒只在两轮之间发得出来，所以它可能来得很晚。** 真跑一遍（25 秒的闸）那次是
  在「还剩 2.8 秒」才说的——一轮本身就要好几秒，从 30% 跨到负数只要一轮。
  budget 大的时候不是问题（30 分钟的闸，阈值是 7 分半），**budget 小到跟单轮耗时
  一个量级时这道提醒基本没用**。要治得按"上一轮花了多久"估还够不够再跑一轮，
  先不做：多一个知道怎么调的旋钮，不如先让 A/B 说话。

**子 agent 不在这道闸里面。** `task` 一跑就是一整个子 agent，闸只在两轮之间看一眼，
所以它可能冲过点几分钟。跟预算那道一样的性质，先记着。

### 尺子：为什么不能拿基线那 24 道做这件事

0.72.0 的 24 道是量**总体水平**的：一道题就是 4 个百分点，而 24 道里本来就有 4 道
一过一不过——它量不出小改动。

0.73.0 还吃过更具体的一次亏：挑去做墙钟 A/B 的三道里，**两道两档都撞满 1800 秒**。
主指标是墙钟而所有样本都被上限截断时，这个指标没有分辨力，改动有用没用量出来都是 1800。
那一节末尾写着「下次做墙钟相关的 A/B 要挑能跑完的题」——`evals/tbench_ab.py` 就是那句话。

挑的规矩全部从基线存档里算（不是手挑）：**两遍都判过、两遍都解出来、两遍都没撞墙钟、
墙钟中位 ≥ 300 秒**。落下来是 7 道，写死在 `evals/tbench_ab.txt`，`--check` 有用例盯着。
「两遍都解出来」这条是关键：要的是一个**它本来做得到**的参照，本来就做不出的题两档都是
0 分，量不出任何东西。

- **软闸一题一个值**（= 这道题基线中位墙钟 × 0.7）。挑出来的题自然耗时差着好几倍
  （254 秒到 1419 秒），一个统一的秒数对一半太松（永远轮不到它说话）、对另一半太紧
  （还没开始干活就到点）。按各自的中位掐，才是同一种"掐掉三成时间"的处理。
- 所以 `--cmd` 出来的是**一题一条 harbor 命令**：`harbor run` 的环境变量对这一次调用里
  的所有题都一样，想一次跑完就只能所有题共用一个秒数，那正是要避免的事。
- 适配器加了 `FEIYU_BENCH_TIME` → `--time-budget`，并把 `FEIYU_DEADLINE_HINT` 加进
  **转发名单**。那个名单是写死的（0.73.0 的坑：`FEIYU_ASSIST` 压根到不了容器，
  两档跑出来一模一样，看着像"这个改动没影响"）——**以后每加一个 A/B 开关都要同时加这儿。**
- `--agent-timeout-multiplier` 照旧放宽 Harbor 那道**硬**闸：这次量的是软闸，
  硬闸先响的话量到的还是「被外面杀掉」，跟改动没关系。

### 还没量

**这一版只有闸和尺子，A/B 没跑**（0.78.1 跑了：量不出差别，而且量出来「这把尺子看不见这个功能」——见那一节）。 一档 7 道的墙钟上界 67 分钟，两档各两遍大约
¥8 / 半天机器时间。**在那个数出来之前，别说"提前收口有用"**——0.69.0 就是实现完一整轮
才发现方向是反的，而 0.73.0 那次便宜在"先用一个开关 + ¥3 把前提证伪掉"。

### 真跑过一遍

25 秒的闸 + 一个要读三十几个文件的活，trace 里 `time_warn`（`said: true`）和
`time_stop` 都在，最后那句「跑满 25 秒，我先停下」也打出来了。
**屏幕上看不到那句提醒是对的**——工具结果在屏幕上本来就是截断显示的，
要确认它真的说了得看 trace，这跟 0.73.0 那条「能用 trace 的时候用 trace」是同一件事。

### 这一轮踩到的

- **用例不能靠"把闸设到零"来触发。** `time_budget = 0.001` 那版绿得很可疑：
  假后端一轮只要几十微秒，1 毫秒的闸能让它跑完五轮还没到点——那不是用例，
  是跟机器快慢掷骰子。改成让工具真的 `sleep(0.02)`，下界才是确定的。
- **中文字符串里混进 ASCII 双引号，第四次。** `ruff --select E9` 当场抓到，
  写完中文注释顺手跑一遍比读三遍便宜。
- 回退验证：`git stash push feiyu/` 之后新用例红了 11 条（3 failures + 8 errors），
  装回去全绿——这条是确认用例真的在测东西，不是在描述现状。

## 0.77.2：断线时那句话活不过一秒

0.77.0 末尾留的那条记号——「`input.mjs` 85 条里有一条一直挂着」——修了，
顺带把同一个毛病的另外四处一起治了。

**病**：`send()` 在断线时写的「没连上，这条没发出去」，写完不到一秒就被冲掉。
0.50.x 定过「断线时不能默默吞掉」，而它其实是被吞掉的，只是吞得慢了一点。
冲掉它的是**定时器**：秒表每秒把状态行重写一遍，`WAIT_AFTER` 那个迟来的
「在跑」块落地时还要再写一次。两者报的都是"现在是什么情况"，而它们手里的
"现在"是断线之前那一刻。

### 状态行上其实有两种话

- **现在是什么情况**：就绪 / 干活中 1:02 / 断了。随时可以重写，定时器每秒重写一遍。
- **留言**：你刚点的那下没成 —— 没连上、换不过去、agent 退了。只说一次，
  而且得等到人看见。

之前只有一个 `setDot()`，两种话抢同一行字，谁后写谁赢 —— 一秒一次的那种
永远赢过只说一次的那种。现在旁边加一条 `sayOnce()`：**留言按住状态行，直到
状态真的变了才让开**（连上了、这一轮完了、下一条留言）。

- **让开的时机不拿秒数去猜。** 给留言定个几秒的保质期，是在赌"这几秒里他正好
  在看"，而且到点之后秒表接着报「干活中 12s」—— 断着线的时候这句本来就是瞎话。
  按状态变没变来判，不用挑一个魔数。
- **挡的只有定时器那一路**（`drawWait(ambient)`）。别处直接调进来的（起一轮、
  报想了多少字）都是「刚发生了什么」，那是该说话的 —— 夹具里恰好有三条用例
  就是直接调 `drawWait()` 的，一并挡掉它们就挂。
- `gone`（agent 退了）那条**先 `stopWait()` 再说话**：它都没了，秒表还在那儿
  数「干活中」就是撒谎。

改走 `sayOnce()` 的五处：没连上 / agent 断了 / 连接断了正在重连 / 正在换工作台 /
换不过去。**「连接断了 · 正在重连」以前同样是看不见的** —— 而一轮正跑着的时候
断线，恰恰是最该看见它的时候。

### 上一轮为什么没修成

0.77.0 记着「试过在秒表那条定时器上加 `if (online)`，没治好」。这次量出来了：
**冲掉它的最后一下不是秒表，是 `showWait()`**（`WAIT_AFTER = 800` 那个定时器
带进来的），它也调 `drawWait()`。只盯着那条 `setInterval` 是找错了人。

怎么量的：给 `#s-state` 的 `textContent` 装一个自己的 setter，每次写入连
`new Error().stack` 一起记下来，跑完夹具把这串栈打出来，谁写的一目了然。
**比对着代码猜快得多**，而 `MutationObserver` 在这儿没用——它的回调是微任务，
拿到的时候栈已经断了。

### 验证

`input.mjs` **85/85**（之前 84/85，挂的就是这条）、`fuzz_ui` 102 条、`md_check` 12 条、
`shoot.mjs` 全绿；Python 1372 个用例、Rust 22 个用例全过。

### 顺带：`sessions.mjs` 那四条假挂修了

它 8 条里长期只过 4 条，**挂的全是数数，产品一直是对的**（列表内容、current、
切回去捞历史都对）。两处夹具没跟上后来的改动：

- `.talk` 现在也数到机器人清单那几行 —— `#bots` 从 0.65.0 起跟会话列表共用这个类。
  **钉进 `#talks` 里**才是数会话。
- 「新对话把正文清空了」数的是 `.block === 0`，可空状态那块 `#hello` 自己就带
  `.block`，而 `cleared` 之后 `syncHello()` 要做的正是把它摆回来。改成断言
  **只剩空状态那一块**。
- 顺带：打花费那行取的是 `m-cost`，早就改叫 `s-cost` 了，一直在印 undefined。

现在 **8/8**（devserver 照旧要起在干净的 `XDG_STATE_HOME` 上，见它自己的文件头）。

还有一条**不是 bug** 的夹具噪声，免得下次又查一遍：**`shoot.mjs` 紧跟在
`input.mjs` 后面、在同一个 devserver 上跑，划词那条会挂** —— `REPLAY` 把上一轮真
跑出来的几百条事件回放给新标签页，正文变长，被选中的那段跑到视口外面去了
（`y` 是负数）。换一个新起的 devserver 就过。

## 0.77.1：正文里的链接点不开

用户点桌面端给出的下载链接，一点反应都没有。

`md()` 渲染出来的是标准的 `<a href target="_blank">` —— 在浏览器里这么写是对的，
但 **webview 不是浏览器**：没有"新标签页"这回事，`target="_blank"` 落到一个不存在
的行为上，于是什么都不发生。看着像链接却是死的，比不给链接更糟。

- Rust 侧加 `open_url`，把 url 交给 `xdg-open`。**只放行 http / https**：正文是
  模型吐出来的 markdown，什么 scheme 都可能混进来，`file://` 直接丢给 xdg-open
  等于把"打开本机任意文件"这件事交出去了。
- `streamEl` 的 click 处理器原先只认 `[data-copy]`，现在先拦 `a[href]`，
  `preventDefault` 之后走 `open_url`。
- **没引 `tauri-plugin-opener`**：官方是有这个插件，但为一行 `xdg-open` 拉一个
  依赖进来不划算。

## 0.77.0：桌面端的动效层，和三样 AI 原生组件

用户给了两个参考站，要求「综合参考和取舍」：
[beautifului.dev](https://www.beautifului.dev/)（21 个 AI 原生组件的**形态**）和
[beui.dev](https://beui.dev/)（React + motion 的**动效工程**，另有 17 个 agent 组件）。

**取舍先写在前面，免得以后有人照着参考站把皮也搬过来。** 两个站都是深底、圆角、
卡片套卡片、紫蓝渐变；而 `app.css` 开头那套规矩是白纸、直角、flat-not-boxed、
`--accent` 永远不做字、没有暗色模式。照搬皮等于推翻整套设计系统。
**借的是「有哪些组件、怎么动」，不是「长什么样」** —— 下面加的东西里没有一个圆角、
没有一处阴影、没有一个裸色值。beUI 那边的代码更是一行都抄不了（它是 React 库，
这边无框架无依赖），能抄的只有动效配方。

参考站有而这边**已经有了**的不重做：审批卡（还比它多一档危险分级）、todo、
`@` 提及与斜杠命令、模型选择器、秒表、diff、侧栏分组。

### 一、想的过程看得见（唯一一处动到后端的）

0.58.0 起 `on_think` 就在报「想了多少字」，但字数只能证明它还活着。
**原文才说得清它在往哪个方向走**，而「方向不对」是思考型模型最贵的失败。

链路：`providers.py` 两个后端都把这一小段原文一起交出去 → `ui.Stream.thinking(size, delta)`
→ `events.emit("thinking", size=, delta=)` → 前端画成一段可折叠的轨迹。

- **原文按同一个步长（`PROGRESS_STEP`）攒着一起发**，不另开一条节流。两条节流
  各走各的话，字数和原文会漂开，前端拿哪个都不对。
- **收尾那一截必须单独冲**（`_flush_thought`）：只在跨档时发的话，不足一个步长的
  尾巴永远留在缓冲里 —— 而尾巴恰好是「它决定要干什么」那一段。
  两个冲的点：`write()`（开口了 = 上一段想完了，**顺序不能反**，反了前端会先收到
  正文再收到属于它前面那段的思考）和 `finish()`（**要放在 tty 那道早退上面**，
  事件跟是不是终端没关系）。
- **`delta` 必须有默认值**：老的假后端只传一个参数（`tests/fakes.py` 和
  `test_agent.py` 里四处），不给默认值就是 0.58.0 那次 108 个 error 重演。
- **在想的时候展开，想完自己收起来**，人点过就听人的。想的过程有用是因为它
  *正在发生*；事后那一大段横在对话中间只会把真正说的话挤下去。
- 终端那半边**一个字没改**：它只用字数。怎么画归前端，照旧。

### 二、动效层

原来全站只有三个动画（`rise`、状态点的 `pulse`、光标的 `blink`）。新增的都收在
`app.css` 末尾那一节，三条底线写在那儿：**只动"刚出现的"和"你刚动了它的"**、
**量出来的数走 CSS 变量**（直接写 `style.left` 的话 `prefers-reduced-motion`
关不掉它）、**一句话全关**。

新加两组 token：`--tap-*`（按压，短促带一点过冲）和 `--slide-*` / `--fold-dur`
（滑动、展开）。**浮现那组（8px / 500ms / ease-out）一个数都没动** —— 它管的是
「内容进场」，跟「你刚按了它」不是一回事。

- **页签底下那条线会滑**（`slideInd()`）。原来是每个页签各自的
  `border-bottom-color`，旧的灭新的亮，两者之间没有连续性。
  **藏着的元素量出来的 `offsetWidth` 是 0**，所以侧栏展开、面板打开、窗口 resize、
  字体加载完这四处都要重新量一次；量到之前不画（没有 `data-ind` 就 `content: none`），
  否则它会从左上角飞过来。
- **`paintInto()`：流式重绘只换真正变了的那几个顶层块。** 0.68.1 把重绘从
  「每个 delta 一次」压到 12.5 次/秒，治的是总量；这儿治的是每一次的形状。
  整块 `innerHTML` 重写有三个代价，只有最后一个是性能：
  **选区每 80ms 断一次**（模型说到一半想把上面那段话选出来复制，做不到）、
  定稿的段落每帧都重建（所以没法只给新出现的那段上淡入）、重排代价跟整条消息
  长度成正比而不是跟这帧改了多少成正比。
  判定**不做任何序列化**：`mdParts()` 直接给顶层块的字符串数组，跟上一帧缓存的
  逐个比。读 DOM 的 `outerHTML` 反过来也能比，但那是把整条消息重新序列化一遍。
  **尾块就地改孩子、不整个换掉** —— 换掉的话它每帧都是"刚出现"，淡入会每 80ms
  重放一次。光标是**同一个节点搬来搬去**，而且放进最后一块**里面**：放在 `.prose`
  末尾的话「最后一块」永远是光标，那条 `:not(:last-child)` 的淡入规则就把真正的
  最后一段也算进去了。
- 会话列表错开进场，**只在清单真的换了时做**（`talksFresh`）：搜索框每敲一个字
  都会重画，那时候再晃五十行就是在闹眼睛。延迟封顶 12 档。
- 整行宽的控件（导航项、会话行）只动底色不缩 —— 缩一下会很夸张。

### 三、几样新组件

- **代码块**：行号 + 语言 + 复制。**行号不能进 `<code>`**（那是拿去复制的正文，
  掺进去复制出来每行都顶着一个数字），所以它是 `<pre>` 外面的一列，
  `overflow-x` 只加在 `<pre>` 上 —— 横滚时行号留在原地。两列的 line-height
  必须是同一个**长度**。复制按钮**走事件委托不逐个绑**：`md()` 是纯函数、流式时
  一秒跑十几次，而且 `paintInto` 换节点时绑上去的东西会一起没掉。
  `navigator.clipboard` 在 webview 里要安全上下文，退回 `execCommand`。
- **长输出折起来**（`FOLD_AFTER = 12` / `FOLD_KEEP = 6`）。一次 `npm test` 能回
  几百行，而值得看的通常是头几行和"它挂没挂"—— 后者已经在 `.tool__note` 上了。
  **摘要那行不折**，折起来等于把"发生了什么"藏了。
  **高度要量出来不能按行数算**：`.tool__lines` 是 `pre-wrap` + `overflow-wrap: anywhere`，
  一条长命令占两行，按行数算的上限会把末尾几行切掉，而且切了不报错。
- **「在跑」那一块**：九个方块按对角线依次亮（beautifului #01）。
  一轮跑十分钟是真会发生的（实测 622 秒），状态栏那个秒表是 0.58.0 补的，
  但**眼睛在正文流上**，而那儿一个字都没有。
  **要等一下再画**（`WAIT_AFTER = 800`）：快的模型一秒就开口，立刻画就是闪一下；
  而且工具是一条跑完接着报下一条的（串行那批 start/result 交替来），不等这一下
  每两条工具之间都会插进来一块。
- **划一段交给它**（beautifului #20）：选中正文 → 浮出解释 / 改写 / 缩短 / 照着做。
  点了是**填进输入框不是直接发出去** —— 跟「开始」那几个例子同一条规矩：
  让人能改，别替他做决定。多按一下回车换来的是「我还能补一句」。
  选中的原文要引起来（`> `），它才分得清哪句是指令哪句是被处理的对象。
- **「回到最新」**：`keepBottom` 刻意不把往回翻的人拽回去，代价是他得自己滚回来，
  而一轮跑起来底下一直在长，滚不到头。
  **这个钮摆在 body 那张网格里、跟 `.sheet` 同一格** —— 网格项可以重叠，所以它
  既不参与对话流的排版、也不会跟着内容滚走。放进 `.sheet` 里面的话，要么占掉
  底下一条空带（显隐时还抖一下），要么 `position: absolute` 跟着滚动内容飘上去。

### 没做的那些（问过用户，记下来免得重查）

参考站上的 Records Table / Filter Table / Flowchart / Insight Cards / Fine-tune Card /
Agent Screen 都没做：它们是**给数据产品用的**，这边没有对应的数据。
「带来源的流式回答」（beautifului #03）也没做 —— 那要后端把 `web_fetch` /
`web_search` 的来源结构化报出来，是另一条链路。

### 这一轮踩到的

- **折叠按钮的闭包抓错了变量。** `think.head.onclick` 里写 `think.touched = true`，
  而 `closeThinking()` 会把模块上那个 `think` 置成 null —— 于是**想完之后再点
  这块轨迹就在 null 上取属性**。抓 `it`（这一块自己）才对。
  **按钮 onclick 里抛异常不会让 `click()` 那条 eval 失败**，所以夹具必须看结果
  （`data-open` 有没有翻过去），不能只看"点得动"。
- **`typeof` 对 TDZ 里的 const 也会抛。** `keepBottom` 要用文件末尾才建的那个
  「回到最新」，`typeof toEnd !== "undefined"` 这种老写法在这儿是会崩的；
  同理 `syncInds` 必须写成**函数声明**，因为 `setSide()` 在模块求值时就跑一次。
- **模板字面量里的 `\n` / `\s` 会被 Node 先吃掉一层。** 夹具里往页面送
  `split("\n")` 实际送过去的是一个真换行（SyntaxError），`/^\s*\d/` 送过去是
  `/^s*d/`（悄悄地匹配错东西）。页面侧的正则和转义要写两道反斜杠。
- **`JSON.stringify(异步函数())` 拿到的是 Promise**，序列化出来是 `{}` ——
  断言会挂在一个什么都没量到的对象上。要让 `stringify` 在 await 之后跑。
- **`0_hello` 那一屏一直是间歇的**（这次终于按住了）：它的前提是"流里一条都没有"，
  而重连之后 agent 会把这一轮里我们自己点出来的东西推过来（改模式、点机器人那些
  note），落在 `sleep` 之后的话「开始」那块就永远不出现。
  跟文件开头那句「要等 agent 回话，不是等 DOM」是同一条，这儿一直漏着。
- **`input.mjs` 85 条里有一条一直挂着，不是这轮弄坏的**（把 `desktop/ui` 整个
  stash 掉在基线上跑，照样挂）：「断线时状态行说一声」—— `send()` 断线时写的
  「没连上，这条没发出去」会被每秒一次的秒表冲掉，一秒之内就看不见，
  而 0.50.x 定过「断线时不能默默吞掉」。
  **试过在秒表那条定时器上加 `if (online)`，没治好**（那一屏前面那条用例起的是
  真 agent 的活，`drawWait` 还从别处被直接调到），而且会把另外三条依赖"断线之后
  照样能直接调 `drawWait`"的用例弄挂。所以撤了，留着这条记号：
  **要修得先把那一屏的时序理清楚，别再照着"加个 online 判断"试一遍。**
  **（0.77.2 修了，见那一节：冲掉它的最后一下是 `showWait()`，不是秒表。）**

### 验证

`dev/shoot.mjs` 多了一张 `15_think` 和**五组量出来的判据**：想的轨迹该有/该收起来/
原文该进去/想完还点得动、长输出折着该矮展开该变高且摘要该露着、行号数该等于行数
且不许掺进正文、滑线该量到宽度且切换后该换位置、划词该浮出来且点完该填进输入框
并引起来。
`fuzz_ui.mjs` 102 条、`md_check.mjs` 12 条、`input.mjs` 85 条（挂的那条见上）全跑过。

## 0.76.0：MCP 的 HTTP 传输，和出图改成多模型

用户要把 Reactive Resume（开源简历工具，自己带 MCP server）接进来，顺带要三个
OpenRouter 的出图模型。两件事都落在「配置只装得下一种形状」上。

### HTTP 传输：推翻 0.44.0 那条「刻意不做」

`mcp.py` 的开头原话是「HTTP 传输刻意不做：要引依赖或者自己写 SSE，而本机能跑的
服务器几乎都有 stdio 形态」。**这话的前提是「本机能跑的」**。Reactive Resume 的
MCP 只有 remote streamable-http 一种形态，前提不成立，所以这条推翻掉。

替代方案 `npx mcp-remote` 当桥**被否掉的理由不是麻烦，是漏 key**：它要
`--header x-api-key:xxx`，而 1758 行那条记着 `/mcp` 会把 argv 原样印出来 ——
终端和网页端都看得到。自己写这一百行比漏一把 key 便宜。

- **传输拆成 `_Stdio` / `_Http` 两个类，`Server` 只管 JSON-RPC**。接口是
  `send(msg, timeout)` / `read(deadline)` / `gone()` / `close()` / `label`。
- **HTTP 没有「随时会来消息」这回事**：回复只跟在自己那条 POST 后面。所以
  `_Http.send()` 顺手把回来的消息塞进 `inbox`，`read()` 只是取走 —— 对上面那个
  `request()` 循环来说两种传输长得一模一样，一行都不用分叉。
- **回复有两种形状，都得认**：`application/json` 一条（或一个数组），
  `text/event-stream` 是 SSE。Reactive Resume 开了 `enableJsonResponse` 回前者，
  但多数服务器默认回后者，只认一种等于只能接一半的服务器。
- **`initialize` 的回复头里有 `mcp-session-id`，之后每条都要带**，不带的话
  第二条就 404。协议版本同理（`MCP-Protocol-Version`）。
- **`${VAR}` 在 `url` 和 `headers` 里展开**，key 就不用落在配置文件里。
  取不到就**直接不起这台服务器**，不能把字面量 `${RR_API_KEY}` 发出去 ——
  那样换回来的是一个 401，而用户会以为是 key 写错了。
- **`label` 只印 url，不印 headers**。`/mcp` 是给人看的，凭证在 headers 里。
  这正是否掉 mcp-remote 的那个理由，自己写也一样要守。
- 假服务器 `tests/mcp_http_server.py` 绑 127.0.0.1 的临时端口，不出网。
  **它记请求头要先把键抹成小写**：urllib 发出来是 `X-api-key`，`dict(headers)`
  一转就丢了大小写不敏感，断言按字面量取会看不见一个明明送到了的头。

`Server.proc` / `Server.argv` 没了，换成 `server.alive()` / `server.label` ——
`__main__._mcp` 和 `Registry.call` 两处跟着改。

### 出图：一份配置装多个模型

出图模型之间差得比对话模型还远：实测一张图 nano-banana-2 $0.067、
nano-banana-pro $0.134、gpt-5.4-image-2 $0.006，有的吃参考图有的不吃。钉死一个不合适。

- **形状照 `models.json`**：`{"default": 别名, "models": {别名: {...}}}`，
  别名变成 `image_generate` 的 `model` 参数。
- **旧的扁平形状不作废**：没有 `models` 字段就把整个对象当成唯一那条，别名
  叫 `default`。老配置一个字都不用动，`test_旧的扁平形状照样认` 盯着。
- **半份配置（只有 base_url 没有 key）当没配**，不进可选名单 —— 否则模型会挑中
  一个必然 401 的别名。
- **enum 在 `agent._with_image_models()` 里填，不能写死在 `TOOL_SCHEMAS` 里**：
  有哪些模型是运行时才知道的。填在 `tool_schemas()` 里是因为它本来就一会话一次、
  结果整会话不变（跟 0.55.0 丢 `image_generate` 同一个理由），不会中途作废 prompt cache。
  **要 deepcopy**：`TOOL_SCHEMAS` 是模块级的一份，就地改了下一个工作区会串味。
- 门禁从 `cfg.get("base_url") and cfg.get("key")` 换成 `image_models()` 非空，
  `agent.py` 和 `serve.py` 两处都要改 —— 漏一处的表现是面板上有开关、点了没用。

### 0.76.1 那轮补的四处

写完之后专门回头找了一遍 bug，三个真的，一个是既有的：

- **协议版本取错了源。** `MCP-Protocol-Version` 要报**服务器在 initialize 回复里
  确认的那个**，我取的是我们自己发过去的。版本是协商出来的，服务器完全可以回一个
  更老的，这时候还报我们提议的那个，严格的实现会 400。改成 `_initialize` 拿到
  结果之后回头告诉传输层（`io.negotiated()`，stdio 没这方法，`getattr` 兜一下）。
- **urllib 默认跟随重定向，而 301/302/303 会把 POST 降成 GET** —— JSON-RPC 的身子
  就没了。实测服务器收到一个空 GET 回 **501**，错误信息里一个字都没提重定向。
  现实形态是端点前面挂了 http→https 或者补斜杠的跳转。改成装一个 `_NoRedirect`
  handler 直接不跟，把 Location 原样报出来。
- **既有 bug：stdio 的 MCP 服务器拿不到环境里的密钥。** `load_mcp` 传的是
  `tools._child_env()`，那份把名字里带 KEY/TOKEN/SECRET 的**全摘了**（防模型一句
  echo 就把密钥读进对话历史）。于是一个指望从环境里拿 `GITHUB_TOKEN` 的 stdio
  服务器静默拿不到 —— 它自己报一句认证失败，看起来跟 feiyu 毫无关系。
  **不能把过滤去掉**（那道防线管的是 bash 跑的模型生成的命令），改成
  **`env` 块里也认 `${VAR}`**，跟 http 那条的 headers 一个规矩。
  **args 刻意不做这个展开**：argv 会被 `/mcp` 原样印出来，展开进去等于把密钥
  印给终端和网页端看。
- `HTTPError` 读完不关是一条 ResourceWarning —— 它也是个响应体。

顺带一条测试自己的教训：验「环境变量传进子进程了」不能比 argv（`proc.args == argv`
恒真，就算展开成字面量 `"${...}"` 也照样过）。假服务器加了个 `getenv` 工具，
**问子进程自己手上那个值是什么**，这才算验到。

### OpenRouter 的出图接口

**它有 `/images/generations`，回的就是 `data[0].b64_json`** —— 跟现有解析完全对得上，
接模型本身一行代码都不用改。动手前我以为它只能走 `/chat/completions` 加
`modalities: ["image"]`，**打一次真请求比猜省事**。`size` 也认。


## 0.80.3：桌面端从 Tauri 换成 Electron（照 Hermes Desktop 的进程划分）

用户问「Ubuntu 上有没有桌面版」「有没有比 Tauri 更好的」，定了照 Hermes 做。
理由是**引擎**：0.80.0 那条 flex-end 在 Chrome 和 WebKitGTK 里各错一半，夹具跑 Chrome、
产品跑 WebKitGTK 是结构性的；换 Electron 之后两边都是 Chromium，`shoot.mjs` / `input.mjs`
量的就是产品本身。代价是体积（AppImage 162M、deb 137M）。

- **Python 照旧是唯一的真源**：会话、模型、权限、工具都在 `feiyu --serve` 里，协议没变。
  Electron 主进程（`desktop/electron/`）只管进程生命周期和原生能力（对话框、通知、pty、读工作区文件）。
- **渲染进程没有 Node**：contextIsolation + sandbox，`preload.cjs` 只暴露白名单里的命令和事件，
  主进程 `trusted()` 再核一次调用来源是不是顶层那个 `dist/index.html`；禁导航、禁新窗口、权限请求一律拒。
  不监听端口（stdio），0.50.1 那三道门照旧不需要。
- **聊天界面没重写**：`ui/app.js` 只把 `TAURI` 换成 `DESKTOP = window.feiyuDesktop || window.__TAURI__`。
  新加的只有 React 那块文件树 / 预览（`renderer/workbench.jsx`），HTML 只当源码显示，不执行。
- **路径边界在 `electron/files.mjs`**：先 realpath 再比，符号链接跳出工作区的一律拒（`pathguard.rs` 的同一条规矩）。
- **打包后的应用只认自带的后端**（`resources/backend/feiyu-backend`，PyInstaller 冻结的解释器 + 依赖，
  `scripts/build_backend.py`），缺了就报错，**不去 PATH 上找一个 feiyu 凑合**——那可能是另一个版本。
  开发时才按 `FEIYU_BIN` → `.venv` → PATH 找。
- 版本号又多了两处手抄的：`desktop/package.json` / `package-lock.json`，`TestVersionSync` 一起盯着。
- **改完 `desktop/ui/` 要 `npm run build`**（它把 ui/ 拷进 dist/），跟 Tauri 时代「要重新编二进制」同一个坑换了个形状。

验证：`cd desktop && npm test`（路径边界、子进程生命周期）、`npm run test:desktop`
（真 Electron + 冻结后端 + pty，不打模型；`FEIYU_TEST_EXECUTABLE=release/linux-unpacked/feiyu-desktop`
测打包产物）、`python3 tests/check_backend.py`（冻结后的两个 SDK 能初始化）。
**还没验的**：Ubuntu 实机、GitHub Actions 那条 22.04 构建（`.github/workflows/desktop-linux.yml`）、Windows / macOS。
`webkit.py` 对 Electron 版已经没有意义，只在还跑 Tauri 版时有用。


## 0.81.0：桌面前端照 Fluid Functionalism 重做，三套主题回到「只换颜色」

用户要「用我给你的组件库重新设计 feiyu-desktop 的前端，走极简」。组件库指
**Fluid Functionalism**（fluidfunctionalism.com，规则全文在 GitHub mickadesign/fluid-functionalism 的
`skills/fluid-functionalism/references/craft.md`）和 **mdxcn**（mdxcn.dev）。两个都是 React 注册表，
这边是**零依赖原生实现它们的规则**，一个包都没加。

### 定下来的几条

- **一套版面，三套配色。** 0.79.0 那套 claude.ai 观感的皮（气泡、输入卡片、空对话居中）变成了三套主题
  共用的版面；衬线正文没留（Fluid 只有一种界面字）。主题又只换颜色了 —— `app.css` 顶上三段 token，
  布局一个像素不分主题，`shoot.mjs` 量着（见下）。Anthropic 的代码块照旧是深色窗，那是 `--code-*` 颜色。
- **Fluid 的硬规矩**：控件只有 36 / 28 两档高（按钮、输入、列表行共用）；界面字六档、行高整像素；
  字重只有 400 / 550 / 700，550 配 `font-variation-settings: "opsz" 18`（Adwaita Sans 带 opsz 轴，
  换字重时字宽不变）—— 汉字落到思源黑体，没有 550，按 CSS 字重匹配规则会取 700；不大写、不加字距、
  不放眉标（meta-rail 和那些大写小标签全没了）；字只有 `--text` / `--muted` 两种颜色，红只给报错和回不去的操作；
  hover / 选中是叠色（墨色 4% / 7%）；动效 80 / 160 / 240ms，退场快一档。
- **对话正文另起一档 15/24**：Fluid 的 14/20 是给拉丁界面字定的，排中文段落太挤。对话一栏 44rem，
  气泡、正文、代码块、输入卡片同宽，正文一行 47 字（Anthropic 那套皮当年是 46）。
- **mdxcn 的框只用在空对话**（问候下面那几个例子）：虚线、四角压「+」、`[ 试试 ]` 骑在上边线上。

### 实现上要记住的

- **主面板是 `body::before`。** 网格容器的伪元素也是网格项，放在第 2 列 2–4 行当 `.sheet` / `.term` /
  `.composer` / `.shelf` 共同的底（圆角 + 阴影），DOM 一个没加。这几样都带 `margin: 0 var(--gut) 0 var(--gut-l)`
  才落在面板里；侧栏收起时 `--gut-l` 才有值。文件面板（`workbench.css`）是右边第二块面板。
- **滑动高亮 `fluidHover(root, 行选择器)`**（app.js 小工具那节）：在 root 上听指针，高亮插进那一行的
  `offsetParent` 里、按 `offsetLeft/Top` 挪 —— 所以**那个容器要 `position: relative`，行也要**（行得画在高亮上面）。
  高亮是 prepend 进去的，**列表里按 `:nth-child` 写的规则会数错**，「开始」那几行改成了 `:nth-child(1 of li)`。
  清单重画把它冲掉没关系，下一次指到时再插回去。
- **按下去的手感是 `::before` 那层表面**：内缩 1px、同色 1px spread 补回满尺寸，按下时 spread 收成 0。
  按钮的底色写在 `--face` 上。**这几条通用规则必须用 `:where()`** —— 头一版用 `:is()`，而 `:is()` 取最高那个参数的
  特指度，列表里一个 `.pick-bar button` 把整条抬到 (0,1,1)，各按钮自己的 `--face` 全被压掉，发送键和「同意一次」
  白字压在白底上，看着就是按钮没了。
- **图标是内联 SVG，`pointer-events: none`**：点中的永远是按钮本身。夹具拿 `elementFromPoint` 量「标题栏的钮有没有被
  盖住」，不加这条量到的是里面的 `<path>`（没有 id），会误报。
- **发出第一句那一下的过渡要排在侧栏那条后面。** `body[data-motion] .composer { transition: margin … }` 跟
  `body[data-glide] .composer { transition: translate … }` 特指度一样，后写的赢 —— 写反了 translate 的过渡被换成只有
  margin 的那份，「落到底」就是一下子跳过去。现在 glide 那条排在最后，而且两样都带上。
- **确认卡的键帽是按钮右上角的角标**（绝对定位）。放在按钮里的话，没到点时它藏着照样占位，按钮上的字偏到左边。
- 工作台那一行只放目录名（全路径在 title 和状态栏）：240px 宽放不下一整条路径，两头一裁就什么都看不出来。

### 夹具

- `shoot.mjs` 里量旧版面的几条换了：行长两档 → **一栏到底**（正文 ≤48 字，代码块、气泡都贴着这一栏）；
  输入框文字对齐 `.block__body` → **输入卡片和对话一栏左右对齐**；meta-rail 对齐 → **Fluid 的几条硬规矩**
  （没有大写和字距、字重只在 400/550/700、控件只有 36/28、「你 / 它说」不画）；Anthropic 皮那一节 →
  **三套主题只换颜色**（同一份事件流三套主题各灌一遍，十个元素的盒子一个像素不许差，底色主色必须换了，
  每套的实心按钮够 AA）；0.80.0 那节「默认主题不该抬」反过来了。
- **`captureBeyondViewport` 量页面大小时会先把视口缩成 1×1。** 窄屏那条媒体查询翻一下，`watchNarrow()` 收起侧栏
  又展开，带着过渡的话拍到的是滑到一半的样子（设置面板压在侧栏上，看着像布局坏了）。`shot()` 拍的那一下把
  `data-motion` 摘掉。不是产品的毛病，产品里没人会把窗口缩成 1×1。
- `sessions.mjs` 要真模型（会花钱），隔离配置里没模型时只有两条 UI 的过；这一轮没拿用户的 key 跑它。

### 验证

`shoot.mjs` 全绿、`input.mjs` 114/114、`fuzz_ui` 102 条、`md_check` 全过；Electron 那边 `npm run build`、
`npm test`、`npm run test:desktop` 都过，另用 Playwright 起真 Electron 窗口拍了默认 / 复古 / Anthropic 三套。
跑夹具用的是一份隔离的 `XDG_CONFIG_HOME`（三份技能其中一份关掉、两份档案、一个机器人、`enabled.json` 关掉
`web_search`）和干净的 `XDG_STATE_HOME`，不碰用户真的配置。

**拆提交时踩的：Bash 工具跑的是 zsh，`for f in $F` 不会按空格拆开 `$F`**（只循环一次、拿整串当文件名），
备份那一步静默没做成，后面的还原照样跑了，把改好的文件盖回了旧版。是靠 `desktop/dist/`（装机时的打包产物）
和当时的编辑脚本逐个找回来的，找回来的前端跟装好的那份逐字节比过。zsh 里要拆就写 `${=F}` 或者用数组。


## 0.82.0：下载即用 —— 第一次打开就能在窗口里连上模型

用户问「能不能像 Hermes 一样下载即用，key 用户自己在里面加」。查下来差三样：没有能下载的地方、
第一次打开的引导把人指去终端、加模型要自己知道接口地址和模型 id。这一版补后两样，发布流程备好。

- **没模型时的引导。** 空对话先摆一张「先连一个模型」的卡（例子先收起来 —— 点了也发不出去），
  点了直接落到「设置 → 模型」摊开「加一条」；没模型时点右下角的模型名也去那儿；发消息时那条报错
  原来说「先在终端里 /key add」，桌面端（trusted）改成指向设置、还带一个同样的按钮（note 事件的 `act`）。
  浏览器入口（devserver）照旧指去终端 —— 它改不了 key。
- **加模型：内置接口地址 + 现场拉模型清单。** 表单最上面一排接口（OpenRouter / DeepSeek / Kimi /
  通义千问 / 硅基流动 / OpenAI / Anthropic），点一下填好接口地址和类型；贴上 key 之后从那家的
  `/models` 现场拉清单，打字筛、点一个就把模型 id、名字、上下文上限填上。
  **这跟「不内置厂商表」那条不冲突**：内置的只有地址（多年不变，跟 `credits._BY_HOST`、`lsp.SERVERS`
  一类），模型 id 和价格照旧一个不写进代码 —— 那正是退役得勤的东西，所以每次现问。
- **`providers.list_models` 的几条规矩**：不跟重定向（urllib 跟过去会把 Authorization / x-api-key
  原样带到新 host 上）；只认 http/https；key 里混进全角或空白先拦（HTTP 头只能是 latin-1，
  不拦的话报出来的是一句 codec 错误）；报错一律说人话、不带 key。
- **serve 的 `model_list` 另起线程跑**：要出网、可能十几秒，堵着读线程的话 esc 和停止都进不来。
  只给桌面端；key 留空时用存着的那条，**但只发给它原来那个 host** —— 表单里地址改去了别处，
  就让人重填，一个请求都不发（跟查余额那条闸一个道理）。回话带 `ticket`，晚到的旧回话前端不认。
- **从应用菜单打开接着用上一次的工作台**（`electron/workspace.mjs`）：菜单起的进程 cwd 是家目录或 `/`，
  这种时候读 `userData/workspace.json` 里记的那个；在项目目录里敲 `feiyu-desktop` 的照旧用那个目录，
  `-w` 给了就听 `-w`。工作台还是家目录时，空对话那块请人选个项目文件夹。
- 设置详情栏加了容器查询：右边开着文件面板时那一栏只有四百来像素，「标签 | 控件」两列会把模型 id
  那格挤成一条缝，窄了就上下摆。
- **发布**：`.github/workflows/desktop-linux.yml` 加了个 `release` job —— 打 `desktop-v*` 标签时把 deb
  和 AppImage 挂到 Releases。单独一个 job，只有它拿写权限。README 两份写了下载和第一次打开怎么连模型。
  **还没推、还没打标签**；Windows / macOS 的包还没有（不签名会被系统拦下，macOS 签名要开发者账号）。

### 测试和夹具

- `tests/test_model_list.py`：本地 127.0.0.1 临时端口上起一个假接口，验两种接口的请求头、不跟重定向
  （跳过去的那个地址一次都没被请求）、报错措辞、全角 key、存着的 key 只发原来的 host、没模型时那句提示。
  **用例不许出网**：头一版「畸形参数」那条没给地址，落到官方默认地址真去连了 api.openai.com，
  被墙了每条等满 15 秒、一共 45 秒才发现 —— 那条里把默认地址指到了本地。本机常年带着代理变量，
  连 127.0.0.1 之前要先摘掉。
- `desktop/tests/workspace.test.mjs`：菜单 / 终端 / `-w` 三种起法挑哪个目录，记录文件坏了当没有。
- `dev/fuzz_serve.py` 加了四条畸形的 `model_list`。
- `shoot.mjs` 文末「第一次打开」那一节（27、28 两张图）；`input.mjs` 加了「没模型时点模型名 = 去加一条」。
  夹具的 agent 本来就没配模型，所以拍「有模型」那几屏之前要先灌一条 ready 的 status，不然点模型名
  就进了设置。
- 真 Electron 窗口里跑过一次真请求：选 OpenRouter、不填 key（它的 `/models` 是公开的）点「列出模型」，
  拉到 472 个，打字能筛。**测的时候要用开发版后端**（`FEIYU_BIN=.venv/bin/feiyu`）——
  `build/backend` 里那份冻结的是上一次打包时的代码，新加的 op 它不认，表现是「列出模型」一直没回音。


## 0.82.2：终端版缩放不再留残影，界面顺手收拾一轮

用户说「TUI 美化优化，窗口放大缩小会有影响」。在 **kitty 自己的屏幕引擎**里复现了：
从 100 列缩到 72 列，输入框旧的顶边（满宽那条线）被终端折成两行留在新框上面，缩一次叠一层；
历史里每一个交出去的输入框、欢迎框也都按当时的宽度画，窗口一缩全碎。

### 根因和修法

- **会重排的终端（kitty、gnome-terminal、Windows Terminal、tmux）缩窄时把超宽的行折到下一行**，
  而 prompt_toolkit 的 `_on_resize` 是按「没折过」的行数从光标往回走、再往下清的 —— 走不到框顶。
- **光标落在哪儿是靠不住的**：屏幕没满时它还在输入行上；满了的话 kitty 把它放到框的**最后一行**
  （被折开的底边那儿）。头一版「从光标往上多走几行」在不满的屏幕上对、满屏时照样留顶边 ——
  是把整个会话的原始输出录下来、在 kitty 引擎里逐步重放才看出光标跑到了哪。
  现在 `_reflow_moves()` 先往下走「输入行下面那几行重排后占的行数」（光标本来就在最后一行的话，
  往下走会停在屏幕底边，还是最后一行），再从框底往上走「整个框重排后的总行数 - 1」，两种情况都落在框顶。
  不缩窄、或者终端不重排（xterm、rxvt、Linux 控制台 —— 它们截断不折，多走会擦掉上面的正文）照旧走
  prompt_toolkit 自己那套。`_on_resize` 一次缩放会被叫两次（SIGWINCH 一次、它自己轮询尺寸又一次），
  第二次尺寸已经是新的，自然落回原来那套。
- **历史里不留框**：输入框 `erase_when_done=True`，交出去的话留一行 `› 原话`（`said()`，主色记号 + 加粗原话、
  续行缩进两格）；欢迎区不画外框（logo + 信息）；markdown 的分隔线最长 40 格。
  原则：**打进 scrollback 的东西不画按窗口宽度撑满的线**，它们在窗口变窄时只会碎。
- **流式输出跟着当前宽度走**：每起一行重新量宽度（原来是这一轮开头量一次）；转圈那条状态行记住画了多宽，
  擦的时候按它现在被折成几行往上擦（`_wipe_status`），不然缩窄后留下上半截。

### 夹具：`dev/ui_resize.py`

```bash
kitty +runpy "import runpy; runpy.run_path('dev/ui_resize.py', run_name='__main__')" [输出目录]
```

主程序挂 `ui_runner.py` 的假后端，跑在 **kitty 的 `Screen`（C 扩展）**里 —— `kitty +runpy` 能直接拿来用，
跟用户终端是同一份重排代码，不开窗口。**pyte 量不出这个**：它改尺寸只截断 / 补空格，不重排。
流式输出途中缩一次，跑完再缩到 60、放到 120、缩到 50、回到 100，每一步拍一张图（回滚区 + 当前屏），
检查屏幕底部只有一个输入提示行、没有半截框线。kitty 回给程序的 CPR 走 Screen 的 `callbacks.write`，
不用自己算。

### 验证

`tests/test_ui.py` 加了六条（框顶底都满宽 / 光标那行也折了 / 不重排的终端不多走 / 状态行擦几行 /
回执格式 / 欢迎区不画框），分隔线那条改成「不撑满一行」；**拿 0.81.0 的 `ui.py` 跑七条全挂**。
`ui_resize.py` 六步全干净，`ui_drive.py` 正常流程（确认、工具、收尾）照旧。

**回退验证别用 `git stash push feiyu/ui.py`**：0.82.1 那次提交已经带上了这一轮做了一半的终端改动，
stash 回去的 HEAD 里一半函数已经在了，验出来只挂四条、看着像用例没用 —— 要对着 0.81.0 那份比。

### 顺带：Ubuntu 上 AppImage 要 libfuse2

0.82.1 打了 `desktop-v0.82.1`（0.82.0 那节写的「还没推、还没打标签」已经过时），CI 在 Ubuntu 22.04 上全绿、
Release 也发了。把两个包下载下来验的时候发现：electron-builder 26 打出来的 AppImage 用的还是老的 AppImageKit
运行时，启动时 `dlopen("libfuse.so.2")`，而 **Ubuntu 从 22.04 起默认不装 libfuse2**（24.04 上包名是 `libfuse2t64`）——
双击没反应，在终端里跑才看得到「AppImages require FUSE to run」。CI 测的是 `release/linux-unpacked`，碰不到这一层。
所以 README 两份和发版说明都改成 Ubuntu 上优先用 deb。deb 没这个问题：依赖写了 t64 的备选，postinst 管了沙箱
（用户命名空间用不了就给 chrome-sandbox 加 SUID）和 24.04 的 AppArmor profile；AppImage 的 AppRun 会先试
`unshare -Ur`，不行就自己加 `--no-sandbox`，24.04 的沙箱限制也不用管。要真的双击就能开，得换成不依赖 libfuse2
的静态运行时，没做。

**在这台机器上别直接执行 AppImage 来验**（连 `--appimage-version` 都不行）：AppImageLauncher 通过 binfmt 接管了执行，
会把文件挪进 `~/Applications/`、往菜单里加一项。看运行时用 `readelf` / `strings`（squashfs 的起点 = ELF 节头表的末尾，
别按 `hsqs` 搜，运行时代码里就有这个串），看内容用 `--appimage-extract`（这个它放行），或者直接用 CI 的 `linux-unpacked`。


## 0.82.3：一行装好（`get.sh`）

用户嫌「先问接口再下载」那两段命令太长：「别人需要用这么长的命令吗」。现在 README 开头是一行：

```bash
curl -fsSL https://raw.githubusercontent.com/feiyuceng06-prog/feiyu-agent/main/get.sh | bash
```

地址是用户挑的（另一个选项是在博客域名上放一份，命令更短，但要动博客再部署）。

- **不挂载，解开。** `--appimage-extract` 不走 FUSE，所以 Ubuntu 上缺 libfuse2 也照样装得上 —— 0.82.2 写进说明的那个坑
  在这条路上不存在。AppImageLauncher 放行 `--appimage-extract`（`--appimage-version` 它会接管，见 0.82.2 末尾）。
  解在 `~/.local/share/feiyu-desktop/` 底下的临时目录里，不在 /tmp：/tmp 可能挂成 noexec，而且同一个文件系统才能直接 mv。
- **包里自带的后端就是完整的终端版**（PyInstaller 打的就是 `feiyu.__main__`，prompt_toolkit 也在里面），所以 `feiyu`
  直接指过去，不用 Python。代价：**终端版跟着发布版走**，main 上的改动要等下一次打 `desktop-v*` 标签才到用户手里。
- **整个脚本都在函数里，最后一行才 `main "$@"`。** curl | bash 是边下边执行的，下到一半断了 bash 读不到最后一行，
  什么都不做。`test_下到一半断了什么都不做` 盯着；往顶层加任何会动文件的语句都会让它红。
- **trap 要当场展开临时目录**（`printf %q`）：EXIT 触发时 main 早返回了，`local` 的变量已经没了，加上 `set -u` 直接报错。
- **留这一版和上一版**（按 mtime，新装的那份 `touch` 一下）：正开着的窗口可能还在用上一版的文件。同一版本重装先把旧的挪进
  临时目录，trap 一起带走。
- **`~/.local/bin/feiyu` 是别的方式装的（比如源码版）就不覆盖**，认的是 shim 里那行标记；`feiyu-desktop` 和菜单项照覆盖
  不误 —— 跟 install.sh 是同一个位置，后装的赢。
- **最新版的地址**：先问 `api.github.com`；它按出口 IP 限流（一小时 60 次，CGNAT 后面很容易撞上），被限了就看
  `/releases/latest` 跳到了哪个标签，按 `Feiyu-<版本>.AppImage` 拼 —— **改了 electron-builder 的 artifactName 要记得这儿**。
- **装之前先跑一次自带程序的 `--version`**：包是在 Ubuntu 22.04 上打的，glibc 比 2.35 老的系统在这一步就起不来，
  这时候原来装的一个字都不动。桌面版缺系统库（ldd 报 not found）只提醒，终端版照样能用。

`tests/test_get.py` 十条，假的 AppImage / curl / uname，不出网。九处承重行逐条改坏过，每处都有用例红。
另外拿 0.82.1 的真包在隔离的 HOME 里装了一遍（387 MB），`feiyu --version` 和伪终端里的 TUI 都起得来。
