"""`@路径`：认哪些、不认哪些、贴出去长什么样、补全给什么 """

import tempfile
import unittest
from pathlib import Path

from feiyu import files, mentions


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "feiyu").mkdir()
        (self.root / "feiyu" / "ui.py").write_text("a\nb\nc\n", encoding="utf-8")
        (self.root / "README.md").write_text("# hi\n", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "junk.py").write_text("x\n", encoding="utf-8")
        (self.root / ".hidden").write_text("x\n", encoding="utf-8")

    def shown(self, text):
        return [s for s, _ in mentions.find(text, self.root)]


class Find(Base):
    def test_plain(self):
        self.assertEqual(self.shown("看看 @feiyu/ui.py"), ["feiyu/ui.py"])

    def test_line_start(self):
        self.assertEqual(self.shown("@README.md 怎么样"), ["README.md"])

    def test_cjk_punctuation_ends_the_path(self):
        """中文里没人打空格：`@feiyu/ui.py，第 3 行` 要能剥回到路径 """
        self.assertEqual(self.shown("@feiyu/ui.py，第 3 行"), ["feiyu/ui.py"])

    def test_brackets(self):
        self.assertEqual(self.shown("(@README.md) 这份"), ["README.md"])

    def test_quoted(self):
        (self.root / "a b.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(self.shown('看 @"a b.txt" 这个'), ["a b.txt"])

    def test_directory(self):
        self.assertEqual(self.shown("@feiyu/ 里都有啥"), ["feiyu/"])

    def test_email_is_not_a_mention(self):
        self.assertEqual(self.shown("发到 a@b.com 去"), [])

    def test_decorator_is_not_a_mention(self):
        """靠"这个路径真的存在吗"来判，不靠正则猜形状 """
        self.assertEqual(self.shown("写个 @property 上去"), [])

    def test_decorator_does_not_latch_onto_a_short_prefix(self):
        """仓库里有个文件叫 `p` 的时候，`@property` 也不许匹配成 `p` """
        (self.root / "p").write_text("x\n", encoding="utf-8")
        self.assertEqual(self.shown("写个 @property 上去"), [])

    def test_missing_path_stays_text(self):
        self.assertEqual(self.shown("@feiyu/nope.py"), [])

    def test_escape_is_refused(self):
        self.assertEqual(self.shown("@../../etc/passwd"), [])
        self.assertEqual(self.shown("@/etc/passwd"), [])

    def test_bare_dot_is_refused(self):
        """剥前缀会剥出 `.`，而它解析出来正好是工作区根 —— 越界的写法不许因此匹配上 """
        self.assertEqual(self.shown("@."), [])
        self.assertEqual(self.shown("@.."), [])

    def test_deduplicates(self):
        self.assertEqual(self.shown("@README.md 和 @README.md"), ["README.md"])


class Expand(Base):
    def test_nothing_to_do(self):
        self.assertEqual(mentions.expand("没有 at 符号", self.root), ("", []))

    def test_body_is_numbered_like_read_file(self):
        note, said = mentions.expand("@feiyu/ui.py", self.root)
        self.assertIn("feiyu/ui.py", note)
        self.assertIn("1\ta", note)
        self.assertIn("3\tc", note)
        self.assertEqual(said, ["feiyu/ui.py（3 行）"])

    def test_records_into_the_watcher(self):
        """贴过去就等于它看过了 不记的话下一步 edit_file 会被「还没读过」挡回去 """
        watcher = files.Watcher()
        mentions.expand("@feiyu/ui.py", self.root, watcher)
        self.assertTrue(watcher.seen(self.root / "feiyu" / "ui.py"))

    def test_directory_lists_one_level(self):
        note, said = mentions.expand("@feiyu/", self.root)
        self.assertIn("ui.py", note)
        self.assertEqual(said, ["feiyu/（1 项）"])

    def test_long_file_says_it_was_cut(self):
        (self.root / "big.txt").write_text(
            "\n".join(str(i) for i in range(mentions.MAX_LINES + 50)), encoding="utf-8")
        note, said = mentions.expand("@big.txt", self.root)
        self.assertIn("read_file", note)
        self.assertIn("offset=", note)
        self.assertIn("/", said[0])

    def test_预算按份分不是先到先得(self):
        """第一个大文件不许把预算吃光 —— 三个是用户一起指出来的 """
        wide = "\n".join("y" * 200 for _ in range(mentions.MAX_LINES))
        (self.root / "one.txt").write_text(wide, encoding="utf-8")
        (self.root / "two.txt").write_text(wide, encoding="utf-8")
        note, said = mentions.expand("@one.txt @two.txt @README.md", self.root)
        self.assertNotIn("未贴", "".join(said))
        self.assertIn("README.md", said[2])

    def test_小文件用不完的份滚给后面(self):
        wide = "\n".join("y" * 200 for _ in range(mentions.MAX_LINES))
        (self.root / "one.txt").write_text(wide, encoding="utf-8")
        alone = mentions.expand("@one.txt", self.root)[1][0]
        after = mentions.expand("@README.md @one.txt", self.root)[1][1]
        self.assertEqual(alone, after)

    def test_指得太多时剩下的只报路径(self):
        """贴不下也要把路径说出来 —— 它至少不用再猜一遍名字 """
        many = []
        for i in range(mentions.MAX_CHARS // mentions.MIN_SHARE + 2):
            (self.root / ("f%d.txt" % i)).write_text("y" * 400, encoding="utf-8")
            many.append("@f%d.txt" % i)
        note, said = mentions.expand(" ".join(many), self.root)
        self.assertIn("未贴", said[-1])
        self.assertIn("f%d.txt" % (len(many) - 1), note)

    def test_one_enormous_line_still_shows_something(self):
        """压缩过的 js 是一整行几十万字：按行切会切出个空的，等于什么都没说 """
        (self.root / "min.js").write_text("z" * (mentions.MAX_CHARS + 10),
                                          encoding="utf-8")
        note, said = mentions.expand("@min.js", self.root)
        self.assertEqual(said, ["min.js（截断）"])
        self.assertIn("zzz", note)
        self.assertIn("read_file", note)

    def test_binary_is_not_pasted(self):
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02")
        note, said = mentions.expand("@blob.bin", self.root)
        self.assertEqual(said, ["blob.bin（二进制）"])

    def test_image_defers_to_read_file(self):
        (self.root / "shot.png").write_bytes(b"\x89PNG\r\n")
        note, said = mentions.expand("@shot.png", self.root)
        self.assertEqual(said, ["shot.png（图片）"])
        self.assertIn("read_file", note)


class Complete(Base):
    def test_root(self):
        got = mentions.complete(self.root, "")
        self.assertIn("feiyu/", got)
        self.assertIn("README.md", got)

    def test_dirs_first(self):
        self.assertEqual(mentions.complete(self.root, "")[0], "feiyu/")

    def test_inside_a_directory(self):
        self.assertEqual(mentions.complete(self.root, "feiyu/u"), ["feiyu/ui.py"])

    def test_case_insensitive(self):
        self.assertEqual(mentions.complete(self.root, "readm"), ["README.md"])

    def test_skips_ignored_dirs(self):
        self.assertNotIn(".venv/", mentions.complete(self.root, ""))

    def test_dotfiles_need_a_dot(self):
        self.assertNotIn(".hidden", mentions.complete(self.root, ""))
        self.assertIn(".hidden", mentions.complete(self.root, "."))

    def test_escape_is_refused(self):
        self.assertEqual(mentions.complete(self.root, "../"), [])


class Typing(Base):
    def test_on_a_mention(self):
        self.assertEqual(mentions.typing("看看 @feiyu/u"), "feiyu/u")

    def test_at_the_very_start(self):
        self.assertEqual(mentions.typing("@RE"), "RE")

    def test_finished_mention(self):
        self.assertIsNone(mentions.typing("看看 @feiyu/ui.py 这个"))

    def test_email(self):
        self.assertIsNone(mentions.typing("a@b"))

    def test_no_at(self):
        self.assertIsNone(mentions.typing("普通一句话"))


class Budget(Base):
    """那道 `MAX_CHARS` 的闸必须真的封得住

    目录列表原来一个字都不数（只按 `MAX_ENTRIES` 数项数），而名字最长能到 255 字：
    八十项就是两万字，正好是预算的全部。于是后面那个文件分到的是**负数预算**，
    `_numbered` 里 `first[:budget]` 变成从末尾切 —— 报出来的话是
    「只截了前 -509 个字符」，实际贴进去的是整整五万字。
    """

    def big_dir(self, n=80, width=253):
        d = self.root / "d"
        d.mkdir()
        for i in range(n):
            (d / ("n" * width + "%02d" % i)).write_text("x", encoding="utf-8")

    def test_名字很长的目录加一个大文件_总量不超上限(self):
        self.big_dir()
        (self.root / "min.js").write_text("a" * 50_000, encoding="utf-8")
        note, said = mentions.expand("看 @d 和 @min.js", self.root)
        self.assertLess(len(note), mentions.MAX_CHARS * 1.2, said)

    def test_不会报出负数的截断长度(self):
        self.big_dir()
        (self.root / "min.js").write_text("a" * 50_000, encoding="utf-8")
        note, _ = mentions.expand("看 @d 和 @min.js", self.root)
        self.assertNotRegex(note, r"只截了前 -")

    def test_目录吃掉自己那一份就停_后面那个照样有预算(self):
        """**按份分这条不能因为目录就破掉**：目录超出自己那份就截断，
        不许挪用后面那个文件的额度（`@目录 @文件` 里用户要的是两样东西）
        """
        self.big_dir()
        (self.root / "min.js").write_text("a" * 50_000, encoding="utf-8")
        note, said = mentions.expand("看 @d 和 @min.js", self.root)
        self.assertIn("只列了前", note)
        self.assertTrue(said[1].startswith("min.js"), said)
        # 后面那个真的拿到了东西，不是"没贴"
        self.assertNotIn("未贴", said[1])

    def test_目录列表自己也会说截断(self):
        self.big_dir(n=200, width=20)
        note, said = mentions.expand("@d", self.root)
        self.assertEqual(said, ["d（%d 项）" % mentions.MAX_ENTRIES])
        self.assertIn("只列了前", note)

    def test_正常目录照旧全列(self):
        note, said = mentions.expand("@feiyu", self.root)
        self.assertEqual(said, ["feiyu（1 项）"])
        self.assertIn("ui.py", note)
        self.assertNotIn("只列了前", note)


if __name__ == "__main__":
    unittest.main()


class OddFiles(Base):
    def test_命名管道不去读它(self):
        """`@` 是在发消息**之前**跑的 —— 挂住的表现是"按了回车什么都没发生" """
        import os
        try:
            os.mkfifo(self.root / "pipe.txt")
        except (AttributeError, OSError):
            self.skipTest("这个平台建不了 FIFO")
        note, said = mentions.expand("@pipe.txt", self.root)
        self.assertEqual(said, ["pipe.txt（命名管道）"])
        self.assertIn("挂住", note)
