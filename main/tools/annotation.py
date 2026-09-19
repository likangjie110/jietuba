# -*- coding: utf-8 -*-
"""第二批标注工具：直线、水印、滤镜、智能擦除、插入图片。

每个工具负责「把参数变成元素」这一步；像素类工具（滤镜 / 智能擦除）自己读底图算结果，
元素只存结果。所有新建元素都经 ``AddItemCommand`` 进撤销栈，撤销 = 移除元素。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QPainter, QPainterPath, QPainterPathStroker,
                           QPen)
from PySide6.QtWidgets import QGraphicsRectItem

from canvas.items import (
    LoupeItem,
    PATCH_ERASE, PATCH_FILTER, InsertedImageItem, LineItem, PixelPatchItem, WatermarkItem,
)
from canvas.undo import AddItemCommand
from core.i18n import make_tr
from core.logger import T, log_debug, log_exception

from .base import Tool, ToolContext, color_with_opacity

_tr = make_tr("InsertImageTool")

#: 水印默认参数（设置页可改，工具从工具设置里读）
WATERMARK_DEFAULTS = {
    "text": "jietuba",
    "font_size": 28,
    "angle": -30.0,
    "gap": 120,
    "opacity": 0.35,
    "color": "#FFFFFF",
}

#: 滤镜默认参数
FILTER_DEFAULTS = {
    "kind": "grayscale",
    "radius": 6,          # 高斯模糊半径
    "strength": 1.0,      # 浮雕强度
}

#: 局部放大默认参数
LOUPE_DEFAULTS = {
    "zoom": 2.0,
}

#: 智能擦除默认参数
ERASE_DEFAULTS = {
    "brush_width": 40,
    "sample_margin": 6,   # 从笔画外侧取样以估计背景色
}


def read_tool_settings(ctx: ToolContext, tool_id: str) -> dict:
    """读某个工具的设置；读不到就是空表（调用方用自己的默认值兜底）。

    注意 ``get_tool_settings`` 返回的是 ``ToolSettings`` 对象而不是 dict：它只提供
    ``get``/``to_dict`` 之类的接口，直接 ``dict(...)`` 会抛 TypeError——那会让每个
    工具都静默退回默认值（真机表现是「改了参数没反应」）。
    """
    manager = getattr(ctx, "settings_manager", None)
    if manager is None:
        return {}
    try:
        tool_settings = manager.get_tool_settings(tool_id)
        if tool_settings is None:
            return {}
        if hasattr(tool_settings, "to_dict"):
            return dict(tool_settings.to_dict())
        return dict(tool_settings)
    except Exception as e:
        log_exception(e, T("读取工具设置: {tool_id}", tool_id=tool_id))
        return {}


def _background_image(ctx: ToolContext):
    """当前场景的底图 QImage；没有背景层时返回 None。"""
    background = getattr(getattr(ctx, "scene", None), "background", None)
    if background is None:
        return None
    try:
        return background.image()
    except Exception as e:
        log_exception(e, T("读取底图"))
        return None


def background_origin(ctx: ToolContext) -> QPointF:
    """底图左上角在场景坐标里的位置（scene_rect 的左上角）。"""
    background = getattr(getattr(ctx, "scene", None), "background", None)
    if background is None:
        return QPointF(0, 0)
    try:
        return QPointF(background.sceneBoundingRect().topLeft())
    except Exception:
        return QPointF(0, 0)


def grab_background(ctx: ToolContext, rect: QRectF) -> QImage | None:
    """从底图上裁出场景矩形对应的那块像素。"""
    image = _background_image(ctx)
    if image is None or image.isNull():
        return None
    origin = background_origin(ctx)
    x = int(round(rect.x() - origin.x()))
    y = int(round(rect.y() - origin.y()))
    width = max(1, int(round(rect.width())))
    height = max(1, int(round(rect.height())))
    if x < 0 or y < 0 or x + width > image.width() or y + height > image.height():
        # 选区可能超出底图（多屏拼接或边缘），裁到有效范围内并补边
        patch = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        patch.fill(QColor(0, 0, 0, 0))
        source = image.copy(max(0, x), max(0, y),
                            max(1, min(width, image.width() - max(0, x))),
                            max(1, min(height, image.height() - max(0, y))))
        painter = QPainter(patch)
        painter.drawImage(max(0, -x), max(0, -y), source)
        painter.end()
        return patch
    return image.copy(x, y, width, height)


def apply_filter(image: QImage, kind: str, *, radius: int = 6, strength: float = 1.0) -> QImage:
    """对一块像素施加滤镜；认不出的种类返回原图的拷贝。

    - grayscale：按 Rec.601 权重去色（保留原 alpha）
    - invert：按通道取反
    - blur：盒式模糊（半径次两趟横竖滑动平均，够用且实现无依赖）
    - emboss：与 3×3 卷积核做浮雕，再整体提亮到中灰
    """
    if image is None or image.isNull():
        return QImage()
    source = image.convertToFormat(QImage.Format.Format_ARGB32)
    if kind == "invert":
        out = QImage(source)
        out.invertPixels(QImage.InvertMode.InvertRgb)
        return out
    if kind == "grayscale":
        out = QImage(source.size(), QImage.Format.Format_ARGB32)
        for y in range(source.height()):
            for x in range(source.width()):
                pixel = source.pixelColor(x, y)
                gray = int(0.299 * pixel.red() + 0.587 * pixel.green() + 0.114 * pixel.blue())
                out.setPixelColor(x, y, QColor(gray, gray, gray, pixel.alpha()))
        return out
    if kind == "blur":
        return _box_blur(source, max(1, int(radius)))
    if kind == "emboss":
        return _emboss(source, strength)
    return QImage(source)


def _box_blur(image: QImage, radius: int) -> QImage:
    """横竖两趟滑动平均（近似高斯）；纯 Python 实现，代价与半径无关。"""
    width, height = image.size().width(), image.size().height()
    window = radius * 2 + 1
    out = QImage(image.size(), QImage.Format.Format_ARGB32)
    # 先横后竖：每趟用前缀和算窗口均值
    horizontal = QImage(image.size(), QImage.Format.Format_ARGB32)
    for y in range(height):
        sums = [0, 0, 0, 0]
        for x in range(-radius, radius + 1):
            pixel = image.pixelColor(min(max(x, 0), width - 1), y)
            for index, value in enumerate((pixel.red(), pixel.green(), pixel.blue(), pixel.alpha())):
                sums[index] += value
        for x in range(width):
            horizontal.setPixelColor(x, y, QColor(*(value // window for value in sums)))
            left = image.pixelColor(max(x - radius, 0), y)
            right = image.pixelColor(min(x + radius + 1, width - 1), y)
            for index, (gone, added) in enumerate((
                    (left.red(), right.red()), (left.green(), right.green()),
                    (left.blue(), right.blue()), (left.alpha(), right.alpha()))):
                sums[index] += added - gone
    for x in range(width):
        sums = [0, 0, 0, 0]
        for y in range(-radius, radius + 1):
            pixel = horizontal.pixelColor(x, min(max(y, 0), height - 1))
            for index, value in enumerate((pixel.red(), pixel.green(), pixel.blue(), pixel.alpha())):
                sums[index] += value
        for y in range(height):
            out.setPixelColor(x, y, QColor(*(value // window for value in sums)))
            top = horizontal.pixelColor(x, max(y - radius, 0))
            bottom = horizontal.pixelColor(x, min(y + radius + 1, height - 1))
            for index, (gone, added) in enumerate((
                    (top.red(), bottom.red()), (top.green(), bottom.green()),
                    (top.blue(), bottom.blue()), (top.alpha(), bottom.alpha()))):
                sums[index] += added - gone
    return out


def _emboss(image: QImage, strength: float) -> QImage:
    """3×3 浮雕卷积（左上为负、右下为正），结果整体加 128 提亮。"""
    width, height = image.size().width(), image.size().height()
    out = QImage(image.size(), QImage.Format.Format_ARGB32)
    factor = max(0.1, float(strength))
    for y in range(height):
        for x in range(width):
            total = [0.0, 0.0, 0.0]
            for dy, weight_y in ((-1, -1), (0, 0), (1, 1)):
                for dx, weight_x in ((-1, -1), (0, 0), (1, 1)):
                    weight = weight_x * weight_y
                    if weight == 0:
                        continue
                    pixel = image.pixelColor(min(max(x + dx, 0), width - 1),
                                             min(max(y + dy, 0), height - 1))
                    total[0] += pixel.red() * weight * factor
                    total[1] += pixel.green() * weight * factor
                    total[2] += pixel.blue() * weight * factor
            alpha = image.pixelColor(x, y).alpha()
            out.setPixelColor(x, y, QColor(*[min(255, max(0, int(value + 128))) for value in total],
                                           alpha))
    return out


def estimate_background_color(image: QImage, path: QPainterPath, *, brush_width: int = 1,
                              margin: int = 6) -> QColor:
    """估计被涂区域「背后」的颜色：在笔画外扩的一圈里取样，取出现次数最多的颜色。

    取样环是「笔宽 + margin 的笔画」减去「笔宽笔画」得到的——直接把路径当内部是不行的：
    路径是**中线**（细到一条线），那样整条黑线都会被当成背景，估计出来的就是黑色。

    真做内容感知填充需要更多上下文；这里的目标是「把涂掉的那块用周围的底色抹平」，
    所以取周围一圈的众数：纯色底上等价于填充，纹理底上也不会糊成一片。
    """
    if image is None or image.isNull():
        return QColor(255, 255, 255)

    width = max(1, int(brush_width))
    inner = _stroked(path, width)
    outer = _stroked(path, width + max(0, int(margin)) * 2)

    bounds = outer.boundingRect().adjusted(-1, -1, 1, 1)
    x0 = max(0, int(bounds.left()))
    y0 = max(0, int(bounds.top()))
    x1 = min(image.width(), int(bounds.right()) + 1)
    y1 = min(image.height(), int(bounds.bottom()) + 1)

    votes: dict = {}
    for y in range(y0, y1):
        for x in range(x0, x1):
            point = QPointF(x + 0.5, y + 0.5)
            if inner.contains(point) or not outer.contains(point):
                continue
            pixel = image.pixelColor(x, y)
            key = (pixel.red() // 8, pixel.green() // 8, pixel.blue() // 8)
            votes[key] = votes.get(key, 0) + 1
    if not votes:
        return QColor(255, 255, 255)
    best = max(votes, key=votes.get)
    return QColor(best[0] * 8 + 4, best[1] * 8 + 4, best[2] * 8 + 4)


def _stroked(path: QPainterPath, width: float) -> QPainterPath:
    """把中线路径按给定宽度描成区域（取样环与填充范围都用它）。"""
    stroker = QPainterPathStroker()
    stroker.setWidth(max(1.0, float(width)))
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def build_erase_patch(image: QImage, path: QPainterPath, *, brush_width: int = 40,
                      color: QColor | None = None) -> QImage:
    """把笔画覆盖到的区域涂成背景色，得到一张与底图同尺寸的补丁图。"""
    patch = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    patch.fill(QColor(0, 0, 0, 0))
    fill = QColor(color) if color is not None else estimate_background_color(
        image, path, brush_width=brush_width)
    painter = QPainter(patch)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(fill, max(1, int(brush_width)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
    return patch


class LineTool(Tool):
    """直线工具：按下拖出一条直线，线型与画笔共用「实线/虚线/密虚线」。"""

    id = "line"
    MIN_LENGTH = 8.0

    def __init__(self):
        self.drawing = False
        self.start_pos = None
        self.current_item = None
        self.line_style = "solid"

    def on_press(self, pos: QPointF, button, ctx: ToolContext):
        if button != Qt.MouseButton.LeftButton:
            return
        settings = read_tool_settings(ctx, self.id)
        self.line_style = settings.get("line_style", "solid")
        # 粗细/颜色/透明度优先用这个工具自己的设置：模板与设置页写的是这些键，
        # 而工具栏改这些值时 ToolController 也会 save_settings 落进同一份（两边不会打架）
        color = settings.get("color") or ctx.color
        width = float(settings.get("stroke_width", ctx.stroke_width) or ctx.stroke_width)
        opacity = float(settings.get("opacity", ctx.opacity))
        pen = QPen(color_with_opacity(QColor(color), opacity), width)
        self.drawing = True
        self.start_pos = QPointF(pos)
        self.current_item = LineItem(pos, pos, pen, self.line_style)
        ctx.scene.addItem(self.current_item)

    def on_move(self, pos: QPointF, ctx: ToolContext):
        if self.drawing and self.current_item is not None:
            self.current_item.set_endpoints(self.start_pos, pos)

    def on_release(self, pos: QPointF, ctx: ToolContext):
        if not self.drawing:
            return
        self.drawing = False
        item = self.current_item
        self.current_item = None
        if item is None:
            return
        # 松手位置是权威终点：鼠标的最后一段移动不一定送得到 on_move
        item.set_endpoints(self.start_pos, pos)
        start, end = item.endpoints()
        if (abs(end.x() - start.x()) + abs(end.y() - start.y())) < self.MIN_LENGTH:
            ctx.scene.removeItem(item)
            return
        ctx.scene.removeItem(item)
        ctx.undo_stack.push_command(AddItemCommand(ctx.scene, item, text="Add Line"))
        ctx.scene.item_auto_select_requested.emit(item)
        log_debug(T("完成绘制: 直线 （{style}）", style=self.line_style), "LineTool")


class WatermarkTool(Tool):
    """水印工具：拖出一个矩形，在该范围内铺满文字水印。"""

    id = "watermark"
    MIN_SIZE = 12

    def __init__(self):
        self.drawing = False
        self.start_pos = None
        self.current_item = None

    def _parameters(self, ctx: ToolContext) -> dict:
        settings = {**WATERMARK_DEFAULTS, **read_tool_settings(ctx, self.id)}
        color = settings.get("color") or WATERMARK_DEFAULTS["color"]
        return {
            "text": str(settings.get("text") or WATERMARK_DEFAULTS["text"]),
            "font_size": int(settings.get("font_size", WATERMARK_DEFAULTS["font_size"])),
            "angle": float(settings.get("angle", WATERMARK_DEFAULTS["angle"])),
            "gap": int(settings.get("gap", WATERMARK_DEFAULTS["gap"])),
            "opacity": float(settings.get("opacity", WATERMARK_DEFAULTS["opacity"])),
            "color": QColor(color) if not isinstance(color, QColor) else color,
        }

    def on_press(self, pos: QPointF, button, ctx: ToolContext):
        if button != Qt.MouseButton.LeftButton:
            return
        parameters = self._parameters(ctx)
        self.drawing = True
        self.start_pos = QPointF(pos)
        self.current_item = WatermarkItem(QRectF(pos, pos), **parameters)
        ctx.scene.addItem(self.current_item)

    def on_move(self, pos: QPointF, ctx: ToolContext):
        if self.drawing and self.current_item is not None:
            self.current_item.apply_state({"rect": QRectF(self.start_pos, pos).normalized()})

    def on_release(self, pos: QPointF, ctx: ToolContext):
        if not self.drawing:
            return
        self.drawing = False
        item = self.current_item
        self.current_item = None
        if item is None:
            return
        # 松手位置是权威终点：最后一段移动不一定送得到 on_move
        item.apply_state({"rect": QRectF(self.start_pos, pos).normalized()})
        rect = item.rect
        if rect.width() < self.MIN_SIZE or rect.height() < self.MIN_SIZE:
            ctx.scene.removeItem(item)
            return
        ctx.scene.removeItem(item)
        ctx.undo_stack.push_command(AddItemCommand(ctx.scene, item, text="Add Watermark"))
        log_debug(T("完成绘制: 水印 {w}x{h}", w=int(rect.width()), h=int(rect.height())),
                  "WatermarkTool")


class FilterTool(Tool):
    """滤镜工具：拖出一个矩形，把该区域的底图像素按设置处理（灰度/反相/模糊/浮雕）。"""

    id = "filter"
    MIN_SIZE = 6

    def __init__(self):
        self.drawing = False
        self.start_pos = None
        self.preview_item = None

    def _parameters(self, ctx: ToolContext) -> dict:
        settings = {**FILTER_DEFAULTS, **read_tool_settings(ctx, self.id)}
        kind = str(settings.get("kind", FILTER_DEFAULTS["kind"]))
        if kind not in ("grayscale", "invert", "blur", "emboss"):
            kind = FILTER_DEFAULTS["kind"]
        return {
            "kind": kind,
            "radius": int(settings.get("radius", FILTER_DEFAULTS["radius"])),
            "strength": float(settings.get("strength", FILTER_DEFAULTS["strength"])),
        }

    def on_press(self, pos: QPointF, button, ctx: ToolContext):
        if button != Qt.MouseButton.LeftButton:
            return
        self.drawing = True
        self.start_pos = QPointF(pos)

    def on_move(self, pos: QPointF, ctx: ToolContext):
        """拖动期间只显示范围框（像素处理放到松手时做一次，避免每帧算全图）。"""
        if not self.drawing:
            return
        rect = QRectF(self.start_pos, pos).normalized()
        if self.preview_item is None:
            preview = RangePreview(rect)
            ctx.scene.addItem(preview)
            self.preview_item = preview
        else:
            self.preview_item.set_range(rect)

    def on_release(self, pos: QPointF, ctx: ToolContext):
        if not self.drawing:
            return
        self.drawing = False
        rect = QRectF(self.start_pos, pos).normalized()
        if self.preview_item is not None:
            ctx.scene.removeItem(self.preview_item)
            self.preview_item = None
        if rect.width() < self.MIN_SIZE or rect.height() < self.MIN_SIZE:
            return

        source = grab_background(ctx, rect)
        if source is None or source.isNull():
            log_exception(RuntimeError("no background"), T("应用滤镜"))
            return
        parameters = self._parameters(ctx)
        processed = apply_filter(source, parameters["kind"],
                                 radius=parameters["radius"],
                                 strength=parameters["strength"])
        item = PixelPatchItem(rect, processed, kind=PATCH_FILTER, params=parameters)
        ctx.undo_stack.push_command(AddItemCommand(ctx.scene, item, text="Add Filter"))
        log_debug(T("滤镜已应用: {kind} ({w}x{h})", kind=parameters["kind"],
                    w=int(rect.width()), h=int(rect.height())), "FilterTool")


class LoupeTool(Tool):
    """局部放大：拖出要放大的区域，松手后在旁边贴一份放大的副本。"""

    id = "loupe"
    MIN_SIZE = 16

    def __init__(self):
        self.drawing = False
        self.start_pos = None
        self.preview_item = None

    def _zoom(self, ctx: ToolContext) -> float:
        settings = read_tool_settings(ctx, self.id)
        try:
            return float(settings.get("zoom", LOUPE_DEFAULTS["zoom"]))
        except (TypeError, ValueError):
            return float(LOUPE_DEFAULTS["zoom"])

    def on_press(self, pos: QPointF, button, ctx: ToolContext):
        if button != Qt.MouseButton.LeftButton:
            return
        self.drawing = True
        self.start_pos = QPointF(pos)

    def on_move(self, pos: QPointF, ctx: ToolContext):
        if not self.drawing:
            return
        rect = QRectF(self.start_pos, pos).normalized()
        if self.preview_item is None:
            preview = RangePreview(rect)
            ctx.scene.addItem(preview)
            self.preview_item = preview
        else:
            self.preview_item.set_range(rect)

    def on_release(self, pos: QPointF, ctx: ToolContext):
        if not self.drawing:
            return
        self.drawing = False
        rect = QRectF(self.start_pos, pos).normalized()
        if self.preview_item is not None:
            ctx.scene.removeItem(self.preview_item)
            self.preview_item = None
        if rect.width() < self.MIN_SIZE or rect.height() < self.MIN_SIZE:
            return

        source = grab_background(ctx, rect)
        if source is None or source.isNull():
            log_exception(RuntimeError("no background"), T("创建局部放大"))
            return
        zoom = self._zoom(ctx)
        item = LoupeItem(rect, source, zoom=zoom)
        ctx.undo_stack.push_command(AddItemCommand(ctx.scene, item, text="Add Loupe"))
        ctx.scene.item_auto_select_requested.emit(item)
        log_debug(T("局部放大已创建: {zoom}x ({w}x{h})", zoom=zoom,
                    w=int(rect.width()), h=int(rect.height())), "LoupeTool")


class SmartEraseTool(Tool):
    """智能擦除：涂抹过的区域用周围的底色抹平（补丁图一次性算好）。"""

    id = "smart_erase"

    def __init__(self):
        self.erasing = False
        self.path = None

    def _parameters(self, ctx: ToolContext) -> dict:
        settings = {**ERASE_DEFAULTS, **read_tool_settings(ctx, self.id)}
        return {
            "brush_width": int(settings.get("brush_width", ERASE_DEFAULTS["brush_width"])),
            "sample_margin": int(settings.get("sample_margin", ERASE_DEFAULTS["sample_margin"])),
        }

    def on_press(self, pos: QPointF, button, ctx: ToolContext):
        if button != Qt.MouseButton.LeftButton:
            return
        self.erasing = True
        self.path = QPainterPath(pos)
        # 单点也要能擦：先画一个极短的线段
        self.path.lineTo(pos + QPointF(0.1, 0.1))

    def on_move(self, pos: QPointF, ctx: ToolContext):
        if self.erasing and self.path is not None:
            self.path.lineTo(pos)

    def on_release(self, pos: QPointF, ctx: ToolContext):
        if not self.erasing:
            return
        self.erasing = False
        path = self.path
        self.path = None
        if path is None:
            return
        # 松手位置是权威终点：最后一段移动不一定送得到 on_move
        path.lineTo(pos)

        parameters = self._parameters(ctx)
        # 补丁范围要把笔宽算进去：只取路径包围盒会把笔画的边缘切掉，露出没擦掉的原内容
        half = parameters["brush_width"] / 2.0 + 2.0
        area = path.boundingRect().adjusted(-half, -half, half, half)
        source = grab_background(ctx, area)
        if source is None or source.isNull():
            log_exception(RuntimeError("no background"), T("智能擦除"))
            return
        # 笔画坐标是场景坐标，裁出来的底图是局部坐标——平移后再算
        local_path = path.translated(-area.topLeft())
        fill = estimate_background_color(source, local_path,
                                         brush_width=parameters["brush_width"],
                                         margin=parameters["sample_margin"])
        patch = build_erase_patch(source, local_path, brush_width=parameters["brush_width"],
                                  color=fill)
        item = PixelPatchItem(area, patch, kind=PATCH_ERASE, params=parameters)
        ctx.undo_stack.push_command(AddItemCommand(ctx.scene, item, text="Smart Erase"))
        log_debug(T("智能擦除完成: {w}x{h}", w=int(area.width()), h=int(area.height())),
                  "SmartEraseTool")


class InsertImageTool(Tool):
    """插入图片：选一张本地图片贴到画布上（点一下放置，保持比例）。"""

    id = "insert_image"

    #: 未指定大小时插入图片的默认边长
    DEFAULT_EDGE = 240

    def on_press(self, pos: QPointF, button, ctx: ToolContext):
        if button != Qt.MouseButton.LeftButton:
            return
        image = self._pick_image(ctx)
        if image is None or image.isNull():
            return
        rect = self._target_rect(pos, image)
        item = InsertedImageItem(image, rect)
        ctx.undo_stack.push_command(AddItemCommand(ctx.scene, item, text="Insert Image"))
        ctx.scene.item_auto_select_requested.emit(item)
        log_debug(T("已插入图片: {w}x{h}", w=image.width(), h=image.height()), "InsertImageTool")

    def _pick_image(self, ctx: ToolContext):
        """选图片：测试可注入 ``ctx.image_picker``（默认弹系统文件对话框）。"""
        picker = getattr(ctx, "image_picker", None)
        if picker is not None:
            return picker()
        from PySide6.QtWidgets import QFileDialog

        path, _selected = QFileDialog.getOpenFileName(
            None, _tr("Insert Image"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;All files (*)")
        if not path or not os.path.exists(path):
            return None
        return QImage(path)

    def _target_rect(self, pos: QPointF, image: QImage) -> QRectF:
        edge = float(self.DEFAULT_EDGE)
        scale = edge / max(1, max(image.width(), image.height()))
        size = (image.width() * scale, image.height() * scale)
        return QRectF(pos.x() - size[0] / 2, pos.y() - size[1] / 2, size[0], size[1])


class RangePreview(QGraphicsRectItem):
    """滤镜拖动时的范围提示框（松手就移除，不参与导出）。"""

    def __init__(self, rect: QRectF):
        super().__init__(QRectF(rect).normalized())
        pen = QPen(QColor(0, 120, 215), 2, Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(60)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_range(self, rect: QRectF) -> None:
        self.setRect(QRectF(rect).normalized())


