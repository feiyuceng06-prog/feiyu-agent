"""bash 沙箱

在此之前 `_resolve` 只管得住三个文件工具，bash 想读写哪儿就读写哪儿——
权限规则和 DENY_PATHS 只降低概率，不是隔离

**写是真隔离**（内核的 mount namespace），**读只是挖空了几个目录**
这两条要分开测，也要分开说
"""

import os
import tempfile
import unittest
from pathlib import Path

from feiyu import sandbox
from feiyu.tools import Toolbox

需要沙箱 = unittest.skipUnless(sandbox.available(), "这台机器上 bwrap 跑不起来")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (sandbox.STORE, sandbox._DIR)
        sandbox._DIR = self.tmp.name
        sandbox.STORE = os.path.join(self.tmp.name, "sandbox.json")
        self._env = os.environ.pop("FEIYU_SANDBOX", None)

    def tearDown(self):
        sandbox.STORE, sandbox._DIR = self._saved
        if self._env is not None:
            os.environ["FEIYU_SANDBOX"] = self._env
        else:
            os.environ.pop("FEIYU_SANDBOX", None)
        self.tmp.cleanup()


class TestLevel(Base):
    def test_没配过就是默认(self):
        want = sandbox.DEFAULT if sandbox.available() else "off"
        self.assertEqual(sandbox.level(), want)

    def test_存了能读回来(self):
        sandbox.set_level("strict")
        self.assertEqual(sandbox.level(), "strict")

    def test_环境变量压过配置(self):
        # 临时关掉不该逼人改配置文件
        sandbox.set_level("strict")
        os.environ["FEIYU_SANDBOX"] = "off"
        self.assertEqual(sandbox.level(), "off")

    def test_环境变量写错了忽略(self):
        sandbox.set_level("strict")
        os.environ["FEIYU_SANDBOX"] = "乱写"
        self.assertEqual(sandbox.level(), "strict")

    def test_配置坏了不崩(self):
        with open(sandbox.STORE, "w", encoding="utf-8") as f:
            f.write("{ 坏了")
        self.assertIn(sandbox.level(), sandbox.LEVELS)

    def test_关掉时_effective_说得清楚(self):
        sandbox.set_level("off")
        mode, why = sandbox.effective()
        self.assertEqual(mode, "off")
        self.assertTrue(why)      # 不能默默不沙箱


class TestWrap(Base):
    def test_关掉时不包(self):
        sandbox.set_level("off")
        self.assertIsNone(sandbox.wrap("ls", workspace="/w", cwd="/w"))

    @需要沙箱
    def test_工作区绑成可写(self):
        sandbox.set_level("workspace")
        argv = sandbox.wrap("ls", workspace="/w", cwd="/w")
        self.assertIn("--bind", argv)
        self.assertEqual(argv[argv.index("--bind") + 1], "/w")

    @需要沙箱
    def test_整个文件系统先只读打底(self):
        sandbox.set_level("workspace")
        argv = sandbox.wrap("ls", workspace="/w", cwd="/w")
        self.assertEqual(argv[1:4], ["--ro-bind", "/", "/"])

    @需要沙箱
    def test_只有_strict_断网(self):
        sandbox.set_level("workspace")
        self.assertNotIn("--unshare-net", sandbox.wrap("ls", workspace="/w", cwd="/w"))
        sandbox.set_level("strict")
        self.assertIn("--unshare-net", sandbox.wrap("ls", workspace="/w", cwd="/w"))

    @需要沙箱
    def test_读过外部内容的那一条单独断网(self):
        sandbox.set_level("workspace")
        self.assertIn("--unshare-net",
                      sandbox.wrap("ls", workspace="/w", cwd="/w", offline=True))

    @需要沙箱
    def test_断网的两档把_run_整个盖掉(self):
        """光 --unshare-net 不够：nss-resolve 走 /run 下的 Unix socket，断了网照样解析域名
        **tmpfs 要排在工作区的 bind 前面**，工作区在 /run/media 下时才不会被它遮住"""
        for level, offline in (("strict", False), ("workspace", True)):
            sandbox.set_level(level)
            argv = sandbox.wrap("ls", workspace="/w", cwd="/w", offline=offline)
            at = [i for i in range(len(argv) - 1) if argv[i:i + 2] == ["--tmpfs", "/run"]]
            self.assertEqual(len(at), 1, level)
            self.assertLess(at[0], argv.index("/w"), level)

    @需要沙箱
    def test_联网那档不盖_run_只盖容器引擎的_socket(self):
        # 盖整个 /run 的话 /etc/resolv.conf 指向的那份没了，联网的命令也解析不了域名
        fake = Path(self.tmp.name) / "docker.sock"
        fake.write_text("")
        self.addCleanup(setattr, sandbox, "DAEMON_SOCKETS", sandbox.DAEMON_SOCKETS)
        sandbox.DAEMON_SOCKETS = (str(fake), str(fake), "$FEIYU_NO_SUCH_VAR/x.sock")
        sandbox.set_level("workspace")
        argv = sandbox.wrap("ls", workspace="/w", cwd="/w")
        self.assertNotIn("/run", argv)
        self.assertEqual(argv.count(str(fake)), 1)
        self.assertEqual(argv[argv.index(str(fake)) - 1], "/dev/null")

    def test_关着的沙箱断不了网(self):
        sandbox.set_level("off")
        self.assertFalse(sandbox.can_cut_net())
        self.assertIsNone(sandbox.wrap("ls", workspace="/w", cwd="/w", offline=True))

    @需要沙箱
    def test_开着就断得了(self):
        for level in ("workspace", "strict"):
            sandbox.set_level(level)
            self.assertTrue(sandbox.can_cut_net(), level)

    @需要沙箱
    def test_额外放行的路径会绑进去(self):
        # bash 工具记 pwd 的临时文件要靠它，否则 cd 跨不了调用
        sandbox.set_level("workspace")
        argv = sandbox.wrap("ls", workspace="/w", cwd="/w", writable=["/tmp/pwd-x"])
        self.assertIn("/tmp/pwd-x", argv)

    @需要沙箱
    def test_密钥目录都在挖空名单里(self):
        for path in ("~/.ssh", "~/.gnupg", "~/.aws"):
            self.assertIn(path, sandbox.SHADOW_DIRS)

    @需要沙箱
    def test_feiyu_自己的目录也要挖空(self):
        # 模型配置里有 API key，state 里有完整对话原文、日志和长期记忆
        self.assertIn("~/.config/feiyu", sandbox.SHADOW_DIRS)
        self.assertIn("~/.local/state/feiyu", sandbox.SHADOW_DIRS)


@需要沙箱
class TestConfinement(Base):
    """真的跑一遍，别只断言 argv 拼对了 """

    def setUp(self):
        super().setUp()
        sandbox.set_level("workspace")
        self.ws = tempfile.TemporaryDirectory()
        self.box = Toolbox(Path(self.ws.name))
        self.home = Path.home()

    LEAK = "沙箱测试勿留.txt"

    def tearDown(self):
        # 沙箱没生效时这些用例是真会往 HOME 里写的（回退验证时就发生过），
        # 所以无论成败都收拾一遍，别把垃圾留在用户家目录
        (self.home / self.LEAK).unlink(missing_ok=True)
        self.ws.cleanup()
        super().tearDown()

    def sh(self, command):
        # 别叫 run —— 那是 TestCase.run，unittest 靠它跑用例
        return self.box.run("bash", {"command": command})

    def test_工作区内照常读写(self):
        out = self.sh("echo 内容 > a.txt && cat a.txt")
        self.assertFalse(out.is_error)
        self.assertIn("内容", out.content)

    def test_写工作区外被挡(self):
        out = self.sh("echo 入侵 > ~/" + self.LEAK)
        self.assertTrue(out.is_error)
        self.assertIn("Read-only file system", out.content)

    def test_挡住之后外面确实没有这个文件(self):
        self.sh("echo 入侵 > ~/" + self.LEAK)
        self.assertFalse((self.home / self.LEAK).exists())

    def test_报错里给模型指条路(self):
        out = self.sh("echo x > /etc/沙箱测试")
        self.assertIn("沙箱", out.content)

    def test_ssh_目录是空的(self):
        # 这条是查找命令，会附带"用 list_files"的提示，只看命令本身的输出
        self.box.hinted.add("native")
        out = self.sh("ls -a ~/.ssh 2>/dev/null | grep -v '^\\.$' | grep -v '^\\.\\.$' | wc -l")
        self.assertEqual(out.content.strip().splitlines()[-1], "0")

    def test_读不到自己的密钥文件(self):
        out = self.sh("cat ~/.config/feiyu/models.json")
        self.assertTrue(out.is_error)

    def test_cd_跨调用仍然保持(self):
        # 沙箱里的 /tmp 是自己的 tmpfs，记 pwd 的文件不单独放行就跨不过来
        (Path(self.ws.name) / "子目录").mkdir()
        self.sh("cd 子目录")
        self.assertEqual(self.box.cwd, Path(self.ws.name).resolve() / "子目录")

    def test_常用工具还能跑(self):
        out = self.sh("python -c 'print(6*7)'")
        self.assertFalse(out.is_error)
        self.assertIn("42", out.content)

    def test_退出码照常带回来(self):
        self.assertIn("退出码: 7", self.sh("exit 7").content)

    def test_超时照常生效(self):
        self.assertTrue(self.box.run("bash", {"command": "sleep 5", "timeout": 1}).is_error)

    @unittest.skipUnless(os.access("/run/docker.sock", os.R_OK | os.W_OK),
                         "这台机器上没有能碰到的 docker socket")
    def test_碰不到_docker(self):
        """碰得到 docker 就能 `docker run -v /:/host`，「只有工作区可写」当场作废 """
        out = self.sh("docker ps")
        self.assertTrue(out.is_error, out.content)
        self.assertIn("socket 被挡在外面", out.content)

    def test_密钥环境变量照样过滤(self):
        # 沙箱管路径，_child_env 管环境变量，两层是独立的
        os.environ["FEIYU_SBTEST_TOKEN"] = "机密"
        try:
            self.assertNotIn("机密", self.sh("echo $FEIYU_SBTEST_TOKEN").content)
        finally:
            del os.environ["FEIYU_SBTEST_TOKEN"]


@需要沙箱
class TestOffline(Base):
    """读过外部内容之后 bash 断网跑：真的跑一遍，别只断言 argv 拼对了"""

    def setUp(self):
        super().setUp()
        sandbox.set_level("workspace")
        self.ws = tempfile.TemporaryDirectory()
        self.addCleanup(self.ws.cleanup)
        self.box = Toolbox(Path(self.ws.name))
        self.box.outside = "网页 evil.example"

    def test_真的断网_而且告诉它可以带_network(self):
        out = self.box.run("bash", {"command": "timeout 5 curl -sS -o /dev/null "
                                               "https://example.com", "timeout": 15})
        self.assertTrue(out.is_error)
        self.assertIn("network: true", out.content)
        self.assertIn("evil.example", out.content)

    def test_断网时域名也解析不了(self):
        # 只加 --unshare-net 时这条是能解析出 IP 的 —— DNS 外带就走这条
        out = self.box.run("bash", {"command": "getent hosts example.com"})
        self.assertTrue(out.is_error, out.content)

    def test_带了_network_就不断(self):
        self.assertTrue(self.box._offline({"command": "x"}))
        self.assertFalse(self.box._offline({"command": "x", "network": True}))

    def test_没读过就不断(self):
        self.box.outside = ""
        self.assertFalse(self.box._offline({"command": "x"}))


@需要沙箱
class TestStrict(Base):
    def test_断网(self):
        sandbox.set_level("strict")
        ws = tempfile.TemporaryDirectory()
        try:
            out = Toolbox(Path(ws.name)).run(
                "bash", {"command": "timeout 5 curl -s -o /dev/null "
                                    "-w %{http_code} https://example.com", "timeout": 15})
            self.assertNotIn("200", out.content)
        finally:
            ws.cleanup()


class TestExecWrapper(unittest.TestCase):
    """`FEIYU_EXEC_WRAPPER`：把命令送到别处执行的钩子

    SWE-bench 那条路靠它把 bash 送进实例容器 —— 实例镜像里 python 是 3.6~3.9，
    agent 本身跑不进去，只有命令要进去。**它不是安全机制**，开着的时候
    「写被关进工作区」不成立，所以 `effective()` 要照实说。
    """

    def setUp(self):
        before = os.environ.get("FEIYU_EXEC_WRAPPER")
        self.addCleanup(lambda: os.environ.__setitem__("FEIYU_EXEC_WRAPPER", before)
                        if before is not None
                        else os.environ.pop("FEIYU_EXEC_WRAPPER", None))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_给了就整条走它_不叠_bwrap(self):
        os.environ["FEIYU_EXEC_WRAPPER"] = "/bin/myexec --flag"
        got = sandbox.wrap("ls -la", workspace=self.root, cwd=self.root)
        self.assertEqual(got, ["/bin/myexec", "--flag", str(self.root), "ls -la"])
        self.assertNotIn("bwrap", got)

    def test_cwd_是第一个参数_命令是第二个(self):
        """容器那边 `docker exec -w "$1" … bash -lc "$2"` 就是照这个约定接的 """
        os.environ["FEIYU_EXEC_WRAPPER"] = "/bin/myexec"
        sub = self.root / "子目录"
        got = sandbox.wrap("echo 你好", workspace=self.root, cwd=sub)
        self.assertEqual(got[1], str(sub))
        self.assertEqual(got[2], "echo 你好")

    def test_带空格的路径要能拆对(self):
        os.environ["FEIYU_EXEC_WRAPPER"] = '"/tmp/有 空格/exec.sh"'
        got = sandbox.wrap("ls", workspace=self.root, cwd=self.root)
        self.assertEqual(got[0], "/tmp/有 空格/exec.sh")

    def test_effective_要照实说不沙箱(self):
        os.environ["FEIYU_EXEC_WRAPPER"] = "/bin/myexec"
        mode, why = sandbox.effective()
        self.assertEqual(mode, "off")
        self.assertIn("外部执行器", why)

    def test_没设就照旧走沙箱那条路(self):
        os.environ.pop("FEIYU_EXEC_WRAPPER", None)
        got = sandbox.wrap("ls", workspace=self.root, cwd=self.root)
        # 装了 bwrap 就该是 bwrap，没装就是 None —— 两种都不该是外挂执行器
        self.assertTrue(got is None or got[0] == "bwrap", got)

    def test_空串当成没设(self):
        os.environ["FEIYU_EXEC_WRAPPER"] = "   "
        got = sandbox.wrap("ls", workspace=self.root, cwd=self.root)
        self.assertTrue(got is None or got[0] == "bwrap", got)


if __name__ == "__main__":
    unittest.main()
