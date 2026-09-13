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


class TestVersionSync(unittest.TestCase):
    """桌面端那三处版本号要跟着 `feiyu/__init__.py` 走

    单一真源是 `__version__`，`pyproject` 用 dynamic 读过来 —— 但**桌面端那三处
    是手抄的**，只能靠人记得改。0.69.1 体检时发现它们已经漂了：
    `Cargo.toml` 停在 0.68.2、`tauri.conf.json` 停在 0.67.2，而 Python 侧是 0.69.1。
    漂了不报错，只是窗口状态栏上显示的版本号是错的。
    """

    def paths(self):
        import pathlib
        return pathlib.Path(__file__).resolve().parent.parent

    def test_桌面端三处跟_python_侧一致(self):
        import json as _json
        import re as _re
        from feiyu import __version__
        root = self.paths()
        toml = (root / "desktop" / "Cargo.toml").read_text(encoding="utf-8")
        got = _re.search(r'^version = "([^"]+)"', toml, _re.M)
        self.assertEqual(got and got.group(1), __version__, "desktop/Cargo.toml")

        conf = _json.loads((root / "desktop" / "tauri.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(conf.get("version"), __version__, "desktop/tauri.conf.json")

        lock = (root / "desktop" / "Cargo.lock").read_text(encoding="utf-8")
        got = _re.search(r'name = "feiyu-desktop"\nversion = "([^"]+)"', lock)
        self.assertEqual(got and got.group(1), __version__, "desktop/Cargo.lock")


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



class TestEffortCommand(unittest.TestCase):
    """`/effort`：会话内临时改推理强度

    两条不变量：
    1. **档位只认接口那五个**（中文是显示名，也收，但要翻回英文再发）
    2. **xhigh / max 配上「不思考」接口会 400** —— 两个方向都要挡：
       改档位时挡一次，在高档位上关思考时也要挡一次
    """

    def setUp(self):
        from pathlib import Path
        from feiyu import keys
        from feiyu.agent import Feiyu
        self.keys = keys
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for attr in ("STORE", "STATE"):
            before = getattr(keys, attr)
            self.addCleanup(setattr, keys, attr, before)
            setattr(keys, attr, os.path.join(self.tmp.name, attr + ".json"))
        keys.add("甲", base_url="https://例子/v1", model="m", key="k")
        self.agent = Feiyu(Path(self.tmp.name), model="甲")

    def run_it(self, arg):
        from feiyu import ui
        from feiyu.__main__ import _effort
        return ui._ANSI.sub("", _effort(self.agent, arg))

    def test_切档位(self):
        self.run_it("low")
        self.assertEqual(self.agent.backend.effort, "low")

    def test_中文档位名也收(self):
        """界面上显示的是中文，用户多半照着打 """
        self.run_it("最高")
        self.assertEqual(self.agent.backend.effort, "max")

    def test_不认的档位要报错并列出可选(self):
        # `ui.notice` 会按终端宽度折行，断言前先把换行抹掉 ——
        # 不抹的话 "xhigh" 可能正好被拆成 "xhig\nh"
        got = self.run_it("超高").replace("\n", "").replace(" ", "")
        self.assertIn("没有", got)
        self.assertIn("xhigh", got)
        self.assertNotEqual(self.agent.backend.effort, "超高")

    def test_在高档位关思考会顺手降档(self):
        self.run_it("xhigh")
        got = self.run_it("关")
        self.assertIs(self.agent.backend.thinking_on, False)
        self.assertEqual(self.agent.backend.effort, "high", "没降档，下次请求会 400")
        self.assertIn("400", got)

    def test_不思考时切到高档会顺手开思考(self):
        self.run_it("关")
        got = self.run_it("max")
        self.assertEqual(self.agent.backend.effort, "max")
        self.assertIs(self.agent.backend.thinking_on, True, "没开思考，下次请求会 400")
        self.assertIn("400", got)

    def test_低档位关思考是允许的(self):
        self.run_it("high")
        self.run_it("关")
        self.assertEqual(self.agent.backend.effort, "high")
        self.assertIs(self.agent.backend.thinking_on, False)

    def test_没有模型时不崩(self):
        self.agent.backend = None
        self.assertIn("还没有", self.run_it("low"))

    def test_进了斜杠命令表(self):
        from feiyu import ui
        self.assertIn("/effort", ui.SLASH)
