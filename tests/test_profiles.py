"""配置档案

最要紧的一条：**名字只有文件名一个来源**。frontmatter 里再写一个 name 的话，
面板上那一行删不掉也存不对 —— 跟 0.56.1 那条删不掉的会话是同一类错。
"""

import os
import tempfile
import unittest

from feiyu import keys, profiles


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for mod, attr, value in (
            (profiles, "DIR", os.path.join(self.tmp.name, "profiles")),
            (keys, "STATE", os.path.join(self.tmp.name, "state.json")),
        ):
            before = getattr(mod, attr)
            setattr(mod, attr, value)
            self.addCleanup(setattr, mod, attr, before)

    def put(self, stem, text):
        os.makedirs(profiles.DIR, exist_ok=True)
        with open(profiles._path(stem), "w", encoding="utf-8") as f:
            f.write(text)


class TestNameIsTheFilename(Base):
    def test_frontmatter_里的_name_不算数(self):
        self.put("我的档案", "---\nname: 严厉评审\ndescription: d\n---\n\n正文\n")
        self.assertEqual([p.name for p in profiles.load()], ["我的档案"])

    def test_两个文件写同一个_name_也不会撞成一条(self):
        self.put("甲", "---\nname: 撞名\n---\n\nA\n")
        self.put("乙", "---\nname: 撞名\n---\n\nB\n")
        rows = {p.name: p.body for p in profiles.load()}
        self.assertEqual(rows, {"甲": "A", "乙": "B"})

    def test_列出来的每一条都删得掉(self):
        """列表和删除必须用同一个名字来源，否则就是一行删不掉的东西 """
        self.put("我的档案", "---\nname: 别的名字\n---\n\n正文\n")
        for row in profiles.load():
            self.assertTrue(profiles.remove(row.name), row.name)
        self.assertEqual(profiles.load(), [])

    def test_列出来的每一条都存得回去(self):
        self.put("我的档案", "---\nname: 别的名字\nmodel: m\n---\n\n旧的\n")
        row = profiles.load()[0]
        self.assertTrue(profiles.write(row.name, "新的"))
        again = profiles.load()
        self.assertEqual(len(again), 1, [p.name for p in again])
        self.assertEqual(again[0].body, "新的")
        self.assertEqual(again[0].model, "m")      # 只换正文，别的留着


class TestWrite(Base):
    def test_名字不合法一律拒(self):
        for bad in ("../跑出去", "a/b", "", " ", "x" * 41, "a b"):
            self.assertFalse(profiles.write(bad, "x"), bad)
            self.assertFalse(profiles.remove(bad), bad)

    def test_超长正文截到上限(self):
        profiles.write("长的", "字" * (profiles.MAX_BODY + 500))
        self.assertLessEqual(len(profiles.get("长的").body), profiles.MAX_BODY)

    def test_删掉正在用的那份要清掉_current(self):
        profiles.write("甲", "x")
        profiles.set_current("甲")
        profiles.remove("甲")
        self.assertEqual(profiles.current(), "")

    def test_current_认不出的名字回空串(self):
        keys._set_state(profile="早就删了")
        self.assertEqual(profiles.current(), "")


class TestPrompt(Base):
    def test_没选就是空串(self):
        self.assertEqual(profiles.as_prompt(""), "")
        self.assertEqual(profiles.as_prompt("没这个"), "")

    def test_正文空的也不占地方(self):
        profiles.write("空的", "")
        self.assertEqual(profiles.as_prompt("空的"), "")

    def test_选了就带上正文和名字(self):
        profiles.write("严厉", "只挑毛病")
        said = profiles.as_prompt("严厉")
        self.assertIn("只挑毛病", said)
        self.assertIn("严厉", said)
