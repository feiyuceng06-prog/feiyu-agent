import unittest

from feiyu import verify

UNITTEST = """FE.
======================================================================
ERROR: test_b (test_s.T.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/x/test_s.py", line 6, in test_b
    raise ValueError("boom")
ValueError: boom

======================================================================
FAIL: test_a (test_s.T.test_a)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/x/test_s.py", line 4, in test_a
    self.assertEqual(1, 2)
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 3 tests in 0.000s

FAILED (failures=1, errors=1)
"""

PYTEST = """============================= test session starts ==============================
collected 3 items

test_s.py F.E                                                            [100%]

=================================== FAILURES ===================================
____________________________________ T.test_a __________________________________
    def test_a(self):
>       assert 1 == 2
E       assert 1 == 2

test_s.py:4: AssertionError
=========================== short test summary info ============================
FAILED test_s.py::T::test_a - assert 1 == 2
ERROR test_s.py::T::test_b - ValueError: boom
========================= 1 failed, 1 passed, 1 error in 0.05s =================
"""

GCC = """c.c: In function 'main':
c.c:1:21: error: initialization of 'int' from 'char *' makes integer from pointer without a cast
c.c:1:33: error: 'y' undeclared (first use in this function)
c.c:1:33: note: each undeclared identifier is reported only once
"""

RUST = """   Compiling demo v0.1.0
error[E0425]: cannot find value `y` in this scope
 --> src/main.rs:2:5
  |
2 |     y
  |     ^ not found in this scope

error: could not compile `demo` (bin "demo") due to 1 previous error
"""


class TestDigest(unittest.TestCase):
    def test_unittest(self):
        got = verify.digest(UNITTEST)
        self.assertEqual(got[0], "ERROR: test_b (test_s.T.test_b)")
        self.assertIn("ValueError: boom", got)
        self.assertIn("FAIL: test_a (test_s.T.test_a)", got)
        self.assertIn("AssertionError: 1 != 2", got)
        self.assertEqual(got[-1], "FAILED (failures=1, errors=1)")

    def test_pytest(self):
        got = verify.digest(PYTEST)
        self.assertIn("FAILED test_s.py::T::test_a - assert 1 == 2", got)
        self.assertIn("ERROR test_s.py::T::test_b - ValueError: boom", got)
        self.assertIn("E       assert 1 == 2", got)
        self.assertTrue(got[-1].startswith("=") and "1 failed" in got[-1])

    def test_gcc_只要_error_不要_note(self):
        got = verify.digest(GCC)
        self.assertEqual(len(got), 2)
        self.assertTrue(all("error:" in g for g in got))

    def test_rust_把位置拼到报错后面(self):
        got = verify.digest(RUST)
        self.assertEqual(got[0], "error[E0425]: cannot find value `y` in this scope  @ src/main.rs:2:5")
        self.assertIn("error: could not compile", got[-1])

    def test_认不出就是空_调用方给原文(self):
        self.assertEqual(verify.digest("一切正常\n没什么可说的\n"), [])

    def test_超过上限截断并说明(self):
        text = "\n".join("FAIL: test_%d (m.T.test_%d)" % (i, i) for i in range(30))
        got = verify.digest(text)
        self.assertEqual(len(got), verify.MAX_ITEMS + 1)
        self.assertIn("还有 18 条", got[-1])

    def test_同一条不重复(self):
        got = verify.digest("AssertionError: x\nAssertionError: x\n")
        self.assertEqual(got, ["AssertionError: x"])
