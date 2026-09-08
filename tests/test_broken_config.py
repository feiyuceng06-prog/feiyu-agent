"""盘上那几份配置写坏了，读它的人不许抛

**合法 JSON 不等于你要的那个形状。** `json.load` 只会在语法坏掉时抛 ValueError，
`null` / `[]` / `123` / `"x"` 全都读得出来 —— 然后 `.items()` 和 `.get()` 抛
AttributeError，而各家的 `except` 只接了 OSError 和 ValueError。

实测三处：`keys.load()`（在启动路径上，表现是 feiyu 一敲就崩，跟"密钥没了"
分不出来）、`permissions.load()`、`sandbox.level()`。`_state()` /
`image_config()` / `mcp._read_json()` / `enabled` 本来就判了形状，这份用例
是把那条规矩钉住，别再漏第四处。

写坏的来路不稀奇：写到一半被 kill、手改过、另一个版本留下的、同步工具冲突。
"""

import os
import tempfile
import unittest
from pathlib import Path

from feiyu import enabled, keys, permissions, sandbox
from feiyu import tools as tools_mod

# 每一条都是合法 JSON，坏的是形状
JUNK = ["{半截", "[]", "null", '"就一个字符串"', "123", "3.5", "true",
        "[[1,2],[3]]", ""]


class TestBrokenConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        # 这几个路径都是 import 时算好的，不换掉会读写用户真正的密钥和规则
        for mod, attr in ((keys, "STORE"), (keys, "STATE"), (permissions, "STORE"),
                          (enabled, "STORE"), (sandbox, "STORE"),
                          (tools_mod, "IMAGE_CONFIG")):
            before = getattr(mod, attr)
            self.addCleanup(setattr, mod, attr, before)
            setattr(mod, attr, str(self.dir / (attr.lower() + ".json")))

    def sweep(self, path: str, probe):
        for junk in JUNK:
            Path(path).write_text(junk, encoding="utf-8")
            with self.subTest(内容=junk[:12]):
                probe()          # 抛出来这条就红了，不用 assert

    def test_models_json(self):
        self.sweep(keys.STORE, keys.load)

    def test_state_json(self):
        self.sweep(keys.STATE, lambda: (keys.last(), keys.helper(),
                                        keys.theme(), keys.learned_limit("x")))

    def test_permissions_json(self):
        self.sweep(permissions.STORE, permissions.load)

    def test_enabled_json(self):
        self.sweep(enabled.STORE, lambda: enabled.on("tools", "bash"))

    def test_sandbox_json(self):
        before = os.environ.pop("FEIYU_SANDBOX", None)
        if before is not None:
            self.addCleanup(os.environ.__setitem__, "FEIYU_SANDBOX", before)
        self.sweep(sandbox.STORE, sandbox.level)

    def test_images_json(self):
        self.sweep(tools_mod.IMAGE_CONFIG, tools_mod.image_config)


if __name__ == "__main__":
    unittest.main()
