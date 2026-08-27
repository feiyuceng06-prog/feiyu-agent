"""把图片文件变成模型收得下的一张图

用已经在依赖里的 pillow 缩图 再大也不会更清楚，只是更贵：一张 1568×1568
的图大约 1600 token，翻一倍边长就是四倍的钱和上下文
"""

import base64
import io
from pathlib import Path

SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# 长边上限，两家接口给的建议都是这个数
MAX_EDGE = 1568
# 编码后的字节上限 anthropic 是 5MB，留出余量
MAX_BYTES = 3_500_000


def kind(path: Path) -> str | None:
    return SUFFIXES.get(path.suffix.lower())


def load(path: Path) -> tuple[str, str, str]:
    """返回 (media_type, base64 数据, 一句给人看的说明) """
    from PIL import Image

    raw = path.read_bytes()
    media = kind(path) or "image/png"
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        w, h = img.size
        if max(w, h) <= MAX_EDGE and len(raw) <= MAX_BYTES:
            return media, base64.b64encode(raw).decode(), "%d×%d" % (w, h)
        media, data = _shrink(img)
    note = "%d×%d，缩过再发的" % (w, h)
    return media, base64.b64encode(data).decode(), note


def _shrink(img) -> tuple[str, bytes]:
    """缩到长边和字节数都进上限 一轮不够就再缩，别在这儿放弃 """
    from PIL import Image

    edge = MAX_EDGE
    while True:
        w, h = img.size
        scale = min(1.0, edge / max(w, h))
        shrunk = img if scale >= 1.0 else img.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        if shrunk.mode in ("RGBA", "LA", "P"):
            # 有透明通道就得走 PNG，转 JPEG 会把透明的地方糊成黑块
            shrunk.convert("RGBA").save(buf, "PNG", optimize=True)
            media = "image/png"
        else:
            shrunk.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
            media = "image/jpeg"
        data = buf.getvalue()
        if len(data) <= MAX_BYTES or edge <= 256:
            return media, data
        edge = int(edge * 0.7)
