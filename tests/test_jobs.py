"""后台 bash 真的起进程，但都是几十毫秒就完的短命令 """

import tempfile
import time
import unittest
from pathlib import Path

from feiyu import jobs as jobs_mod
from feiyu.tools import Toolbox


def wait(check, limit=5.0):
    """轮询到条件成立 后台命令是真进程，不能假设它立刻跑完 """
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = Toolbox(Path(self.tmp.name).resolve())

    def tearDown(self):
        self.box.jobs.kill_all()
        self.tmp.cleanup()

    def start(self, command):
        out = self.box.run("bash", {"command": command, "background": True})
        self.assertFalse(out.is_error, out.content)
        return self.box.jobs.all()[-1]

    def job(self, action, **kw):
        return self.box.run("job", dict(action=action, **kw))


class TestStart(Base):
    def test_立刻返回不等它跑完(self):
        began = time.monotonic()
        out = self.box.run("bash", {"command": "sleep 3", "background": True})
        self.assertLess(time.monotonic() - began, 1.5)
        self.assertIn("#1", out.summary)

    def test_编号从一开始往上递增(self):
        self.assertEqual([self.start("true").id, self.start("true").id], [1, 2])

    def test_在工作区里跑(self):
        job = self.start("pwd > 记一下.txt")
        self.assertTrue(wait(lambda: (self.box.root / "记一下.txt").exists()))
        self.assertTrue(wait(lambda: not job.running))

    def test_后台命令不改_cwd(self):
        # cd 是跨调用保持的，但后台命令跟后面的命令并行，跟着它走只会串味
        (self.box.root / "子目录").mkdir()
        self.start("cd 子目录")
        self.assertEqual(self.box.cwd, self.box.root)


class TestOutput(Base):
    def test_无换行输出在进程运行时可读(self):
        job = self.start("printf ready; sleep 3")
        pieces = []
        def received():
            pieces.append(job.take()[0])
            return "ready" in "".join(pieces)
        self.assertTrue(wait(received, limit=2))
        self.assertTrue(job.running)

    def test_超长单行也受限并保留末尾(self):
        self.addCleanup(setattr, jobs_mod, "MAX_CHARS", jobs_mod.MAX_CHARS)
        jobs_mod.MAX_CHARS = 200
        job = self.start("head -c 100000 /dev/zero | tr '\\0' x; printf END")
        self.assertTrue(wait(lambda: not job.running))
        self.assertTrue(wait(lambda: job.proc.stdout.closed))
        text, dropped = job.take()
        self.assertTrue(dropped)
        self.assertEqual(len(text), 200)
        self.assertTrue(text.endswith("END"))

    def test_读过的不再重复给(self):
        job = self.start("echo 第一段")
        self.assertTrue(wait(lambda: not job.running))
        first = self.job("output", id=job.id)
        self.assertIn("第一段", first.content)
        again = self.job("output", id=job.id)
        self.assertNotIn("第一段", again.content)

    def test_结束了要报退出码(self):
        job = self.start("exit 3")
        self.assertTrue(wait(lambda: not job.running))
        self.assertIn("退出码 3", self.job("output", id=job.id).content)

    def test_还在跑时说运行中(self):
        job = self.start("sleep 3")
        self.assertIn("运行中", self.job("output", id=job.id).content)

    def test_stderr_也收(self):
        job = self.start("echo 出错了 >&2")
        self.assertTrue(wait(lambda: not job.running))
        self.assertIn("出错了", self.job("output", id=job.id).content)

    def test_没有这个编号(self):
        self.assertTrue(self.job("output", id=99).is_error)

    def test_输出超上限从最早的丢(self):
        # 跑飞的命令不该把内存吃光，末尾那段才是有用的
        self.addCleanup(setattr, jobs_mod, "MAX_CHARS", jobs_mod.MAX_CHARS)
        jobs_mod.MAX_CHARS = 200
        job = self.start("seq 1 500")
        self.assertTrue(wait(lambda: not job.running))
        time.sleep(0.2)
        text, dropped = job.take()
        self.assertTrue(dropped)
        self.assertLessEqual(len(text), 400)
        self.assertIn("500", text)          # 留下的是末尾
        self.assertNotIn("\n1\n", text)


class TestKill(Base):
    def test_停得掉(self):
        job = self.start("sleep 30")
        self.assertTrue(job.running)
        self.job("kill", id=job.id)
        self.assertFalse(job.running)

    def test_连它拉起来的子进程一起杀(self):
        # 只杀 pid 的话 `npm run dev` 底下那个 node 会活下来 —— 所以要杀进程组
        marker = self.box.root / "还活着"
        job = self.start("sh -c 'sleep 30 && touch %s' & sleep 30" % marker)
        time.sleep(0.3)
        self.job("kill", id=job.id)
        time.sleep(0.5)
        self.assertFalse(marker.exists())

    def test_停已经结束的不算错(self):
        job = self.start("true")
        self.assertTrue(wait(lambda: not job.running))
        self.assertFalse(self.job("kill", id=job.id).is_error)

    def test_kill_all_全停(self):
        for _ in range(3):
            self.start("sleep 30")
        self.box.jobs.kill_all()
        self.assertEqual(self.box.jobs.running(), [])


class TestList(Base):
    def test_空的时候也有话说(self):
        self.assertIn("没有", self.job("list").content)

    def test_列出编号命令和状态(self):
        self.start("sleep 30")
        out = self.job("list")
        self.assertIn("#1", out.content)
        self.assertIn("sleep 30", out.content)
        self.assertIn("运行中", out.content)


class TestFinishedNote(unittest.TestCase):
    """结束了要主动说一声，而且只说一次 """

    def setUp(self):
        from feiyu.agent import Feiyu
        from tests.fakes import FakeBackend, call
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.name, self.agent.mode = "fake", "auto"
        self.backend = FakeBackend([("", [call("c1", "bash", command="true")]), ("好", [])])
        self.agent.backend = self.backend
        self.call = call

    def tearDown(self):
        self.agent.toolbox.jobs.kill_all()
        self.tmp.cleanup()

    def results(self):
        return [m["content"] for m in self.backend.messages if m["role"] == "tool"]

    def test_结束了搭在工具结果上告诉模型(self):
        import contextlib
        import io
        job = self.agent.toolbox.run("bash", {"command": "exit 7", "background": True})
        self.assertIn("#1", job.summary)
        self.assertTrue(wait(lambda: not self.agent.toolbox.jobs.get(1).running))
        with contextlib.redirect_stdout(io.StringIO()):
            self.agent.send("接着做别的")
        self.assertIn("后台命令已经结束", self.results()[-1])
        self.assertIn("退出码 7", self.results()[-1])

    def test_只说一次(self):
        self.agent.toolbox.run("bash", {"command": "true", "background": True})
        self.assertTrue(wait(lambda: not self.agent.toolbox.jobs.get(1).running))
        pool = self.agent.toolbox.jobs
        self.assertEqual(len(pool.finished_since()), 1)
        self.assertEqual(pool.finished_since(), [])

    def test_还在跑的不报(self):
        self.agent.toolbox.run("bash", {"command": "sleep 30", "background": True})
        self.assertEqual(self.agent.toolbox.jobs.finished_since(), [])


class TestRegistered(unittest.TestCase):
    def setUp(self):
        from feiyu import permissions
        # STORE 是 import 时算好的，不换掉就会读写用户真正的规则
        self.tmp = tempfile.TemporaryDirectory()
        self._store = permissions.STORE
        permissions.STORE = Path(self.tmp.name) / "permissions.json"

    def tearDown(self):
        from feiyu import permissions
        permissions.STORE = self._store
        self.tmp.cleanup()

    def test_权限规则和显示名都登记了(self):
        from feiyu import permissions, ui
        self.assertEqual(permissions.RULE_NAME["job"], "Job")
        self.assertIn("Job(*)", permissions.DEFAULT_ALLOW)
        self.assertEqual(ui.tool_label("job"), "Job")

    def test_出厂就放行(self):
        from feiyu import permissions
        self.assertEqual(permissions.verdict("job", {"action": "output", "id": 1}), "allow")
