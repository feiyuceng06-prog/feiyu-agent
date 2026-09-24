"""钩子：固定几个时机跑外部命令

两件事最要紧：**项目钩子是仓库里带下来的命令**，不经同意不能跑；
以及钩子自己炸了不能把 agent 带崩
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from feiyu import hooks
from feiyu.agent import Feiyu
from feiyu.tools import ToolResult
from tests.fakes import FakeBackend, call, unpaired
from tests.test_agent import quiet


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self._saved = (hooks.GLOBAL, hooks.TRUST, hooks._DIR)
        hooks._DIR = self.home.name
        hooks.GLOBAL = os.path.join(self.home.name, "hooks.json")
        hooks.TRUST = os.path.join(self.home.name, "trusted-hooks.json")
        self.ws = Path(self.tmp.name).resolve()

    def tearDown(self):
        hooks.GLOBAL, hooks.TRUST, hooks._DIR = self._saved
        self.tmp.cleanup()
        self.home.cleanup()

    def write_global(self, data):
        with open(hooks.GLOBAL, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def write_project(self, data):
        folder = self.ws / ".feiyu"
        folder.mkdir(exist_ok=True)
        with open(folder / "hooks.json", "w", encoding="utf-8") as f:
            json.dump(data, f)


class TestMatching(unittest.TestCase):
    def hook(self, matcher):
        return hooks.Hook("PostToolUse", {"matcher": matcher, "command": "true"}, "t")

    def test_星号匹配一切(self):
        self.assertTrue(hooks.matches(self.hook("*"), "bash", "随便"))

    def test_没有_matcher_也匹配一切(self):
        h = hooks.Hook("PostToolUse", {"command": "true"}, "t")
        self.assertTrue(hooks.matches(h, "bash", "随便"))

    def test_裸工具名当成全匹配(self):
        self.assertTrue(hooks.matches(self.hook("Bash"), "bash", "ls"))
        self.assertFalse(hooks.matches(self.hook("Bash"), "write_file", "a.py"))

    def test_竖线是或(self):
        h = self.hook("Write|Update")
        self.assertTrue(hooks.matches(h, "write_file", "a.py"))
        self.assertTrue(hooks.matches(h, "edit_file", "a.py"))
        self.assertFalse(hooks.matches(h, "bash", "ls"))

    def test_用权限规则那套前缀语法(self):
        h = self.hook("Bash(git push:*)")
        self.assertTrue(hooks.matches(h, "bash", "git push origin main"))
        self.assertFalse(hooks.matches(h, "bash", "git status"))

    def test_pick_按事件筛(self):
        a = hooks.Hook("PreToolUse", {"command": "x"}, "t")
        b = hooks.Hook("PostToolUse", {"command": "y"}, "t")
        self.assertEqual(hooks.pick([a, b], "PreToolUse", "bash", "ls"), [a])

    def test_无工具的事件不做匹配(self):
        h = hooks.Hook("Stop", {"matcher": "Bash", "command": "x"}, "t")
        self.assertEqual(hooks.pick([h], "Stop"), [h])


class TestRun(Base):
    def hook(self, command, timeout=None):
        spec = {"command": command}
        if timeout:
            spec["timeout"] = timeout
        return hooks.Hook("PostToolUse", spec, "t")

    def test_退出码_0_算通过(self):
        self.assertTrue(hooks.run(self.hook("true"), {}, self.ws).ok)

    def test_非零算拦下(self):
        self.assertFalse(hooks.run(self.hook("exit 3"), {}, self.ws).ok)

    def test_失败时_stdout_和_stderr_都要(self):
        # unittest 把失败写 stderr，pytest 写 stdout，只取一个总有一半是空的
        self.assertIn("走 stdout", hooks.run(self.hook("echo 走 stdout; exit 1"), {}, self.ws).output)
        self.assertIn("走 stderr", hooks.run(self.hook("echo 走 stderr >&2; exit 1"), {}, self.ws).output)

    def test_成功时只要_stdout(self):
        out = hooks.run(self.hook("echo 有用的; echo 噪音 >&2"), {}, self.ws).output
        self.assertIn("有用的", out)
        self.assertNotIn("噪音", out)

    def test_载荷从_stdin_进去(self):
        out = hooks.run(self.hook("cat"), {"event": "X", "tool": "bash"}, self.ws).output
        self.assertEqual(json.loads(out)["tool"], "bash")

    def test_在工作区里跑(self):
        self.assertIn(str(self.ws), hooks.run(self.hook("pwd"), {}, self.ws).output)

    def test_超时算失败不挂死(self):
        out = hooks.run(self.hook("sleep 5", timeout=1), {}, self.ws)
        self.assertFalse(out.ok)
        self.assertIn("超过", out.output)

    def test_命令不存在也不抛(self):
        self.assertFalse(hooks.run(self.hook("绝对没有这个命令 --x"), {}, self.ws).ok)

    def test_输出截断(self):
        out = hooks.run(self.hook("head -c 99999 /dev/zero | tr '\\\\0' x; exit 1"), {}, self.ws)
        self.assertLessEqual(len(out.output), hooks.MAX_OUTPUT)

    def test_密钥不传给钩子(self):
        os.environ["FEIYU_HOOKTEST_TOKEN"] = "机密"
        try:
            out = hooks.run(self.hook("echo $FEIYU_HOOKTEST_TOKEN"), {}, self.ws)
            self.assertNotIn("机密", out.output)
        finally:
            del os.environ["FEIYU_HOOKTEST_TOKEN"]


class TestTrust(Base):
    """项目 hooks.json 是仓库里带下来的，直接跑等于 clone 就执行任意代码 """

    def test_没有项目文件时只有全局(self):
        self.write_global({"Stop": [{"command": "echo 全局"}]})
        found, note = hooks.load(self.ws)
        self.assertEqual(len(found), 1)
        self.assertEqual(note, "")

    def test_项目钩子默认不跑(self):
        self.write_project({"Stop": [{"command": "echo 项目"}]})
        found, note = hooks.load(self.ws)
        self.assertEqual(found, [])
        self.assertIn("未启用", note)

    def test_不同意就不跑(self):
        self.write_project({"Stop": [{"command": "echo 项目"}]})
        found, _ = hooks.load(self.ws, approve=lambda path, n: False)
        self.assertEqual(found, [])

    def test_同意了才跑(self):
        self.write_project({"Stop": [{"command": "echo 项目"}]})
        found, note = hooks.load(self.ws, approve=lambda path, n: True)
        self.assertEqual(len(found), 1)
        self.assertIn("已信任", note)

    def test_同意过下次不再问(self):
        self.write_project({"Stop": [{"command": "echo 项目"}]})
        hooks.load(self.ws, approve=lambda path, n: True)
        asked = []
        found, _ = hooks.load(self.ws, approve=lambda path, n: asked.append(1) or True)
        self.assertEqual(asked, [])
        self.assertEqual(len(found), 1)

    def test_内容变了要重新问(self):
        # 不按内容认的话，同意过一次之后仓库里换成 rm -rf 也照跑
        self.write_project({"Stop": [{"command": "echo 老的"}]})
        hooks.load(self.ws, approve=lambda path, n: True)
        self.write_project({"Stop": [{"command": "rm -rf /"}]})
        found, note = hooks.load(self.ws)
        self.assertEqual(found, [])
        self.assertIn("未启用", note)

    def test_非交互场景不问就不跑(self):
        self.write_project({"Stop": [{"command": "echo 项目"}]})
        self.assertEqual(hooks.load(self.ws, approve=None)[0], [])

    def test_空的项目文件不打扰(self):
        self.write_project({})
        found, note = hooks.load(self.ws, approve=lambda p, n: True)
        self.assertEqual(note, "")

    def test_坏掉的配置当作没有(self):
        folder = self.ws / ".feiyu"
        folder.mkdir()
        (folder / "hooks.json").write_text("{ 坏了", encoding="utf-8")
        self.assertEqual(hooks.load(self.ws, approve=lambda p, n: True)[0], [])

    def test_没有_command_的条目忽略(self):
        self.write_global({"Stop": [{"matcher": "*"}, {"command": "true"}]})
        self.assertEqual(len(hooks.load(self.ws)[0]), 1)

    def test_不认识的事件忽略(self):
        self.write_global({"没这个事件": [{"command": "true"}]})
        self.assertEqual(hooks.load(self.ws)[0], [])

    def test_信任文件是_600(self):
        import stat
        hooks.trust(self.ws, "abc")
        self.assertEqual(stat.S_IMODE(os.stat(hooks.TRUST).st_mode), 0o600)


class TestInLoop(Base):
    def setUp(self):
        super().setUp()
        self.agent = Feiyu(self.ws)
        self.agent.name = "fake"
        self.agent.mode = "auto"
        self.ran = []

    def use(self, script):
        self.agent.backend = FakeBackend(script)
        return self.agent.backend

    def tools_ok(self):
        def run(name, args):
            self.ran.append(name)
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run

    def hook(self, event, command, matcher="*"):
        self.agent.hooks = [hooks.Hook(event, {"matcher": matcher, "command": command}, "测试")]

    def results(self, backend):
        return [m["content"] for m in backend.messages if m["role"] == "tool"]

    def test_pretooluse_能拦下工具(self):
        b = self.use([("", [call("c1", "bash", command="rm -rf /")]), ("好吧", [])])
        self.tools_ok()
        self.hook("PreToolUse", "echo 不许这么干 >&2; exit 1")
        quiet(self.agent.send, "删掉")
        self.assertEqual(self.ran, [])
        self.assertIn("不许这么干", self.results(b)[0])
        self.assertEqual(unpaired(b.messages), set())

    def test_pretooluse_通过就照常跑(self):
        self.use([("", [call("c1", "bash", command="ls")]), ("好", [])])
        self.tools_ok()
        self.hook("PreToolUse", "true")
        quiet(self.agent.send, "列一下")
        self.assertEqual(self.ran, ["bash"])

    def test_matcher_不中的不拦(self):
        self.use([("", [call("c1", "bash", command="ls")]), ("好", [])])
        self.tools_ok()
        self.hook("PreToolUse", "exit 1", matcher="Bash(git push:*)")
        quiet(self.agent.send, "列一下")
        self.assertEqual(self.ran, ["bash"])

    def test_posttooluse_失败把输出回灌(self):
        b = self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好", [])])
        self.tools_ok()
        self.hook("PostToolUse", "echo lint 挂了 >&2; exit 1", matcher="Write|Update")
        quiet(self.agent.send, "写个文件")
        self.assertIn("lint 挂了", self.results(b)[0])
        self.assertEqual(unpaired(b.messages), set())

    def test_只读工具不触发_posttooluse(self):
        b = self.use([("", [call("c1", "read_file", path="a.py")]), ("好", [])])
        self.tools_ok()
        self.hook("PostToolUse", "exit 1")
        quiet(self.agent.send, "读一下")
        self.assertNotIn("没有通过", self.results(b)[0])

    def test_userpromptsubmit_能拦下提问(self):
        b = self.use([("不该走到这", [])])
        self.hook("UserPromptSubmit", "echo 现在别问 >&2; exit 1")
        out = quiet(self.agent.send, "帮我改个东西")
        self.assertIn("现在别问", out)
        self.assertEqual([m["role"] for m in b.messages], ["system"])

    def test_userpromptsubmit_的输出当背景带上(self):
        b = self.use([("好", [])])
        self.hook("UserPromptSubmit", "echo 当前分支是 main")
        quiet(self.agent.send, "现在什么情况")
        self.assertIn("当前分支是 main", b.messages[1]["content"])

    def test_stop_在没有工具调用时跑(self):
        self.use([("答完了", [])])
        marker = self.ws / "跑过了"
        self.hook("Stop", "touch %s" % marker)
        quiet(self.agent.send, "问题")
        self.assertTrue(marker.exists())

    def test_verify_被合成成钩子(self):
        # verify 就是「改完文件跑个命令」，本来就是 PostToolUse 的特例
        self.agent.verify = "make test"
        found = [h for h in self.agent.all_hooks if h.source == "verify:"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].event, "PostToolUse")
        self.assertEqual(found[0].command, "make test")

    def test_verify_仍然记结果(self):
        self.use([("", [call("c1", "write_file", path="a.py", content="x")]), ("好", [])])
        self.tools_ok()
        self.agent.verify = "true"
        quiet(self.agent.send, "写个文件")
        self.assertTrue(self.agent.verified_ok)

    def test_没配就是没钩子(self):
        self.agent.verify = ""
        self.assertEqual(self.agent.all_hooks, [])


if __name__ == "__main__":
    unittest.main()
