# -*- coding: utf-8 -*-
"""导出前的美化：留白、背景、圆角阴影版式与多尺寸导出。

分享截图时常见的三件事，都在这里做成纯函数（进 QImage 出 QImage / 出尺寸表）：

- ``add_margins``：四周留白 + 背景色（可选渐变之外的纯色），是「看起来像样」的最低成本做法；
- ``apply_layout``：留白 + 背景 + 圆角 + 投影，一次画完——顺序很重要（先贴图、再画圆角遮罩、
  最后落投影），所以不让调用方自己拼；
- ``export_sizes``：按给定的目标宽度导出多份（等比缩放，不放大超过原图）。

没有任何一步会读配置或碰界面：设置由调用方（动作/设置页）传进来。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from core.logger import T, log_debug, log_warning

#: 留白与投影的取值范围（设置页与这里共用一份）
MARGIN_RANGE = (0, 200)
RADIUS_RANGE = (0, 60)
SHADOW_RANGE = (0, 60)

#: 多尺寸导出的目标宽度（像素）
EXPORT_WIDTHS = (640, 1280, 1920)


def _colour(value) -> QColor:
    """配置里的颜色字符串 → QColor；认不出来就用白色（不留半透明脏底）。"""
    colour = QColor(value) if value else QColor()
    return colour if colour.isValid() else QColor("white")


def _rounded_path(width: int, height: int, radius: int) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
    return path


def add_margins(image, margin: int = 24, background: str = "#FFFFFF"):
    """四周加留白（背景色），图像本身不缩放。"""
    if image is None or image.isNull():
        return QImage()
    margin = max(0, int(margin or 0))
    output = QImage(image.width() + margin * 2, image.height() + margin * 2,
                    QImage.Format.Format_ARGB32)
    output.fill(_colour(background))
    painter = QPainter(output)
    painter.drawImage(margin, margin, image)
    painter.end()
    return output


def apply_layout(image, *, margin: int = 24, background: str = "#FFFFFF",
                 radius: int = 12, shadow: int = 18, shadow_color: str = "#33000000"):
    """留白 + 背景 + 圆角 + 投影，一次画完。

    投影画在圆角遮罩之下、留白之上：先把图按圆角裁好并贴进画布，再沿同一路径把投影
    铺在图像下方（偏移 shadow/4）。半径或投影为 0 时对应步骤直接跳过。
    """
    if image is None or image.isNull():
        return QImage()
    margin = max(0, int(margin or 0))
    radius = max(0, int(radius or 0))
    shadow = max(0, int(shadow or 0))
    offset = shadow // 4
    width = image.width() + margin * 2 + shadow
    height = image.height() + margin * 2 + shadow
    output = QImage(width, height, QImage.Format.Format_ARGB32)
    output.fill(_colour(background))

    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    source = image
    if radius > 0:
        # 先在图自己的画布上按圆角裁一次，避免直接往大画布上套路径时露出直角
        masked = QImage(image.size(), QImage.Format.Format_ARGB32)
        masked.fill(Qt.GlobalColor.transparent)
        mask_painter = QPainter(masked)
        mask_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mask_painter.setClipPath(_rounded_path(image.width(), image.height(), radius))
        mask_painter.drawImage(0, 0, image)
        mask_painter.end()
        source = masked

    if shadow > 0:
        colour = QColor(shadow_color)
        if not colour.isValid():
            colour = QColor(0, 0, 0, 51)
        path = _rounded_path(image.width(), image.height(), radius or 8)
        painter.translate(margin + offset, margin + offset)
        painter.fillPath(path, colour)
        painter.translate(-(margin + offset), -(margin + offset))

    painter.drawImage(margin, margin, source)
    painter.end()
    log_debug(T("美化导出: 留白 {margin}px、圆角 {radius}px、投影 {shadow}px",
                margin=margin, radius=radius, shadow=shadow), "Beautify")
    return output


def export_sizes(image, widths=EXPORT_WIDTHS, *, allow_upscale: bool = False):
    """按目标宽度等比缩放，返回 ``[(宽度, QImage), ...]``。

    默认不放大小图——把 600px 的截图拉成 1920px 只会得到一张糊图，那不算「多尺寸导出」。
    """
    if image is None or image.isNull():
        return []
    result = []
    for target in widths:
        try:
            width = int(target)
        except (TypeError, ValueError):
            continue
        if width <= 0:
            continue
        if width > image.width() and not allow_upscale:
            log_debug(T("跳过放大到 {width}px（原图更窄）", width=width), "Beautify")
            continue
        if width == image.width():
            scaled = image.copy()
        else:
            scaled = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        result.append((width, scaled))
    if not result:
        log_warning(T("没有可导出的尺寸（原图比所有目标都窄）"), "Beautify")
    return result
