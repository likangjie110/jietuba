# -*- coding: utf-8 -*-
"""第二批标注工具的画布元素：水印、直线、像素补丁（滤镜 / 智能擦除）、插入图片。

这一批的共同点是「作用对象是底图像素」：滤镜与智能擦除要读底图、算出结果像素，水印与
插入图片则是贴上去的新内容。像素处理一律在**工具**里一次算完（工具拿得到底图的
QImage），元素只负责把结果画出来——这样撤销就是「删掉这个元素」，不必记录像素历史，
导出时也只是一次普通的场景渲染。

只有一个例外：水印是矢量平铺（跟着它的矩形现画），因为它是唯一一个需要按参数随时重排
的元素（改角度/间距/字号都不该重新光栅化）。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem

#: 滤镜种类（PixelPatchItem.kind）
FILTER_KINDS = ("grayscale", "invert", "blur", "emboss")

#: 像素补丁元素的种类：滤镜区域 / 智能擦除的补丁
PATCH_FILTER = "filter"
PATCH_ERASE = "erase"


class WatermarkItem(QGraphicsItem):
    """平铺水印：在给定矩形里按角度与间距重复一段文字。

    平铺在**旋转后的坐标系**里做：先转到中心、旋转、再在一个对角线见方的范围里按行列
    铺字，这样斜着的水印也能盖满整块区域而不留角上的空白。
    """

    DEFAULT_TEXT = "jietuba"
    Z_VALUE = 8          # 马赛克/幕布之上、普通标注之下

    def __init__(self, rect: QRectF, text: str = DEFAULT_TEXT, *, font_size: int = 28,
                 color: QColor | None = None, angle: float = -30.0, gap: int = 120,
                 opacity: float = 0.35):
        super().__init__()
        self._rect = QRectF(rect).normalized()
        self._text = str(text or self.DEFAULT_TEXT)
        self._font_size = max(6, int(font_size))
        self._color = QColor(color) if color is not None else QColor(255, 255, 255)
        self._angle = float(angle)
        self._gap = max(10, int(gap))
        self._alpha = max(0.05, min(1.0, float(opacity)))
        self.setZValue(self.Z_VALUE)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        # 水印不该抢走标注的选中：它是一层「底纹」
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

    # ── 属性 ──

    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    @property
    def text(self) -> str:
        return self._text

    @property
    def font_size(self) -> int:
        return self._font_size

    @property
    def angle(self) -> float:
        return self._angle

    @property
    def gap(self) -> int:
        return self._gap

    @property
    def color(self) -> QColor:
        return QColor(self._color)

    def state(self) -> dict:
        """可撤销/可存模板的参数快照。"""
        return {
            "text": self._text, "font_size": self._font_size, "color": QColor(self._color),
            "angle": self._angle, "gap": self._gap, "alpha": self._alpha,
            "rect": QRectF(self._rect),
        }

    def apply_state(self, state: dict) -> None:
        """按快照改参数（撤销与模板套用走这里）。"""
        if "rect" in state and state["rect"] is not None:
            self._rect = QRectF(state["rect"]).normalized()
        if "text" in state:
            self._text = str(state["text"] or self.DEFAULT_TEXT)
        if "font_size" in state:
            self._font_size = max(6, int(state["font_size"]))
        if state.get("color") is not None:
            self._color = QColor(state["color"])
        if "angle" in state:
            self._angle = float(state["angle"])
        if "gap" in state:
            self._gap = max(10, int(state["gap"]))
        if "alpha" in state:
            self._alpha = max(0.05, min(1.0, float(state["alpha"])))
        self.prepareGeometryChange()
        self.update()

    # ── 绘制 ──

    def boundingRect(self) -> QRectF:
        pad = self._font_size * 2 + self._gap
        return self._rect.adjusted(-pad, -pad, pad, pad)

    def _font(self) -> QFont:
        font = QFont()
        font.setPixelSize(self._font_size)
        font.setBold(True)
        return font

    def paint(self, painter: QPainter, _option, _widget=None):
        if self._rect.isEmpty():
            return
        text = self._text
        font = self._font()
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(text)
        text_height = metrics.height()
        if text_width <= 0:
            return

        center = self._rect.center()
        span = math.hypot(self._rect.width(), self._rect.height())
        step_x = max(20.0, text_width + self._gap)
        step_y = max(12.0, text_height + self._gap)

        painter.save()
        painter.setClipRect(self._rect)
        painter.setOpacity(self._opacity_value())
        painter.setFont(font)
        painter.setPen(QPen(self._color))
        painter.translate(center)
        painter.rotate(self._angle)
        # 旋转后在 ±span/2 的方形里铺满；行列比需求多一层，保证边界处不留白
        start = -span / 2.0
        rows = int(span / step_y) + 2
        columns = int(span / step_x) + 2
        for row in range(rows):
            y = start + row * step_y
            for column in range(columns):
                x = start + column * step_x
                painter.drawText(QPointF(x, y), text)
        painter.restore()

    @property
    def alpha(self) -> float:
        """水印自身的淡度（不占用元素的不透明度，免得两处叠加）。"""
        return self._alpha

    def _opacity_value(self) -> float:
        return self._alpha

    def set_alpha(self, alpha: float) -> None:
        self._alpha = max(0.05, min(1.0, float(alpha)))
        self.update()


class LineItem(QGraphicsPathItem):
    """直线：两端点 + 虚线样式（与矩形/椭圆的线型词汇一致）。"""

    def __init__(self, start: QPointF, end: QPointF, pen: QPen, line_style: str = "solid"):
        super().__init__()
        self._line_style = line_style if line_style in ("solid", "dashed", "dashed_dense") else "solid"
        pen = QPen(pen)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._apply_style(pen)
        self.setPen(pen)
        self._start = QPointF(start)
        self._end = QPointF(end)
        self.update_geometry()

    @property
    def line_style(self) -> str:
        return self._line_style

    def endpoints(self) -> tuple:
        return QPointF(self._start), QPointF(self._end)

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self._start = QPointF(start)
        self._end = QPointF(end)
        self.prepareGeometryChange()
        self.update_geometry()

    def set_line_style(self, line_style: str) -> None:
        if line_style not in ("solid", "dashed", "dashed_dense"):
            return
        self._line_style = line_style
        pen = QPen(self.pen())
        self._apply_style(pen)
        self.setPen(pen)

    def _apply_style(self, pen: QPen) -> None:
        if self._line_style == "dashed":
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([3, 2])
        elif self._line_style == "dashed_dense":
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([1, 2])
        else:
            pen.setStyle(Qt.PenStyle.SolidLine)
            pen.setDashPattern([])

    def update_geometry(self) -> None:
        path = QPainterPath(self._start)
        path.lineTo(self._end)
        self.setPath(path)


class PixelPatchItem(QGraphicsItem):
    """一块「算好的像素」压在指定矩形上：滤镜结果与智能擦除的补丁共用。

    元素不重算像素（那是工具的事），只把 QImage 画出来；``kind`` 与 ``params`` 用于
    撤销与模板套用时的自述。
    """

    def __init__(self, rect: QRectF, image: QImage, *, kind: str = PATCH_FILTER,
                 params: dict | None = None):
        super().__init__()
        self._rect = QRectF(rect).normalized()
        self._image = image
        self._kind = kind
        self._params = dict(params or {})
        self.setZValue(9)          # 压在马赛克之上、标注之下
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def params(self) -> dict:
        return dict(self._params)

    @property
    def image(self) -> QImage:
        return self._image

    def boundingRect(self) -> QRectF:
        return QRectF(self._rect)

    def paint(self, painter: QPainter, _option, _widget=None):
        if self._image is None or self._image.isNull() or self._rect.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(self._rect, self._image)


class InsertedImageItem(QGraphicsItem):
    """贴到画布上的外部图片：按保持比例缩放到目标框里，可移动。"""

    def __init__(self, image: QImage, rect: QRectF, *, opacity: float = 1.0):
        super().__init__()
        self._image = image
        self._pixmap = QPixmap.fromImage(image) if image is not None else QPixmap()
        self._rect = QRectF(rect).normalized()
        self.setOpacity(max(0.05, min(1.0, float(opacity))))
        self.setZValue(15)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

    @property
    def image(self) -> QImage:
        return self._image

    @property
    def target_rect(self) -> QRectF:
        return QRectF(self._rect)

    def set_target_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(rect).normalized()
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(self._rect)

    def paint(self, painter: QPainter, _option, _widget=None):
        if self._pixmap.isNull() or self._rect.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self._rect, self._pixmap, QRectF(self._pixmap.rect()))


def fit_rect(source_size, target: QRectF, *, keep_aspect: bool = True) -> QRectF:
    """把源尺寸按比例缩放到目标框里（居中）；``keep_aspect`` 关掉时直接铺满。"""
    if target.isEmpty() or source_size[0] <= 0 or source_size[1] <= 0:
        return QRectF(target)
    if not keep_aspect:
        return QRectF(target)
    scale = min(target.width() / source_size[0], target.height() / source_size[1])
    width = source_size[0] * scale
    height = source_size[1] * scale
    return QRectF(target.x() + (target.width() - width) / 2,
                  target.y() + (target.height() - height) / 2,
                  width, height)


class LoupeItem(QGraphicsItem):
    """局部放大：把图上的一处细节按倍数放大后贴在旁边（带边框与引线）。

    源框由工具拖出来，放大用的像素是**松手那一刻**从底图取的一份拷贝——所以它是一张
    「二次取景」，不是活引用：之后在源区域上再画东西不会进放大图。这样导出结果稳定，
    也避免每次重绘都去读场景。
    """

    def __init__(self, source_rect: QRectF, image: QImage, *, zoom: float = 2.0,
                 offset: float = 18.0):
        super().__init__()
        self._source = QRectF(source_rect).normalized()
        self._image = image
        self._zoom = max(1.1, min(8.0, float(zoom)))
        self._offset = max(0.0, float(offset))
        self.setZValue(20)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

    @property
    def source_rect(self) -> QRectF:
        return QRectF(self._source)

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def image(self) -> QImage:
        return self._image

    def target_rect(self) -> QRectF:
        """放大图的落点：源框右下方 offset 处，尺寸 = 源框 × 倍数。"""
        return QRectF(self._source.right() + self._offset,
                      self._source.bottom() + self._offset,
                      self._source.width() * self._zoom,
                      self._source.height() * self._zoom)

    def boundingRect(self) -> QRectF:
        return self._source.united(self.target_rect()).adjusted(-2, -2, 2, 2)

    def paint(self, painter: QPainter, _option, _widget=None):
        if self._image is None or self._image.isNull() or self._source.isEmpty():
            return
        target = self.target_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        pen = QPen(QColor(30, 136, 229), 1.5)
        painter.setPen(pen)
        painter.drawLine(self._source.center(), target.center())
        painter.drawImage(target, self._image)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target)
        painter.drawRect(self._source)
