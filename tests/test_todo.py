"""任务清单与计划模式

清单不是为了好看：**收尾时还没做完的会被点出来**，治的是「五件事做完三件
就宣布干完了」这个毛病 计划模式治的是另一头：还没想清楚就开始改文件
"""

import tempfile
import unittest
from pathlib import Path

from feiyu import agent as agent_mod
from feiyu import todo
from feiyu.agent import Feiyu
from feiyu.todo import Todos
from feiyu.tools import Toolbox, ToolResult
from tests.fakes import FakeBackend, call, unpaired
from tests.test_agent import quiet


class TestTodos(unittest.TestCase):
    def setUp(self):
        self.t = Todos()

    def test_整份替换(self):
        self.t.replace([{"text": "一", "status": "done"}])
        self.t.replace([{"text": "二", "status": "pending"}])
        self.assertEqual([i["text"] for i in self.t.items], ["二"])

    def test_纯字符串当作没开始(self):
        self.t.replace(["随手写的一条"])
        self.assertEqual(self.t.items[0]["status"], todo.PENDING)

    def test_统计做完几条(self):
        ok, note = self.t.replace([{"text": "a", "status": "done"},
                                   {"text": "b", "status": "pending"}])
        self.assertTrue(ok)
        self.assertEqual(note, "1 / 2 做完")

    def test_没做完的挑出来(self):
        self.t.replace([{"text": "a", "status": "done"},
                        {"text": "b", "status": "doing"},
                        {"text": "c", "status": "pending"}])
        self.assertEqual([i["text"] for i in self.t.unfinished()], ["b", "c"])

    def test_全做完就没有未完成(self):
        self.t.replace([{"text": "a", "status": "done"}])
        self.assertEqual(self.t.unfinished(), [])

    def test_只能有一条_doing(self):
        ok, note = self.t.replace([{"text": "a", "status": "doing"},
                                   {"text": "b", "status": "doing"}])
        self.assertFalse(ok)
        self.assertIn("一条 doing", note)

    def test_status_写错要报出来(self):
        ok, note = self.t.replace([{"text": "a", "status": "进行中"}])
        self.assertFalse(ok)
        self.assertIn("pending", note)

    def test_没有_text_要报出来(self):
        self.assertFalse(self.t.replace([{"status": "done"}])[0])
        self.assertFalse(self.t.replace([{"text": "   "}])[0])

    def test_不是数组要报出来(self):
        self.assertFalse(self.t.replace("一条")[0])
        self.assertFalse(self.t.replace(None)[0])

    def test_条数上限(self):
        ok, note = self.t.replace([{"text": str(i)} for i in range(todo.MAX_ITEMS + 1)])
        self.assertFalse(ok)
        self.assertIn("拆得太碎", note)

    def test_单条太长会截断(self):
        self.t.replace([{"text": "长" * 500}])
        self.assertEqual(len(self.t.items[0]["text"]), todo.MAX_TEXT)

    def test_失败时不动原来的清单(self):
        self.t.replace([{"text": "本来的", "status": "done"}])
        self.t.replace([{"text": "坏的", "status": "乱写"}])
        self.assertEqual([i["text"] for i in self.t.items], ["本来的"])

    def test_清空(self):
        self.t.replace([{"text": "a"}])
        ok, note = self.t.replace([])
        self.assertTrue(ok)
        self.assertEqual(note, "清单清空了")

    def test_恢复坏数据不崩(self):
        self.t.restore("这不是清单")
        self.t.restore(None)
        self.assertEqual(self.t.items, [])

    def test_恢复好数据(self):
        self.t.restore([{"text": "接着做", "status": "doing"}])
        self.assertEqual(self.t.items[0]["text"], "接着做")


class TestTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = Toolbox(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_收下清单(self):
        out = self.box.run("todo", {"items": [{"text": "读代码", "status": "done"}]})
        self.assertFalse(out.is_error)
        self.assertEqual(len(self.box.todos.items), 1)

    def test_出错回给模型(self):
        out = self.box.run("todo", {"items": "不是数组"})
        self.assertTrue(out.is_error)

    def test_带出可以画的行(self):
        out = self.box.run("todo", {"items": [{"text": "甲", "status": "done"},
                                              {"text": "乙", "status": "doing"}]})
        kinds = [kind for kind, _ in out.detail]
        self.assertEqual(kinds, ["done", "doing"])
        self.assertIn("甲", out.detail[0][1])

    def test_不在计划模式时交方案要报错(self):
        self.assertTrue(self.box.run("exit_plan", {"plan": "随便"}).is_error)


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name = "fake"
        self.agent.mode = "auto"
        self.ran = []

    def tearDown(self):
        self.tmp.cleanup()

    def use(self, script):
        self.agent.backend = FakeBackend(script)
        return self.agent.backend

    def tools_ok(self):
        real = self.agent.toolbox.run
        def run(name, args):
            self.ran.append(name)
            if name in ("todo", "exit_plan"):
                return real(name, args)
            return ToolResult("ok", summary="ok")
        self.agent.toolbox.run = run


class TestNudge(Loop):
    def test_还有没做完的会被叫回去(self):
        b = self.use([("", [call("c1", "todo", items=[{"text": "甲", "status": "done"},
                                                      {"text": "乙", "status": "pending"}])]),
                      ("干完了", []), ("这回真干完了", [])])
        self.tools_ok()
        quiet(self.agent.send, "做两件事")
        # 第一次说"干完了"时清单还没做完，会被塞一条提醒再问一轮
        self.assertIn("还有没做完", b.messages[-2]["content"] + b.messages[-3]["content"])

    def test_只提醒一次(self):
        # 提醒到底会转不出来
        b = self.use([("", [call("c1", "todo", items=[{"text": "乙", "status": "pending"}])]),
                      ("干完了", []), ("真的干完了", []), ("别问了", [])])
        self.tools_ok()
        out = quiet(self.agent.send, "做点事")
        self.assertEqual(out, "真的干完了")

    def test_全做完就不打扰(self):
        b = self.use([("", [call("c1", "todo", items=[{"text": "甲", "status": "done"}])]),
                      ("干完了", [])])
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "做一件事"), "干完了")

    def test_没列清单也不打扰(self):
        self.use([("直接答完", [])])
        self.tools_ok()
        self.assertEqual(quiet(self.agent.send, "一句话的问题"), "直接答完")

    def test_提醒之后历史仍然配对(self):
        b = self.use([("", [call("c1", "todo", items=[{"text": "乙", "status": "pending"}])]),
                      ("干完了", []), ("真完了", [])])
        self.tools_ok()
        quiet(self.agent.send, "做点事")
        self.assertEqual(unpaired(b.messages), set())


class TestPlanMode(Loop):
    def test_计划模式拒绝写(self):
        self.agent.set_mode("plan")
        for name, args in (("write_file", {"path": "a", "content": "x"}),
                           ("bash", {"command": "rm x"}),
                           ("edit_file", {"path": "a", "old_str": "x", "new_str": "y"})):
            ok, _, label, reason = self.agent._gate(call("c", name, **args), False)
            self.assertFalse(ok, name)
            self.assertIn("计划模式", label)
            self.assertIn("exit_plan", reason)

    def test_计划模式放行读(self):
        self.agent.set_mode("plan")
        for name, args in (("read_file", {"path": "a"}), ("search", {"regex": "x"}),
                           ("list_files", {"pattern": "*"})):
            self.assertTrue(self.agent._gate(call("c", name, **args), False)[0], name)

    def test_切到计划模式会改系统提示(self):
        b = self.use([])
        self.agent.set_mode("plan")
        self.assertIn("计划模式", b.system)
        self.agent.set_mode("default")
        self.assertNotIn("计划模式", b.system)

    def test_同意方案就切回默认(self):
        self.use([("", [call("c1", "exit_plan", plan="先改 a 再改 b")]), ("那我开始", [])])
        self.tools_ok()
        self.agent.set_mode("plan")
        self.agent.approve_plan = lambda plan: True
        quiet(self.agent.send, "想个方案")
        self.assertEqual(self.agent.mode, "default")

    def test_不同意就留在计划模式(self):
        b = self.use([("", [call("c1", "exit_plan", plan="随便搞搞")]), ("那我再想想", [])])
        self.tools_ok()
        self.agent.set_mode("plan")
        self.agent.approve_plan = lambda plan: False
        quiet(self.agent.send, "想个方案")
        self.assertEqual(self.agent.mode, "plan")
        results = [m["content"] for m in b.messages if m["role"] == "tool"]
        self.assertIn("别绕过去", results[0])

    def test_没有界面就当不同意(self):
        self.agent.set_mode("plan")
        self.agent.approve_plan = None
        agreed, _ = quiet(self.agent._exit_plan, "方案")
        self.assertFalse(agreed)
        self.assertEqual(self.agent.mode, "plan")

    def test_模式在四档里循环(self):
        self.assertEqual(agent_mod.MODE_ORDER,
                         ("default", "auto", "plan", "readonly"))
        self.assertIn("plan", agent_mod.MODES)


if __name__ == "__main__":
    unittest.main()
