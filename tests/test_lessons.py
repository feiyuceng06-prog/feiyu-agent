"""同一件事栽第二次时提醒换做法，缓过来之后提一句值不值得记

「同一件事」认得很窄是这块的承重墙：错认一次就是在它好好干活的时候叫它停下来
所以一半的用例在验**不该说**的时候真的不说
"""

import unittest

from feiyu.lessons import Stumbles


def bash(s, command, output, ok=False, can=True):
    content = "退出码: %d\n%s" % (0 if ok else 1, output)
    return s.after("bash", {"command": command}, not ok, content, can_remember=can)


class TestRepeat(unittest.TestCase):
    def setUp(self):
        self.s = Stumbles()

    def test_同一条命令同样的错_第二次说(self):
        first, n1 = bash(self.s, "pytest", "ModuleNotFoundError: No module named 'x'")
        second, n2 = bash(self.s, "pytest", "ModuleNotFoundError: No module named 'x'")
        self.assertEqual((first, n1), ("", 1))
        self.assertIn("两次", second)
        self.assertEqual(n2, 2)

    def test_第三次叫它停下来问人(self):
        for _ in range(2):
            bash(self.s, "make", "error: missing separator")
        third, n = bash(self.s, "make", "error: missing separator")
        self.assertIn("告诉用户", third)
        self.assertEqual(n, 3)

    def test_之后不再唠叨(self):
        for _ in range(3):
            bash(self.s, "make", "boom")
        self.assertEqual(bash(self.s, "make", "boom")[0], "")

    def test_挂的换了一批是在往前走_不算(self):
        """改完代码再跑测试、挂的不一样了 —— 这是正常的迭代，叫停它就是帮倒忙 """
        bash(self.s, "pytest", "FAILED test_a.py::test_one\nFAILED test_a.py::test_two\n2 failed")
        got, _ = bash(self.s, "pytest", "FAILED test_a.py::test_two\n1 failed")
        self.assertEqual(got, "")

    def test_耗时和行号不一样照样认得出(self):
        bash(self.s, "pytest", "E NameError: name 'y' is not defined (line 45)\n1 failed in 0.51s")
        got, _ = bash(self.s, "pytest", "E NameError: name 'y' is not defined (line 46)\n1 failed in 0.49s")
        self.assertIn("两次", got)

    def test_两个不同的文件都不存在不算(self):
        miss = "文件不存在"
        self.s.after("read_file", {"path": "a.py"}, True, miss, can_remember=True)
        got, _ = self.s.after("read_file", {"path": "b.py"}, True, miss, can_remember=True)
        self.assertEqual(got, "")

    def test_同一个文件同样的替换失败算(self):
        err = "文件中找不到 old_str 最接近的是第 12 行"
        args = {"path": "a.py", "old_str": "x", "new_str": "y"}
        self.s.after("edit_file", args, True, err, can_remember=True)
        got, _ = self.s.after("edit_file", args, True, err, can_remember=True)
        self.assertIn("两次", got)


class TestNudge(unittest.TestCase):
    def setUp(self):
        self.s = Stumbles()
        for _ in range(2):
            bash(self.s, "pytest -q", "ModuleNotFoundError: No module named 'x'")

    def test_缓过来之后提一句_remember(self):
        # 换了解释器路径就是它想明白了 —— `pytest` 和 `.venv/bin/pytest` 是同一个程序
        got, n = bash(self.s, ".venv/bin/pytest -q", "3 passed", ok=True)
        self.assertIn("remember", got)
        self.assertIn("2 次", got)
        self.assertEqual(n, 0)

    def test_只说一次(self):
        bash(self.s, ".venv/bin/pytest -q", "ok", ok=True)
        for _ in range(2):
            bash(self.s, "npm test", "boom")
        self.assertEqual(bash(self.s, "npm test", "ok", ok=True)[0], "")

    def test_记不了的时候不引它去记(self):
        # 只读 / 计划模式下 remember 会被拒，引它去记是白碰一次壁
        got, _ = bash(self.s, ".venv/bin/pytest -q", "ok", ok=True, can=False)
        self.assertEqual(got, "")

    def test_别的程序成了不算缓过来(self):
        self.assertEqual(bash(self.s, "ls", "a b", ok=True)[0], "")
        self.assertIn("remember", bash(self.s, ".venv/bin/pytest -q", "ok", ok=True)[0])

    def test_没栽过就什么都不说(self):
        fresh = Stumbles()
        self.assertEqual(bash(fresh, "pytest", "ok", ok=True), ("", 0))

    def test_清零之后从头算(self):
        self.s.reset()
        self.assertEqual(bash(self.s, ".venv/bin/pytest -q", "ok", ok=True)[0], "")


if __name__ == "__main__":
    unittest.main()
