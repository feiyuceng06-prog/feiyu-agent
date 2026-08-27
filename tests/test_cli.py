"""命令行参数 起真进程，但都在参数解析就退出，不建后端也不打接口 """

import subprocess
import sys
import unittest


def run(*args):
    return subprocess.run([sys.executable, "-m", "feiyu", *args],
                          capture_output=True, text=True, timeout=60)


class TestArgs(unittest.TestCase):
    def test_version(self):
        from feiyu import __version__
        self.assertIn(__version__, run("-V").stdout + run("-V").stderr)

    def test_help_里有_mode(self):
        out = run("--help").stdout
        self.assertIn("--mode", out)
        for name in ("default", "auto", "plan", "readonly"):
            self.assertIn(name, out)

    def test_不认的模式直接退出(self):
        # 一次性任务和管道模式下没人能回答确认，这个参数是它自己动手的唯一开关
        done = run("--mode", "没有这个模式", "随便什么任务")
        self.assertEqual(done.returncode, 2)

    def test_不认的工作目录报错(self):
        self.assertEqual(run("-w", "/这个目录不存在", "任务").returncode, 2)
