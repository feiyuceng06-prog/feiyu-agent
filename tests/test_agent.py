"""agent 循环 大部分断言盯的是同一件事：**历史必须始终是接口收得下的形状**

tool_use 少一条配对结果、角色不交替，下一次请求就是 400，会话再也发不出去
（DeepSeek 实测 "An assistant message with 'tool_calls' must be followed by
tool messages responding to each 'tool_call_id'"）而且损坏状态会被存盘带走
"""

import contextlib
import datetime
import io
import os
import json
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path

from feiyu import agent as agent_mod
from feiyu import enabled, keys, permissions, tools as tools_mod
from feiyu.agent import Feiyu
from feiyu.tools import ToolResult
from tests.fakes import FakeBackend, call, unpaired


def quiet(fn, *a, **kw):
    """agent 会往屏幕上画东西，测试里不看 """
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 不换掉的话读的是用户真正的规则文件，断言会随他改过什么而变
        permissions.STORE = os.path.join(self.tmp.name, "permissions.json")
        # 学到的上下文上限写在 state.json 里，不换掉会写进用户真正的那份
        keys.STORE = os.path.join(self.tmp.name, "models.json")
        keys.STATE = os.path.join(self.tmp.name, "state.json")
        # image_generate 挂不挂看这份配置在不在 不换掉的话，工具清单会随
        # 这台机器上的用户有没有配出图接口而变
        tools_mod.IMAGE_CONFIG = os.path.join(self.tmp.name, "images.json")
        enabled.STORE = os.path.join(self.tmp.name, "enabled.json")
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.mode = "auto"
        self.ran = []

    def tearDown(self):
        self.tmp.cleanup()

    def use(self, script, **kw):
        self.agent.backend = FakeBackend(script, **kw)
        return self.agent.backend

    def tools_ok(self):
        def run(name, args):
            self.ran.append((name, args))
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run

    def tool_results(self, backend):
        return [m["content"] for m in backend.messages if m["role"] == "tool"]

    def tools_interrupt_at(self, nth):
        """跑到第 nth 次调用时抛 KeyboardInterrupt（0 表示第一次就抛）"""
        count = {"n": 0}
        def run(name, args):
            if count["n"] >= nth:
                raise KeyboardInterrupt()
            count["n"] += 1
            self.ran.append((name, args))
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run


class TestInterruptKeepsHistoryValid(Base):
    def test_第一个工具就被打断(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        self.assertEqual(unpaired(b.messages), set())

    def test_打断落在第二个工具上(self):
        b = self.use([("", [call("c1"), call("c2")])])
        self.tools_interrupt_at(1)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        self.assertEqual(unpaired(b.messages), set())
        # 第一个真的跑过了，结果要保住；第二个补的是「被打断」
        results = [m for m in b.messages if m["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertIn("打断", results[1]["content"])

    def test_补的结果是错误状态(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        # openai 后端把 is_error 拼成「错误: 」前缀
        self.assertTrue(b.messages[-1]["content"].startswith("错误"))

    def test_模型正说话时被打断(self):
        b = self.use([])
        def boom(on_text, on_tool=None, on_think=None):
            on_text("我正在读 build.mjs")
            raise KeyboardInterrupt()
        b.run_turn = boom
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "看看")
        # 已经说出口的话要留住，而且角色要接着交替
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertIn("我正在读 build.mjs", b.messages[-1]["content"])

    def test_一个字都没说就被打断(self):
        b = self.use([])
        def boom(on_text, on_tool=None, on_think=None):
            raise KeyboardInterrupt()
        b.run_turn = boom
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "看看")
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertTrue(b.messages[-1]["content"])

    def test_打断之后还能接着聊(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "跑个长命令")
        b.script = [("好的", [])]
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "算了"), "好的")
        self.assertEqual(unpaired(b.messages), set())

    def test_角色始终交替(self):
        b = self.use([("", [call("c1")])])
        self.tools_interrupt_at(0)
        with self.assertRaises(KeyboardInterrupt):
            quiet(self.agent.send, "做点事")
        b.add_user_message("下一句")
        roles = [m["role"] for m in b.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])


class TestToolArgProgress(Base):
    """写一个大文件那十几秒里，参数流要接到 Stream 上，不能只有 spinner """

    def test_把_stream_的_progress_传下去(self):
        seen = []
        b = self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好", [])])
        real = b.run_turn
        def run_turn(on_text, on_tool=None, on_think=None):
            seen.append(on_tool)
            return real(on_text, on_tool)
        b.run_turn = run_turn
        self.tools_ok()
        quiet(self.agent.send, "写个文件")
        self.assertTrue(seen)
        for fn in seen:
            self.assertEqual(getattr(fn, "__name__", ""), "progress")
            fn("write_file", '{"path": "a.py"')      # 拿真参数调一下，别在真跑时才崩


class TestToolLoop(Base):
    def test_没有工具调用就结束(self):
        self.use([("答案是 42", [])])
        self.assertEqual(quiet(self.agent.send, "问题"), "答案是 42")

    def test_工具结果回灌后继续(self):
        b = self.use([("", [call("c1")]), ("看完了", [])])
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "读一下"), "看完了")
        self.assertEqual(len(self.ran), 1)
        self.assertEqual(unpaired(b.messages), set())

    def test_到轮数上限会停下并补一条_assistant(self):
        # 不补的话角色不交替，下一轮请求直接报错
        b = self.use([("", [call("c%d" % i)]) for i in range(agent_mod.MAX_ROUNDS + 2)])
        self.tools_ok()
        out = quiet(self.agent.send, "停不下来的任务")
        self.assertIn("停下来", out)
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertEqual(len(self.ran), agent_mod.MAX_ROUNDS)

    def test_用户说接着做就接着跑(self):
        """上限是防空转的，不是拦大重构的 —— 几十轮的任务不该被一刀切掉 """
        script = [("", [call("c%d" % i)]) for i in range(agent_mod.MAX_ROUNDS)]
        script.append(("做完了", []))
        b = self.use(script)
        self.tools_ok()
        asked = []
        self.agent.ask_user = lambda q, o: asked.append((q, o)) or agent_mod.Feiyu.CONTINUE
        self.assertEqual(quiet(self.agent.send, "很大的重构"), "做完了")
        self.assertEqual(len(asked), 1)
        self.assertIn(agent_mod.Feiyu.CONTINUE, asked[0][1])
        self.assertEqual(len(self.ran), agent_mod.MAX_ROUNDS)
        self.assertEqual(unpaired(b.messages), set())

    def test_用户在那儿说了别的就带给模型(self):
        """丢掉的话他下一句得把同样的话再说一遍 """
        script = [("", [call("c%d" % i)]) for i in range(agent_mod.MAX_ROUNDS + 2)]
        b = self.use(script)
        self.tools_ok()
        self.agent.ask_user = lambda q, o: "别改了，先看看 build.mjs"
        out = quiet(self.agent.send, "停不下来的任务")
        self.assertIn("别改了，先看看 build.mjs", out)
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertEqual(len(self.ran), agent_mod.MAX_ROUNDS)

    def test_max_rounds_可以调大(self):
        """出厂 25 是给交互用的；SWE-bench 那种题几十上百轮，
        不调大的话量出来的是上限不是能力 """
        self.agent.max_rounds = 3
        b = self.use([("", [call("c%d" % i)]) for i in range(6)])
        self.tools_ok()
        out = quiet(self.agent.send, "停不下来的任务")
        self.assertIn("停下来", out)
        self.assertEqual(len(self.ran), 3, "没按 self.max_rounds 走")
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertEqual(unpaired(b.messages), set())

    def test_max_rounds_写_0_就没有闸(self):
        """Lean 那种「改一行 → 编译 → 再改」的循环几百轮起步，事先给不出对的数
        放开轮数之后收口的是 budget，不是轮数 """
        self.agent.max_rounds = 0
        n = agent_mod.MAX_ROUNDS * 3
        script = [("", [call("c%d" % i)]) for i in range(n)]
        script.append(("做完了", []))
        b = self.use(script)
        self.tools_ok()
        self.agent.ask_user = lambda q, o: self.fail("不该问，闸已经关了")
        self.assertEqual(quiet(self.agent.send, "很长的任务"), "做完了")
        self.assertEqual(len(self.ran), n)
        self.assertEqual(unpaired(b.messages), set())

    def test_不设闸也还有预算那道(self):
        """轮数放开了钱不能放开 否则一道题能烧掉一整轮评测的预算 """
        self.agent.max_rounds = 0
        self.agent.budget = 0.01
        b = self.use([("", [call("c%d" % i)]) for i in range(40)])
        self.tools_ok()
        b.priced, b.cost = True, 0.5     # cost 是 property，得从后端那边给
        out = quiet(self.agent.send, "很贵的任务")
        # 断言要认准是**预算**那道闸停的：轮数那道的措辞里也有"我先停下"，
        # 只断这三个字的话，闸没关它照样绿
        self.assertIn("到了你设的上限", out)
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertEqual(unpaired(b.messages), set())

    def test_max_rounds_调大之后真的多跑(self):
        """闸是 `rounds >= max_rounds`，**正好在第 n 轮就触发** ——
        所以要跑满 n 轮还能收尾，上限得比 n 大一。这跟出厂 25 的语义一致。
        """
        n = agent_mod.MAX_ROUNDS + 4
        self.agent.max_rounds = n + 1
        script = [("", [call("c%d" % i)]) for i in range(n)]
        script.append(("做完了", []))
        b = self.use(script)
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "很长的任务"), "做完了")
        self.assertEqual(len(self.ran), n, "还是被出厂的 25 挡住了")
        self.assertEqual(unpaired(b.messages), set())

    def test_那条空串和_None_的契约(self):
        """**这条横跨三个函数且是承重的**：`_ask_user_cb` 没装回调时返回 `""`，
        `_round_limit` 把 `""` 原样传回去，`send()` 只在 `is None` 时清零重跑。

        写成 `if not said` 或者让 `_ask_user_cb` 返回 `None`，非交互那条路
        就从"硬停"变成**无限续跑** —— 而那不会有任何用例自己红，只会烧钱。
        """
        self.agent.ask_user = None
        self.assertEqual(self.agent._ask_user_cb("问", ["甲"]), "",
                         "没装回调时必须是空串，不能是 None")
        self.assertEqual(self.agent._round_limit(9), "", "没人回答要原样返回空串")
        self.agent.ask_user = lambda q, o: agent_mod.Feiyu.CONTINUE
        self.assertIsNone(self.agent._round_limit(9), "说接着做才返回 None")

    def test_超预算就停_并补一条_assistant(self):
        """放开轮数之后这是唯一的另一道闸 """
        self.agent.max_rounds = 999
        self.agent.budget = 1.0
        b = self.use([("", [call("c%d" % i)]) for i in range(5)])
        self.tools_ok()
        b.priced = True
        b.cost = 2.5                       # 已经花超了
        out = quiet(self.agent.send, "很贵的任务")
        self.assertIn("上限", out)
        self.assertEqual(b.messages[-1]["role"], "assistant",
                         "不补 assistant 的话下一轮请求角色不交替，直接 400")
        self.assertEqual(len(self.ran), 1, "超了预算还接着跑")
        self.assertEqual(unpaired(b.messages), set())

    def test_没配价格时预算这道闸自己让开(self):
        """`self.cost` 是 None（priced=False）—— 0.67.2 刚因为 round(None) 把
        --serve 整个弄死过。没有价格就没法谈预算，只能靠轮数那道闸 """
        self.agent.budget = 1.0
        b = self.use([("", [call("c1")]), ("做完了", [])])
        self.tools_ok()
        b.priced = False
        self.assertIsNone(self.agent.cost)
        self.assertEqual(self.agent._over_budget(), "")
        self.assertEqual(quiet(self.agent.send, "干活"), "做完了")

    def test_没设预算就不管钱(self):
        self.agent.budget = 0.0
        b = self.use([("", [call("c1")]), ("做完了", [])])
        self.tools_ok()
        b.priced, b.cost = True, 999.0
        self.assertEqual(self.agent._over_budget(), "")
        self.assertEqual(quiet(self.agent.send, "干活"), "做完了")

    def test_没人能回答照旧停下(self):
        """管道和一次性任务没人看着，空转很贵 """
        b = self.use([("", [call("c%d" % i)]) for i in range(agent_mod.MAX_ROUNDS + 2)])
        self.tools_ok()
        self.assertIsNone(self.agent.ask_user)
        self.assertIn("停下来", quiet(self.agent.send, "停不下来的任务"))
        self.assertEqual(len(self.ran), agent_mod.MAX_ROUNDS)

    def test_参数没解析出来就不执行工具(self):
        bad = call("c1")
        bad.error = "参数不是完整的 JSON"
        b = self.use([("", [bad]), ("重来了", [])])
        self.tools_ok()
        quiet(self.agent.send, "写个文件")
        self.assertEqual(self.ran, [])                       # 工具一次都没跑
        self.assertEqual(unpaired(b.messages), set())        # 但结果照样配上了
        self.assertIn("JSON", self.tool_results(b)[0])

    def test_撞上长度上限会提示(self):
        self.use([("半截话", [])], truncated=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.agent.send("写点长的")
        self.assertIn("长度上限", buf.getvalue())

    def test_没有模型时报错(self):
        self.agent.backend = None
        with self.assertRaises(RuntimeError):
            self.agent.send("你好")


class TestGate(Base):
    def setUp(self):
        super().setUp()
        self.use([("", [call("c1")]), ("好", [])])
        self.tools_ok()

    def test_只读模式拒绝写类工具(self):
        self.agent.mode = "readonly"
        for name, args in (("bash", {"command": "ls"}),
                           ("write_file", {"path": "a", "content": "x"}),
                           ("edit_file", {"path": "a", "old_str": "x", "new_str": "y"})):
            ok = self.agent._gate(call("c", name, **args), False)[0]
            self.assertFalse(ok, name)

    def test_只读模式放行读类工具(self):
        self.agent.mode = "readonly"
        for name, args in (("read_file", {"path": "a"}),
                           ("list_files", {"pattern": "*"}),
                           ("search", {"regex": "x"}),
                           ("web_fetch", {"url": "https://x.com"})):
            self.assertTrue(self.agent._gate(call("c", name, **args), False)[0], name)

    def test_自动模式一律放行(self):
        self.agent.mode = "auto"
        self.assertTrue(self.agent._gate(call("c", "bash", command="rm -rf x"), False)[0])

    def test_默认模式没人应答就是拒绝(self):
        self.agent.mode = "default"
        self.agent.approve = None
        ok, _, _, reason = self.agent._gate(call("c", "bash", command="rm -rf x"), False)
        self.assertFalse(ok)
        self.assertIn("拒绝", reason)

    def test_本轮都允许只问一次(self):
        self.agent.mode = "default"
        asked = []
        self.agent.approve = lambda n, a: (asked.append(n), "always")[1]
        b = self.use([("", [call("c1", "bash", command="rm a"),
                            call("c2", "bash", command="rm b")]), ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "删两个文件")
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(self.ran), 2)

    def test_拒绝的调用把原因回给模型(self):
        self.agent.mode = "default"
        self.agent.approve = lambda n, a: "no"
        b = self.use([("", [call("c1", "bash", command="rm -rf x")]), ("那算了", [])])
        self.tools_ok()
        quiet(self.agent.send, "删掉")
        self.assertEqual(self.ran, [])
        self.assertEqual(unpaired(b.messages), set())
        self.assertIn("拒绝", self.tool_results(b)[0])


class TestCompact(Base):
    def test_压缩后重建成两条(self):
        b = self.use([])
        b.messages += [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.assertEqual(self.agent.compact(), "这是摘要")
        self.assertEqual([m["role"] for m in b.messages], ["system", "user", "assistant"])
        self.assertEqual(b.tokens_context, 0)

    def test_循环内压缩要补一条_user(self):
        # compact_to 收尾是 assistant，任务没做完还要接着调 run_turn，
        # 不补 user 的话角色不交替
        b = self.use([])
        b.messages += [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        b.tokens_context = self.agent.context_limit
        quiet(self.agent._maybe_compact, mid_turn=True)
        self.assertEqual(b.messages[-1]["role"], "user")

    def test_收尾压缩不补_user(self):
        b = self.use([])
        b.messages += [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        b.tokens_context = self.agent.context_limit
        quiet(self.agent._maybe_compact)
        self.assertEqual(b.messages[-1]["role"], "assistant")

    def test_刚读进来的大文件在下一次请求前就算进上下文(self):
        """接口报的 tokens_context 是上一轮的数，一次 read_file 读进 16k token 之后
        它仍然显示没满，等真发出去就是 400 追加的工具结果要先粗算进去 """
        self.agent.toolbox.run = lambda name, args: ToolResult("X" * 30000, summary="读了")
        b = self.use([("看", [call("r1", "read_file", path="big.py")]), ("好了", [])])
        b.tokens_context = int(self.agent.context_limit * 0.7)
        seen = []
        real = self.agent._maybe_compact
        self.agent._maybe_compact = lambda **kw: seen.append(self.agent.context)
        quiet(self.agent.send, "读一下")
        # 30000 字符按三个字符一个 token 估，加上去就过了 75% 的线
        self.assertGreater(seen[0], self.agent.context_limit * 0.75)
        # run_turn 回来之后接口报了准确的数，估算作废
        self.assertEqual(self.agent.context, b.tokens_context)

    def test_没到阈值不压(self):
        b = self.use([])
        b.tokens_context = int(self.agent.context_limit * 0.5)
        before = list(b.messages)
        quiet(self.agent._maybe_compact)
        self.assertEqual(b.messages, before)

    def test_摘要为空时历史照旧(self):
        b = self.use([])
        b.ask = lambda *a, **kw: "   "
        b.messages.append({"role": "user", "content": "a"})
        before = list(b.messages)
        self.assertEqual(self.agent.compact(), "")
        self.assertEqual(b.messages, before)


class TestProjectContext(Base):
    def test_按顺序找第一个存在的(self):
        root = Path(self.tmp.name)
        (root / "CLAUDE.md").write_text("claude 的约定", encoding="utf-8")
        (root / "AGENTS.md").write_text("agents 的约定", encoding="utf-8")
        name, text = agent_mod.load_project_context(root)
        self.assertEqual(name, "AGENTS.md")          # AGENTS 排在 CLAUDE 前面
        self.assertEqual(text, "agents 的约定")

    def test_空文件跳过(self):
        root = Path(self.tmp.name)
        (root / "AGENTS.md").write_text("   \n", encoding="utf-8")
        (root / "CLAUDE.md").write_text("有内容", encoding="utf-8")
        self.assertEqual(agent_mod.load_project_context(root)[0], "CLAUDE.md")

    def test_没有就返回两个空串(self):
        self.assertEqual(agent_mod.load_project_context(Path(self.tmp.name)), ("", ""))

    def test_超长要截断(self):
        root = Path(self.tmp.name)
        (root / "FEIYU.md").write_text("x" * 20000, encoding="utf-8")
        self.assertEqual(len(agent_mod.load_project_context(root)[1]),
                         agent_mod.MAX_CONTEXT_CHARS)

    def test_约定会进系统提示(self):
        root = Path(self.tmp.name)
        (root / "FEIYU.md").write_text("只用制表符缩进", encoding="utf-8")
        b = self.use([])
        self.agent.reload_context()
        self.assertIn("只用制表符缩进", b.system)


class TestModes(Base):
    def test_shift_tab_循环(self):
        self.agent.mode = "default"
        seen = [self.agent.cycle_mode() for _ in agent_mod.MODE_ORDER]
        self.assertEqual(seen, ["auto", "plan", "readonly", "default"])

    def test_风格会改系统提示(self):
        b = self.use([])
        self.agent.set_style("teaching")
        self.assertIn(agent_mod.STYLES["teaching"][1].strip()[:10], b.system)

    def test_两个集合的划分(self):
        # web_fetch 会把内容发出去，要问；但它不改状态，只读模式放行
        self.assertIn("web_fetch", agent_mod.GUARDED)
        self.assertNotIn("web_fetch", agent_mod.WRITES)
        self.assertTrue(agent_mod.WRITES <= agent_mod.GUARDED)


class TestEnvBlock(Base):
    """不给它环境信息的话：日期靠编，装包命令按别的发行版给，
    每次想知道自己在哪还要多跑一条 pwd """

    def test_四项都在(self):
        text = agent_mod.env_block(Path(self.tmp.name))
        self.assertIn(self.tmp.name, text)
        self.assertIn(datetime.date.today().isoformat(), text)
        self.assertIn(platform.system(), text)

    def test_不是仓库就说不是(self):
        self.assertIn("不是 git 仓库", agent_mod.env_block(Path(self.tmp.name)))

    def test_在仓库里要报出分支名(self):
        root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", "-b", "话题分支"], cwd=root, check=True)
        self.assertIn("话题分支", agent_mod.env_block(root))

    def test_进得了系统提示(self):
        self.agent.backend = FakeBackend([])
        self.agent._refresh_system()
        self.assertIn("当前环境", self.agent.backend.system)
        self.assertIn(self.tmp.name, self.agent.backend.system)


class TestVerifyConfig(unittest.TestCase):
    def test_单起一行才算配置(self):
        self.assertEqual(agent_mod.find_verify("# 项目\n\nverify: npm test\n\n别的"), "npm test")

    def test_剥掉反引号(self):
        self.assertEqual(agent_mod.find_verify("verify: `pytest -q`"), "pytest -q")

    def test_行中间提一嘴不算(self):
        # 正文里随口说到 verify: 不该被当成配置
        self.assertEqual(agent_mod.find_verify("我们用 verify: 这个词表示校验"), "")

    def test_大小写不敏感(self):
        self.assertEqual(agent_mod.find_verify("Verify: make check"), "make check")

    def test_没配就是空串(self):
        self.assertEqual(agent_mod.find_verify("什么都没有"), "")
        self.assertEqual(agent_mod.find_verify(""), "")

    def test_从约定文件里读出来(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "FEIYU.md").write_text("verify: make test\n", encoding="utf-8")
            self.assertEqual(Feiyu(Path(tmp)).verify, "make test")


class TestLimitConfig(unittest.TestCase):
    """`max-rounds:` / `budget:` 跟 verify 同一个机制 出厂 25 是给交互用的，
    证明、几十个文件的重构这类活本来就要几百轮，项目得能自己定 """

    def test_单起一行才算配置(self):
        self.assertEqual(agent_mod.find_limits("# 项目\n\nmax-rounds: 400\n"),
                         {"max_rounds": 400})

    def test_下划线和空格都认(self):
        self.assertEqual(agent_mod.find_limits("max_rounds: 8"), {"max_rounds": 8})
        self.assertEqual(agent_mod.find_limits("maxrounds: 8"), {"max_rounds": 8})

    def test_行中间提一嘴不算(self):
        self.assertEqual(agent_mod.find_limits("上限那条叫 max-rounds: 多少合适？"), {})

    def test_没写的键不出现(self):
        """0 是"不限"这个明确的选择，不能拿它表示"没配"——
        混起来的话约定文件里没写 budget 就会把命令行给的 budget 清成 0 """
        self.assertEqual(agent_mod.find_limits("max-rounds: 0"), {"max_rounds": 0})
        self.assertEqual(agent_mod.find_limits("什么都没有"), {})

    def test_budget_带不带货币符号都认(self):
        self.assertEqual(agent_mod.find_limits("budget: 5"), {"budget": 5.0})
        self.assertEqual(agent_mod.find_limits("budget: ¥2.5"), {"budget": 2.5})

    def test_从约定文件里读出来(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "FEIYU.md").write_text(
                "verify: make test\nmax-rounds: 400\nbudget: 5\n", encoding="utf-8")
            a = Feiyu(Path(tmp))
            self.assertEqual(a.max_rounds, 400)
            self.assertEqual(a.budget, 5.0)

    def test_没配就用出厂值(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "FEIYU.md").write_text("verify: make test\n", encoding="utf-8")
            a = Feiyu(Path(tmp))
            self.assertEqual(a.max_rounds, agent_mod.MAX_ROUNDS)
            self.assertEqual(a.budget, 0.0)

    def test_命令行给过的不被约定文件盖掉(self):
        """用户敲了 --max-rounds 就是要这个数，`/context` 重载不该把它改回去 """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "FEIYU.md").write_text("max-rounds: 400\n", encoding="utf-8")
            a = Feiyu(Path(tmp))
            a.max_rounds = 7
            a.limit_cli.add("max_rounds")
            a.reload_context()
            self.assertEqual(a.max_rounds, 7)


class TestVerifyLoop(Base):
    def setUp(self):
        super().setUp()
        self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好了", [])])
        self.tools_ok()

    def test_只有写文件才触发(self):
        # bash 也能改文件，但每次 ls / git status 后都跑测试代价太大
        self.assertIn("write_file", agent_mod.VERIFY_AFTER)
        self.assertIn("edit_file", agent_mod.VERIFY_AFTER)
        self.assertNotIn("bash", agent_mod.VERIFY_AFTER)

    def test_通过时不打扰模型(self):
        b = self.agent.backend
        self.agent.verify = "true"
        quiet(self.agent.send, "写个文件")
        self.assertTrue(self.agent.verified_ok)
        self.assertNotIn("没有通过", self.tool_results(b)[0])

    def test_没过就把报错回灌(self):
        b = self.agent.backend
        self.agent.verify = "echo 三个测试挂了 >&2; exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertFalse(self.agent.verified_ok)
        result = self.tool_results(b)[0]
        self.assertIn("没有通过", result)
        self.assertIn("三个测试挂了", result)

    def test_没过的结果标成错误(self):
        b = self.agent.backend
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertTrue(b.messages[-2]["content"].startswith("错误")
                        or "没有通过" in self.tool_results(b)[0])

    def test_回灌之后历史仍然配对(self):
        b = self.agent.backend
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertEqual(unpaired(b.messages), set())

    def test_没配就不跑(self):
        self.agent.verify = ""
        quiet(self.agent.send, "写个文件")
        self.assertIsNone(self.agent.verified_ok)

    def test_命令跑不起来也不崩(self):
        self.agent.verify = "绝对没有这个命令 --xyz"
        quiet(self.agent.send, "写个文件")
        self.assertFalse(self.agent.verified_ok)

    def test_只读工具不触发(self):
        self.use([("", [call("c1", "read_file", path="a.py")]), ("好了", [])])
        self.tools_ok()
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "读个文件")
        self.assertIsNone(self.agent.verified_ok)

    def test_被拒的写不触发(self):
        self.agent.mode = "default"
        self.agent.approve = lambda n, a: "no"
        self.agent.verify = "exit 1"
        quiet(self.agent.send, "写个文件")
        self.assertIsNone(self.agent.verified_ok)


class TestReviewHelpers(Base):
    def test_评分能认出来(self):
        self.assertEqual(agent_mod.find_score("结论如下\n\n评分: 8/10"), 8.0)
        self.assertEqual(agent_mod.find_score("评分：9.5/10"), 9.5)
        self.assertIsNone(agent_mod.find_score("还行吧"))

    def test_没有改动就不评(self):
        self.use([])
        self.assertEqual(self.agent.review("任务"), "")

    def test_没有_git_时算出真的_diff(self):
        # 只给改完的整份文件，评分的模型看不到改动前的样子，
        # 会把"这文件本来就只有一个函数"读成"它把别的都删了"（实测踩过）
        (Path(self.tmp.name) / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        self.agent.touched = ["a.py"]
        self.agent.before = {"a.py": "x = 0\ny = 2\n"}
        body = self.agent.changes()
        self.assertIn("-x = 0", body)
        self.assertIn("+x = 1", body)
        self.assertNotIn("-y = 2", body)        # 没动的行不该出现在减号里

    def test_新建的文件整份都是新增(self):
        (Path(self.tmp.name) / "新.py").write_text("a\n", encoding="utf-8")
        self.agent.touched = ["新.py"]
        self.agent.before = {"新.py": ""}
        self.assertIn("+a", self.agent.changes())

    def test_读不出来的文件不崩(self):
        self.agent.touched = ["没有这个.py"]
        self.assertIn("没有这个.py", self.agent.changes())

    def test_动手前会存一份原样(self):
        (Path(self.tmp.name) / "a.py").write_text("原来的\n", encoding="utf-8")
        self.use([("", [call("c1", "edit_file", path="a.py",
                             old_str="原来的", new_str="改过的")]), ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "改一下")
        self.assertEqual(self.agent.before["a.py"], "原来的\n")

    def test_写文件会记进改动清单(self):
        self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "写个文件")
        self.assertEqual(self.agent.touched, ["a.py"])

    def test_同一个文件不重复记(self):
        self.use([("", [call("c1", "write_file", path="a.py", content="x"),
                        call("c2", "edit_file", path="a.py", old_str="x", new_str="y")]),
                  ("好", [])])
        self.tools_ok()
        quiet(self.agent.send, "改两次")
        self.assertEqual(self.agent.touched, ["a.py"])


class TestFinishJson(Base):
    SPEC = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}

    def test_一次就对(self):
        self.use([('{"ok": true}', [])])
        self.assertEqual(quiet(self.agent.finish_json, self.SPEC), ({"ok": True}, []))

    def test_裹了代码块也认(self):
        self.use([('```json\n{"ok": false}\n```', [])])
        self.assertEqual(quiet(self.agent.finish_json, self.SPEC)[0], {"ok": False})

    def test_不合规会让它重出(self):
        b = self.use([('{"ok": "yes"}', []), ('{"ok": true}', [])])
        value, problems = quiet(self.agent.finish_json, self.SPEC)
        self.assertEqual(value, {"ok": True})
        self.assertEqual(problems, [])

    def test_一直不合规就报出问题(self):
        self.use([('{"不对": 1}', [])] * (4))
        value, problems = quiet(self.agent.finish_json, self.SPEC)
        self.assertTrue(problems)

    def test_没有模型时报错(self):
        self.agent.backend = None
        self.assertTrue(quiet(self.agent.finish_json, self.SPEC)[1])


class TestTransient(unittest.TestCase):
    """判断一个错值不值得重试 不绑 SDK 的异常类：两种后端，
    openai 还是可选依赖，硬 import 会把 anthropic-only 的装法弄坏 """

    def make(self, name, code=None):
        exc = type(name, (Exception,), {})()
        if code is not None:
            exc.status_code = code
        return exc

    def test_按状态码认(self):
        for code in (429, 500, 502, 503, 504, 408):
            self.assertTrue(agent_mod.transient(self.make("Whatever", code)), code)

    def test_客户端错误不重试(self):
        for code in (400, 401, 403, 404, 422):
            self.assertFalse(agent_mod.transient(self.make("Whatever", code)), code)

    def test_按类名认连接类错误(self):
        for name in ("APIConnectionError", "APITimeoutError", "RateLimitError"):
            self.assertTrue(agent_mod.transient(self.make(name)), name)

    def test_认得出子类(self):
        base = type("APIConnectionError", (Exception,), {})
        self.assertTrue(agent_mod.transient(type("我的错", (base,), {})()))

    def test_普通异常不重试(self):
        self.assertFalse(agent_mod.transient(ValueError("随便")))


class TestStreamRetry(Base):
    def setUp(self):
        super().setUp()
        # 别让测试真的睡两秒
        self._wait = agent_mod.RETRY_WAIT
        agent_mod.RETRY_WAIT = 0
        self.addCleanup(lambda: setattr(agent_mod, "RETRY_WAIT", self._wait))

    def boom(self, times, exc_factory, text=""):
        """前 times 次抛错，之后正常返回 """
        state = {"n": 0}
        b = self.use([("答完了", [])])
        real = b.run_turn
        def run_turn(on_text, on_tool=None, on_think=None):
            if state["n"] < times:
                state["n"] += 1
                if text:
                    on_text(text)
                raise exc_factory()
            return real(on_text, on_tool)
        b.run_turn = run_turn
        return b, state

    def rate_limit(self):
        exc = type("RateLimitError", (Exception,), {})()
        exc.status_code = 429
        return exc

    def test_还没吐字就重试(self):
        b, state = self.boom(1, self.rate_limit)
        self.assertEqual(quiet(self.agent.send, "问题"), "答完了")
        self.assertEqual(state["n"], 1)

    def test_重试有上限(self):
        b, state = self.boom(99, self.rate_limit)
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        self.assertEqual(state["n"], agent_mod.STREAM_RETRIES + 1)

    def test_已经吐字了就不重试(self):
        # 重发会让模型把刚说的话再说一遍
        b, state = self.boom(99, self.rate_limit, text="我先看一下这个文件")
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        self.assertEqual(state["n"], 1)

    def test_已经吐的字要留在历史里(self):
        b, _ = self.boom(99, self.rate_limit, text="我先看一下")
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        self.assertEqual(b.messages[-1]["role"], "assistant")
        self.assertIn("我先看一下", b.messages[-1]["content"])

    def test_报错后角色仍然交替(self):
        # 不补 assistant 的话，下次请求两条连着的 user，跟中断那个坑一样
        b, _ = self.boom(99, self.rate_limit)
        with self.assertRaises(Exception):
            quiet(self.agent.send, "问题")
        b.add_user_message("再说一句")
        roles = [m["role"] for m in b.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_不该重试的错直接抛(self):
        bad = lambda: ValueError("参数写错了")
        b, state = self.boom(99, bad)
        with self.assertRaises(ValueError):
            quiet(self.agent.send, "问题")
        self.assertEqual(state["n"], 1)

    def test_重试计数每轮清零(self):
        # 第一轮重试过，不该影响后面几轮的额度
        self.assertEqual(agent_mod.STREAM_RETRIES, 2)


if __name__ == "__main__":
    unittest.main()


class Overflowing(FakeBackend):
    """前 n 次请求都回"输入太长"的 400，之后照脚本走 """

    def __init__(self, script, fail=1, message="Input token exceed the limit"):
        super().__init__(script)
        self.fail = fail
        self.message = message
        self.compacted = 0

    def run_turn(self, on_text, on_tool=None, on_think=None):
        if self.fail:
            self.fail -= 1
            exc = Exception("Error code: 400 - " + self.message)
            exc.status_code = 400
            raise exc
        return super().run_turn(on_text, on_tool)

    def compact_to(self, summary):
        self.compacted += 1
        super().compact_to(summary)


class TestOverflow(Base):
    """接口说输入太长之后不能让会话废掉：先瘦身重发，再摘要重发 """

    def setUp(self):
        super().setUp()
        self.tools_ok()

    def test_有大块工具结果时先瘦身重发(self):
        backend = self.use([("好了", [])])
        big = Overflowing([("好了", [])], fail=1)
        big.messages = backend.messages
        # 历史里塞一条巨大的工具结果，模拟上一轮读了个大文件
        big.messages += [
            {"role": "user", "content": "读"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "r", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "r", "content": "X" * 20000},
        ]
        self.agent.backend = big
        out = quiet(self.agent.send, "接着")
        self.assertEqual(out, "好了")
        body = [m for m in big.messages if m.get("role") == "tool"][0]["content"]
        self.assertLess(len(body), 3000)
        self.assertEqual(big.compacted, 0)
        self.assertEqual(unpaired(big.messages), set())

    def test_没得瘦身就摘要重发(self):
        big = Overflowing([("好了", [])], fail=1)
        self.agent.backend = big
        out = quiet(self.agent.send, "接着")
        self.assertEqual(out, "好了")
        self.assertEqual(big.compacted, 1)
        roles = [m["role"] for m in big.messages]
        # 摘要之后要补一条 user，角色才交替得上
        self.assertEqual(roles[:4], ["system", "user", "assistant", "user"])

    def test_救两次还不行就放弃并保住历史(self):
        big = Overflowing([("好了", [])], fail=3)
        self.agent.backend = big
        with self.assertRaises(Exception):
            quiet(self.agent.send, "接着")
        self.assertEqual(big.messages[-1]["role"], "assistant")

    def test_别家措辞也认(self):
        for text in ("This model's maximum context length is 128000 tokens",
                     "prompt is too long: 213462 tokens > 200000 maximum",
                     "context_length_exceeded"):
            exc = Exception(text)
            exc.status_code = 400
            self.assertTrue(agent_mod.overflow(exc), text)

    def test_普通_400_不当溢出(self):
        exc = Exception("Invalid request: tool_use ids must be unique")
        exc.status_code = 400
        self.assertFalse(agent_mod.overflow(exc))
        exc = Exception("Input token exceed the limit")
        exc.status_code = 500
        self.assertFalse(agent_mod.overflow(exc))


class TestLearnLimit(Base):
    """上下文上限：配置里填的 > 撞墙学到的 > 出厂 128k 撞过一次就不该再撞 """

    def test_报错里写了上限就记下来(self):
        big = Overflowing([("好了", [])], fail=1,
                          message="This model's maximum context length is 65536 tokens")
        self.agent.backend = big
        big.tokens_context = 60000
        quiet(self.agent.send, "接着")
        self.assertEqual(self.agent.context_limit, 65536)
        self.assertEqual(self.agent.limit_source, "learned")
        self.assertEqual(keys.learned_limit("fake"), 65536)

    def test_报错没写数字就按上次成功的大小算(self):
        big = Overflowing([("好了", [])], fail=1)
        self.agent.backend = big
        big.tokens_context = 70000
        quiet(self.agent.send, "接着")
        self.assertEqual(self.agent.context_limit, 70000)

    def test_太小的数不学(self):
        big = Overflowing([("好了", [])], fail=1)
        self.agent.backend = big
        big.tokens_context = 3000        # 第一轮就 400，多半不是窗口的问题
        quiet(self.agent.send, "接着")
        self.assertEqual(self.agent.context_limit, agent_mod.DEFAULT_CONTEXT_LIMIT)
        self.assertEqual(keys.learned_limit("fake"), 0)

    def test_用户填了的不动(self):
        self.agent.limit_source = "config"
        self.agent.context_limit = 200000
        big = Overflowing([("好了", [])], fail=1,
                          message="maximum context length is 65536 tokens")
        self.agent.backend = big
        big.tokens_context = 100000
        quiet(self.agent.send, "接着")
        self.assertEqual(self.agent.context_limit, 200000)

    def test_学到的下次启动还在_配置优先(self):
        keys.save({"m": {"kind": "openai", "model": "m", "key": "k"},
                   "c": {"kind": "openai", "model": "c", "key": "k", "limit": 32000}})
        keys.learn_limit("m", 50000)
        keys.learn_limit("c", 50000)
        a = Feiyu(Path(self.tmp.name))
        a._apply_limit("m", keys.load()["m"])
        self.assertEqual((a.context_limit, a.limit_source), (50000, "learned"))
        a._apply_limit("c", keys.load()["c"])
        self.assertEqual((a.context_limit, a.limit_source), (32000, "config"))

    def test_措辞认不出的_400_上下文过半也当溢出(self):
        big = Overflowing([("好了", [])], fail=1, message="Provider returned error")
        self.agent.backend = big
        big.tokens_context = int(self.agent.context_limit * 0.7)
        out = quiet(self.agent.send, "接着")
        self.assertEqual(out, "好了")
        self.assertEqual(big.compacted, 1)

    def test_措辞认不出且上下文很小的_400_照旧报错(self):
        big = Overflowing([("好了", [])], fail=1, message="Provider returned error")
        self.agent.backend = big
        big.tokens_context = 2000
        with self.assertRaises(Exception):
            quiet(self.agent.send, "接着")

    def test_从报错里抠上限(self):
        cases = {
            "This model's maximum context length is 128000 tokens": 128000,
            "prompt is too long: 213462 tokens > 200000 maximum": 200000,
            "Input token exceed the limit": 0,
            "max_tokens 8192 is too large": 0,      # 那是输出上限，不是窗口
        }
        for text, want in cases.items():
            self.assertEqual(agent_mod.limit_in_error(Exception(text)), want, text)


class TestGitGate(Base):
    """只读 / 计划模式拒的是 commit，不是整个 git 工具 """

    def test_只读模式能看不能提交(self):
        self.tools_ok()
        self.agent.set_mode("readonly")
        backend = self.use([
            ("", [call("a", "git", action="status"), call("b", "git", action="commit", message="m")]),
            ("完", []),
        ])
        quiet(self.agent.send, "看看")
        got = self.tool_results(backend)
        self.assertEqual(got[0], "ok")
        self.assertIn("只读模式", got[1])
        self.assertEqual(unpaired(backend.messages), set())

    def test_计划模式同样(self):
        self.tools_ok()
        self.agent.set_mode("plan")
        backend = self.use([
            ("", [call("a", "git", action="diff"), call("b", "git", action="commit", message="m")]),
            ("完", []),
        ])
        quiet(self.agent.send, "看看")
        got = self.tool_results(backend)
        self.assertEqual(got[0], "ok")
        self.assertIn("计划模式", got[1])


class TestHelperModel(Base):
    """旁活（摘要、子 agent、网页提炼）走 helper 那条配置，钱也要算进来 """

    def setUp(self):
        super().setUp()
        self.use([("好", [])])
        self.side = FakeBackend([])
        self.side.tokens_total = 700
        self.side.cost = 0.7
        self.agent.helper = "small"
        self.agent._side = self.side

    def test_摘要走旁路后端_主后端的_ask_不被调(self):
        self.agent.backend.add_user_message("之前聊过的")
        self.assertEqual(quiet(self.agent.compact), "这是摘要")
        self.assertEqual(len(self.side.asked), 1)
        self.assertIn("之前聊过的", self.side.asked[0])
        self.assertEqual(self.agent.backend.asked, [])

    def test_网页提炼也走旁路(self):
        self.agent._ask_model("读这页")
        self.assertEqual(self.side.asked, ["读这页"])

    def test_旁路的用量算进总账(self):
        self.agent.backend.tokens_total = 1000
        self.agent.backend.cost = 1.0
        self.assertEqual(self.agent.tokens, 1700)
        self.assertAlmostEqual(self.agent.cost, 1.7)

    def test_子_agent_用_helper_那条配置(self):
        seen = {}
        def fake_run(prompt, *, entry, workspace, trace=None, lsp=None):
            seen["entry"] = entry
            return "结论", [], {"tokens": 1, "priced": False, "cost": 0}
        real_load, real_run = agent_mod.keys.load, agent_mod.subagent_mod.run
        agent_mod.keys.load = lambda: {"fake": {"model": "big"}, "small": {"model": "tiny"}}
        agent_mod.subagent_mod.run = fake_run
        try:
            self.agent._spawn("查一下")
        finally:
            agent_mod.keys.load = real_load
            agent_mod.subagent_mod.run = real_run
        self.assertEqual(seen["entry"]["model"], "tiny")

    def test_没设_helper_就是主后端自己(self):
        self.agent.helper = ""
        self.agent._side = None
        self.assertIs(self.agent._side_backend(), self.agent.backend)


class TestSideCharge(unittest.TestCase):
    def test_旁路请求算钱不算上下文(self):
        b = FakeBackend([])
        b.tokens_context = 5000
        b._charge(0, 800, 200, context=False)
        self.assertEqual(b.tokens_context, 5000)
        self.assertEqual(b.tokens_total, 1000)


class TestVerifyDigest(Base):
    def test_校验失败时要点在前原文在后(self):
        self.agent.verify = "python -c \"import sys; print('FAIL: test_a (m.T.test_a)'); print('AssertionError: 1 != 2'); sys.exit(1)\""
        self.agent.approve_hooks = None
        backend = self.use([
            ("", [call("w", "write_file", path="x.py", content="x = 1\n")]),
            ("完", []),
        ])
        quiet(self.agent.send, "写")
        body = self.tool_results(backend)[0]
        self.assertIn("要点：", body)
        self.assertIn("- FAIL: test_a (m.T.test_a)", body)
        self.assertIn("- AssertionError: 1 != 2", body)
        self.assertLess(body.index("要点"), body.index("完整输出"))


class TestPreviewOnce(Base):
    def test_确认前画过_diff_结果里就不再画(self):
        (Path(self.tmp.name) / "a.py").write_text("x = 1\n")
        self.agent.toolbox.files.record(Path(self.tmp.name) / "a.py")
        self.agent.mode = "default"
        self.agent.approve = lambda name, args: "yes"
        backend = self.use([
            ("", [call("e", "edit_file", path="a.py", old_str="x = 1", new_str="x = 2")]),
            ("完", []),
        ])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.agent.send("改")
        self.assertEqual(out.getvalue().count("- x = 1"), 1)
        self.assertEqual(out.getvalue().count("+ x = 2"), 1)
        self.assertIn("已修改 1 处", out.getvalue())


class TestUndoNote(Base):
    """/undo 之后下一句话要告诉模型，不然它接着按改完的状态往下推 """

    def test_下一句带说明_只带一次(self):
        (Path(self.tmp.name) / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.agent.toolbox.files.record(Path(self.tmp.name) / "a.py")
        self.agent.toolbox.run("write_file", {"path": "a.py", "content": "x = 2\n"})
        self.assertIn("a.py", self.agent.undo())
        b = self.use([("好", [])])
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.send("继续")
        self.assertIn("/undo", b.messages[1]["content"])
        b = self.use([("好", [])])
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.send("再来")
        self.assertNotIn("/undo", b.messages[1]["content"])

    def test_没得退(self):
        self.assertIsNone(self.agent.undo())


class TestImageToolGate(Base):
    """没配出图接口就不挂 image_generate 工具块在请求前缀，每轮都要发 """

    def names(self):
        return [t["name"] for t in self.agent.tool_schemas()]

    def test_没配就不挂(self):
        self.assertNotIn("image_generate", self.names())

    def test_配了才挂(self):
        with open(tools_mod.IMAGE_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"base_url": "https://relay.example/v1", "key": "sk-x"}, f)
        self.assertIn("image_generate", self.names())

    def test_只填一半不算(self):
        with open(tools_mod.IMAGE_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"base_url": "https://relay.example/v1"}, f)
        self.assertNotIn("image_generate", self.names())


class TestToggles(Base):
    """面板上的开关：关掉的工具不挂进请求，关掉的技能不进系统提示 """

    def setUp(self):
        super().setUp()
        self.use(["好"])

    def names(self):
        return [t["name"] for t in self.agent.tool_schemas()]

    def test_关掉的工具不挂(self):
        self.assertIn("web_fetch", self.names())
        enabled.set_on("tools", "web_fetch", False)
        self.assertNotIn("web_fetch", self.names())
        self.assertIn("bash", self.names())

    def test_exit_plan_关不掉(self):
        enabled.set_on("tools", "exit_plan", False)
        self.assertIn("exit_plan", self.names())

    def test_按了开关之后活着的实例要跟上(self):
        """写盘不算数 —— 得当场把工具表重挂给后端，不然按了没反应 """
        self.agent.apply_toggles()
        hung = lambda: [t["function"]["name"] for t in self.agent.backend.tools]
        self.assertIn("web_search", hung())
        enabled.set_on("tools", "web_search", False)
        self.agent.apply_toggles()
        self.assertNotIn("web_search", hung())

    def test_关掉的技能不进系统提示_也读不出来(self):
        from feiyu.skills import Skill
        self.agent.toolbox.skills = {
            "甲": Skill("甲", "甲的用途", "甲的正文", "全局", "/x/甲.md"),
            "乙": Skill("乙", "乙的用途", "乙的正文", "全局", "/x/乙.md"),
        }
        self.agent._refresh_system()
        self.assertIn("甲的用途", self.agent.backend.system)
        self.assertIn("乙的用途", self.agent.backend.system)

        enabled.set_on("skills", "甲", False)
        self.agent._refresh_system()
        self.assertNotIn("甲的用途", self.agent.backend.system)
        self.assertIn("乙的用途", self.agent.backend.system)

        # 名字对得上但被关了，报错要说清是"关掉了"而不是"没有这份"——
        # 只说没有的话它会换个名字接着猜
        out = self.agent.toolbox.run("skill", {"name": "甲"})
        self.assertTrue(out.is_error)
        self.assertIn("关掉", out.content)
        self.assertNotIn("甲的正文", out.content)
        self.assertFalse(self.agent.toolbox.run("skill", {"name": "乙"}).is_error)

    def test_一份都没开的时候当作没有技能(self):
        from feiyu.skills import Skill
        self.agent.toolbox.skills = {"甲": Skill("甲", "d", "b", "全局", "/x")}
        enabled.set_on("skills", "甲", False)
        out = self.agent.toolbox.run("skill", {"name": "甲"})
        self.assertTrue(out.is_error)


class TestThinkingWired(Base):
    """`on_think` 得真的从后端接到 Stream 上 —— 中间断一处就白做 """

    def test_后端报的想字数传到了_stream(self):
        b = self.use([("好", [])])
        b.thinks = [120, 900, 4000]
        seen = []
        import feiyu.ui as ui_mod
        real = ui_mod.Stream.thinking
        ui_mod.Stream.thinking = lambda self, n: seen.append(n)
        self.addCleanup(setattr, ui_mod.Stream, "thinking", real)
        quiet(self.agent.send, "想想")
        self.assertEqual(seen, [120, 900, 4000])


class TestResetWithoutBackend(Base):
    """一条模型配置都没加时 `backend` 是 None，而 `/clear` / `/bot` 照样该能用 """

    def test_没有后端也清得动(self):
        self.agent.backend = None
        self.agent.reset()                  # 不能抛
        self.assertFalse(self.agent.ready)


class TestCompactEmpty(Base):
    """**没东西可压就别发请求。** 空历史照样调一次模型是双份的坏：花了钱，
    还拿"关于空无一物的摘要"把历史换掉了 —— 之后每一轮都带着那句废话。"""

    def test_空历史不发请求(self):
        asked = []
        backend = self.use([])
        backend.ask = lambda *a, **k: (asked.append(a), "摘要")[1]
        self.assertEqual(self.agent.compact(), "")
        self.assertEqual(asked, [])

    def test_有内容才发(self):
        asked = []
        backend = self.use([])
        backend.ask = lambda *a, **k: (asked.append(a), "摘要")[1]
        backend.messages.append({"role": "user", "content": "帮我改个文件"})
        self.assertEqual(self.agent.compact(), "摘要")
        self.assertTrue(asked)


class TestModeTables(unittest.TestCase):
    def test_MODES_和_MODE_ORDER_要对齐(self):
        """cycle_mode 走的是 MODE_ORDER.index(self.mode)，而 restore / start_bot
        校验的是 MODES —— 两张表不一致的话，shift+tab 会在某个模式上抛 ValueError """
        self.assertEqual(set(agent_mod.MODES), set(agent_mod.MODE_ORDER))


class TestMentions(Base):
    """`@路径` 在 send() 这一层就展开 —— 这是它省下一整轮往返的地方"""

    def test_内容跟着这句话一起发出去(self):
        (Path(self.tmp.name) / "note.txt").write_text("秘密\n", encoding="utf-8")
        b = self.use([("好", [])])
        quiet(self.agent.send, "看看 @note.txt")
        said = [m["content"] for m in b.messages if m["role"] == "user"][0]
        self.assertIn("秘密", said)
        self.assertIn("note.txt", said)

    def test_用户打的那句话本身不变(self):
        """展开出来的东西挂在这句话后面，`task` 记的仍是用户说的话
        （trace 和 /cost 按它分组，掺进几千行文件就没法看了）"""
        (Path(self.tmp.name) / "note.txt").write_text("x\n", encoding="utf-8")
        self.use([("好", [])])
        quiet(self.agent.send, "看看 @note.txt")
        self.assertEqual(self.agent.task, "看看 @note.txt")

    def test_附过的文件算它读过了(self):
        """不记进 Watcher 的话，它下一步 edit_file 会被「你还没读过」挡回去 ——
        而用户明明已经把内容摆在它面前了"""
        target = Path(self.tmp.name) / "note.txt"
        target.write_text("老内容\n", encoding="utf-8")
        self.use([("好", [])])
        quiet(self.agent.send, "把 @note.txt 里的老改成新")
        got = self.agent.toolbox.run("edit_file", {
            "path": "note.txt", "old_str": "老内容", "new_str": "新内容"})
        self.assertFalse(got.is_error, got.content)

    def test_没有_at_的一句话一个字都不多(self):
        b = self.use([("好", [])])
        quiet(self.agent.send, "随便说点什么")
        said = [m["content"] for m in b.messages if m["role"] == "user"][0]
        self.assertEqual(said, "随便说点什么")


class TestReproduceFirst(Base):
    """动手改第一个文件之前，先跑一遍 `verify:` 看现在什么是坏的

    治的是一个实测到的失败：SWE-bench 的 `django__django-13212` 里，
    5 条该转绿的测试全在 `forms_tests` 那个包，而模型 52 轮只盯着题面提到的
    `core/validators.py` —— 方向对、改了一半，**从头到尾没看见那几个测试名**。
    那几个名字就在一条命令之外。
    """

    def use_verify(self, script, command="跑一下"):
        self.agent.verify = command
        return self.use(script)

    def fake_run(self, ok, output):
        import feiyu.hooks as hooks_mod
        calls = []
        def run(hook, payload, cwd):
            calls.append((hook.command, payload.get("event")))
            return hooks_mod.Outcome(hook=hook, code=0 if ok else 1, output=output)
        self.addCleanup(setattr, hooks_mod, "run", hooks_mod.run)
        hooks_mod.run = run
        return calls

    def test_第一次写文件之前会跑一遍(self):
        calls = self.fake_run(False, "FAIL: test_x (forms_tests.tests.test_validators.T)")
        b = self.use_verify([("", [call("c1", "write_file", path="a.py", content="x")]),
                             ("好了", [])])
        quiet(self.agent.send, "修一下")
        self.assertTrue(any(e == "PreWrite" for _, e in calls), "动手前没跑")
        got = [m["content"] for m in b.messages if m["role"] == "tool"][0]
        self.assertIn("你动手之前", got)
        self.assertIn("forms_tests", got, "失败的测试名没进上下文")

    def test_全绿也要说一声(self):
        """全绿意味着"没有现成的失败可以照着修"，那多半是加功能不是修 bug ——
        不说的话模型会去找一个不存在的红灯 """
        self.fake_run(True, "")
        b = self.use_verify([("", [call("c1", "write_file", path="a.py", content="x")]),
                             ("好了", [])])
        quiet(self.agent.send, "加个功能")
        got = [m["content"] for m in b.messages if m["role"] == "tool"][0]
        self.assertIn("全绿", got)

    def test_一个会话只跑一次(self):
        calls = self.fake_run(False, "FAIL: test_x")
        self.use_verify([("", [call("c1", "write_file", path="a.py", content="x")]),
                         ("", [call("c2", "write_file", path="b.py", content="y")]),
                         ("好了", [])])
        quiet(self.agent.send, "改两个文件")
        pre = [c for c in calls if c[1] == "PreWrite"]
        self.assertEqual(len(pre), 1, "跑了 %d 次，应该只跑一次" % len(pre))

    def test_现场照片只挂在第一次写的结果上(self):
        self.fake_run(False, "FAIL: test_x")
        b = self.use_verify([("", [call("c1", "write_file", path="a.py", content="x")]),
                             ("", [call("c2", "write_file", path="b.py", content="y")]),
                             ("好了", [])])
        quiet(self.agent.send, "改两个文件")
        tools = [m["content"] for m in b.messages if m["role"] == "tool"]
        self.assertIn("你动手之前", tools[0])
        self.assertNotIn("你动手之前", tools[1], "第二次写又挂了一遍")

    def test_没配_verify_就什么都不做(self):
        calls = self.fake_run(False, "x")
        self.agent.verify = ""
        self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好了", [])])
        quiet(self.agent.send, "改一下")
        self.assertEqual([c for c in calls if c[1] == "PreWrite"], [])

    def test_只读的调用不触发(self):
        """读文件、搜索不该把测试跑起来 —— 那是每轮都要等一次 """
        calls = self.fake_run(False, "x")
        self.use_verify([("", [call("c1", "read_file", path="a.py")]), ("好了", [])])
        quiet(self.agent.send, "看一眼")
        self.assertEqual([c for c in calls if c[1] == "PreWrite"], [])

    def test_跑不起来也不该挡着干活(self):
        """**只让动手前那一次抛。** 写成"所有 hook 都抛"是测过头了：
        写文件成功之后 `_fire` 还要跑 PostToolUse，而那条路径没有 try
        （`hooks.run` 本身设计成不抛，内部把 OSError 都接住了）。
        这条要验的是"动手前那次跑挂了，不该把这一轮带走"。
        """
        import feiyu.hooks as hooks_mod
        real = hooks_mod.run
        def maybe_boom(hook, payload, cwd):
            if payload.get("event") == "PreWrite":
                raise RuntimeError("命令不存在")
            return real(hook, payload, cwd)
        self.addCleanup(setattr, hooks_mod, "run", real)
        hooks_mod.run = maybe_boom
        b = self.use_verify([("", [call("c1", "write_file", path="a.py", content="x")]),
                             ("好了", [])])
        self.assertEqual(quiet(self.agent.send, "改一下"), "好了")
        self.assertEqual(unpaired(b.messages), set())
