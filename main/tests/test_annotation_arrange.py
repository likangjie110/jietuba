# -*- coding: utf-8 -*-
"""箭头路径样式与端点、元素层级与对齐、标注样式模板。

驱动的是发布实现：真 `ArrowItem`、真 `SmartEditController`（走它的选中元素与撤销栈）、
真 `canvas/annotation_templates.py`（真写配置），像素断言走真 `ExportService.export`。
"""

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPen

from canvas.annotation_templates import (apply_to_item, apply_to_manager,
                                         capture_from_manager, delete_template,
                                         load_templates, save_template)
from canvas.arrange import ALIGN_MODES, alignment_delta, next_z_value
from canvas.items import ArrowItem, RectItem, WatermarkItem
from canvas.scene import CanvasScene
from core.export import ExportService

CANVAS = (240, 160)


def background(color="#FFFFFF", width=CANVAS[0], height=CANVAS[1]) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


@pytest.fixture
def scene(qapp):
    canvas = CanvasScene(background(), QRectF(0, 0, CANVAS[0], CANVAS[1]), enable_mosaic=False)
    canvas.selection_model.activate()
    canvas.selection_model.set_rect(QRectF(0, 0, CANVAS[0], CANVAS[1]))
    yield canvas
    canvas.clear()


def export(scene) -> QImage:
    return ExportService(scene).export(QRectF(0, 0, CANVAS[0], CANVAS[1]))


def ink(image: QImage) -> int:
    """白底上的笔墨：任一通道明显偏离白就算（红色墨的红通道仍是 255，只看红会漏掉）。"""
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if min(color.red(), color.green(), color.blue()) < 200:
                count += 1
    return count


def make_arrow(scene, *, path_style="straight", head_start="inherit", head_end="inherit",
               start=(20, 120), end=(200, 40)) -> ArrowItem:
    item = ArrowItem(QPointF(*start), QPointF(*end), QPen(QColor("#FF0000"), 6),
                     path_style=path_style, head_start=head_start, head_end=head_end)
    from canvas.undo import AddItemCommand

    scene.undo_stack.push_command(AddItemCommand(scene, item))
    return item


# ── 箭头：路径样式 ────────────────────────────────────

class TestArrowPathStyles:
    def test_straight_keeps_the_legacy_geometry(self):
        item = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6))
        assert item.path_style == "straight"
        assert item.uses_custom_heads() is False
        assert item.path().elementCount() > 0
        # 直线：中段是实心的箭杆
        assert item.path().contains(QPointF(50, 0))

    def test_elbow_path_has_a_corner(self):
        item = ArrowItem(QPointF(20, 20), QPointF(120, 100), QPen(QColor("#FF0000"), 6),
                         path_style="elbow")
        assert item.path_style == "elbow"
        bounds = item.path().boundingRect()
        # 折线（先横后竖）的包围盒与两端点围成的矩形一致
        assert bounds.width() >= 95 and bounds.height() >= 75
        # 横段上有墨、被折线切掉的左下角没有：这就是「L 形」而不是直连
        assert item.path().contains(QPointF(80, 20))
        assert not item.path().contains(QPointF(40, 90))

    def test_curve_uses_the_control_point(self):
        item = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6))
        assert item.set_path_style("curve") is True
        item.set_control_point(QPointF(50, -60))
        item.update_geometry()
        assert item.is_curved() is True
        # 曲线在控制点那一侧鼓出来
        assert item.path().boundingRect().top() < -20

    def test_unknown_path_style_is_rejected(self):
        item = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6))
        assert item.set_path_style("spiral") is False
        assert item.path_style == "straight"

    def test_exported_elbow_looks_like_an_l(self, scene):
        make_arrow(scene, path_style="elbow", start=(20, 30), end=(200, 130))
        image = export(scene)
        # 拐角处（终点 x、起点 y）应该有墨，而两端对角线中点附近基本没有
        corner = image.pixelColor(196, 32)
        assert min(corner.red(), corner.green(), corner.blue()) < 200, f"拐角处没有墨: {corner.getRgb()}"
        # 折线以外的地方（第一段下方、第二段左侧）不该有墨
        outside = image.pixelColor(60, 130)
        assert min(outside.red(), outside.green(), outside.blue()) > 200


# ── 箭头：端点样式 ────────────────────────────────────

class TestArrowHeads:
    def test_custom_heads_replace_the_builtin_ones(self):
        item = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6),
                         head_start="circle", head_end="triangle")
        assert item.uses_custom_heads() is True
        assert (item.head_start, item.head_end) == ("circle", "triangle")
        assert item.path_style == "straight"
        # 定制的两端真的进了几何：与「两端都不画头」的轮廓不一样
        headless = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6),
                             head_start="none", head_end="none")
        assert item.path().boundingRect() != headless.path().boundingRect()

    def test_head_none_draws_nothing(self):
        item = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6),
                         head_start="none", head_end="none")
        assert item.uses_custom_heads() is True
        # 两端都不画头：轮廓不会越过端点（没有凸出来的端头）
        bounds = item.path().boundingRect()
        assert bounds.left() >= -1 and bounds.right() <= 101

    def test_unknown_head_is_rejected(self):
        item = ArrowItem(QPointF(0, 0), QPointF(100, 0), QPen(QColor("#FF0000"), 6))
        assert item.set_head_end("star") is False
        assert item.head_end == "inherit"

    def test_head_styles_change_the_exported_pixels(self, scene):
        plain = make_arrow(scene, head_start="none", head_end="none")
        without = ink(export(scene))
        scene.removeItem(plain)

        make_arrow(scene, head_start="none", head_end="triangle")
        with_triangle = ink(export(scene))

        assert with_triangle > without, f"端点没画出来: {without} -> {with_triangle}"

    def test_endpoint_style_changes_are_undoable(self, scene, qapp):
        item = make_arrow(scene)
        controller = scene.tool_controller  # 仅为触发场景初始化

        from canvas.view import CanvasView

        view = CanvasView(scene)
        controller = getattr(view, "smart_edit_controller", None)
        if controller is None:
            pytest.skip("视图没有智能编辑控制器")
        controller.selected_item = item

        before = item.arrow_state()
        assert controller.on_arrow_head_end_changed("diamond") is True
        assert item.head_end == "diamond"

        scene.undo_stack.undo()

        assert item.arrow_state() == before
        assert item is not None
        view.deleteLater()
        qapp.processEvents()


# ── 层级与对齐 ────────────────────────────────────────

class TestArrangeMath:
    def test_alignment_deltas(self):
        bounds = QRectF(10, 20, 30, 40)
        target = QRectF(100, 200, 200, 300)
        assert alignment_delta(bounds, target, "left") == QPointF(90, 0)
        assert alignment_delta(bounds, target, "right") == QPointF(260, 0)
        assert alignment_delta(bounds, target, "top") == QPointF(0, 180)
        assert alignment_delta(bounds, target, "bottom") == QPointF(0, 440)
        assert alignment_delta(bounds, target, "hcenter") == QPointF(175, 0)
        assert alignment_delta(bounds, target, "vcenter") == QPointF(0, 310)

    def test_unknown_mode_or_empty_rect_does_nothing(self):
        assert alignment_delta(QRectF(0, 0, 10, 10), QRectF(0, 0, 10, 10), "nope") == QPointF(0, 0)
        assert alignment_delta(QRectF(), QRectF(0, 0, 10, 10), "left") == QPointF(0, 0)

    def test_z_values(self):
        assert next_z_value([1, 5, 3], 2, "front") == 6
        assert next_z_value([1, 5, 3], 2, "back") == 0
        assert next_z_value([1, 5, 3], 2, "forward") == 3
        assert next_z_value([1, 5, 3], 2, "backward") == 1


class TestArrangeOnCanvas:
    def _controller(self, scene, qapp):
        from canvas.view import CanvasView

        view = CanvasView(scene)
        controller = getattr(view, "smart_edit_controller", None)
        if controller is None:
            pytest.skip("视图没有智能编辑控制器")
        return view, controller

    def test_align_moves_the_selected_item_to_the_selection_edge(self, scene, qapp):
        item = RectItem(QRectF(60, 60, 40, 30), QPen(QColor("#FF0000"), 4))
        from canvas.undo import AddItemCommand

        scene.undo_stack.push_command(AddItemCommand(scene, item))
        view, controller = self._controller(scene, qapp)
        controller.selected_item = item

        selection = scene.selection_model.rect()
        before = QRectF(item.sceneBoundingRect())
        assert controller.arrange_selected("left") is True
        assert abs(item.sceneBoundingRect().left() - selection.left()) < 1

        scene.undo_stack.undo()

        assert item.sceneBoundingRect() == before
        view.deleteLater()
        qapp.processEvents()

    def test_every_align_mode_lands_on_the_expected_edge(self, scene, qapp):
        view, controller = self._controller(scene, qapp)
        selection = scene.selection_model.rect()
        for mode in ALIGN_MODES:
            item = RectItem(QRectF(50, 40, 30, 20), QPen(QColor("#FF0000"), 4))
            scene.addItem(item)
            controller.selected_item = item
            assert controller.arrange_selected(mode) is True, mode
            bounds = item.sceneBoundingRect()
            if mode == "left":
                assert abs(bounds.left() - selection.left()) < 1
            elif mode == "right":
                assert abs(bounds.right() - selection.right()) < 1
            elif mode == "hcenter":
                assert abs(bounds.center().x() - selection.center().x()) < 1
            elif mode == "top":
                assert abs(bounds.top() - selection.top()) < 1
            elif mode == "bottom":
                assert abs(bounds.bottom() - selection.bottom()) < 1
            elif mode == "vcenter":
                assert abs(bounds.center().y() - selection.center().y()) < 1
            scene.removeItem(item)
        view.deleteLater()
        qapp.processEvents()

    def test_z_order_changes_rendering_order(self, scene, qapp):
        from canvas.items import RectItem as _Rect

        back = _Rect(QRectF(20, 20, 100, 100), QPen(QColor("#FF0000"), 1))
        back.setBrush(QColor("#FF0000"))
        front = _Rect(QRectF(60, 60, 100, 100), QPen(QColor("#0000FF"), 1))
        front.setBrush(QColor("#0000FF"))
        for item in (back, front):
            scene.addItem(item)

        view, controller = self._controller(scene, qapp)
        controller.selected_item = back
        assert controller.change_z_order("front") is True
        # 叠放顺序变了：重叠处从蓝色变成被抬上来的红色
        assert export(scene).pixelColor(100, 100).red() > 200

        scene.undo_stack.undo()

        assert export(scene).pixelColor(100, 100).blue() > 200
        view.deleteLater()
        qapp.processEvents()

    def test_unknown_modes_do_nothing(self, scene, qapp):
        item = RectItem(QRectF(20, 20, 30, 30), QPen(QColor("#FF0000"), 4))
        scene.addItem(item)
        view, controller = self._controller(scene, qapp)
        controller.selected_item = item
        assert controller.arrange_selected("diagonal") is False
        assert controller.change_z_order("sideways") is False
        view.deleteLater()
        qapp.processEvents()


# ── 样式模板 ──────────────────────────────────────────

class TestStyleTemplates:
    def test_save_load_delete_round_trip(self, tmp_path):
        from PySide6.QtCore import QSettings

        from settings.tool_settings import ToolSettingsManager

        config = ToolSettingsManager(
            qsettings=QSettings(str(tmp_path / "tpl.ini"), QSettings.Format.IniFormat))
        config.update_settings("line", line_style="dashed", stroke_width=12)

        templates = save_template(config, "我的线", "line",
                                  {"line_style": "dashed", "stroke_width": 12})
        assert "我的线" in templates
        loaded = load_templates(config)
        assert loaded["我的线"]["line"]["line_style"] == "dashed"

        delete_template(config, "我的线")
        assert load_templates(config) == {}

    def test_a_blank_name_is_refused(self, tmp_path):
        from PySide6.QtCore import QSettings

        from settings.tool_settings import ToolSettingsManager

        config = ToolSettingsManager(
            qsettings=QSettings(str(tmp_path / "tpl2.ini"), QSettings.Format.IniFormat))
        assert save_template(config, "   ", "line", {"line_style": "solid"}) == {}
        assert load_templates(config) == {}

    def test_corrupt_config_falls_back_to_an_empty_table(self, tmp_path):
        from PySide6.QtCore import QSettings

        from settings.tool_settings import ToolSettingsManager

        config = ToolSettingsManager(
            qsettings=QSettings(str(tmp_path / "tpl3.ini"), QSettings.Format.IniFormat))
        config.set_app_setting("annotation_templates", "{ not json")
        assert load_templates(config) == {}

    def test_capture_reads_the_current_tool_settings(self, tmp_path):
        from PySide6.QtCore import QSettings

        from settings.tool_settings import ToolSettingsManager

        config = ToolSettingsManager(
            qsettings=QSettings(str(tmp_path / "tpl4.ini"), QSettings.Format.IniFormat))
        config.update_settings("watermark", text="水印字", font_size=33, angle=-15.0)
        values = capture_from_manager(config, "watermark")
        assert values["text"] == "水印字"
        assert values["font_size"] == 33
        assert isinstance(values["angle"], float)

    def test_apply_writes_the_tool_settings(self, tmp_path):
        from PySide6.QtCore import QSettings

        from settings.tool_settings import ToolSettingsManager

        config = ToolSettingsManager(
            qsettings=QSettings(str(tmp_path / "tpl5.ini"), QSettings.Format.IniFormat))
        written = apply_to_manager(config, "filter", {"kind": "invert", "radius": 12})
        assert written == 2
        assert config.get_setting("filter", "kind") == "invert"

    def test_applying_a_template_to_an_item_sets_its_properties(self):
        item = WatermarkItem(QRectF(0, 0, 100, 100), "old", font_size=10)
        changed = apply_to_item(item, "watermark",
                                {"text": "new", "font_size": 24, "angle": -45.0})
        assert changed >= 3
        assert item.text == "new" and item.font_size == 24 and item.angle == -45.0

    def test_an_arrow_template_carries_the_appearance(self):
        item = ArrowItem(QPointF(0, 0), QPointF(50, 0), QPen(QColor("#FF0000"), 4))
        changed = apply_to_item(item, "arrow",
                                {"path_style": "elbow", "head_end": "circle",
                                 "stroke_width": 7})
        assert changed >= 2
        assert item.path_style == "elbow"
        assert item.head_end == "circle"

    def test_the_next_drawn_element_takes_the_template_values(self, scene, isolated_tool_settings):
        """套用模板 → 之后画出来的元素参数就等于模板值（这是模板存在的意义）。"""
        from settings import get_tool_settings_manager
        from ui.annotation_settings_panel import StyleTemplateControls

        manager = get_tool_settings_manager()
        manager.update_settings("line", line_style="dashed_dense", stroke_width=11)
        save_template(manager, "粗虚线", "line",
                      {"line_style": "dashed_dense", "stroke_width": 11})

        controls = StyleTemplateControls()
        try:
            controls.set_tool("line")
            controls.refresh()
            index = controls.template_combo.findText("粗虚线")
            assert index >= 0
            controls.template_combo.setCurrentIndex(index)

            # 先把设置改回别的值，再套模板，确认是真的被模板覆盖
            manager.update_settings("line", line_style="solid", stroke_width=3)
            assert controls.apply_selected() is True
            assert manager.get_setting("line", "line_style") == "dashed_dense"
            assert manager.get_setting("line", "stroke_width") == 11

            # 用工具真画一条，元素的参数就是模板值
            from tools.annotation import LineTool

            tool = LineTool()
            context = scene.tool_controller.context
            tool.on_press(QPointF(10, 10), Qt.MouseButton.LeftButton, context)
            tool.on_move(QPointF(100, 100), context)
            tool.on_release(QPointF(200, 120), context)

            lines = [item for item in scene.items() if type(item).__name__ == "LineItem"]
            assert lines and lines[0].line_style == "dashed_dense"
            assert lines[0].pen().widthF() == 11
        finally:
            controls.deleteLater()
