# -*- coding: utf-8 -*-
"""第二批标注工具：直线 / 水印 / 滤镜 / 智能擦除 / 插入图片。

每条用例都走**发布路径**：真的工具对象 + 真 `CanvasScene` + 真 `ToolController` 分发
（`on_press/on_move/on_release`）+ 真 `ExportService.export` 渲染，最后按像素断言效果，
并断言撤销之后画布回到原样。没有一条只断言「没抛异常」。
"""


import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage

from canvas.items import (PATCH_ERASE, PATCH_FILTER, InsertedImageItem, LineItem,
                          PixelPatchItem, WatermarkItem)
from canvas.scene import CanvasScene
from core.export import ExportService
from tools.annotation import (apply_filter, estimate_background_color)

CANVAS = (240, 160)


def make_background(color="#3366CC", width=CANVAS[0], height=CANVAS[1]) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


@pytest.fixture
def scene(qapp):
    """真场景：背景是纯色图，选区已确认（导出要选区矩形）。"""
    canvas = CanvasScene(make_background(), QRectF(0, 0, CANVAS[0], CANVAS[1]), enable_mosaic=False)
    canvas.selection_model.activate()
    canvas.selection_model.set_rect(QRectF(0, 0, CANVAS[0], CANVAS[1]))
    yield canvas
    canvas.clear()


def export(scene) -> QImage:
    return ExportService(scene).export(QRectF(0, 0, CANVAS[0], CANVAS[1]))


def ink_pixels(image: QImage, predicate) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if predicate(image.pixelColor(x, y)):
                count += 1
    return count


def dispatch(scene, tool_id, points, *, button=Qt.MouseButton.LeftButton):
    """像用户那样按下→移动→松开（走真实的 ToolController 分发）。"""
    controller = scene.tool_controller
    controller.activate(tool_id)
    assert controller.current_tool is not None and controller.current_tool.id == tool_id, (
        f"工具没切换成功: {tool_id}")
    points = list(points)
    controller.on_press(QPointF(*points[0]), button)
    for point in points[1:-1]:
        controller.on_move(QPointF(*point))
    controller.on_release(QPointF(*points[-1]))


def items_of(scene, cls) -> list:
    return [item for item in scene.items() if isinstance(item, cls)]


def drop_items(scene, cls) -> None:
    """只清掉某一类元素（不要用 scene.clear()：那会把选区框一起删掉，导出就没法用了）。"""
    for item in items_of(scene, cls):
        scene.removeItem(item)


# ── 直线 ──────────────────────────────────────────────

class TestLineTool:
    def test_dragging_creates_a_line_with_the_pen_and_style(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        manager = get_tool_settings_manager()
        manager.update_settings("line", line_style="solid")

        dispatch(scene, "line", [(20, 120), (120, 60), (200, 30)])

        lines = items_of(scene, LineItem)
        assert len(lines) == 1
        start, end = lines[0].endpoints()
        assert (start.x(), start.y()) == (20, 120)
        assert (end.x(), end.y()) == (200, 30)
        assert lines[0].pen().color().name().lower() == "#ff0000"   # 默认画笔色

    def test_the_exported_image_has_ink_along_the_line(self, scene, isolated_tool_settings):
        before = ink_pixels(export(scene), lambda color: color.green() > 150)
        dispatch(scene, "line", [(20, 80), (100, 80), (220, 80)])
        after = export(scene)
        line_ink = ink_pixels(after, lambda color: color.red() > 150 and color.blue() < 120)
        assert line_ink > 100, f"线上没有笔墨: {line_ink}"
        assert ink_pixels(after, lambda color: color.green() > 150) < before + 10

    def test_dashed_line_paints_less_than_a_solid_one(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        manager = get_tool_settings_manager()
        manager.update_settings("line", line_style="dashed_dense")
        dispatch(scene, "line", [(10, 80), (120, 80), (230, 80)])
        dashed_ink = ink_pixels(export(scene), lambda color: color.red() > 150 and color.blue() < 120)

        drop_items(scene, LineItem)
        manager.update_settings("line", line_style="solid")
        dispatch(scene, "line", [(10, 80), (120, 80), (230, 80)])
        solid_ink = ink_pixels(export(scene), lambda color: color.red() > 150 and color.blue() < 120)

        assert 0 < dashed_ink < solid_ink

    def test_undo_removes_the_line(self, scene, isolated_tool_settings):
        dispatch(scene, "line", [(10, 10), (100, 100), (200, 140)])
        assert len(items_of(scene, LineItem)) == 1

        scene.undo_stack.undo()

        assert items_of(scene, LineItem) == []
        assert ink_pixels(export(scene), lambda color: color.red() > 150 and color.blue() < 120) == 0

    def test_a_tiny_drag_is_discarded(self, scene, isolated_tool_settings):
        dispatch(scene, "line", [(50, 50), (51, 51), (52, 52)])
        assert items_of(scene, LineItem) == []


# ── 水印 ──────────────────────────────────────────────

class TestWatermarkTool:
    def test_dragging_creates_a_watermark_with_settings(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings(
            "watermark", text="secret", font_size=20, angle=-45.0, gap=40, opacity=0.5)
        dispatch(scene, "watermark", [(10, 10), (150, 100), (230, 150)])

        marks = items_of(scene, WatermarkItem)
        assert len(marks) == 1
        mark = marks[0]
        assert mark.text == "secret"
        assert mark.font_size == 20
        assert mark.angle == -45.0
        assert mark.gap == 40
        assert abs(mark.alpha - 0.5) < 1e-6
        assert mark.rect == QRectF(10, 10, 220, 140)

    def test_the_exported_image_carries_watermark_text_in_the_area(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings(
            "watermark", text="JTB", font_size=28, angle=0.0, gap=30, opacity=1.0, color="#FFFFFF")
        dispatch(scene, "watermark", [(10, 30), (220, 100), (230, 140)])

        exported = export(scene)
        # 白字画在蓝底上：区域内出现白色像素，区域外没有
        inside = ink_pixels(exported.copy(10, 30, 220, 110), lambda color: color.red() > 220 and color.blue() > 220)
        below = ink_pixels(exported.copy(0, 145, CANVAS[0], 15), lambda color: color.red() > 220)
        assert inside > 200, f"水印没画出来: {inside}"
        assert below == 0

    def test_angle_changes_where_the_text_lands(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        manager = get_tool_settings_manager()
        manager.update_settings("watermark", text="JTB", font_size=30, angle=0.0, gap=30,
                                opacity=1.0, color="#FFFFFF")
        dispatch(scene, "watermark", [(20, 20), (120, 80), (220, 140)])
        flat = ink_pixels(export(scene), lambda color: color.red() > 220 and color.green() > 220)

        drop_items(scene, WatermarkItem)
        manager.update_settings("watermark", angle=-60.0)
        dispatch(scene, "watermark", [(20, 20), (120, 80), (220, 140)])
        tilted = ink_pixels(export(scene), lambda color: color.red() > 220 and color.green() > 220)

        assert flat > 0 and tilted > 0 and flat != tilted

    def test_undo_removes_the_watermark(self, scene, isolated_tool_settings):
        dispatch(scene, "watermark", [(10, 10), (120, 80), (230, 150)])
        assert len(items_of(scene, WatermarkItem)) == 1
        white_before = ink_pixels(export(scene), lambda color: color.red() > 220 and color.green() > 220)

        scene.undo_stack.undo()

        assert items_of(scene, WatermarkItem) == []
        assert ink_pixels(export(scene), lambda color: color.red() > 220 and color.green() > 220) == 0
        assert white_before > 0


# ── 滤镜 ──────────────────────────────────────────────

class TestFilterMath:
    """滤镜本身是纯函数，直接对像素断言（不经过画布）。"""

    def test_grayscale_equalises_the_channels(self):
        source = make_background("#FF0000", 8, 8)
        out = apply_filter(source, "grayscale")
        color = out.pixelColor(4, 4)
        assert color.red() == color.green() == color.blue()
        assert 70 < color.red() < 80            # 0.299 * 255 ≈ 76

    def test_invert_flips_every_channel(self):
        source = make_background("#102030", 8, 8)
        color = apply_filter(source, "invert").pixelColor(4, 4)
        assert (color.red(), color.green(), color.blue()) == (255 - 0x10, 255 - 0x20, 255 - 0x30)

    def test_blur_lowers_the_variance(self):
        source = QImage(24, 24, QImage.Format.Format_ARGB32)
        for y in range(24):
            for x in range(24):
                source.setPixelColor(x, y, QColor(255, 255, 255) if (x + y) % 2 == 0 else QColor(0, 0, 0))
        sharp = _variance(source)
        blurred = _variance(apply_filter(source, "blur", radius=3))
        assert blurred < sharp * 0.5

    def test_emboss_lifts_flat_areas_towards_grey(self):
        source = make_background("#404040", 8, 8)
        color = apply_filter(source, "emboss", strength=1.0).pixelColor(4, 4)
        assert abs(color.red() - 128) <= 6      # 平坦区卷积为 0，加 128 后是中灰

    def test_unknown_kind_returns_the_source(self):
        source = make_background("#123456", 4, 4)
        out = apply_filter(source, "nonsense")
        assert out.pixelColor(1, 1) == source.pixelColor(1, 1)


def _variance(image: QImage) -> float:
    values = [image.pixelColor(x, y).red() for y in range(image.height()) for x in range(image.width())]
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


class TestFilterTool:
    def test_dragging_applies_the_filter_to_the_area_only(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings("filter", kind="grayscale", radius=4)
        before = export(scene)

        dispatch(scene, "filter", [(40, 40), (120, 80), (200, 120)])

        patches = items_of(scene, PixelPatchItem)
        assert len(patches) == 1
        assert patches[0].kind == PATCH_FILTER
        assert patches[0].params["kind"] == "grayscale"
        assert patches[0].rect == QRectF(40, 40, 160, 80)

        after = export(scene)
        # 区域里变成灰（三通道相等），区域外还是原来的蓝
        inside = after.pixelColor(120, 80)
        assert inside.red() == inside.green() == inside.blue()
        outside = after.pixelColor(10, 10)
        assert outside == before.pixelColor(10, 10) == QColor("#3366CC")

    def test_invert_filter_turns_the_area_dark(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings("filter", kind="invert")
        dispatch(scene, "filter", [(20, 20), (100, 60), (200, 130)])

        color = export(scene).pixelColor(100, 60)
        assert (color.red(), color.green(), color.blue()) == (255 - 0x33, 255 - 0x66, 255 - 0xCC)

    def test_undo_restores_the_pixels(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings("filter", kind="invert")
        original = export(scene)
        dispatch(scene, "filter", [(20, 20), (100, 60), (200, 130)])
        assert export(scene).pixelColor(100, 60) != original.pixelColor(100, 60)

        scene.undo_stack.undo()

        assert items_of(scene, PixelPatchItem) == []
        assert export(scene).pixelColor(100, 60) == original.pixelColor(100, 60)

    def test_a_tiny_drag_is_ignored(self, scene, isolated_tool_settings):
        dispatch(scene, "filter", [(30, 30), (31, 31), (32, 32)])
        assert items_of(scene, PixelPatchItem) == []


# ── 智能擦除 ──────────────────────────────────────────

class TestEstimateBackground:
    def test_picks_the_surrounding_colour(self):
        from PySide6.QtGui import QPainterPath

        image = make_background("#FFFFFF", 60, 60)
        painter_image = image
        from PySide6.QtGui import QPainter, QPen
        painter = QPainter(painter_image)
        painter.setPen(QPen(QColor("#000000"), 10))
        painter.drawLine(10, 30, 50, 30)
        painter.end()

        path = QPainterPath(QPointF(0, 25))
        path.lineTo(QPointF(60, 25))
        path.lineTo(QPointF(60, 35))
        path.lineTo(QPointF(0, 35))
        path.closeSubpath()

        color = estimate_background_color(image, path, margin=6)
        assert color.red() > 200 and color.green() > 200 and color.blue() > 200


class TestSmartErase:
    @staticmethod
    def _is_black(color) -> bool:
        """黑线判据：三个通道都低。蓝底 (#3366CC) 的红通道也只有 51，只看红会把它算进去。"""
        return color.red() < 60 and color.green() < 60 and color.blue() < 60

    def _canvas_with_stroke(self, scene):
        """在底图上画一条黑色横线（当作要被抹掉的内容）。"""
        from PySide6.QtGui import QPainter, QPen

        image = scene.background.image()
        painter = QPainter(image)
        painter.setPen(QPen(QColor("#000000"), 14))
        painter.drawLine(20, 80, 220, 80)
        painter.end()
        scene.background.update_image(image)

    def test_erasing_fills_the_area_with_the_background(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings("smart_erase", brush_width=26)
        self._canvas_with_stroke(scene)
        dark_before = ink_pixels(export(scene), self._is_black)

        dispatch(scene, "smart_erase", [(30, 80), (100, 80), (200, 80)])

        patches = items_of(scene, PixelPatchItem)
        assert len(patches) == 1
        assert patches[0].kind == PATCH_ERASE

        after = export(scene)
        dark_after = ink_pixels(after, self._is_black)
        assert dark_after < dark_before * 0.25, f"擦除没盖住黑线: {dark_before} -> {dark_after}"
        # 擦除的语义是「用周围底色填平」：擦过的地方应当等于底图原本的颜色
        erased = after.pixelColor(120, 80)
        original = make_background().pixelColor(120, 80)
        assert abs(erased.red() - original.red()) <= 8
        assert abs(erased.green() - original.green()) <= 8
        assert abs(erased.blue() - original.blue()) <= 8

    def test_undo_brings_the_painted_line_back(self, scene, isolated_tool_settings):
        self._canvas_with_stroke(scene)
        before = ink_pixels(export(scene), self._is_black)
        assert before > 0
        dispatch(scene, "smart_erase", [(30, 80), (120, 80), (210, 80)])
        assert ink_pixels(export(scene), self._is_black) < before

        scene.undo_stack.undo()

        assert items_of(scene, PixelPatchItem) == []
        assert ink_pixels(export(scene), self._is_black) == before


# ── 插入图片 ──────────────────────────────────────────

class TestInsertImageTool:
    def test_clicking_places_the_chosen_image(self, scene, isolated_tool_settings, tmp_path):
        source = make_background("#00FF00", 80, 40)
        path = tmp_path / "logo.png"
        assert source.save(str(path), "PNG")

        controller = scene.tool_controller
        controller.activate("insert_image")
        context = controller.context
        original_picker = getattr(context, "image_picker", None)
        context.image_picker = lambda: QImage(str(path))
        try:
            controller.on_press(QPointF(120, 80), Qt.MouseButton.LeftButton)
        finally:
            if original_picker is None:
                del context.image_picker
            else:
                context.image_picker = original_picker

        inserted = items_of(scene, InsertedImageItem)
        assert len(inserted) == 1
        item = inserted[0]
        # 保持比例：80x40 的图缩到长边 240 -> 240x120，居中在点击点
        assert item.target_rect.width() == 240
        assert item.target_rect.height() == 120
        assert item.target_rect.center() == QPointF(120, 80)

        green = ink_pixels(export(scene), lambda color: color.green() > 200 and color.red() < 80)
        assert green > 1000, f"插入的图片没画到画布上: {green}"

    def test_cancelling_the_picker_inserts_nothing(self, scene, isolated_tool_settings):
        controller = scene.tool_controller
        controller.activate("insert_image")
        context = controller.context
        original_picker = getattr(context, "image_picker", None)
        context.image_picker = lambda: None
        try:
            controller.on_press(QPointF(60, 60), Qt.MouseButton.LeftButton)
        finally:
            if original_picker is None:
                del context.image_picker
            else:
                context.image_picker = original_picker

        assert items_of(scene, InsertedImageItem) == []

    def test_undo_removes_the_inserted_image(self, scene, isolated_tool_settings):
        source = make_background("#00FF00", 60, 60)
        controller = scene.tool_controller
        controller.activate("insert_image")
        context = controller.context
        original_picker = getattr(context, "image_picker", None)
        context.image_picker = lambda: source
        try:
            controller.on_press(QPointF(120, 80), Qt.MouseButton.LeftButton)
        finally:
            if original_picker is None:
                del context.image_picker
            else:
                context.image_picker = original_picker

        assert ink_pixels(export(scene), lambda color: color.green() > 200 and color.red() < 80) > 1000

        scene.undo_stack.undo()

        assert items_of(scene, InsertedImageItem) == []
        assert ink_pixels(export(scene), lambda color: color.green() > 200 and color.red() < 80) == 0
