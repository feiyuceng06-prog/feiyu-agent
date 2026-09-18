"""Firecrawl 后端：什么时候回退、什么时候**不**回退、以及它照样要过内网拦截

不出网 builtin 那侧假掉 `_fetch_raw`，Firecrawl 那侧假掉 `_firecrawl_post`，
中间的分发、回退和解析都是真的
"""

import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from feiyu import tools
from feiyu.tools import Toolbox

SCRAPED = {"success": True, "data": {
    "markdown": "# 真的标题\n\n正文在这儿 ",
    "metadata": {"sourceURL": "https://x.com/final", "statusCode": 200},
}}
BLOCKED = (None, "HTTP 403 Forbidden", "HTTP 403")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        # 配置路径是 import 时算好的，不换掉会读写用户真正的 key
        for name in ("FETCH_CONFIG", "SEARCH_CONFIG"):
            before = getattr(tools, name)
            setattr(tools, name, str(self.root / (name.lower() + ".json")))
            self.addCleanup(setattr, tools, name, before)
        # 缓存是模块级的，不清会串到下一个用例
        tools._web_cache.clear()
        self.addCleanup(tools._web_cache.clear)
        self.box = Toolbox(self.root)
        self.addCleanup(self.box.shell_reset)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, which="FETCH_CONFIG", **fields):
        Path(getattr(tools, which)).write_text(json.dumps(fields), encoding="utf-8")

    def fetch(self, url="https://x.com/a", **args):
        return self.box.run("web_fetch", {"url": url, **args})


class TestRetryWorthy(Base):
    def test_只有反爬要登录和限流值得重抓(self):
        for short in ("HTTP 401", "HTTP 403", "HTTP 429"):
            self.assertTrue(tools._retry_worthy(short), short)

    def test_地址错了和服务器自己崩了不重抓(self):
        # 404 换谁抓都是 404，500 是对面的事 —— 花额度也救不回来
        for short in ("HTTP 404", "HTTP 500", "HTTP 502", "抓取失败", "内网地址已拦", "HTTP", ""):
            self.assertFalse(tools._retry_worthy(short), short)

    def test_短提示认得出状态码(self):
        self.assertEqual(tools._err_short("HTTP 402 insufficient credits"), "HTTP 402")
        self.assertEqual(tools._err_short("连不上"), "抓取失败")
        self.assertEqual(tools._err_short("HTTP"), "抓取失败")


class TestConfig(Base):
    def test_没有文件或坏_JSON_都当没配(self):
        self.assertEqual(tools.fetch_config(), {})
        Path(tools.FETCH_CONFIG).write_text("{坏的", encoding="utf-8")
        self.assertEqual(tools.fetch_config(), {})
        Path(tools.FETCH_CONFIG).write_text('["不是对象"]', encoding="utf-8")
        self.assertEqual(tools.fetch_config(), {})


class TestFallback(Base):
    def test_没配就一次都不碰_firecrawl(self):
        """出厂零配置：撞上 403 也不该有任何出网动作，更不该报「没配 key」"""
        with mock.patch.object(Toolbox, "_fetch_raw", return_value=BLOCKED), \
             mock.patch.object(Toolbox, "_firecrawl_post",
                               return_value=(SCRAPED, "")) as spy:
            out = self.fetch()
        spy.assert_not_called()
        self.assertTrue(out.is_error)
        self.assertIn("403", out.content)
        self.assertNotIn("Firecrawl", out.content)

    def test_反爬被拦时回退过去(self):
        self.config(key="fc-test")
        with mock.patch.object(Toolbox, "_fetch_raw", return_value=BLOCKED), \
             mock.patch.object(Toolbox, "_firecrawl_post", return_value=(SCRAPED, "")):
            out = self.fetch()
        self.assertFalse(out.is_error)
        self.assertIn("真的标题", out.content)
        # 花没花额度必须在 summary 上看得见
        self.assertIn("Firecrawl", out.summary)

    def test_builtin_抓得到就不花钱(self):
        self.config(key="fc-test")
        with mock.patch.object(Toolbox, "_fetch_raw",
                               return_value=("正文", "https://x.com/a", "text/plain")), \
             mock.patch.object(Toolbox, "_firecrawl_post",
                               return_value=(SCRAPED, "")) as spy:
            out = self.fetch()
        spy.assert_not_called()
        self.assertFalse(out.is_error)
        self.assertNotIn("Firecrawl", out.summary)

    def test_404_不花额度重抓(self):
        self.config(key="fc-test")
        with mock.patch.object(Toolbox, "_fetch_raw",
                               return_value=(None, "HTTP 404 Not Found", "HTTP 404")), \
             mock.patch.object(Toolbox, "_firecrawl_post",
                               return_value=(SCRAPED, "")) as spy:
            out = self.fetch()
        spy.assert_not_called()
        self.assertTrue(out.is_error)
        self.assertIn("404", out.content)

    def test_回退也没成时两边的原因都要说(self):
        self.config(key="fc-test")
        with mock.patch.object(Toolbox, "_fetch_raw", return_value=BLOCKED), \
             mock.patch.object(Toolbox, "_firecrawl_post",
                               return_value=(None, "HTTP 402 insufficient credits")):
            out = self.fetch()
        self.assertTrue(out.is_error)
        self.assertIn("403", out.content)          # builtin 原来那次
        self.assertIn("Firecrawl", out.content)    # 回退也试过了
        self.assertIn("402", out.content)          # 额度用完这种必须看得见
        self.assertIn("爬虫", out.content)          # _fetch_note 的人话还在


class TestProviderFirecrawl(Base):
    def test_一律走_firecrawl_时不碰_builtin(self):
        self.config(provider="firecrawl", key="fc-test")
        with mock.patch.object(Toolbox, "_fetch_raw",
                               return_value=("builtin 的正文", "https://x.com/a", "text/plain")) as spy, \
             mock.patch.object(Toolbox, "_firecrawl_post", return_value=(SCRAPED, "")):
            out = self.fetch()
        spy.assert_not_called()
        self.assertIn("真的标题", out.content)
        self.assertIn("Firecrawl", out.summary)

    def test_回的_markdown_不再转一遍(self):
        """ctype 是 text/markdown 不能进 _Markdown，否则代码块里的标签会被当 HTML 吃掉 """
        raw = {"success": True, "data": {"markdown": "用 `<div>` 包起来 ",
                                         "metadata": {"sourceURL": "https://x.com/a"}}}
        self.config(provider="firecrawl", key="fc-test")
        with mock.patch.object(Toolbox, "_firecrawl_post", return_value=(raw, "")):
            out = self.fetch()
        self.assertIn("<div>", out.content)

    def test_没填_key_直说(self):
        self.config(provider="firecrawl")
        out = self.fetch()
        self.assertTrue(out.is_error)
        self.assertIn("key", out.content)

    def test_页面没正文不当成抓到了(self):
        raw = {"success": True, "data": {"markdown": "", "metadata": {"statusCode": 403}}}
        self.config(provider="firecrawl", key="fc-test")
        with mock.patch.object(Toolbox, "_firecrawl_post", return_value=(raw, "")):
            out = self.fetch()
        self.assertTrue(out.is_error)
        self.assertIn("403", out.summary)

    def test_同一个地址十五分钟内不重复花钱(self):
        self.config(provider="firecrawl", key="fc-test")
        hits = []

        def once(*a, **k):
            hits.append(a)
            return SCRAPED, ""

        with mock.patch.object(Toolbox, "_firecrawl_post", side_effect=once):
            self.fetch()
            self.fetch()
        self.assertEqual(len(hits), 1)


class TestSSRF(Base):
    def test_走_firecrawl_也不准抓内网(self):
        """自托管实例就在本机，让它替我们去抓元数据接口跟自己抓没有区别 """
        self.config(provider="firecrawl", key="fc-test")
        with mock.patch.object(Toolbox, "_firecrawl_post",
                               return_value=(SCRAPED, "")) as spy:
            out = self.fetch("http://169.254.169.254/latest/meta-data/")
        spy.assert_not_called()
        self.assertTrue(out.is_error)
        self.assertIn("内网", out.summary)

    def test_本机地址同样拦(self):
        self.config(provider="firecrawl", key="fc-test")
        with mock.patch.object(Toolbox, "_firecrawl_post",
                               return_value=(SCRAPED, "")) as spy:
            out = self.fetch("http://127.0.0.1:3002/admin")
        spy.assert_not_called()
        self.assertTrue(out.is_error)
        self.assertIn("内网", out.summary)


class TestSearch(Base):
    def test_按_web_那组解析(self):
        raw = {"success": True, "data": {
            "web": [{"url": "https://a.com", "title": "甲", "description": "第一条"},
                    {"url": "https://b.com", "title": "乙", "description": "第二条"}],
            "news": [{"url": "https://n.com", "title": "不要这组"}]}}
        self.config("SEARCH_CONFIG", provider="firecrawl", key="fc-test")
        with mock.patch.object(Toolbox, "_firecrawl_post", return_value=(raw, "")):
            out = self.box.run("web_search", {"query": "x"})
        self.assertIn("https://a.com", out.content)
        self.assertIn("第二条", out.content)
        self.assertNotIn("不要这组", out.content)
        self.assertIn("Firecrawl", out.summary)

    def test_没填_key_直说(self):
        self.config("SEARCH_CONFIG", provider="firecrawl")
        out = self.box.run("web_search", {"query": "x"})
        self.assertTrue(out.is_error)
        self.assertIn("key", out.content)

    def test_认不出的_provider_把四个都列出来(self):
        self.config("SEARCH_CONFIG", provider="谷歌")
        out = self.box.run("web_search", {"query": "x"})
        self.assertTrue(out.is_error)
        self.assertIn("firecrawl", out.content)


if __name__ == "__main__":
    unittest.main()
