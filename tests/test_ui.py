"""ui 里能脱离终端断言的部分 画框那些要看真实渲染，见 CLAUDE.md 的验证方式一节

这里盯死一个坑：**量宽度前必须剥 ANSI**，get_cwidth 会把转义序列当可见字符
（box 和 top_border 各犯过一次）
"""

import contextlib
import io
import pathlib
import unittest

from feiyu import ui


class Colored(unittest.TestCase):
    """_COLOR 是按 isatty 定的，测试里不是终端就一律不上色，
    颜色相关的断言会全部变成空转 """

    def setUp(self):
        self._saved = ui._COLOR
        ui._COLOR = True

    def tearDown(self):
        ui._COLOR = self._saved


class TestWidth(Colored):
    def test_颜色码不占宽度(self):
        painted = ui.paint("brown", "abc")
        self.assertEqual(ui.visible(painted), 3)

    def test_粗体也不占(self):
        self.assertEqual(ui.visible(ui.bold("abc")), 3)

    def test_cjk_算两格(self):
        self.assertEqual(ui.visible("你好"), 4)

    def test_中英混排(self):
        self.assertEqual(ui.visible("ab你好"), 6)

    def test_剥了色码之后再量(self):
        # 直接 len() 的话这里会得到一个大得离谱的数
        painted = ui.paint("brown", "你好")
        self.assertGreater(len(painted), 2)          # 确认真的上了色
        self.assertEqual(ui.visible(painted), 4)
        self.assertLess(ui.visible(painted), len(painted))


class TestClip(Colored):
    def test_短的不动(self):
        self.assertEqual(ui.clip("abc", 10), "abc")

    def test_按显示宽度截而不是字符数(self):
        self.assertLessEqual(ui.visible(ui.clip("你好世界你好世界", 6)), 6)

    def test_截过的带省略号(self):
        self.assertTrue(ui.clip("abcdefghij", 5).endswith("…"))

    def test_极小的上限不崩(self):
        for limit in (0, 1, 2):
            ui.clip("你好世界", limit)


class TestTokens(unittest.TestCase):
    def test_小数字原样(self):
        self.assertEqual(ui.tokens(999), "999")

    def test_上千用_k(self):
        self.assertEqual(ui.tokens(1500), "1.5k")

    def test_整千不留小数点(self):
        self.assertEqual(ui.tokens(2000), "2k")


class TestPad(unittest.TestCase):
    def test_按显示宽度补齐_不是字符数(self):
        # ljust 数字符数，CJK 一个字占两格，技能名可以是中文
        self.assertEqual(ui.visible(ui.pad("发版", 10)), 10)
        self.assertEqual(ui.visible(ui.pad("deploy", 10)), 10)
        self.assertEqual(ui.visible(ui.pad("/查", 10)), 10)

    def test_超出宽度不截断(self):
        long = "一二三四五六"
        self.assertEqual(ui.pad(long, 4), long)


class TestLabels(unittest.TestCase):
    def test_工具名有中文标签(self):
        from feiyu.tools import TOOL_SCHEMAS
        for schema in TOOL_SCHEMAS:
            self.assertNotEqual(ui.tool_label(schema["name"]), schema["name"], schema["name"])

    def test_不认识的名字原样返回(self):
        self.assertEqual(ui.tool_label("没这个工具"), "没这个工具")

    def test_权限规则名覆盖了所有工具(self):
        from feiyu import permissions
        from feiyu.tools import TOOL_SCHEMAS
        for schema in TOOL_SCHEMAS:
            self.assertIn(schema["name"], permissions.RULE_NAME, schema["name"])


class TestStream(unittest.TestCase):
    def test_攒下原文供中断时补历史(self):
        s = ui.Stream()
        # 不是终端时 write 会直接吐到 stdout，测试里挡掉
        with contextlib.redirect_stdout(io.StringIO()):
            s.write("你好 ")
            s.write("world")
        self.assertEqual(s.text, "你好 world")
        self.assertTrue(s.wrote_text)

    def test_一个字都没写(self):
        s = ui.Stream()
        self.assertEqual(s.text, "")
        self.assertFalse(s.wrote_text)

    def test_空串不算写过(self):
        s = ui.Stream()
        with contextlib.redirect_stdout(io.StringIO()):
            s.write("")
        self.assertFalse(s.wrote_text)


class TestWrap(unittest.TestCase):
    def test_中文按宽度断(self):
        rows = ui.wrap("中" * 20, 10)
        self.assertTrue(all(ui.visible(r) <= 10 for r in rows))

    def test_西文不劈开单词(self):
        rows = ui.wrap("想接着聊就 feiyu --resume 就行了", 24)
        self.assertTrue(all(ui.visible(r) <= 24 for r in rows), rows)
        self.assertNotIn("--res\n", "\n".join(rows) + "\n")
        self.assertIn("--resume", " ".join(rows))

    def test_单个超长词硬断(self):
        rows = ui.wrap("a" * 40, 10)
        self.assertTrue(all(ui.visible(r) <= 10 for r in rows))
        self.assertEqual("".join(rows), "a" * 40)

    def test_内容不丢(self):
        text = "混排 mixed 内容 with words 和中文 一起 test"
        self.assertEqual(" ".join(ui.wrap(text, 12)).split(), text.split())


class TestMarkdown(unittest.TestCase):
    """流式 markdown 模型每句话都带 ** 和反引号，不渲染就是满屏记号 """

    def run_md(self, text, size=7):
        m = ui.MarkdownStream()
        out = []
        for i in range(0, len(text), size):
            out += m.feed(text[i:i + size])
        out += m.flush()
        return out

    def plain(self, text, size=7):
        return "".join(t for t, _ in self.run_md(text, size))

    def styled(self, text, style, size=7):
        return "".join(t for t, s in self.run_md(text, size) if s == style)

    def merged(self, text, size):
        """按样式合并相邻片段 切法不同时分段位置本来就会不同，看的是最终结果 """
        out = []
        for piece, style in self.run_md(text, size):
            if out and out[-1][1] == style:
                out[-1] = (out[-1][0] + piece, style)
            else:
                out.append((piece, style))
        return out

    def test_粗体(self):
        self.assertEqual(self.plain("说 **重点** 了"), "说 重点 了")
        self.assertEqual(self.styled("说 **重点** 了", "bold"), "重点")

    def test_行内代码(self):
        self.assertEqual(self.plain("用 `ls -la` 看"), "用 ls -la 看")
        self.assertEqual(self.styled("用 `ls -la` 看", "code"), "ls -la")

    def test_标题只管到行尾(self):
        out = self.plain("# 标题\n正文\n")
        self.assertEqual(out, "标题\n正文\n")
        self.assertEqual(self.styled("# 标题\n正文\n", "bold"), "标题")

    def test_列表变圆点(self):
        self.assertEqual(self.plain("- 一\n- 二\n"), "• 一\n• 二\n")

    def test_代码块整段压暗且吃掉围栏(self):
        text = "看：\n```python\nx = 1\n```\n完\n"
        self.assertEqual(self.plain(text), "看：\nx = 1\n完\n")
        self.assertEqual(self.styled(text, "block"), "x = 1\n")

    def test_代码块里的缩进要留住(self):
        text = "```py\ndef f():\n    return 1\n```\n"
        self.assertIn("    return 1", self.plain(text))

    def test_代码块里的记号不当格式(self):
        text = "```\na = b ** 2\npath = `x`\n```\n"
        self.assertIn("b ** 2", self.plain(text))
        self.assertIn("`x`", self.plain(text))

    def test_引用变竖线(self):
        self.assertEqual(self.plain("> 一句话\n正文\n"), "│ 一句话\n正文\n")
        self.assertEqual(self.styled("> 一句话\n正文\n", "quote"), "│ 一句话")

    def test_单个星号不动(self):
        # 命令行里 * 多半是通配符，认成斜体到处误伤
        self.assertEqual(self.plain("匹配 *.py 文件"), "匹配 *.py 文件")

    def test_任何切法结果都一样(self):
        """半个 ``` 落在 chunk 边界上时曾经整段串味，收尾围栏再也认不出来 """
        text = ("讲 **重点**：用 `rg` 搜\n\n```python\nimport os\n"
                "    x = 1\n```\n\n- 一条\n- 两条\n# 小标题\n收工 **了**\n")
        want = self.merged(text, 1)
        for size in range(2, 13):
            self.assertEqual(self.merged(text, size), want, "chunk=%d" % size)

    def test_编号列表的记号单独成段(self):
        out = self.run_md("1. 一\n12) 二\n")
        self.assertEqual([t for t, s in out if s == "mark"], ["1. ", "12) "])
        self.assertEqual(self.plain("1. 一\n12) 二\n"), "1. 一\n12) 二\n")
        # 年份、版本号这种不是列表
        self.assertEqual(self.styled("2026 年\n1.5 倍\n", "mark"), "")

    def test_分隔线(self):
        out = self.run_md("上\n---\n下\n")
        self.assertEqual([s for _, s in out if s == "hr"], ["hr"])
        self.assertEqual(self.plain("上\n---\n下\n").replace("─", ""), "上\n下\n")
        # 两个减号不是分隔线，- 列表更不是
        self.assertEqual(self.plain("--\n- 一\n"), "--\n• 一\n")

    def test_表格对齐并画表头线(self):
        text = "| 文件 | 行数 |\n|---|---:|\n| calc.py | 2 |\n| 很长的文件名.py | 123 |\n完\n"
        out = self.plain(text)
        rows = out.split("\n")
        # 第一列宽 15（六个汉字 + .py），「文件」占 4，补 11 格再加两格列距
        self.assertEqual(rows[0].rstrip(), "文件" + " " * 13 + "行数")
        self.assertTrue(set(rows[1]) <= set("─ "))
        self.assertEqual(rows[2].rstrip(), "calc.py" + " " * 13 + "2")     # 右对齐
        self.assertEqual(rows[3].rstrip(), "很长的文件名.py   123")
        self.assertEqual(rows[4], "完")
        self.assertEqual(self.styled(text, "bold"), "文件" + " " * 13 + "行数")

    def test_表格太宽就削最宽的列(self):
        m = ui.MarkdownStream()
        m.limit = 30
        out = m.feed("| a | b |\n|---|---|\n| %s | %s |\n" % ("x" * 40, "y" * 40))
        out += m.flush()
        for row, _ in out:
            for line in row.split("\n"):
                self.assertLessEqual(ui.visible(line), 28)

    def test_链接变成文字加地址(self):
        text = "看 [文档](https://x.y/z) 吧"
        self.assertEqual(self.plain(text), "看 文档 (https://x.y/z) 吧")
        self.assertEqual(self.styled(text, "rule"), " (https://x.y/z)")
        # 不是链接的方括号原样出
        self.assertEqual(self.plain("数组 a[0] 和 [x] 是"), "数组 a[0] 和 [x] 是")

    def test_任何切法结果都一样_表格分隔线链接(self):
        text = ("| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n\n1. 见 [doc](http://d) 好\n"
                "- 二\n\n**粗** 了\n")
        want = self.merged(text, 1)
        for size in range(2, 13):
            self.assertEqual(self.merged(text, size), want, "chunk=%d" % size)

    def test_没收完的记号在_flush_时吐出来(self):
        m = ui.MarkdownStream()
        got = "".join(t for t, _ in m.feed("话说到一半 *"))
        got += "".join(t for t, _ in m.flush())
        self.assertEqual(got, "话说到一半 *")


class TestToolProgress(unittest.TestCase):
    """工具参数流式生成时的那行状态 参数是没收完的 JSON """

    def test_只有工具名时也能出一行(self):
        self.assertEqual(ui.tool_progress("read_file", '{"pa'), "Read")

    def test_路径一到齐就显出来(self):
        self.assertIn("a/b.py", ui.tool_progress("write_file", '{"path": "a/b.py", "content": "x'))

    def test_命令也认(self):
        self.assertIn("npm test", ui.tool_progress("bash", '{"command": "npm test"}'))

    def test_短参数不报行数(self):
        self.assertNotIn("已生成", ui.tool_progress("write_file", '{"path": "a.py", "content": "x'))

    def test_长参数报到目前为止的行数(self):
        partial = '{"path": "a.py", "content": "' + "x = 1\\n" * 60
        self.assertIn("已生成 61 行", ui.tool_progress("write_file", partial))

    def test_转义按_JSON_解(self):
        out = ui.tool_progress("write_file", '{"path": "a\\u002fb.py", "content": "')
        self.assertIn("a/b.py", out)

    def test_值里的换行压成空格(self):
        out = ui.tool_progress("bash", '{"command": "a\\nb"}')
        self.assertNotIn("\n", out)

    def test_坏转义不崩(self):
        ui.tool_progress("write_file", '{"path": "a\\uZZZZ", "content": "')


class TestStreamStatus(unittest.TestCase):
    """状态行什么时候画 正文开始之后乱画会把话切成一段一段的 """

    def make(self):
        s = ui.Stream()
        s._begin = 0.0          # 越过 spinner_delay，否则一律不画
        return s

    def test_还没说话时画思考中(self):
        self.assertTrue(self.make()._should_draw(None))

    def test_延迟没到不画(self):
        s = ui.Stream()
        self.assertFalse(s._should_draw(None))

    def test_正文开始后没有工具参数就不画(self):
        s = self.make()
        s.wrote_text = True
        self.assertFalse(s._should_draw(None))

    def test_正文开始后有工具参数才画(self):
        s = self.make()
        s.wrote_text = True
        self.assertTrue(s._should_draw(("write_file", '{"path": "a.py"')))

    def test_没画过就不擦(self):
        s = self.make()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            s._hide_status()
        self.assertEqual(buf.getvalue(), "")     # 擦了的话会把正文那半行抹掉

    def test_非终端时_progress_不做事(self):
        s = ui.Stream()
        s.progress("write_file", '{"path": "a.py"')
        self.assertIsNone(s._status)


class TestSlashCommands(unittest.TestCase):
    """0.5.0 用区间切片重写 _model 时把夹在中间的 _mode 一起删掉了，
    /mode 崩了才发现 每个命令都要有分支、有实现 """

    def setUp(self):
        import re
        from pathlib import Path
        self.src = Path(__file__).resolve().parent.parent.joinpath(
            "feiyu/__main__.py").read_text(encoding="utf-8")
        self.re = re

    def test_每个命令都有分支(self):
        handled = set(self.re.findall(r'cmd == "(/\w+)"', self.src))
        self.assertEqual(handled, set(ui.SLASH))

    def test_调用到的函数都定义了(self):
        called = set(self.re.findall(r"print\((_\w+)\(", self.src))
        defined = set(self.re.findall(r"^def (_\w+)\(", self.src, self.re.M))
        self.assertEqual(called - defined, set())

    def test_确认回调装在早退分支之前(self):
        # 装在后面的话 `feiyu "改一下 x"` 的每个写操作都是「用户拒绝」
        approve = self.src.index("agent.approve =")
        early = self.src.index("if args.prompt:")
        self.assertLess(approve, early)


if __name__ == "__main__":
    unittest.main()


class TestGutter(unittest.TestCase):
    """正文每行开头那两格：第一行是 ⏺，代码块是竖线，其余两个空格 """

    def render(self, text):
        ui._COLOR = False
        s = ui.Stream()
        s._tty = True       # 走渲染那条路，但不起线程：直接喂
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            s._emit(text)
            s._runs(s._md.flush())
        return out.getvalue()

    def test_第一行带标记_后面的行两格缩进(self):
        got = self.render("第一句\n第二句\n")
        self.assertEqual(got, ui.BULLET + " 第一句\n  第二句\n")

    def test_代码块用竖线隔开_缩进留住(self):
        got = self.render("说明\n```python\ndef f():\n    return 1\n```\n完\n")
        self.assertIn("\n│ def f():\n│     return 1\n", got)
        self.assertTrue(got.endswith("  完\n"))

    def test_标记只出现一次_折行的续行也不带(self):
        got = self.render("a" * 150 + "\n")
        self.assertEqual(got.count(ui.BULLET), 1)
        self.assertTrue(got.startswith(ui.BULLET + " "))

    def test_列表项折行后续行对齐到记号后面(self):
        got = self.render("- " + "字" * 60 + "\n1. " + "x " * 60 + "\n正文\n")
        rows = got.split("\n")
        self.assertTrue(rows[0].startswith(ui.BULLET + " • "))
        self.assertTrue(rows[1].startswith("    字"))       # 两格缩进 + 两格悬挂
        self.assertTrue(rows[2].startswith("  1. x"))
        self.assertTrue(rows[3].startswith("     x"))       # 「1. 」三格宽
        self.assertTrue(rows[-2].startswith("  正文"))       # 换行之后不再悬挂
        self.assertTrue(all(ui.visible(r) <= ui.width() for r in rows))

    def test_长_URL_整个挪到下一行而不是劈开(self):
        url = "https://docs.example.org/en/stable/getting-started.html"
        got = self.render("参考这里这里这里这里这里这里这里这里这里这里这里这里 " + url + "\n")
        self.assertIn(url, got)

    def test_分隔线占满一行(self):
        got = self.render("上\n---\n下\n")
        rows = got.split("\n")
        self.assertEqual(rows[1], "  " + "─" * (ui.width() - 4))
        self.assertEqual(rows[2], "  下")


class TestTheme(unittest.TestCase):
    """主题只换调色板和记号，切回来要一模一样 """

    def tearDown(self):
        ui.set_theme("default")

    def test_切换与还原(self):
        before = dict(ui._RGB)
        self.assertTrue(ui.set_theme("swiss"))
        self.assertEqual(ui.THEME, "swiss")
        self.assertEqual(ui.BULLET, "■")
        self.assertEqual(ui.CORNERS, "┌┐└┘")
        self.assertNotEqual(ui._RGB["brown"], before["brown"])
        self.assertTrue(ui.set_theme("default"))
        self.assertEqual(ui._RGB, before)
        self.assertEqual(ui.BULLET, "⏺")

    def test_不认识的名字不动现状(self):
        self.assertFalse(ui.set_theme("bauhaus"))
        self.assertEqual(ui.THEME, "default")

    def test_框和列表记号跟着换(self):
        ui.set_theme("swiss")
        top = ui.box(["x"]).split("\n")[0]
        self.assertIn("┌", top)
        self.assertNotIn("╭", top)
        runs = ui.MarkdownStream().feed("- 一条\n")
        self.assertTrue(any(text.strip() == "–" for text, style in runs if style == "mark"))

    def test_钩子行挂在工具结果那一级(self):
        line = ui.hook_line(True, "钩子通过：x")
        self.assertTrue(line.startswith("  " + ui.ELBOW))


class TestThemes(unittest.TestCase):
    """主题只换调色板和记号，**每套都得配齐**

    `set_theme` 是 `_RGB.clear()` 之后整份换上去的：少一个键，之后每一次
    `paint("那个键", …)` 都是 KeyError，而且要等真画到那种东西时才炸。
    记号同理，少一个就留着上一套的（`globals().update` 不会删）。
    """

    def setUp(self):
        self.addCleanup(ui.set_theme, "default")

    def test_每套主题的键都配齐(self):
        base = ui.THEMES["default"]
        for name, spec in ui.THEMES.items():
            self.assertEqual(set(spec["rgb"]), set(base["rgb"]), name)
            self.assertEqual(set(spec["glyphs"]), set(base["glyphs"]), name)
            self.assertTrue(spec.get("label"), name)

    def test_切过去每个颜色都画得出来(self):
        ui._COLOR = True
        self.addCleanup(setattr, ui, "_COLOR", ui._COLOR)
        for name in ui.THEMES:
            self.assertTrue(ui.set_theme(name), name)
            for key in ui.THEMES["default"]["rgb"]:
                self.assertIn("\x1b[", ui.paint(key, "字"), "%s/%s" % (name, key))

    def test_没这套主题就回_False_且不改动任何东西(self):
        ui.set_theme("swiss")
        before = dict(ui._RGB)
        self.assertFalse(ui.set_theme("没这套"))
        self.assertEqual(ui._RGB, before)

    def test_暖色那两套的加减号要分得开(self):
        """整套暖色里，diff 的新增（blue）和删除（red）不能糊成一片 ——
        桌面端那边同一条不变量由 shoot.mjs 量 RGB 距离盯着 """
        for name in ("retro", "anthropic"):
            got = ui.THEMES[name]["rgb"]
            plus = [int(x) for x in got["blue"].split(";")]
            minus = [int(x) for x in got["red"].split(";")]
            far = sum((a - b) ** 2 for a, b in zip(plus, minus)) ** 0.5
            self.assertGreater(far, 90, "%s: %s vs %s" % (name, plus, minus))

    def test_Anthropic_在浅底深底上都读得清(self):
        """终端的底色不归我们管：用户的 kitty 是浅的，别人的多半是深的
        每个色在两种底上都得够 3:1（大字 / 非文本的 AA），不然换个终端就糊了 """
        def lum(rgb):
            def lin(c):
                c /= 255
                return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            r, g, b = (lin(v) for v in rgb)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        def contrast(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        for key, raw in ui.THEMES["anthropic"]["rgb"].items():
            rgb = [int(x) for x in raw.split(";")]
            for bg in ((250, 249, 245), (20, 20, 20)):
                self.assertGreaterEqual(contrast(rgb, bg), 3.0, "%s on %s" % (key, bg))


class TestFixtureKeepsUp(unittest.TestCase):
    """`dev/ui_runner.py` 的假后端必须收得下真后端 run_turn 的每个参数

    0.58.0 给 `run_turn` 加了 `on_think`，假后端没跟上 —— 于是 pty 夹具
    **从那一版起就一句话都跑不完**（"got an unexpected keyword argument"），
    而它是唯一能看见真实渲染的东西。中间七个版本都没人发现，因为夹具挂了
    只是少几张图，没有任何一条断言会红。这条就是那条断言。
    """

    def test_签名对得上(self):
        import ast
        import inspect

        from feiyu.providers import AnthropicBackend, OpenAICompatBackend

        want = set()
        for real in (AnthropicBackend, OpenAICompatBackend):
            want |= set(inspect.signature(real.run_turn).parameters) - {"self"}
        source = pathlib.Path(__file__).resolve().parent.parent / "dev" / "ui_runner.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        fake = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "run_turn")
        got = {a.arg for a in fake.args.args} - {"self"}
        self.assertLessEqual(want, got, "夹具的假后端少收了：%s" % sorted(want - got))
