"""机器人：一键开一段带人格、模型、权限模式的新对话

跟配置档案的界限：档案是"手动切的人格"，机器人是"开一段什么样的新对话"。
**人格那个位置是共用的** —— 同时套两个人格只会互相打架，
而"你是谁"这个问题只该有一个答案。
"""

import os
import tempfile
import unittest
from pathlib import Path

from feiyu import bots, keys, permissions, profiles, session, tools as tools_mod
from feiyu.agent import Feiyu
from tests.fakes import FakeBackend

FULL = """---
name: 代码评审
description: 只挑毛病
model: 甲
mode: readonly
style: concise
---

你是一个刻薄但准确的评审。
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for mod, attr, value in (
            (bots, "DIR", os.path.join(self.tmp.name, "bots")),
            (profiles, "DIR", os.path.join(self.tmp.name, "profiles")),
            (keys, "STORE", os.path.join(self.tmp.name, "models.json")),
            (keys, "STATE", os.path.join(self.tmp.name, "state.json")),
            (permissions, "STORE", os.path.join(self.tmp.name, "permissions.json")),
            (tools_mod, "IMAGE_CONFIG", os.path.join(self.tmp.name, "images.json")),
            (session, "DIR", os.path.join(self.tmp.name, "sessions")),
        ):
            before = getattr(mod, attr)
            setattr(mod, attr, value)
            self.addCleanup(setattr, mod, attr, before)
        self.agent = Feiyu(Path(self.tmp.name))
        self.agent.backend = FakeBackend([])
        self.agent.name = "甲"

    def put(self, stem, text=FULL):
        os.makedirs(bots.DIR, exist_ok=True)
        with open(bots._path(stem), "w", encoding="utf-8") as f:
            f.write(text)


class TestFile(Base):
    def test_读得出_frontmatter_里那几项(self):
        self.put("代码评审")
        one = bots.load()[0]
        self.assertEqual((one.name, one.model, one.mode, one.style),
                         ("代码评审", "甲", "readonly", "concise"))
        self.assertIn("刻薄", one.body)

    def test_名字以文件名为准(self):
        """理由同 profiles：列得出来的每一条都得删得掉、存得回去 """
        self.put("我的机器人", FULL)
        self.assertEqual([b.name for b in bots.load()], ["我的机器人"])
        self.assertTrue(bots.remove("我的机器人"))
        self.assertEqual(bots.load(), [])

    def test_名字不合法一律拒(self):
        for bad in ("../跑出去", "a/b", "", " ", "x" * 41):
            self.assertFalse(bots.write(bad, "x"), bad)
            self.assertFalse(bots.remove(bad), bad)

    def test_只改正文时别的字段留着(self):
        bots.write("甲", "旧", description="说明", model="m", mode="auto")
        bots.write("甲", "新")
        one = bots.get("甲")
        self.assertEqual((one.body, one.description, one.model, one.mode),
                         ("新", "说明", "m", "auto"))

    def test_超长正文截到上限(self):
        bots.write("长的", "字" * (bots.MAX_BODY + 500))
        self.assertLessEqual(len(bots.get("长的").body), bots.MAX_BODY)


class TestStart(Base):
    def test_套上之后模式风格都跟着换(self):
        self.put("代码评审")
        self.assertEqual(self.agent.start_bot("代码评审"), "代码评审")
        self.assertEqual(self.agent.mode, "readonly")
        self.assertEqual(self.agent.style, "concise")
        self.assertIn("刻薄", self.agent.backend.system)

    def test_没写的字段不动现在的(self):
        self.put("光有人格", "---\nname: x\n---\n\n只有正文\n")
        self.agent.mode = "auto"
        self.agent.start_bot("光有人格")
        self.assertEqual(self.agent.mode, "auto")

    def test_摘掉就把人格拿走(self):
        self.put("代码评审")
        self.agent.start_bot("代码评审")
        self.agent.start_bot("")
        self.assertEqual(self.agent.bot, "")
        self.assertNotIn("刻薄", self.agent.backend.system)

    def test_没这个机器人回空串_且什么都不改(self):
        self.agent.mode = "plan"
        self.assertEqual(self.agent.start_bot("没这个"), "")
        self.assertEqual(self.agent.mode, "plan")
        self.assertEqual(self.agent.bot, "")

    def test_机器人和档案不会同时套着(self):
        """人格那个位置只有一个 —— 同时套两个只会互相打架 """
        self.put("代码评审")
        profiles.write("温柔", "你说话很温柔")
        self.agent.set_profile("温柔")
        self.agent.start_bot("代码评审")
        self.assertEqual(self.agent.profile, "")
        self.assertIn("刻薄", self.agent.backend.system)
        self.assertNotIn("温柔", self.agent.backend.system)

        self.agent.set_profile("温柔")
        self.assertEqual(self.agent.bot, "")
        self.assertIn("温柔", self.agent.backend.system)
        self.assertNotIn("刻薄", self.agent.backend.system)


class TestRemembered(Base):
    """一段对话记得自己是哪个机器人开的 —— 不然切回来人格就没了，
    而前面说过的话全是按那个人格说的 """

    def agentish(self):
        b = FakeBackend([])
        b.messages += [{"role": "user", "content": "你好"},
                       {"role": "assistant", "content": "在"}]
        self.agent.backend = b
        return self.agent

    def test_存盘带上机器人_恢复时套回去(self):
        self.put("代码评审")
        one = self.agentish()
        one.start_bot("代码评审")
        session.save(Path(self.tmp.name), one, "20260101-000000")
        data = session.load(Path(self.tmp.name), "20260101-000000")
        self.assertEqual(data["bot"], "代码评审")

        fresh = Feiyu(Path(self.tmp.name))
        fresh.backend = FakeBackend([])
        fresh.use_model = lambda n: True
        fresh.restore(data)
        self.assertEqual(fresh.bot, "代码评审")

    def test_机器人被删了就当没套(self):
        self.put("代码评审")
        one = self.agentish()
        one.start_bot("代码评审")
        session.save(Path(self.tmp.name), one, "20260101-000000")
        data = session.load(Path(self.tmp.name), "20260101-000000")
        bots.remove("代码评审")
        fresh = Feiyu(Path(self.tmp.name))
        fresh.backend = FakeBackend([])
        fresh.use_model = lambda n: True
        fresh.restore(data)
        self.assertEqual(fresh.bot, "")


class TestSlashCommand(Base):
    """终端的 `/bot` **只在图形界面里能开机器人的话，没有图形界面的机器就用不了它**
    （跟 0.56.0 给 `/skills <名字>` 留一条终端路子同一个理由）。"""

    def run_cmd(self, arg):
        import contextlib
        import io as _io

        from feiyu import __main__ as cli
        with contextlib.redirect_stdout(_io.StringIO()):
            return cli._bot(self.agent, Path(self.tmp.name), arg)

    def test_开一个机器人就是开一段新对话(self):
        self.put("代码评审")
        before = self.agent.session_id = session.new_id()
        self.agent.backend.messages.append({"role": "user", "content": "旧那段"})
        said = self.run_cmd("代码评审")
        self.assertEqual(self.agent.bot, "代码评审")
        self.assertNotEqual(self.agent.session_id, before)
        # 旧那段的正文不能跟进新会话（不 reset 的话它会被原样重发一遍）
        self.assertNotIn("旧那段", str(self.agent.backend.messages))
        self.assertIn("代码评审", said)
        # 旧那段留在盘上，不是 /clear
        self.assertIn(before, [row["id"] for row in session.listing(Path(self.tmp.name))])

    def test_名字不对就什么都不动(self):
        self.agent.session_id = before = session.new_id()
        said = self.run_cmd("没这个")
        self.assertEqual(self.agent.bot, "")
        self.assertEqual(self.agent.session_id, before)
        self.assertIn("没有叫", said)

    def test_off_摘掉人格也照样开新的(self):
        self.put("代码评审")
        self.run_cmd("代码评审")
        before = self.agent.session_id
        self.run_cmd("off")
        self.assertEqual(self.agent.bot, "")
        self.assertNotEqual(self.agent.session_id, before)

    def test_不带参数就是列一遍_不动任何状态(self):
        self.put("代码评审")
        self.agent.session_id = before = session.new_id()
        said = self.run_cmd("")
        self.assertIn("代码评审", said)
        self.assertEqual(self.agent.session_id, before)
        self.assertEqual(self.agent.bot, "")
