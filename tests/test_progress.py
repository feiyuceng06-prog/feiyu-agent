"""跨会话的任务进度 跟 memory 的规矩正好相反，用例盯的就是这些区别：
整份替换、会过期、做完就删、措辞里必须写明"可能已经过时"
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from feiyu import progress


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 存储路径是 import 时算好的，不换掉就会写用户真正的状态目录
        self.saved_dir = progress.DIR
        progress.DIR = os.path.join(self.tmp.name, "progress")
        self.root = Path(self.tmp.name) / "work"

    def tearDown(self):
        progress.DIR = self.saved_dir
        self.tmp.cleanup()

    def items(self, *pairs):
        return [{"text": t, "status": s} for t, s in pairs]


class TestSave(Base):
    def test_存了能读回来(self):
        progress.save(self.root, "抽成单一真源", self.items(("第一步", "done"),
                                                            ("第二步", "doing")))
        data = progress.load(self.root)
        self.assertEqual(data["task"], "抽成单一真源")
        self.assertEqual(len(data["items"]), 2)

    def test_全做完就把旧的删掉(self):
        progress.save(self.root, "活儿", self.items(("第一步", "doing")))
        self.assertTrue(progress.load(self.root))
        progress.save(self.root, "活儿", self.items(("第一步", "done")))
        self.assertEqual(progress.load(self.root), {})

    def test_没列清单也把旧的删掉(self):
        progress.save(self.root, "活儿", self.items(("第一步", "doing")))
        progress.save(self.root, "别的活儿", [])
        self.assertEqual(progress.load(self.root), {})

    def test_整份替换不追加(self):
        progress.save(self.root, "甲", self.items(("甲的第一步", "doing")))
        progress.save(self.root, "乙", self.items(("乙的第一步", "doing")))
        data = progress.load(self.root)
        self.assertEqual(data["task"], "乙")
        self.assertEqual(len(data["items"]), 1)

    def test_不同目录互不影响(self):
        other = Path(self.tmp.name) / "other"
        progress.save(self.root, "甲", self.items(("x", "doing")))
        self.assertEqual(progress.load(other), {})

    def test_权限是_600(self):
        progress.save(self.root, "活儿", self.items(("x", "doing")))
        mode = os.stat(progress.path_for(self.root)).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_坏文件不崩(self):
        os.makedirs(progress.DIR, exist_ok=True)
        Path(progress.path_for(self.root)).write_text("不是 json", encoding="utf-8")
        self.assertEqual(progress.load(self.root), {})


class TestPrompt(Base):
    def fresh_data(self, *pairs, task="活儿"):
        progress.save(self.root, task, self.items(*pairs))
        return progress.load(self.root)

    def test_没做完的会进提示_并且写明可能过时(self):
        text = progress.as_prompt(self.fresh_data(("第一步", "done"), ("第二步", "pending")))
        self.assertIn("第二步", text)
        self.assertIn("可能已经过时", text)
        self.assertIn("不是用户此刻的指令", text)

    def test_太旧的不带(self):
        """隔了两周的半截任务说出来只会让它照着一份错的现状动手 """
        data = self.fresh_data(("第一步", "doing"))
        data["stamp"] = time.time() - (progress.STALE_DAYS + 1) * 86400
        self.assertEqual(progress.as_prompt(data), "")

    def test_刚过一天的还带(self):
        data = self.fresh_data(("第一步", "doing"))
        data["stamp"] = time.time() - 86400
        self.assertIn("第一步", progress.as_prompt(data))

    def test_空的返回空串(self):
        self.assertEqual(progress.as_prompt({}), "")
        self.assertEqual(progress.as_prompt({"items": [], "stamp": time.time()}), "")

    def test_没有时间戳当成过期(self):
        self.assertEqual(progress.as_prompt({"items": [{"text": "x"}]}), "")


class TestInAgent(Base):
    def test_自己列了新清单就不再带上次那份(self):
        """新清单已经在对话里了，再把旧的塞进系统提示只会打架 """
        import contextlib, io
        from feiyu.agent import Feiyu
        from tests.fakes import FakeBackend

        self.root.mkdir(parents=True)
        progress.save(self.root, "上次的活儿", self.items(("上次的第一步", "doing")))
        agent = Feiyu(self.root)
        agent.backend = FakeBackend([])
        agent.name = "fake"

        with contextlib.redirect_stdout(io.StringIO()):
            agent._refresh_system()
        self.assertIn("上次的第一步", agent.backend.system)

        agent.toolbox.todos.replace([{"text": "这次的第一步", "status": "doing"}])
        with contextlib.redirect_stdout(io.StringIO()):
            agent._refresh_system()
        self.assertNotIn("上次的第一步", agent.backend.system)


if __name__ == "__main__":
    unittest.main()
