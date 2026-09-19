# -*- coding: utf-8 -*-
"""标注「局部放大」：走发布路径（真工具 + 真场景 + 真分发 + 真导出渲染）。

判据是导出图上的像素：放大框里那一点应当等于源区域对应点的颜色——放大镜就是「同一块
像素按倍数重画一遍」，所以只要源图有可辨认的图案就能直接验。
"""

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from canvas.items import LoupeItem
from canvas.scene import CanvasScene
from core.export import ExportService

CANVAS = (240, 160)


def make_background() -> QImage:
    """左半红、右半蓝、左上角一小块绿：放大后颜色位置关系不变，好断言。"""
    image = QImage(CANVAS[0], CANVAS[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("#CC3333"))
    painter = QPainter(image)
    painter.fillRect(QRectF(120, 0, 120, 160), QColor("#3333CC"))
    painter.fillRect(QRectF(0, 0, 20, 20), QColor("#33CC33"))
    painter.end()
    return image


@pytest.fixture
def scene(qapp):
    canvas = CanvasScene(make_background(), QRectF(0, 0, CANVAS[0], CANVAS[1]),
                         enable_mosaic=False)
    canvas.selection_model.activate()
    canvas.selection_model.set_rect(QRectF(0, 0, CANVAS[0], CANVAS[1]))
    yield canvas
    canvas.clear()


def export(scene) -> QImage:
    return ExportService(scene).export(QRectF(0, 0, CANVAS[0], CANVAS[1]))


def dispatch(scene, tool_id, points, *, button=Qt.MouseButton.LeftButton):
    controller = scene.tool_controller
    controller.activate(tool_id)
    assert controller.current_tool is not None and controller.current_tool.id == tool_id
    controller.on_press(QPointF(*points[0]), button)
    for point in points[1:-1]:
        controller.on_move(QPointF(*point))
    controller.on_release(QPointF(*points[-1]))


def loupes(scene) -> list:
    return [item for item in scene.items() if isinstance(item, LoupeItem)]


class TestLoupeItem:
    def test_item_is_created_by_dragging(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(30, 30), (80, 60)])

        created = loupes(scene)
        assert len(created) == 1
        item = created[0]
        assert item.source_rect == QRectF(30, 30, 50, 30)
        assert item.zoom > 1.0

    def test_zoom_comes_from_the_tool_settings(self, scene, isolated_tool_settings):
        from settings import get_tool_settings_manager

        get_tool_settings_manager().update_settings("loupe", zoom=3.0)

        dispatch(scene, "loupe", [(30, 30), (80, 60)])

        assert loupes(scene)[0].zoom == pytest.approx(3.0)

    def test_a_tiny_drag_creates_nothing(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(30, 30), (34, 33)])
        assert loupes(scene) == []

    def test_undo_removes_the_loupe(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(30, 30), (80, 60)])
        assert loupes(scene)

        scene.undo_stack.undo()

        assert loupes(scene) == []


class TestLoupePixels:
    def test_the_magnified_copy_matches_the_source_pixels(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(20, 20), (60, 50)])
        item = loupes(scene)[0]
        target = item.target_rect()

        rendered = export(scene)

        # 放大框里 (dx, dy) 处对应源框里 (dx/zoom, dy/zoom)
        for dx, dy in ((2, 2), (10, 10), (30, 20)):
            source_pixel = make_background().pixelColor(
                20 + int(dx / item.zoom), 20 + int(dy / item.zoom))
            assert rendered.pixelColor(int(target.left()) + dx,
                                       int(target.top()) + dy) == source_pixel, (
                f"放大框里的 ({dx},{dy}) 与源图对应点不一致")

    def test_the_source_area_is_marked_but_not_replaced(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(20, 20), (60, 50)])
        item = loupes(scene)[0]

        rendered = export(scene)

        # 源框内部仍然是原图内容（放大镜是「旁边再画一份」，不是就地替换）。
        # 取样点避开中心：引线正好从源框中心出发，那里本来就有线条像素。
        inside = QPointF(item.source_rect.left() + 6, item.source_rect.top() + 6).toPoint()
        assert rendered.pixelColor(inside) == make_background().pixelColor(inside)

    def test_the_copy_is_larger_by_the_zoom_factor(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(20, 20), (60, 50)])
        item = loupes(scene)[0]

        target = item.target_rect()
        assert target.width() == pytest.approx(item.source_rect.width() * item.zoom)
        assert target.height() == pytest.approx(item.source_rect.height() * item.zoom)

    def test_the_connector_line_is_drawn(self, scene, isolated_tool_settings):
        dispatch(scene, "loupe", [(20, 20), (60, 50)])
        item = loupes(scene)[0]

        rendered = export(scene)

        start = item.source_rect.center()
        end = item.target_rect().center()
        background = make_background()
        changed = 0
        for step in range(1, 10):
            fraction = step / 20.0            # 只取样引线的前半段：后半段会被放大图盖住
            point = QPointF(start.x() + (end.x() - start.x()) * fraction,
                            start.y() + (end.y() - start.y()) * fraction).toPoint()
            if rendered.pixelColor(point) != background.pixelColor(point):
                changed += 1
        assert changed >= 4, "源框到放大框之间应当画出引线"
