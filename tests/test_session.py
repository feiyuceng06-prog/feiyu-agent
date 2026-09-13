"""会话存盘 存的是完整对话原文，所以既要能存回来，也要 0600 """

import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from feiyu import keys as keys_mod
from feiyu import session
from tests.fakes import FakeBackend


class Boxish:
    def __init__(self):
        from feiyu.todo import Todos
        self.todos = Todos()


class Agentish:
    """session.save 只用到这几样，不必真起一个 Feiyu """
    def __init__(self, backend, name="fake"):
        self.backend = backend
        self.name = name
        self.style = "concise"
        self.mode = "readonly"
        self.ready = True
        self.toolbox = Boxish()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = session.DIR
        session.DIR = os.path.join(self.tmp.name, "sessions")
        self.ws = Path(self.tmp.name) / "ws"

    def tearDown(self):
        session.DIR = self._saved
        self.tmp.cleanup()


class TestRoundTrip(Base):
    def make(self):
        b = FakeBackend([])
        b.messages += [{"role": "user", "content": "你好"},
                       {"role": "assistant", "content": "在"}]
        b.tokens_total, b.tokens_context, b.cost, b.priced = 500, 300, 1.25, True
        return b

    def test_清单也一起存(self):
        a = Agentish(self.make())
        a.toolbox.todos.replace([{"text": "改 parser", "status": "doing"}])
        session.save(self.ws, a)
        self.assertEqual(session.load(self.ws)["todos"][0]["text"], "改 parser")

    def test_存了能读回来(self):
        session.save(self.ws, Agentish(self.make()))
        data = session.load(self.ws)
        self.assertEqual(data["model"], "fake")
        self.assertEqual(data["style"], "concise")
        self.assertEqual(data["mode"], "readonly")
        self.assertEqual(data["tokens_total"], 500)
        self.assertEqual(data["cost"], 1.25)
        self.assertEqual(len(data["messages"]), 3)

    def test_权限是_600(self):
        session.save(self.ws, Agentish(self.make()))
        mode = stat.S_IMODE(os.stat(session._legacy_path(self.ws)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_不同工作区互不干扰(self):
        session.save(self.ws, Agentish(self.make()))
        other = Path(self.tmp.name) / "另一个"
        self.assertIsNone(session.load(other))

    def test_只有系统提示时不存(self):
        session.save(self.ws, Agentish(FakeBackend([])))
        self.assertIsNone(session.load(self.ws))

    def test_没有模型时不存(self):
        a = Agentish(self.make())
        a.ready = False
        session.save(self.ws, a)
        self.assertIsNone(session.load(self.ws))

    def test_删掉(self):
        session.save(self.ws, Agentish(self.make()))
        session.drop(self.ws)
        self.assertIsNone(session.load(self.ws))

    def test_删不存在的不崩(self):
        session.drop(self.ws)

    def test_给了_id_就写进那一份(self):
        session.save(self.ws, Agentish(self.make()), "20260101-000000")
        rows = session.listing(self.ws)
        self.assertEqual([r["id"] for r in rows], ["20260101-000000"])

    def test_一个目录能存好几份_新的在前(self):
        for sid in ("20260101-000000", "20260102-000000", "20260103-000000"):
            session.save(self.ws, Agentish(self.make()), sid)
        rows = session.listing(self.ws)
        self.assertEqual([r["id"] for r in rows],
                         ["20260103-000000", "20260102-000000", "20260101-000000"])
        # 不给 sid 时读到的是最近那一份
        self.assertEqual(session.latest_id(self.ws), "20260103-000000")

    def test_标题取人说的第一句(self):
        b = FakeBackend([])
        b.messages = [{"role": "system", "content": "系统"},
                      {"role": "user", "content": "把 parser 拆一下"},
                      {"role": "assistant", "content": "好"}]
        session.save(self.ws, Agentish(b), "20260101-000000")
        self.assertEqual(session.listing(self.ws)[0]["title"], "把 parser 拆一下")

    def test_标题只取第一段(self):
        """机器拼在后面的东西不许进标题 —— 桌面端的附件说明、`@路径` 附的
        整份文件正文都是用空行隔开接在后面的，拍平了截 60 个字的话，一列
        会话会全长成同一句「我上传了这些文件（相对工作区）：…」"""
        for said, want in (
                ("看看这张图\n\n我上传了这些文件（相对工作区）：\n- a.png", "看看这张图"),
                ("照着 @feiyu/ui.py 改\n\n----- feiyu/ui.py -----\n1\timport json",
                 "照着 @feiyu/ui.py 改"),
                ("  \n\n开头是空行", "开头是空行"),
                ("第一行\n第二行", "第一行 第二行")):
            b = FakeBackend([])
            b.messages = [{"role": "system", "content": "系统"},
                          {"role": "user", "content": said},
                          {"role": "assistant", "content": "好"}]
            session.save(self.ws, Agentish(b), "20260101-000000")
            self.assertEqual(session.listing(self.ws)[0]["title"], want)

    def test_标题不要把工具结果当成人说的话(self):
        """anthropic 那边 tool_result 是挂在 user 消息里的 """
        b = FakeBackend([])
        b.messages = [{"role": "system", "content": "系统"},
                      {"role": "user", "content": [{"type": "tool_result", "content": "读取 3 行"}]},
                      {"role": "user", "content": [{"type": "text", "text": "这才是我说的"}]}]
        session.save(self.ws, Agentish(b), "20260101-000000")
        self.assertEqual(session.listing(self.ws)[0]["title"], "这才是我说的")

    def test_老位置那份照样读得到(self):
        """升级之后 feiyu --resume 不能突然说没有上次的对话 """
        session.save(self.ws, Agentish(self.make()))          # 不给 sid = 写老位置
        rows = session.listing(self.ws)
        self.assertEqual([r["id"] for r in rows], [session.LEGACY])
        self.assertIsNotNone(session.load(self.ws, session.LEGACY))
        self.assertIsNotNone(session.load(self.ws))

    def test_超过上限从最早的删(self):
        for n in range(session.MAX_KEPT + 5):
            session.save(self.ws, Agentish(self.make()), "2026%04d-000000" % n)
        rows = session.listing(self.ws)
        self.assertEqual(len(rows), session.MAX_KEPT)
        self.assertNotIn("20260000-000000", [r["id"] for r in rows])

    def test_删指定的那一份(self):
        session.save(self.ws, Agentish(self.make()), "20260101-000000")
        session.save(self.ws, Agentish(self.make()), "20260102-000000")
        session.drop(self.ws, "20260101-000000")
        self.assertEqual([r["id"] for r in session.listing(self.ws)], ["20260102-000000"])

    def test_id_不合法一律不认(self):
        """id 会拼进路径，`../` 这种要挡住 """
        for bad in ("../../etc/passwd", "a/b", "", "x" * 50, "有中文"):
            session.save(self.ws, Agentish(self.make()), bad)
            self.assertIsNone(session.load(self.ws, bad), bad)
        self.assertEqual(session.listing(self.ws), [])

    def test_坏文件当作没有(self):
        os.makedirs(session.DIR, exist_ok=True)
        with open(session._legacy_path(self.ws), "w", encoding="utf-8") as f:
            f.write("{ 坏了")
        self.assertIsNone(session.load(self.ws))


class TestPlain(Base):
    class Obj:
        def __init__(self, data):
            self._data = data
        def model_dump(self):
            return self._data

    def test_摊平_sdk_对象(self):
        # anthropic 的 messages 里是 SDK 对象，不摊平 json.dump 会炸
        out = session._plain({"role": "assistant",
                              "content": [self.Obj({"type": "text", "text": "你好"})]})
        self.assertEqual(out["content"][0], {"type": "text", "text": "你好"})
        json.dumps(out)

    def test_嵌套的也摊(self):
        out = session._plain([self.Obj({"a": self.Obj({"b": 1})})])
        self.assertEqual(out, [{"a": {"b": 1}}])

    def test_普通结构原样(self):
        self.assertEqual(session._plain({"a": [1, "x", None]}), {"a": [1, "x", None]})

    def test_存盘会先摊平(self):
        b = FakeBackend([])
        b.messages.append({"role": "assistant",
                           "content": [self.Obj({"type": "text", "text": "你好"})]})
        session.save(self.ws, Agentish(b))
        self.assertIn("你好", json.dumps(session.load(self.ws)["messages"], ensure_ascii=False))


class TestDescribe(Base):
    def test_有模型名和条数(self):
        text = session.describe({"model": "kimi", "messages": [1, 2, 3], "saved_at": 0})
        self.assertIn("kimi", text)
        self.assertIn("3 条消息", text)

    def test_字段缺了也不崩(self):
        self.assertIn("?", session.describe({}))


class TestKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._store, self._state = keys_mod.STORE, keys_mod.STATE
        keys_mod.STORE = os.path.join(self.tmp.name, "models.json")
        keys_mod.STATE = os.path.join(self.tmp.name, "state.json")
        keys_mod._DIR = self.tmp.name

    def tearDown(self):
        keys_mod.STORE, keys_mod.STATE = self._store, self._state
        self.tmp.cleanup()

    def test_加了能读回来(self):
        keys_mod.add("kimi", base_url="https://x", model="kimi-k2", key="sk-abc")
        entry = keys_mod.load()["kimi"]
        self.assertEqual(entry["model"], "kimi-k2")
        self.assertEqual(entry["key"], "sk-abc")

    def test_密钥文件是_600(self):
        keys_mod.add("kimi", base_url="https://x", model="kimi-k2", key="sk-abc")
        self.assertEqual(stat.S_IMODE(os.stat(keys_mod.STORE).st_mode), 0o600)

    def test_删掉(self):
        keys_mod.add("kimi", base_url="https://x", model="k", key="sk")
        self.assertTrue(keys_mod.remove("kimi"))
        self.assertFalse(keys_mod.remove("kimi"))
        self.assertEqual(keys_mod.load(), {})

    def test_上次用的会记下来(self):
        keys_mod.add("a", base_url="", model="m", key="k")
        keys_mod.add("b", base_url="", model="m", key="k")
        keys_mod.set_last("b")
        self.assertEqual(keys_mod.pick_default(), "b")

    def test_上次那条被删了就退回第一条(self):
        keys_mod.add("a", base_url="", model="m", key="k")
        keys_mod.add("b", base_url="", model="m", key="k")
        keys_mod.set_last("b")
        keys_mod.remove("b")
        self.assertEqual(keys_mod.pick_default(), "a")

    def test_清单为空时没有默认(self):
        self.assertEqual(keys_mod.pick_default(), "")

    def test_遮住密钥(self):
        masked = keys_mod.mask("sk-1234567890abcdef")
        self.assertNotIn("234567890abc", masked)

    def test_没有内置厂商或价格表(self):
        # 模型 id 退役很勤，写死的表迟早给出错的报价
        self.assertEqual(keys_mod.load(), {})


if __name__ == "__main__":
    unittest.main()


class TestLegacyId(Base):
    """`legacy` 是个**读旧位置的别名**，不能同时又是一个真的文件名

    两边不对称的后果是一行删不掉的会话：listing 从新目录里扫出 legacy.json、
    给它 id `legacy`，而 drop 见到这个 id 只去删旧位置那份 —— 删不掉，
    界面却照样回一句「删掉了一段对话」。用户拿截图来问过。
    """

    def agent(self):
        b = FakeBackend([])
        b.messages += [{"role": "user", "content": "你好"},
                       {"role": "assistant", "content": "在"}]
        return Agentish(b)

    def test_存不出_legacy_json(self):
        session.save(self.ws, self.agent(), session.LEGACY)
        self.assertFalse(os.path.exists(session._path(self.ws, session.LEGACY)))
        self.assertTrue(os.path.exists(session._legacy_path(self.ws)))
        # 照样列得出来、读得回来
        self.assertEqual([r["id"] for r in session.listing(self.ws)], [session.LEGACY])
        self.assertTrue(session.load(self.ws, session.LEGACY))

    def test_旧版留下的_legacy_json_删得掉(self):
        """0.55.0 以前的 save 会往新目录里写 legacy.json，那份也得删得掉 """
        os.makedirs(session.folder(self.ws), exist_ok=True)
        with open(session._path(self.ws, session.LEGACY), "w", encoding="utf-8") as f:
            json.dump({"messages": [{"role": "user", "content": "你好"}], "saved_at": 1}, f)
        self.assertEqual([r["id"] for r in session.listing(self.ws)], [session.LEGACY])
        session.drop(self.ws, session.LEGACY)
        self.assertEqual(session.listing(self.ws), [])

    def test_两个位置都有的时候一起删(self):
        os.makedirs(session.folder(self.ws), exist_ok=True)
        for path in (session._path(self.ws, session.LEGACY), session._legacy_path(self.ws)):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"messages": [{"role": "user", "content": "x"}], "saved_at": 1}, f)
        session.drop(self.ws, session.LEGACY)
        self.assertEqual(session.listing(self.ws), [])

    def test_删别的不碰旧位置那份(self):
        os.makedirs(session.folder(self.ws), exist_ok=True)
        with open(session._legacy_path(self.ws), "w", encoding="utf-8") as f:
            json.dump({"messages": [{"role": "user", "content": "x"}], "saved_at": 1}, f)
        with open(session._path(self.ws, "20260101-000000"), "w", encoding="utf-8") as f:
            json.dump({"messages": [{"role": "user", "content": "y"}], "saved_at": 2}, f)
        session.drop(self.ws, "20260101-000000")
        self.assertEqual([r["id"] for r in session.listing(self.ws)], [session.LEGACY])


class TestCard(Base):
    """侧边栏那张小卡片

    `listing()` 原来把每一份存盘整个 json.loads 一遍，只为了拿一行标题：
    40 份长会话（96 MB）要 131 ms。卡片是**派生数据**，坏了 / 没有 / 过期
    都退回读整份 —— 它是缓存，不是第二份真相。
    """

    def agent(self, said="你好"):
        b = FakeBackend([])
        b.messages += [{"role": "user", "content": said},
                       {"role": "assistant", "content": "在"}]
        return Agentish(b)

    def test_存的时候顺手写一张(self):
        session.save(self.ws, self.agent(), "20260101-000000")
        path = session._path(self.ws, "20260101-000000")
        self.assertTrue(os.path.exists(session._card_path(path)))
        self.assertEqual(os.stat(session._card_path(path)).st_mode & 0o777, 0o600)

    def test_列表不读整份存盘(self):
        session.save(self.ws, self.agent("我说一句很长的话"), "20260101-000000")
        real = session._read
        # legacy 那条路照旧要读（它没有卡片），只盯新目录里的
        folder = session.folder(self.ws)
        # **不能用 startswith**：旧位置是 DIR/<hash>.json，正好以 DIR/<hash> 开头
        session._read = lambda p: (self.fail("不该读整份：" + p)
                                   if os.path.dirname(p) == folder else real(p))
        self.addCleanup(setattr, session, "_read", real)
        rows = session.listing(self.ws)
        self.assertEqual(len(rows), 1)
        self.assertIn("我说一句很长的话", rows[0]["title"])
        self.assertEqual(rows[0]["count"], 3)

    def test_没有卡片就退回读整份(self):
        session.save(self.ws, self.agent(), "20260101-000000")
        os.remove(session._card_path(session._path(self.ws, "20260101-000000")))
        self.assertEqual([r["id"] for r in session.listing(self.ws)], ["20260101-000000"])

    def test_卡片坏了也退回读整份(self):
        session.save(self.ws, self.agent(), "20260101-000000")
        card = session._card_path(session._path(self.ws, "20260101-000000"))
        with open(card, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        self.assertEqual([r["id"] for r in session.listing(self.ws)], ["20260101-000000"])

    def test_存盘比卡片新就不信卡片(self):
        """别人（或者老版本）直接改了存盘，卡片就过期了 """
        session.save(self.ws, self.agent("旧标题"), "20260101-000000")
        path = session._path(self.ws, "20260101-000000")
        data = json.load(open(path, encoding="utf-8"))
        data["title"] = "新标题"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.utime(path, (time.time() + 10, time.time() + 10))
        self.assertEqual(session.listing(self.ws)[0]["title"], "新标题")

    def test_卡片自己不算一份会话(self):
        session.save(self.ws, self.agent(), "20260101-000000")
        self.assertEqual(len(session.listing(self.ws)), 1)

    def test_删存盘要把卡片一起带走(self):
        session.save(self.ws, self.agent(), "20260101-000000")
        path = session._path(self.ws, "20260101-000000")
        session.drop(self.ws, "20260101-000000")
        self.assertFalse(os.path.exists(session._card_path(path)))

    def test_数上限时不能把卡片算进去(self):
        """算进去的话一份存盘顶两个，实际只留一半 """
        for n in range(session.MAX_KEPT + 3):
            session.save(self.ws, self.agent(), "202601%02d-000000" % (n + 1))
        left = [n for n in os.listdir(session.folder(self.ws))
                if n.endswith(".json") and not n.endswith(session.CARD_SUFFIX)]
        self.assertEqual(len(left), session.MAX_KEPT)
        # 清掉的那几份不留孤儿卡片
        cards = [n for n in os.listdir(session.folder(self.ws))
                 if n.endswith(session.CARD_SUFFIX)]
        self.assertEqual(len(cards), session.MAX_KEPT)


class TestNewId(unittest.TestCase):
    """**同一秒里连开两段不能撞。** 时间戳只到秒，而"开一段新的"是一下点出来的
    （侧栏点两个机器人、连按两次新对话）—— 撞了的话第二段的存盘直接盖掉第一段。"""

    def test_连着要三个都不一样(self):
        got = [session.new_id() for _ in range(3)]
        self.assertEqual(len(set(got)), 3, got)

    def test_都还是合法的_id(self):
        for sid in [session.new_id() for _ in range(3)]:
            self.assertTrue(session._ID_OK.match(sid), sid)

    def test_头一个不带后缀_排序还是按时间(self):
        session._last_id = ""
        first = session.new_id()
        self.assertRegex(first, r"^\d{8}-\d{6}$")
        self.assertTrue(session.new_id() > first)


class TestBrokenFiles(unittest.TestCase):
    """一份写坏的存盘不许把整份侧边栏带走

    盘上的东西不保证是数：写到一半被 kill、手改过、别的版本留下的都可能。
    原来 `saved_at` 是个字符串就足够让 `listing()` 抛 TypeError ——
    **一条历史都列不出来**，而用户看到的是"侧边栏空了"。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        before = session.DIR
        session.DIR = os.path.join(self.tmp.name, "sessions")
        self.addCleanup(setattr, session, "DIR", before)
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        self.here = Path(session.folder(self.ws))
        self.here.mkdir(parents=True)

    def put(self, name, body):
        (self.here / name).write_text(body, encoding="utf-8")

    def test_坏的_saved_at_不影响别的(self):
        self.put("20260101-000001.json", '{"messages":[{"role":"user","content":"坏"}], '
                                         '"saved_at":"不是数"}')
        self.put("20260101-000002.json", '{"messages":[{"role":"user","content":"好"}], '
                                         '"saved_at":1788000000}')
        rows = session.listing(self.ws)               # 不该抛
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "好")      # 好的那份排前面

    def test_大到离谱的时间戳也不抛(self):
        """1e300 喂给 time.localtime 是 OverflowError，照样一条都列不出来 """
        self.put("20260101-000001.json", '{"messages":[{"role":"user","content":"x"}], '
                                         '"saved_at":1e300}')
        self.assertEqual(len(session.listing(self.ws)), 1)

    def test_messages_不是列表就当这份坏了(self):
        """只判真值的话这个字符串会被装进 backend.messages，下一次请求直接 400 """
        self.put("20260101-000001.json", '{"messages":"不是列表"}')
        self.assertEqual(session.listing(self.ws), [])
        self.assertIsNone(session.load(self.ws, "20260101-000001"))

    def test_标题和条数不是那个类型时不崩(self):
        self.put("20260101-000001.json", '{"messages":[{"role":"user","content":"好"}], '
                                         '"title":["列表"], "count":"多", "model":7}')
        row = session.listing(self.ws)[0]
        self.assertEqual(row["title"], "好")
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["model"], "")

    def test_content_不是字符串时不拿它当标题(self):
        self.put("20260101-000001.json", '{"messages":[{"role":"user","content":{"x":1}}]}')
        self.assertEqual(session.listing(self.ws)[0]["title"], "（没有标题）")


class TestDescribeBroken(unittest.TestCase):
    """`describe()` 也要挡住坏数据 —— 它比 `listing()` 更疼

    `listing()` 坏了是侧边栏空一块；`describe()` 在**启动路径**上
    （`__main__` 里 `--resume` 和「这个目录有上次的对话」那两句都不带兜底），
    一抛 TypeError 就是 **feiyu 在这个目录里根本起不来**，连 /clear 都够不着。
    `_num()` 当初就是为这条写的，只是漏了这一处。
    """

    def probe(self, data):
        text = session.describe(data)
        self.assertIsInstance(text, str)
        return text

    def test_saved_at_是什么都不崩(self):
        for bad in ("不是数", None, True, [], {}, 1e300, -1e300, float("nan")):
            with self.subTest(saved_at=bad):
                self.probe({"model": "m", "messages": [], "saved_at": bad})

    def test_model_和_messages_的类型也兜住(self):
        self.assertIn("? ", self.probe({"model": 7, "messages": "坏了"}))
        self.assertIn("0 条消息", self.probe({"model": 7, "messages": "坏了"}))
