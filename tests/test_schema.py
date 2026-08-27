"""结构化输出：从模型的回答里抠 JSON，再按 schema 校验

只实现了 schema 里真正用得上的关键词，所以这里也只测那些——
测不认识的关键词等于给"不管它"这个行为上锁
"""

import json
import os
import tempfile
import unittest

from feiyu import schema


class TestExtract(unittest.TestCase):
    def test_干净的_json(self):
        self.assertEqual(schema.extract('{"a": 1}'), ({"a": 1}, ""))

    def test_剥掉代码块(self):
        self.assertEqual(schema.extract('```json\n{"a": 1}\n```')[0], {"a": 1})

    def test_剥掉不带语言的代码块(self):
        self.assertEqual(schema.extract('```\n{"a": 1}\n```')[0], {"a": 1})

    def test_前面有废话(self):
        # 模型很爱先说一句"好的，结果如下"
        self.assertEqual(schema.extract('好的，结果如下：\n{"a": 1}')[0], {"a": 1})

    def test_前后都有废话(self):
        self.assertEqual(schema.extract('结果：\n{"a": 1}\n希望有帮助')[0], {"a": 1})

    def test_压根没有_json(self):
        value, bad = schema.extract("我觉得这个问题很复杂")
        self.assertIsNone(value)
        self.assertTrue(bad)

    def test_花括号里不是合法_json(self):
        value, bad = schema.extract("{ 这不是 json }")
        self.assertIsNone(value)
        self.assertIn("JSON", bad)

    def test_空回答(self):
        self.assertIsNone(schema.extract("")[0])


class TestValidate(unittest.TestCase):
    SPEC = {
        "type": "object",
        "required": ["ok", "issues"],
        "additionalProperties": False,
        "properties": {
            "ok": {"type": "boolean"},
            "count": {"type": "integer"},
            "level": {"enum": ["low", "high"]},
            "issues": {"type": "array", "items": {"type": "string"}},
        },
    }

    def test_合规的通过(self):
        self.assertEqual(schema.validate(
            {"ok": True, "issues": ["a"], "count": 2, "level": "low"}, self.SPEC), [])

    def test_缺必填字段(self):
        problems = schema.validate({"ok": True}, self.SPEC)
        self.assertEqual(len(problems), 1)
        self.assertIn("issues", problems[0])

    def test_类型不对(self):
        self.assertTrue(schema.validate({"ok": "yes", "issues": []}, self.SPEC))

    def test_不许有额外字段(self):
        problems = schema.validate({"ok": True, "issues": [], "多的": 1}, self.SPEC)
        self.assertTrue(any("多的" in p for p in problems))

    def test_默认允许额外字段(self):
        spec = {"type": "object", "properties": {"a": {"type": "string"}}}
        self.assertEqual(schema.validate({"a": "x", "b": 1}, spec), [])

    def test_enum(self):
        self.assertTrue(schema.validate(
            {"ok": True, "issues": [], "level": "mid"}, self.SPEC))

    def test_数组元素逐个查(self):
        problems = schema.validate({"ok": True, "issues": ["a", 2, "c"]}, self.SPEC)
        self.assertEqual(len(problems), 1)
        self.assertIn("[1]", problems[0])

    def test_布尔不算整数(self):
        # JSON 里 True 是 int 的子类，但 schema 写 integer 时肯定不想收到 true
        self.assertTrue(schema.validate({"ok": True, "issues": [], "count": True}, self.SPEC))

    def test_整数可以当数字(self):
        self.assertEqual(schema.validate(3, {"type": "number"}), [])

    def test_嵌套对象报出路径(self):
        spec = {"type": "object", "properties": {
            "a": {"type": "object", "properties": {"b": {"type": "string"}}}}}
        problems = schema.validate({"a": {"b": 1}}, spec)
        self.assertIn("a.b", problems[0])

    def test_不认识的_type_不管(self):
        self.assertEqual(schema.validate("随便", {"type": "没这个类型"}), [])

    def test_顶层不是对象也能查(self):
        self.assertTrue(schema.validate("x", {"type": "array"}))
        self.assertEqual(schema.validate([1, 2], {"type": "array"}), [])

    def test_错误信息里带位置(self):
        problems = schema.validate({"ok": 1, "issues": []}, self.SPEC)
        self.assertIn("ok", problems[0])


class TestDescribe(unittest.TestCase):
    def test_把_schema_原样贴给模型(self):
        text = schema.describe({"type": "object", "properties": {"名字": {"type": "string"}}})
        self.assertIn("名字", text)
        self.assertIn("只输出一个 JSON 对象", text)


class TestLoad(unittest.TestCase):
    def test_读文件(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"type": "object"}, f)
            self.assertEqual(schema.load(path), {"type": "object"})

    def test_顶层不是对象要报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("[1, 2]")
            with self.assertRaises(ValueError):
                schema.load(path)

    def test_文件不存在要报错(self):
        with self.assertRaises(OSError):
            schema.load("/绝对没有这个文件.json")


if __name__ == "__main__":
    unittest.main()
