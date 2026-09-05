"""`feiyu --serve`：把 agent 挂在 stdio 上，说一行一个 JSON

网页端（`feiyu-web`，Rust）起一个这样的子进程，两边一行一个 JSON 对象来回。
**stdout 只放协议**，人看的那些 ANSI 整体重定向到 stderr —— 跟 `--json-schema`
一模一样的做法（`contextlib.redirect_stdout(sys.stderr)` 整体包住，
不要逐处去改 print 的目标）。

## 为什么不是「加一个 HTTP 服务」

Python 侧一个新依赖都不加（项目硬性约定）。HTTP、WebSocket、静态资源、并发连接
全在 Rust 那半边，这边只管把 agent 跑起来、把结构化事件吐出去。

## 事件从哪儿来

不是在这儿把 agent 重写一遍，而是 `feiyu/events.py` 那个 sink：`ui.py` 是唯一
给人看的输出层，装上 sink 之后它照旧返回 ANSI（终端那条路一个字没变），
同时把同一件事按结构报一份。所以 agent.py 里那三十处 `print(ui.xxx(...))` 一处没动。

## 要确认的时候

`approve` / `ask_user` / `approve_plan` 这些本来就是交互层塞进来的回调
（`__main__.py` 里塞的是 `ui.confirm`），这边塞的是「发一条 ask 事件出去，
在队列上等回话」。所以确认流跟终端是同一套语义，包括按 `[s]` 记规则。

## 中断

走 `_thread.interrupt_main()` 往主线程扔 KeyboardInterrupt —— 跟 `EscInterrupt`
落点相同，0.12.0 那套「中断要让历史保持自洽」的不变量原样成立，不用另写一套。
"""

import contextlib
import json
import queue
import sys
import threading
import _thread

from . import agent as agent_mod
from . import enabled, events, keys, permissions, profiles as profiles_mod
from . import session as session_mod
from . import skills as skills_mod, tools as tools_mod, ui
from . import __version__

# 等一个回话最多等这么久 网页关掉了、用户走开了，不能让 agent 永远挂在那儿
REPLY_TIMEOUT = 600.0
# 一条消息最多这么长 正常的一句话几百字，几十万字的只可能是坏掉的客户端
MAX_TEXT = 200_000

# 终端有、网页端**刻意不给**的命令，连同理由 —— 这份表同时用来
# 1) 从补全菜单里剔掉它们（不该提供你不打算执行的东西）
# 2) 用户硬打进来时给一句人话，而不是一句「未知命令」
TERMINAL_ONLY = {
    "/key": "密钥的增删只在终端做 网页端连读都不给，免得它成为一条把密钥捞出去的路 "
            "要改就回终端敲 feiyu，再 /key add ",
    "/theme": "主题换的是终端的配色和记号，网页端用的是自己那套样式，切了没有意义 ",
    "/exit": "关掉这个标签页就行 要停掉服务回终端按 ctrl-c ",
}


class Protocol:
    """一行一个 JSON 出去，一行一个 JSON 进来 """

    def __init__(self, out):
        self._out = out
        self._lock = threading.Lock()          # 事件从工作线程也会来（并行工具）
        self._waiting: dict[int, queue.Queue] = {}
        self._next_id = 0

    def send(self, kind: str, **payload) -> None:
        # 载荷里的 kind 会把事件名盖掉，前端就再也认不出这条是什么 —— 真踩过一次
        payload.pop("kind", None)
        line = json.dumps({"kind": kind, **payload}, ensure_ascii=False)
        with self._lock:
            try:
                self._out.write(line + "\n")
                self._out.flush()
            except (OSError, ValueError):
                pass                            # 对面没了，agent 不该因此崩掉

    def request(self, kind: str, **payload):
        """发一条要回话的事件，阻塞等回复 超时或对面没了返回 None """
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            box: queue.Queue = queue.Queue(maxsize=1)
            self._waiting[rid] = box
        self.send(kind, id=rid, **payload)
        try:
            return box.get(timeout=REPLY_TIMEOUT)
        except queue.Empty:
            return None
        finally:
            with self._lock:
                self._waiting.pop(rid, None)

    def reply(self, rid: int, value) -> None:
        with self._lock:
            box = self._waiting.get(rid)
        if box is not None:
            with contextlib.suppress(queue.Full):
                box.put_nowait(value)

    def fail_all(self) -> None:
        """对面断了：把所有还在等的都放掉，不然 agent 卡到超时 """
        with self._lock:
            boxes = list(self._waiting.values())
            self._waiting.clear()
        for box in boxes:
            with contextlib.suppress(queue.Full):
                box.put_nowait(None)


def _plain(text: str) -> str:
    """斜杠命令的返回是画好的 ANSI，发给浏览器之前剥干净 —— 前端自己决定怎么画 """
    return ui._ANSI.sub("", text or "").rstrip()


class Server:
    def __init__(self, agent, workspace, tracer, proto: Protocol):
        self.agent = agent
        self.workspace = workspace
        self.trace = tracer
        self.proto = proto
        self.inbox: queue.Queue = queue.Queue()
        self.busy = False
        self.stopped = False
        self.commands = {c.name: c for c in skills_mod.load_commands(workspace)}

    # ---- agent 要问人的三条回调 ----

    # 界面上那几个按钮的值 → agent 认的那几个词。
    # **这张表不能省。** `_gate` 只认 yes / always / remember，而这边原来直接把
    # `all` / `save` 交上去，两个都落到最后那句 return，于是按了「都允许」
    # 反而**全被当成拒绝**（用户拿截图来问过：一批 web_fetch 全是「没有执行」）。
    # 加新按钮时先回去看 `Feiyu._gate` 认哪几个词。
    _ANSWERS = {"yes": "yes", "no": "no", "all": "always", "save": "remember"}

    def approve(self, name: str, args: dict) -> str:
        answer = self.proto.request(
            "ask", type="approve", tool=name, label=ui.tool_label(name),
            target=ui.tool_target(name, args), args=args,
            rule=permissions.suggest(name, args))
        # 没回话按拒绝，跟非交互场景一致 **先判类型**：对面回一个列表的话
        # dict.get 会抛 unhashable，而这行跑在一轮的中途，抛出去就把这轮带走了
        return self._ANSWERS.get(answer, "no") if isinstance(answer, str) else "no"

    def approve_plan(self, plan: str) -> bool:
        return self.proto.request("ask", type="plan", plan=plan or "") == "yes"

    def ask_user(self, question: str, options: list[str]) -> str:
        answer = self.proto.request("ask", type="choice", question=question,
                                    options=list(options or []))
        return answer if isinstance(answer, str) else ""

    def approve_trust(self, kind: str, where: str, names) -> bool:
        """项目钩子 / MCP / 技能的信任问句 三个的形状一样，走同一条 """
        return self.proto.request("ask", type="trust", what=kind, where=str(where),
                                  names=[str(n) for n in names]) == "yes"

    # ---- 状态 ----

    def status(self) -> dict:
        agent = self.agent
        out = {"ready": agent.ready, "mode": agent.mode, "style": agent.style,
               "profile": agent.profile, "workspace": str(self.workspace),
               "version": __version__, "busy": self.busy}
        if agent.ready:
            out.update(model=agent.model, name=agent.name,
                       tokens=agent.tokens, cost=round(agent.cost, 6),
                       context=agent.context, context_limit=agent.context_limit)
        return out

    def push_status(self) -> None:
        self.proto.send("status", **self.status())

    # ---- 会话 ----

    def push_sessions(self) -> None:
        """侧边栏那份列表 只出摘要，正文点开才读 """
        self.proto.send("sessions", rows=session_mod.listing(self.workspace),
                        current=self.agent.session_id)

    def new_session(self) -> None:
        """开一段新对话 **旧的那份留在盘上**，这是它跟 /clear 的全部区别 """
        session_mod.save(self.workspace, self.agent, self.agent.session_id)
        self.agent.reset()
        self.agent.session_id = session_mod.new_id()
        self.proto.send("cleared")
        self.push_sessions()
        self.push_status()

    def delete_session(self, sid: str) -> None:
        """删掉一段对话

        **删的是正在看的那段时，得把 agent 也归零**：不然盘上没了、内存里还在，
        下一轮存盘又会照着旧 id 写回去，等于没删。
        """
        if not sid:
            return
        session_mod.drop(self.workspace, sid)
        if sid == self.agent.session_id:
            self.agent.reset()
            self.agent.session_id = session_mod.new_id()
            self.proto.send("cleared")
            self.push_status()
        self.push_sessions()
        self.proto.send("note", level="notice", text="删掉了一段对话")

    def open_session(self, sid: str) -> None:
        if sid == self.agent.session_id:
            return
        # 先把手上这份存住，不然切过去就丢了
        session_mod.save(self.workspace, self.agent, self.agent.session_id)
        saved = session_mod.load(self.workspace, sid)
        if not saved:
            self.proto.send("note", level="error", text="这段对话读不出来了（文件没了或者坏了）")
            self.push_sessions()
            return
        self.agent.reset()
        if not self.agent.restore(saved):
            self.proto.send("note", level="error", text="这段对话恢复不了")
            self.push_sessions()
            return
        self.agent.session_id = sid
        self.proto.send("cleared")
        self.proto.send("opened", id=sid, about=session_mod.describe(saved),
                        history=history_view(self.agent))
        self.push_sessions()
        self.push_status()

    # ---- 技能与工具 ----

    def push_catalog(self) -> None:
        """面板要的三份清单

        **技能正文不放在这儿。** 五十份技能各上万字，一次全推过去几百 KB，
        而面板一次只看得见一份 —— 点开哪份再要哪份（`detail` 那条）。
        这跟 `catalog()` 只放名字和用途是同一个道理，只是省的东西不一样：
        那边省的是每轮的 token，这边省的是一次推送的体积。
        """
        box = self.agent.toolbox
        cfg = tools_mod.image_config()
        has_image = bool(cfg.get("base_url") and cfg.get("key"))
        skills = [{"name": s.name, "description": s.description, "source": s.source,
                   "path": s.path, "chars": len(s.body), "on": enabled.on("skills", s.name)}
                  for s in sorted(box.skills.values(), key=lambda s: s.name)]
        tools = []
        for spec in tools_mod.TOOL_SCHEMAS:
            name = spec["name"]
            if name == "image_generate" and not has_image:
                # 没配出图接口它压根不存在，摆一个永远打不开的开关只会让人以为坏了
                continue
            tools.append({
                "name": name, "label": ui.tool_label(name),
                "description": " ".join(str(spec.get("description") or "").split()),
                "args": sorted((spec.get("input_schema") or {}).get("properties") or {}),
                "on": enabled.on("tools", name), "locked": name in enabled.LOCKED,
            })
        servers = []
        for name, srv in sorted(box.mcp.servers.items()):
            servers.append({
                "name": name, "on": enabled.on("mcp", name),
                "tools": [{"name": t.get("name") or "",
                           "description": " ".join(str(t.get("description") or "").split())}
                          for t in srv.tools],
            })
        for name, why in sorted(box.mcp.errors.items()):
            servers.append({"name": name, "on": False, "error": why, "tools": []})
        self.proto.send("catalog", skills=skills, tools=tools, mcp=servers)

    def push_detail(self, kind: str, name: str) -> None:
        """点开一项时才要的正文 技能和档案有正文，工具和 MCP 没有 """
        if kind == "profile":
            one = profiles_mod.get(name)
            self.proto.send("detail", what="profile", name=name,
                            body=one.body if one else "")
            return
        if kind != "skill":
            return
        found = self.agent.toolbox.skills.get(name)
        # **载荷里不能有 kind**：`send()` 是 `{"kind": 事件名, **载荷}`，
        # 载荷里再来一个 kind 会把事件名整个盖掉，前端那条 case 永远不匹配
        # （协议测试一跑就露馅了 —— 这就是它存在的理由）
        self.proto.send("detail", what="skill", name=name,
                        body=found.body if found else "")

    def toggle(self, kind: str, name: str, value: bool) -> None:
        plural = {"skill": "skills", "tool": "tools", "mcp": "mcp"}.get(kind)
        if not plural:
            return
        enabled.set_on(plural, name, value)
        # 写盘之后当场重挂工具表、重拼系统提示 —— 少这一步就是"按了没反应"
        self.agent.apply_toggles()
        self.push_catalog()
        self.push_status()
        self.push_help()

    # ---- 命令补全 ----

    def push_help(self) -> None:
        """命令清单 + **每条命令的参数有哪些**

        只给命令名的话，`/mode ` 之后补全就没了，得自己记住有哪四档
        （用户拿截图来问过）。参数是会变的（模型清单、档案、技能），
        所以每次这些东西一变就重推一遍，别让界面拿着一份过期的。

        只列网页端真能跑的：TERMINAL_ONLY 那几条剔掉 ——
        **不该在补全里提供一个点了就报错的东西**。
        """
        from . import sandbox as sandbox_mod

        rows = {**ui.SLASH, **{"/" + n: c.description for n, c in self.commands.items()}}
        models = list(keys.load())
        args = {
            "/mode": list(agent_mod.MODE_ORDER),
            "/style": list(agent_mod.STYLES),
            "/model": models,
            "/helper": models + ["off"],
            "/profile": [p.name for p in profiles_mod.load()] + ["off"],
            "/skills": sorted(self.agent.toolbox.skills),
            "/sandbox": list(sandbox_mod.LEVELS),
        }
        self.proto.send("help",
                        commands=[{"name": n, "about": d} for n, d in rows.items()
                                  if n not in TERMINAL_ONLY],
                        args={k: v for k, v in args.items() if v and k not in TERMINAL_ONLY})

    # ---- 配置档案 ----

    def push_profiles(self) -> None:
        now = self.agent.profile
        self.proto.send("profiles", rows=[p.row(now) for p in profiles_mod.load()],
                        current=now)

    def profile(self, action: str, name: str, body: str, description: str) -> None:
        """面板上那几个动作 一律做完就把清单重推一遍，界面不自己猜结果 """
        if action == "use":
            self.agent.set_profile(name)
        elif action == "off":
            self.agent.set_profile("")
        elif action == "save":
            if profiles_mod.write(name, body, description=description):
                # 存的正好是正在用的那份就要重拼系统提示 —— 不然编辑完这一轮还是旧人格
                if name == self.agent.profile:
                    self.agent.set_profile(name)
            else:
                self.proto.send("note", level="error",
                                text="存不下这份档案（名字只能是字母数字下划线连字符或中文，40 字以内）")
                return
        elif action == "delete":
            profiles_mod.remove(name)
            if name == self.agent.profile:
                self.agent.set_profile("")
        else:
            return
        self.push_profiles()
        self.push_help()
        self.push_status()

    # ---- 命令 ----

    def run_command(self, text: str) -> None:
        """斜杠命令 内置的复用 __main__ 那些函数（返回 ANSI，剥掉再发）；
        自定义命令展开成一句话，当普通输入走
        """
        from . import __main__ as cli

        head, _, rest = text.partition(" ")
        rest = rest.strip()
        custom = self.commands.get(head[1:]) if head not in ui.SLASH else None
        if custom is not None:
            self.handle_prompt(skills_mod.expand(custom, rest))
            return
        agent = self.agent
        # 下面这些 cli 函数内部就是拿 ui.notice() 拼的，不挡住会报两遍
        with events.paused():
            self._dispatch(cli, head, rest)
        self.push_status()

    def _dispatch(self, cli, head: str, rest: str) -> None:
        agent = self.agent
        if head == "/clear":
            agent.reset()
            session_mod.drop(self.workspace, agent.session_id)
            agent.session_id = session_mod.new_id()
            self.proto.send("cleared")
            self.push_sessions()
            self.proto.send("note", level="notice", text="对话历史已清空，存盘也删了")
        elif head == "/profile":
            self.proto.send("note", level="notice", text=_plain(cli._profile(agent, rest)))
            self.push_catalog()
        elif head == "/mode":
            self.proto.send("note", level="notice", text=_plain(cli._mode(agent, rest)))
        elif head == "/model":
            self.proto.send("note", level="notice", text=_plain(cli._model(agent, rest)))
        elif head == "/style":
            self.proto.send("note", level="notice", text=_plain(cli._style(agent, rest)))
        elif head == "/compact":
            self.proto.send("note", level="notice", text=_plain(cli._compact(agent)))
        elif head == "/undo":
            said = agent.undo()
            self.proto.send("note", level="notice" if said else "warn",
                            text=said + "（下一句话时会告诉它）" if said else "没有可退的改动")
        elif head == "/skills":
            self.proto.send("note", level="notice",
                            text=_plain(cli._skills(agent, self.commands, rest)))
            self.push_catalog()
        elif head == "/cost":
            self.proto.send("note", level="notice",
                            text=_plain(cli._cost(agent, self.workspace)))
        elif head == "/context":
            self.proto.send("note", level="notice", text=_plain(cli._context(agent)))
        elif head == "/memory":
            self.proto.send("note", level="notice", text=_plain(cli._memory(agent, rest)))
        elif head == "/allow":
            self.proto.send("note", level="notice", text=_plain(cli._allow(rest)))
        elif head == "/jobs":
            self.proto.send("note", level="notice", text=_plain(cli._jobs(agent, rest)))
        elif head == "/hooks":
            self.proto.send("note", level="notice", text=_plain(cli._hooks(agent)))
        elif head == "/mcp":
            self.proto.send("note", level="notice", text=_plain(cli._mcp(agent)))
        elif head == "/verify":
            self.proto.send("note", level="notice", text=_plain(cli._verify(agent, rest)))
        elif head == "/helper":
            self.proto.send("note", level="notice", text=_plain(cli._helper(agent, rest)))
        elif head == "/review":
            # 要多花一次 API 调用，所以跟终端一样是手动触发的
            self.proto.send("note", level="notice", text=_plain(cli._review(agent, rest)))
        elif head in TERMINAL_ONLY:
            self.proto.send("note", level="warn",
                            text="%s 只在终端里有：%s" % (head, TERMINAL_ONLY[head]))
        elif head == "/sandbox":
            self.proto.send("note", level="notice", text=_plain(cli._sandbox(rest)))
        elif head == "/help":
            self.push_help()
        else:
            self.proto.send("note", level="error", text="未知命令 " + head)

    def handle_prompt(self, text: str) -> None:
        if not self.agent.ready:
            self.proto.send("note", level="error",
                            text="还没有模型，先在终端里 feiyu 起来用 /key add 添加一条")
            return
        self.busy = True
        self.proto.send("turn_start", text=text)
        self.push_status()
        try:
            self.agent.send(text)
        except KeyboardInterrupt:
            # 0.12.0 的不变量：历史已经在 send() 的 finally 里补齐了
            self.proto.send("note", level="warn", text="已中断")
        except Exception as exc:              # agent 挂了也不能把服务带走
            self.proto.send("note", level="error", text="%s: %s" % (type(exc).__name__, exc))
        finally:
            self.busy = False
            session_mod.save(self.workspace, self.agent, self.agent.session_id)
            self.proto.send("turn_end")
            self.push_status()
            # 新对话的第一轮跑完才有标题，侧边栏得跟着变
            self.push_sessions()

    # ---- 读 stdin 的线程 ----

    def reader(self) -> None:
        for line in sys.stdin:
            try:
                if not self._dispatch_in(line.strip()):
                    break
            except Exception:
                # **一条坏消息不能把这条线程弄死。** 死了之后 stdin 再也没人读，
                # 页面上看起来是「不响应了」，而 stderr 上那条 traceback 没人看得到。
                # 实测过：`{"op":"reply","id":"不是数字"}` 就足够（int() 抛 ValueError）。
                continue
        self.stopped = True
        self.proto.fail_all()
        self.inbox.put(("stop", ""))

    def _dispatch_in(self, line: str) -> bool:
        """处理一条进来的消息 返回 False 表示对面要停了 """
        if not line:
            return True
        try:
            msg = json.loads(line)
        except ValueError:
            return True
        if not isinstance(msg, dict):
            return True
        op = msg.get("op")
        if op == "stop":
            return False
        if op == "reply":
            try:
                rid = int(msg.get("id"))
            except (TypeError, ValueError):
                return True
            self.proto.reply(rid, msg.get("value"))
        elif op == "interrupt":
            # 跟 esc 落在同一处，0.12.0 那套补历史的机制原样生效
            if self.busy:
                _thread.interrupt_main()
        elif op in ("prompt", "command"):
            text = str(msg.get("text") or "")[:MAX_TEXT]
            self.inbox.put((op, text))
        elif op == "status":
            self.push_status()
        elif op == "sessions":
            self.push_sessions()
        elif op == "new":
            self.inbox.put(("new", ""))
        elif op == "open":
            self.inbox.put(("open", str(msg.get("id") or "")))
        elif op == "delete":
            self.inbox.put(("delete", str(msg.get("id") or "")))
        elif op == "catalog":
            self.push_catalog()
        elif op == "detail":
            self.push_detail(str(msg.get("kind") or ""), str(msg.get("name") or ""))
        elif op == "cycle_mode":
            # shift+tab 桌面端跟终端走**同一个** agent.cycle_mode()，
            # 不各写一套（终端那边是 prompt_toolkit 的键位，也不是斜杠命令）
            self.inbox.put(("cycle_mode", " "))
        elif op == "profiles":
            self.push_profiles()
        elif op == "profile":
            self.inbox.put(("profile", json.dumps({
                "action": msg.get("action"), "name": msg.get("name"),
                "body": str(msg.get("body") or "")[:MAX_TEXT],
                "description": msg.get("description")})))
        elif op == "toggle":
            # 改的是工具表和系统提示，跟跑一轮是同一份状态 —— 放主线程做
            self.inbox.put(("toggle", json.dumps(
                {"kind": msg.get("kind"), "name": msg.get("name"), "on": msg.get("on")})))
        return True

    def run(self) -> int:
        threading.Thread(target=self.reader, daemon=True).start()
        self.proto.send("hello", **self.status())
        while True:
            try:
                op, text = self.inbox.get()
            except KeyboardInterrupt:
                # 用户在起 feiyu-web 的那个终端上按了 ctrl-c：SIGINT 发给整个前台
                # 进程组，我们这个子进程也吃到一份，而这一下正落在 inbox.get() 上。
                # **不接住的话会甩一整页 traceback**，而且直接冲出 serve()，
                # `_finish` 里那些清理（杀后台命令、关语言服务器和 MCP 进程）全跳过了。
                return 0
            if op == "stop":
                return 0
            if not text.strip() and op != "new":
                continue
            try:
                # 换会话要在主线程做：它动 agent 的历史，跟跑一轮是同一份状态
                if op == "new":
                    self.new_session()
                elif op == "open":
                    self.open_session(text)
                elif op == "delete":
                    self.delete_session(text)
                elif op == "cycle_mode":
                    self.proto.send("note", level="notice",
                                    text="已切到「%s」" % agent_mod.MODES[
                                        self.agent.cycle_mode()][0])
                    self.push_status()
                elif op == "profile":
                    row = json.loads(text)
                    self.profile(str(row.get("action") or ""), str(row.get("name") or ""),
                                 str(row.get("body") or ""), str(row.get("description") or ""))
                elif op == "toggle":
                    row = json.loads(text)
                    self.toggle(str(row.get("kind") or ""), str(row.get("name") or ""),
                                bool(row.get("on")))
                elif op == "command" or text.startswith("/"):
                    self.run_command(text if text.startswith("/") else "/" + text)
                else:
                    self.handle_prompt(text)
            except KeyboardInterrupt:
                # 中断是从 reader 线程用 interrupt_main() 扔过来的，它按 self.busy
                # 判断该不该扔，而 busy 是在 handle_prompt 的 finally 里清的 ——
                # 中间有个窗口，这一下会落在收尾代码或者下面的 inbox.get() 上。
                # 不接住的话整个服务就没了，而用户只是按了一下中断。
                self.busy = False
                self.proto.send("note", level="warn", text="已中断")
                self.proto.send("turn_end")
                self.push_status()


def serve(agent, workspace, tracer, resume: bool = False) -> int:
    """把 agent 挂到 stdio 上 返回退出码 """
    real_stdout = sys.stdout
    proto = Protocol(real_stdout)
    server = Server(agent, workspace, tracer, proto)

    agent.approve = server.approve
    agent.approve_plan = server.approve_plan
    agent.ask_user = server.ask_user
    agent.approve_hooks = lambda path, count: server.approve_trust(
        "hooks", path, ["%d 条" % count])
    agent.approve_mcp = lambda path, names: server.approve_trust("mcp", path, names)
    agent.approve_skills = lambda folder, names: server.approve_trust("skills", folder, names)

    events.install(lambda kind, payload: proto.send(kind, **payload))
    # 人看的那些 ANSI 全挪到 stderr，stdout 只剩协议 —— 跟 --json-schema 同一个做法
    with contextlib.redirect_stdout(sys.stderr):
        # 启动时这三句是**环境说明**，不是对话。单独一种事件，让前端摆在别处 ——
        # 当成 note 发的话它会在正文流里占一整块，看着像 agent 开口说了句什么，
        # 还会把「把要做的事写在下面」那条空态提示顶掉（用户拿截图来问过）。
        # 规矩照 0.26.1：常态不占位置，异常才出现。
        for kind, text in (("hooks", agent.load_hooks()),
                           ("mcp", agent.load_mcp()),
                           ("skills", agent.load_skills())):
            if text:
                # 「未启用 / 没跑起来」这类要显眼，其余只是说一声接了什么
                bad = any(w in text for w in ("未启用", "失败", "没起来", "出错"))
                proto.send("boot", what=kind, level="warn" if bad else "notice",
                           text=text)
        saved = session_mod.load(workspace) if resume else None
        if saved and agent.restore(saved):
            agent.session_id = session_mod.latest_id(workspace)
            proto.send("resumed", about=session_mod.describe(saved),
                       history=history_view(agent))
        else:
            agent.session_id = session_mod.new_id()
        server.push_sessions()
        try:
            return server.run()
        finally:
            events.clear()


def history_view(agent) -> list[dict]:
    """`--resume` 回来时给网页端一份能画的历史

    **只要人说过的和它说过的**，工具调用和工具返回不回放：那些在终端的 scrollback
    里是有来龙去脉的，回放到网页上只是一堆没有上下文的碎片
    两种后端的 messages 结构不通用，所以这儿两种形状都认（照 `transcript()` 那样）
    """
    from .providers import _field

    out = []
    for msg in getattr(agent.backend, "messages", []) or []:
        role = msg.get("role") if isinstance(msg, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if role == "system" or role == "tool":
            continue
        if isinstance(content, str):
            if role in ("user", "assistant") and content.strip():
                out.append({"role": role, "text": content.strip()})
            continue
        for block in content or []:
            # user 消息里的 tool_result 块要跳过，它不是人说的话
            if _field(block, "type") != "text":
                continue
            text = (_field(block, "text") or "").strip()
            if text:
                out.append({"role": "assistant", "text": text})
    return out
