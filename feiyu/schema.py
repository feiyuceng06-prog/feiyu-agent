"""结构化输出：按用户给的 JSON schema 收尾，让结果能接进别的流程

只实现 schema 里真正用得上的那部分（type / required / properties / items /
enum / additionalProperties），**不引 jsonschema**——完整规范里九成的关键词
在这个场景下用不到，为它加一个依赖不划算

校验失败会把错误回给模型让它重出一次，所以错误信息要写得能照着改
"""

import json

MAX_REPAIR = 2      # 格式不对最多让它重出几次


def describe(schema: dict) -> str:
    """给模型看的要求 直接把 schema 贴过去比翻译成中文准确 """
    return (
        "任务做完了 现在**只输出一个 JSON 对象**，不要有别的话，"
        "不要包在 ```json 代码块里，不要解释 它必须满足这个 JSON schema：\n\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )


def extract(text: str) -> tuple[object, str]:
    """从模型的回答里抠出 JSON 返回 (值, 出错说明)

    模型很爱在 JSON 外面裹一层 ```json 或者一句"好的，结果如下"，
    直接 json.loads 会失败，所以先剥壳再退回找第一个花括号
    """
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1] if "\n" in body else ""
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    body = body.strip()
    try:
        return json.loads(body), ""
    except ValueError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(body[start : end + 1]), ""
        except ValueError as exc:
            return None, "花括号之间的内容不是合法 JSON：%s" % exc
    return None, "回答里找不到 JSON 对象"


_TYPES = {
    "object": dict, "array": list, "string": str,
    "number": (int, float), "integer": int, "boolean": bool, "null": type(None),
}


def validate(value, schema: dict, path: str = "") -> list[str]:
    """返回问题列表，空列表表示通过 """
    where = path or "顶层"
    problems: list[str] = []

    kind = schema.get("type")
    if kind:
        wanted = _TYPES.get(kind)
        if wanted is None:
            return problems                      # 不认识的 type 就不管
        # JSON 里 True 也是 int，但 schema 写 integer 时肯定不想要 bool
        if kind in ("number", "integer") and isinstance(value, bool):
            return ["%s 应该是 %s，收到的是 boolean" % (where, kind)]
        if not isinstance(value, wanted):
            return ["%s 应该是 %s，收到的是 %s" % (where, kind, _name(value))]

    if "enum" in schema and value not in schema["enum"]:
        problems.append("%s 只能是 %s 之一，收到 %r" % (
            where, "、".join(repr(v) for v in schema["enum"]), value))

    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                problems.append("%s 缺少必填字段 %r" % (where, name))
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    problems.append("%s 多了字段 %r（schema 不允许额外字段）" % (where, name))
        for name, sub in props.items():
            if name in value:
                problems += validate(value[name], sub, "%s.%s" % (path, name) if path else name)

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], "%s[%d]" % (path or "顶层", i))

    return problems


def _name(value) -> str:
    for label, kinds in _TYPES.items():
        if label != "number" and isinstance(value, kinds) and not (
                label == "integer" and isinstance(value, bool)):
            return label
    return type(value).__name__


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    if not isinstance(schema, dict):
        raise ValueError("schema 文件的顶层必须是一个对象")
    return schema
