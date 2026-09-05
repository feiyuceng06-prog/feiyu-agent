"""读图片：缩图、两种后端的消息形状、以及 base64 不该出现的地方 """

import json
import tempfile
import unittest
from pathlib import Path

from feiyu import images, providers, session
from feiyu.tools import Toolbox


def make(path: Path, size, mode="RGB", color=(200, 100, 60)) -> Path:
    from PIL import Image
    Image.new(mode, size, color if mode != "RGBA" else color + (255,)).save(path)
    return path


class TestKind(unittest.TestCase):
    def test_认得的扩展名(self):
        for name, media in (("a.png", "image/png"), ("a.JPG", "image/jpeg"),
                            ("a.jpeg", "image/jpeg"), ("a.gif", "image/gif"),
                            ("a.webp", "image/webp")):
            self.assertEqual(images.kind(Path(name)), media, name)

    def test_不是图片的返回_None(self):
        for name in ("a.py", "a.pdf", "a.txt", "a"):
            self.assertIsNone(images.kind(Path(name)))


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def size_of(self, data: str):
        import base64, io
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(data))) as img:
            return img.size

    def test_小图原样发(self):
        p = make(self.dir / "a.png", (64, 48))
        media, data, note = images.load(p)
        self.assertEqual(media, "image/png")
        self.assertIn("64×48", note)
        self.assertEqual(self.size_of(data), (64, 48))

    def test_大图缩到长边上限之内(self):
        p = make(self.dir / "a.png", (images.MAX_EDGE * 2, images.MAX_EDGE // 2))
        _, data, note = images.load(p)
        self.assertLessEqual(max(self.size_of(data)), images.MAX_EDGE)
        self.assertIn("缩过", note)
        # 说明里报的是原始尺寸，不然人对不上自己那张图
        self.assertIn("%d×" % (images.MAX_EDGE * 2), note)

    def test_长宽比不变(self):
        p = make(self.dir / "a.png", (3000, 1500))
        _, data, _ = images.load(p)
        w, h = self.size_of(data)
        self.assertAlmostEqual(w / h, 2.0, places=1)

    def test_有透明通道的走_png(self):
        # 转 JPEG 会把透明的地方糊成黑块
        p = make(self.dir / "a.png", (2400, 2400), mode="RGBA")
        media, _, _ = images.load(p)
        self.assertEqual(media, "image/png")

    def test_不透明的大图走_jpeg(self):
        p = make(self.dir / "a.png", (2400, 2400))
        media, _, _ = images.load(p)
        self.assertEqual(media, "image/jpeg")

    def test_字节数超上限会继续缩(self):
        self.addCleanup(setattr, images, "MAX_BYTES", images.MAX_BYTES)
        import base64
        images.MAX_BYTES = 3000
        p = make(self.dir / "a.png", (1200, 1200))
        _, data, _ = images.load(p)
        self.assertLessEqual(len(base64.b64decode(data)), 3000)


class TestReadFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.box = Toolbox(self.root)
        make(self.root / "图.png", (32, 32))

    def tearDown(self):
        self.tmp.cleanup()

    def test_没标_vision_就明说而不是发出去(self):
        out = self.box.run("read_file", {"path": "图.png"})
        self.assertTrue(out.is_error)
        self.assertIsNone(out.image)
        self.assertIn("vision", out.content)

    def test_标了就把图带上(self):
        self.box.vision = True
        out = self.box.run("read_file", {"path": "图.png"})
        self.assertFalse(out.is_error)
        self.assertEqual(out.image[0], "image/png")
        self.assertNotIn(out.image[1][:40], out.content)   # base64 不进正文

    def test_坏图不崩(self):
        (self.root / "坏.png").write_bytes("这不是 png".encode("utf-8"))
        self.box.vision = True
        out = self.box.run("read_file", {"path": "坏.png"})
        self.assertTrue(out.is_error)
        self.assertIn("打不开", out.content)

    def test_图片不再被当成二进制拒掉(self):
        self.box.vision = True
        self.assertNotIn("二进制", self.box.run("read_file", {"path": "图.png"}).content)


class TestMessageShape(unittest.TestCase):
    def output(self):
        return providers.ToolOutput(id="c1", content="（一张图）", image=("image/png", "AAAA"))

    def test_anthropic_放进_tool_result(self):
        b = object.__new__(providers.AnthropicBackend)
        b.messages = []
        b.add_tool_results([self.output()])
        blocks = b.messages[0]["content"][0]["content"]
        self.assertEqual([x["type"] for x in blocks], ["text", "image"])
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")

    def test_openai_另起一条_user(self):
        # tool 角色的 content 只能是纯文本
        b = object.__new__(providers.OpenAICompatBackend)
        b.messages = []
        b.add_tool_results([self.output()])
        self.assertEqual([m["role"] for m in b.messages], ["tool", "user"])
        self.assertIsInstance(b.messages[0]["content"], str)
        self.assertEqual(b.messages[1]["content"][1]["image_url"]["url"],
                         "data:image/png;base64,AAAA")

    def test_没有图就不多一条_user(self):
        b = object.__new__(providers.OpenAICompatBackend)
        b.messages = []
        b.add_tool_results([providers.ToolOutput(id="c1", content="ok")])
        self.assertEqual([m["role"] for m in b.messages], ["tool"])


class TestStripped(unittest.TestCase):
    """图片进了历史就一直在 摘要和存盘里都不该带 base64 """

    def test_两种形状都剥得掉(self):
        raw = [
            {"role": "user", "content": [{"type": "image", "source": {"data": "XXXX"}}]},
            {"role": "user", "content": [{"type": "image_url",
                                          "image_url": {"url": "data:image/png;base64,XXXX"}}]},
        ]
        text = json.dumps(providers.without_images(raw), ensure_ascii=False)
        self.assertNotIn("XXXX", text)
        self.assertIn(providers.IMAGE_NOTE, text)

    def test_别的内容原样留着(self):
        raw = [{"role": "assistant", "content": "说了句话"},
               {"role": "tool", "tool_call_id": "c1", "content": "工具结果"}]
        self.assertEqual(providers.without_images(raw), raw)

    def test_存盘里没有_base64(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, session, "DIR", session.DIR)
        session.DIR = str(Path(tmp.name) / "sessions")

        b = object.__new__(providers.OpenAICompatBackend)
        b.messages = [{"role": "system", "content": "s"}]
        b.add_tool_results([providers.ToolOutput(
            id="c1", content="（一张图）", image=("image/png", "SHOULDNOTAPPEAR"))])
        b.tokens_total = b.tokens_context = 0
        b.cost, b.priced = 0.0, False

        agent = type("A", (), {})()
        agent.ready, agent.backend = True, b
        agent.name, agent.style, agent.mode = "fake", "default", "default"
        agent.toolbox = type("T", (), {"todos": type("D", (), {"items": []})()})()
        session.save(Path(tmp.name), agent)
        with open(session._legacy_path(Path(tmp.name)), encoding="utf-8") as f:
            saved = f.read()
        self.assertNotIn("SHOULDNOTAPPEAR", saved)
        self.assertIn(providers.IMAGE_NOTE, saved)
