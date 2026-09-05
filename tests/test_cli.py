"""命令行参数 起真进程，但都在参数解析就退出，不建后端也不打接口

底下 TestKeyAdd 不起进程，直接调 __main__ 里的 _key_add
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
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

class _Agent:
    """只认 _key_add 用到的那几样 """

    def __init__(self, name="", backend=None):
        self.name = name
        self.backend = backend
        self.switched = []

    @property
    def ready(self) -> bool:
        return self.backend is not None

    def use_model(self, name: str) -> bool:
        self.switched.append(name)
        self.name = name
        return True


class TestKeyAdd(unittest.TestCase):
    """改的是正在用的那条时，后端要当场重建

    只写盘不重建的话，客户端还挂着上一次建它时的 base_url 和 key，这一轮改的
    东西本次会话一律不生效；而 /model 同名会早退，用户只剩重启一条路
    """

    def setUp(self):
        from feiyu import keys
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for attr, value in (("_DIR", tmp.name),
                            ("STORE", os.path.join(tmp.name, "models.json")),
                            ("STATE", os.path.join(tmp.name, "state.json"))):
            self.addCleanup(setattr, keys, attr, getattr(keys, attr))
            setattr(keys, attr, value)

    def _add(self, agent, *answers):
        import prompt_toolkit
        from feiyu import __main__ as cli
        seq = list(answers)
        self.addCleanup(setattr, prompt_toolkit, "prompt", prompt_toolkit.prompt)
        prompt_toolkit.prompt = lambda *a, **kw: seq.pop(0) if seq else ""
        with contextlib.redirect_stdout(io.StringIO()):
            return cli._key_add(agent)

    def test_改当前正在用的那条会重建后端(self):
        agent = _Agent(name="luna", backend=object())
        self._add(agent, "luna", "https://openrouter.ai/api/v1",
                  "openai/gpt-5.6-luna", "sk-test")
        self.assertEqual(agent.switched, ["luna"])

    def test_清单原来是空的加完直接启用(self):
        agent = _Agent()
        self._add(agent, "luna", "https://openrouter.ai/api/v1",
                  "openai/gpt-5.6-luna", "sk-test")
        self.assertEqual(agent.switched, ["luna"])

    def test_加的是别的那条不动当前模型(self):
        # 加一条备用配置不该把人从正在用的模型上踢走
        agent = _Agent(name="kimi", backend=object())
        self._add(agent, "luna", "https://openrouter.ai/api/v1",
                  "openai/gpt-5.6-luna", "sk-test")
        self.assertEqual(agent.switched, [])

    def test_写进去的确实是新地址(self):
        from feiyu import keys
        agent = _Agent(name="luna", backend=object())
        self._add(agent, "luna", "https://openrouter.ai/api/v1/responses",
                  "openai/gpt-5.6-luna", "sk-test")
        self._add(agent, "luna", "https://openrouter.ai/api/v1",
                  "openai/gpt-5.6-luna", "sk-test")
        self.assertEqual(keys.load()["luna"]["base_url"],
                         "https://openrouter.ai/api/v1")
        self.assertEqual(agent.switched, ["luna", "luna"])

