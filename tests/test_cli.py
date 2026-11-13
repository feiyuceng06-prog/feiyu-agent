"""命令行参数 起真进程，但都在参数解析就退出，不建后端也不打接口

底下 TestKeyAdd 不起进程，直接调 __main__ 里的 _key_add
"""

import contextlib
import io
import json
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

        # Electron 那份：electron-builder 拿它命名安装包（Feiyu-<版本>.AppImage）
        for name in ("package.json", "package-lock.json"):
            pkg = _json.loads((root / "desktop" / name).read_text(encoding="utf-8"))
            self.assertEqual(pkg.get("version"), __version__, "desktop/" + name)


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


class TestExitCode(unittest.TestCase):
    """接口挂了，一次性任务的退出码不能还是 0

    0.72.0 那轮 24 道基线跑到一半 openrouter 余额见底，11 次 trial 全部当场退出、
    **退出码 0**，判分那边于是把它们记成「没解决」，解决率被白白压低 ——
    是事后翻 stdout 才发现的。屏幕前有人时无所谓（他看得见那句红字），
    没人看屏幕的场景里这个区别就是全部。
    """

    def setUp(self):
        import http.server
        import threading

        class 只会说402(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps({"error": {"message": "requires more credits",
                                             "code": 402}}).encode()
                self.send_response(402)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass        # 别往测试输出里糊访问日志

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), 只会说402)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        # 清理是倒着跑的：先 shutdown 停下循环，再 server_close 关掉监听的那个 socket
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = os.path.join(self.tmp.name, "feiyu")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "models.json"), "w", encoding="utf-8") as f:
            # **key 必须是 ASCII**：它会拼进 `Authorization: Bearer …` 这个 HTTP header，
            # 而 header 不能带非 ASCII —— 第一版写成 "sk-假的"，报出来的是
            # "'ascii' codec can't encode characters in position 10-11"，
            # 看着像 feiyu 的编码 bug，其实是夹具自己的
            json.dump({"假的": {
                "kind": "openai", "model": "随便",  "key": "sk-not-a-real-key",
                "base_url": "http://127.0.0.1:%d/v1" % self.srv.server_port}}, f)
        self.env = {**os.environ, "XDG_CONFIG_HOME": self.tmp.name,
                    "XDG_STATE_HOME": self.tmp.name, "FEIYU_NO_LOG": "1",
                    "NO_COLOR": "1"}

    def 跑(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, "-m", "feiyu", "-w", self.tmp.name, "--mode", "auto", *args],
            capture_output=True, text=True, timeout=120, env=self.env, input=stdin)

    def test_一次性任务接口报错退出码非零(self):
        done = self.跑("随便什么任务")
        self.assertEqual(done.returncode, 1, done.stdout[-500:] + done.stderr[-500:])

    def test_管道里也一样(self):
        done = self.跑(stdin="第一件事\n第二件事\n")
        self.assertEqual(done.returncode, 1, done.stdout[-500:] + done.stderr[-500:])

    def test_报错本身还是要打出来给人看(self):
        """退出码是给脚本看的，人还是得看见那句话 —— 两样都要，不是二选一 """
        done = self.跑("随便什么任务")
        self.assertIn("余额", done.stdout + done.stderr)


class TestExplain402(unittest.TestCase):
    """402 必须按**状态码**认，不能只靠措辞

    这次实战撞到的是 openrouter，它说的是 "requires more credits" ——
    一个 `insufficient` 都没有，而 `_API_CODES` 原来也没有 402，
    于是最常见的计费错误只会甩一坨原始 JSON 给人看。
    措辞那条也留着，是给**把 402 塞进 400** 的接口兜底。
    """

    def explain(self, code=None, text=""):
        from feiyu.__main__ import _explain
        exc = type("假报错", (Exception,), {"status_code": code})(text)
        return _explain(exc)

    def test_按状态码认(self):
        """报错文本里一个线索都没有时，只能靠状态码 """
        self.assertIn("余额", self.explain(402, "Payment Required"))

    def test_措辞那条给塞进400的接口兜底(self):
        self.assertIn("余额", self.explain(400, "This request requires more credits"))

    def test_别的码不受影响(self):
        self.assertIn("限流", self.explain(429, "slow down"))
        self.assertIn("key", self.explain(401, "bad key"))
        self.assertEqual(self.explain(418, "我是茶壶"), "")


class TestPipedInterrupt(unittest.TestCase):
    """管道里按 ctrl-c 要整个停下，不是只打断当前这条

    原来是一路跑到底的：`cat 活儿.txt | feiyu --mode auto` 中途按 ctrl-c，
    只打断了当前这一条，后面几条照样执行 —— 而 auto 模式下那意味着它接着改文件、
    接着跑命令。**人按 ctrl-c 的意思是"全停"。**
    """

    def piped(self, 怎么结束):
        """`怎么结束` 收一个任务名，返回 None 正常、抛 KeyboardInterrupt 打断、
        抛别的异常算出错"""
        import types
        from feiyu import __main__ as M
        跑过 = []

        class 假Agent:
            session_id = "x"

            def send(self, task):
                跑过.append(task)
                怎么结束(task)
                return ""

        老 = M.session
        M.session = types.SimpleNamespace(save=lambda *a, **k: None)
        self.addCleanup(lambda: setattr(M, "session", 老))
        老输入 = sys.stdin
        sys.stdin = io.StringIO("甲\n乙\n丙\n")
        self.addCleanup(lambda: setattr(sys, "stdin", 老输入))
        with contextlib.redirect_stdout(io.StringIO()):
            code = M._piped(假Agent(), "/tmp")
        return code, 跑过

    def test_打断之后不再跑剩下的(self):
        def 第一条就打断(task):
            if task == "甲":
                raise KeyboardInterrupt
        code, 跑过 = self.piped(第一条就打断)
        self.assertEqual(跑过, ["甲"])
        self.assertEqual(code, 130)

    def test_出错不停_那是另一回事(self):
        """一条失败不代表后面几条也做不了 """
        def 第一条报错(task):
            if task == "甲":
                raise RuntimeError("接口挂了")
        code, 跑过 = self.piped(第一条报错)
        self.assertEqual(跑过, ["甲", "乙", "丙"])
        self.assertEqual(code, 1)

    def test_全都正常时退出码是零(self):
        code, 跑过 = self.piped(lambda task: None)
        self.assertEqual((code, 跑过), (0, ["甲", "乙", "丙"]))
