# -*- coding: utf-8 -*-
"""配色提取：从一张图里取前 N 个主色，本地聚类，不联网。

「这张图用了哪些颜色」不需要大模型：Pillow 自带的中位切分量化（``quantize``）就是
主色提取的标准做法，而 pillow 本来就是运行依赖，所以这条能力零新增依赖、零联网。

三个取舍：

- **先缩小再聚类**：量化只要颜色分布，2000×2000 与 256×256 得到的主色几乎一样，缩图
  让这一步从几十毫秒掉到几毫秒。
- **带 alpha 的先合成到白底**：透明像素经 ``convert("RGB")`` 会变成纯黑，一个带透明边的
  贴图会凭空多出一块黑色主色。
- **不合并相近色**：量化已经把相近色归到同一格；再做二次合并会把「深蓝 / 蓝」这种用户
  看得出区别的两个色合成一个，反而失真。
"""

from dataclasses import dataclass
from typing import Any, List, Optional

from core.logger import T, log_exception, log_warning

#: 默认取几个主色。6 个是「够描述一张图、又不至于列成色卡」的量级
DEFAULT_COLORS = 6

#: 上限：量化到几十格之后返回的已经不只是「主色」了
MAX_COLORS = 16

#: 聚类前把图缩到这个边长以内（只影响速度，不影响主色）
_MAX_EDGE = 256

#: 单色占比低于这个值就不列出来：OCR 图里的抗锯齿边缘会产生大量只占千分之几的杂色
MIN_RATIO = 0.01


@dataclass(frozen=True)
class PaletteColor:
    """一个主色：``#RRGGBB`` 与它在这张图里的占比（0~1）。"""

    hex: str
    ratio: float

    def __str__(self) -> str:
        return self.hex


def _png_bytes(image) -> bytes:
    """QImage/QPixmap → PNG 字节；空图返回空字节。

    不借 ``ocr.engine`` 的同类助手：core 不该依赖 ocr（那边的 import 会连带注册 OCR 与
    公式引擎），这里只需要 Qt 的编码器就够了。
    """
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    if image is None:
        return b""
    source = image if isinstance(image, QImage) else image.toImage()
    if source is None or source.isNull():
        return b""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    source.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def _normalized_count(count: Any) -> int:
    try:
        number = int(count)
    except (TypeError, ValueError):
        return DEFAULT_COLORS
    return max(1, min(MAX_COLORS, number))


def _flatten_alpha(pil_image):
    """带 alpha 的图先合成到白底（见模块 docstring 的第二条取舍）。"""
    if "A" not in pil_image.getbands():
        return pil_image.convert("RGB")
    from PIL import Image

    rgba = pil_image.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def extract_palette(image, count: int = DEFAULT_COLORS) -> List[PaletteColor]:
    """从图里取主色，按占比从高到低；取不出（空图、Pillow 不可用、量化失败）返回空表。

    调用方拿空表就当作「这张图没有可提取的配色」，不要在这里抛异常——配色提取失败
    不该把调用它的动作（截图链路）带塌。
    """
    data = _png_bytes(image)
    if not data:
        return []
    try:
        import io

        from PIL import Image
    except Exception as e:  # pragma: no cover - pillow 是运行依赖，装不上属于环境坏了
        log_exception(e, T("导入 Pillow"))
        return []

    limit = _normalized_count(count)
    try:
        with Image.open(io.BytesIO(data)) as opened:
            flattened = _flatten_alpha(opened)
        work = flattened.copy()
        work.thumbnail((_MAX_EDGE, _MAX_EDGE))
        quantized = work.quantize(colors=limit, method=Image.Quantize.MEDIANCUT)
    except Exception as e:
        log_exception(e, T("提取图片配色"))
        return []

    palette = quantized.getpalette() or []
    entries = quantized.getcolors(maxcolors=limit + 1) or []
    total = sum(item[0] for item in entries)
    if not total:
        return []

    colors: List[PaletteColor] = []
    for pixel_count, index in sorted(entries, key=lambda item: -item[0]):
        offset = index * 3
        if offset + 2 >= len(palette):
            continue
        ratio = pixel_count / total
        if ratio < MIN_RATIO:
            continue
        colors.append(PaletteColor(
            "#%02X%02X%02X" % (palette[offset], palette[offset + 1], palette[offset + 2]),
            ratio,
        ))
        if len(colors) >= limit:
            break
    if not colors:
        log_warning(T("配色提取没有得到任何颜色"), "Palette")
    return colors


def palette_text(colors: Optional[List[PaletteColor]], with_ratio: bool = False) -> str:
    """配色拼成可复制的文本；剪贴板里默认只放色值，方便直接粘进 CSS。"""
    if not colors:
        return ""
    if not with_ratio:
        return "\n".join(color.hex for color in colors)
    return "\n".join(f"{color.hex}  {color.ratio * 100:.1f}%" for color in colors)
