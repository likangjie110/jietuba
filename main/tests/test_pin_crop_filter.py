# -*- coding: utf-8 -*-
"""贴图裁剪与滤镜。

用例建的是**真贴图窗口**（走 `PinManager.create_pin`，与用户钉图完全同一条路径），
断言的是「窗口里现在那张图的像素」——裁剪后尺寸变小且内容对得上，滤镜后通道关系符合定义。
"""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent

from pin import pin_actions
from pin.pin_manager import PinManager


def image(width=80, height=60) -> QImage:
    """左半红、右半蓝、上方一条绿——裁剪与滤镜都能从像素上看出来。"""
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#CC3333"))
    for x in range(width // 2, width):
        for y in range(height):
            picture.setPixelColor(x, y, QColor("#3333CC"))
    for x in range(width):
        for y in range(6):
            picture.setPixelColor(x, y, QColor("#33CC33"))
    return picture


@pytest.fixture
def manager(qapp, isolated_tool_settings):
    instance = PinManager.instance()
    yield instance
    instance.close_all()
    instance.restore_visibility()
    qapp.processEvents()


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    manager = get_tool_settings_manager()
    manager.set_app_setting("ocr_enabled", False)
    return manager


@pytest.fixture
def pin(manager, config, qapp):
    window = manager.create_pin(image(), QPoint(140, 140), config)
    assert window is not None
    qapp.processEvents()
    yield window
    if window in manager.get_all_pins():
        manager.remove_pin(window)
    qapp.processEvents()


class TestCrop:
    def test_crop_keeps_the_requested_region(self, pin):
        # 裁 x=20..60：正好横跨原图 40px 处的红蓝分界
        assert pin.crop_to(QRectF(20, 0, 40, 60)) is True

        cropped = pin.current_image()
        assert (cropped.width(), cropped.height()) == (40, 60)
        assert cropped.pixelColor(19, 30) == QColor("#CC3333")
        assert cropped.pixelColor(21, 30) == QColor("#3333CC")

    def test_crop_is_clamped_to_the_image(self, pin):
        assert pin.crop_to(QRectF(60, 40, 100, 100)) is True

        cropped = pin.current_image()
        assert (cropped.width(), cropped.height()) == (20, 20)

    def test_invalid_rect_is_refused(self, pin):
        assert pin.crop_to(QRectF(10, 10, 0, 0)) is False
        assert pin.current_image().size() == image().size()

    def test_crop_mode_tracks_a_drag_and_crops_on_release(self, pin, qapp):
        assert pin.start_crop_mode() is True

        viewport = pin.view.viewport()
        start = QPointF(4, 4)
        end = QPointF(44, 34)
        for kind, point in ((QEvent.Type.MouseButtonPress, start),
                            (QEvent.Type.MouseMove, end),
                            (QEvent.Type.MouseButtonRelease, end)):
            event = QMouseEvent(kind, point, viewport.mapToGlobal(point.toPoint()),
                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            qapp.sendEvent(viewport, event)
        qapp.processEvents()

        cropped = pin.current_image()
        assert cropped.size() != image().size(), "拖完没有裁剪"
        assert pin._crop_mode is False, "裁剪完成后应退出裁剪模式"

    def test_escape_cancels_crop_mode(self, pin, qapp):
        from PySide6.QtGui import QKeyEvent

        assert pin.start_crop_mode() is True
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                          Qt.KeyboardModifier.NoModifier)
        qapp.sendEvent(pin, event)
        qapp.processEvents()

        assert pin._crop_mode is False
        assert pin.current_image().size() == image().size()

    def test_action_table_exposes_crop(self, pin):
        assert pin_actions.run_pin_action("crop", pin) is True
        assert pin._crop_mode is True
        pin.cancel_crop_mode()


class TestFilters:
    def test_grayscale_makes_channels_equal(self, pin):
        assert pin.apply_filter("grayscale") is True

        color = pin.current_image().pixelColor(5, 20)
        assert color.red() == color.green() == color.blue()

    def test_invert_flips_the_channels(self, pin):
        assert pin.apply_filter("invert") is True

        color = pin.current_image().pixelColor(5, 20)
        # 原色 #CC3333 反相后是 #33CCCC：红通道掉下来、绿蓝通道升上去
        assert color.red() < 100 and color.green() > 200 and color.blue() > 200

    def test_blur_averages_a_hard_edge(self, pin):
        before = pin.current_image().pixelColor(40, 30)

        assert pin.apply_filter("blur") is True

        after = pin.current_image().pixelColor(40, 30)
        assert after != before, "模糊应当让边界处的颜色靠近邻域均值"

    def test_emboss_keeps_the_size(self, pin):
        size = pin.current_image().size()
        assert pin.apply_filter("emboss") is True
        assert pin.current_image().size() == size

    def test_unknown_filter_is_refused(self, pin):
        assert pin.apply_filter("oil-painting") is False
        assert pin.current_image().size() == image().size()

    def test_action_table_exposes_the_filters(self, pin):
        assert pin_actions.run_pin_action("filter_grayscale", pin) is True
        color = pin.current_image().pixelColor(5, 20)
        assert color.red() == color.green() == color.blue()


class TestMenuEntries:
    def test_context_menu_has_crop_and_filter(self, pin, qapp):
        from pin.pin_context_menu import PinContextMenu

        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        PinContextMenu(pin)._add_menu_items(menu, {"thumbnail_mode": False})
        labels = [action.text() for action in menu.actions()]
        submenus = {action.text(): action.menu() for action in menu.actions()
                    if action.menu() is not None}

        assert any("Crop" in label for label in labels), labels
        filter_menu = next((menu_ for name, menu_ in submenus.items()
                            if "Filter" in name), None)
        assert filter_menu is not None
        assert len(filter_menu.actions()) == 4
