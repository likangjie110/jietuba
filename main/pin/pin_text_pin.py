# -*- coding: utf-8 -*-
"""把一段文字做成贴图（「文字贴图」）。

判断依据（这一项的参考页语义只有名字，没有描述，所以按最贴近现有能力的方式落地）：
贴图窗口的渲染、缩放、透明度、会话保存全都是围绕 ``QImage`` 的管线，因此这里把文字
**渲染成一张图像贴图**，而不是另立一条可编辑的文本贴图管线。代价是贴出来的字不能再
改（要改就重新贴一张），换来的是它自动拥有贴图的全部能力：滚轮缩放、钉住、复制、
缩略图、会话恢复。

字体从平台层取（``core/platform/fonts.py``），配色跟随当前主题，这样浅色/深色主题下
贴出来的文字都看得清。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

#: 内边距与行距（像素）
PADDING = 16
LINE_SPACING = 1.25

#: 字号与最大宽度的范围（设置页与这里共用）
TEXT_FONT_SIZE_RANGE = (8, 48)
TEXT_MAX_WIDTH_RANGE = (100, 2000)


def _theme_colors() -> tuple:
    """当前主题下的（背景色, 文字色, 边框色）。"""
    from core.ui_theme import get_ui_theme

    tokens = get_ui_theme().tokens
    try:
        border = get_theme_border()
    except Exception:
        border = tokens.separator
    return QColor(tokens.surface_strong), QColor(tokens.text), QColor(border)


def get_theme_border():
    """主题描边色（取不到时由调用方兜底）。"""
    from core.ui_theme import get_ui_theme

    return get_ui_theme().tokens.border


def build_font(font_size: int) -> QFont:
    """贴图里用的字体：平台默认界面字体 + 指定字号。"""
    from core.platform.fonts import ui_font_family

    font = QFont(ui_font_family())
    font.setPixelSize(max(TEXT_FONT_SIZE_RANGE[0], min(TEXT_FONT_SIZE_RANGE[1], int(font_size))))
    return font


def render_text_image(text: str, *, font_size: int, max_width: int) -> QImage:
    """把 ``text`` 渲染成贴图用的图像；空文本返回空图。

    宽度：``max_width`` 是**贴图整体宽度上限（含内边距）**。一行放得下就用文字本身的
    宽度（短句贴出来不会顶着一条又宽又空的边框），放不下才用满上限换行。
    """
    text = (text or "").strip()
    if not text:
        return QImage()

    font = build_font(font_size)
    metrics = QFontMetrics(font)
    total_limit = max(TEXT_MAX_WIDTH_RANGE[0], min(TEXT_MAX_WIDTH_RANGE[1], int(max_width)))
    content_limit = total_limit - PADDING * 2

    # 宽度策略：一行放得下就用文字本身的宽度（短句贴出来很紧凑）；放不下就按上限
    # 换行，贴出来像一张固定宽度的便签。Qt 在换行模式下量到的是「可用宽度」而不是
    # 实际用到多宽，所以这里自己判断，不靠 boundingRect 的宽度。
    single_line = metrics.horizontalAdvance(text)
    content_width = content_limit if single_line > content_limit else max(
        single_line, metrics.horizontalAdvance("M")
    )

    bounds = metrics.boundingRect(
        0, 0, content_width, 10_000, int(Qt.TextFlag.TextWordWrap), text
    )
    width = content_width + PADDING * 2
    height = max(bounds.height(), metrics.height()) + PADDING * 2
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    background, foreground, border = _theme_colors()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(border)
    painter.drawRoundedRect(0, 0, width, height, 6, 6)          # 1px 描边：先画底色
    painter.setBrush(background)
    painter.drawRoundedRect(1, 1, width - 2, height - 2, 5, 5)

    painter.setPen(foreground)
    painter.setFont(font)
    painter.drawText(
        PADDING, PADDING, content_width, height - PADDING * 2,
        int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft),
        text,
    )
    painter.end()
    return image
