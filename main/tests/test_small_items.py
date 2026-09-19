# -*- coding: utf-8 -*-
"""小项：保存格式按运行时能力派生、PDF 页尺寸、托盘滚轮动作、拖拽类鼠标手势。

格式与 PDF 两条都驱动真保存服务并**真写文件再读回**（PDF 用 QtPdf 读页尺寸）；托盘
滚轮与拖动手势走真分发路径（``MainApp.on_tray_activated`` / ``ShortcutManager`` 的
手势回调），动作执行侧只替 ``run_action`` 这一个外部边界。
"""


import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage

from core import image_formats
from core.platform import pointer as platform_pointer
from core.save import SaveService, pdf_image_rect, pdf_page_size
from settings.tool_settings import ToolSettingsManager


def sample(width=80, height=40, color="#3366CC") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "small.ini"), QSettings.Format.IniFormat))


# ── 保存格式派生 ──────────────────────────────────────

class TestFormatDerivation:
    def test_the_list_only_contains_writable_formats(self):
        for fmt, _filter in image_formats.available_formats():
            assert image_formats.can_write(fmt), fmt

    def test_png_is_always_there(self):
        assert "PNG" in image_formats.format_names()

    def test_formats_without_an_encoder_are_not_listed(self):
        """本机没有 JXL/AVIF 编码器时，它们不该出现在清单里。"""
        writable = {name.upper() for name in _qt_writer_formats()}
        for fmt in ("JXL", "AVIF"):
            if fmt not in writable:
                assert fmt not in image_formats.format_names()

    def test_every_listed_format_really_writes_a_readable_file(self, qapp, tmp_path):
        results = image_formats.verify_formats(str(tmp_path))
        assert results, "没有任何可写格式"
        assert all(results.values()), results

    def test_the_dialog_filter_is_built_from_the_same_list(self):
        filter_text = image_formats.save_dialog_filter()
        for fmt in image_formats.format_names():
            assert fmt in filter_text.upper() or (
                fmt == "JPG" and "JPG" in filter_text.upper())

    def test_an_unavailable_format_falls_back_to_a_writable_one(self):
        assert image_formats.preferred_format("NOPE") in image_formats.format_names()
        assert image_formats.preferred_format("png") == "PNG"

    def test_normalize_handles_aliases(self):
        assert image_formats.normalize("jpeg") == "JPG"
        assert image_formats.normalize(".webp") == "WEBP"


def _qt_writer_formats():
    from PySide6.QtGui import QImageWriter

    return {bytes(name).decode("ascii") for name in QImageWriter.supportedImageFormats()}


# ── PDF 页尺寸 ────────────────────────────────────────

class TestPdfPageSize:
    def _page_points(self, path: str):
        from PySide6.QtPdf import QPdfDocument

        document = QPdfDocument()
        assert document.load(path) is not None
        assert document.pageCount() >= 1
        return document.pagePointSize(0)

    def test_original_page_size_matches_the_image(self, qapp, tmp_path):
        path = tmp_path / "original.pdf"
        assert SaveService().save_qimage_to_path(
            sample(400, 200), str(path), image_format="PDF", pdf_page="original") is True
        size = self._page_points(str(path))
        assert (round(size.width()), round(size.height())) == (400, 200)

    def test_a4_portrait_and_landscape(self, qapp, tmp_path):
        portrait = tmp_path / "a4p.pdf"
        landscape = tmp_path / "a4l.pdf"
        assert SaveService().save_qimage_to_path(
            sample(400, 200), str(portrait), image_format="PDF", pdf_page="a4_portrait")
        assert SaveService().save_qimage_to_path(
            sample(400, 200), str(landscape), image_format="PDF", pdf_page="a4_landscape")

        portrait_size = self._page_points(str(portrait))
        landscape_size = self._page_points(str(landscape))
        assert (round(portrait_size.width()), round(portrait_size.height())) == (595, 842)
        assert (round(landscape_size.width()), round(landscape_size.height())) == (842, 595)

    def test_the_configured_page_size_is_used(self, qapp, config, tmp_path, monkeypatch):
        from core.save import SaveService as Service

        config.set_pdf_page_size("a4_landscape")
        path = tmp_path / "configured.pdf"
        assert Service(config_manager=config).save_qimage_to_path(
            sample(200, 100), str(path), image_format="PDF") is True
        size = self._page_points(str(path))
        assert round(size.width()) > round(size.height())

    def test_the_page_size_setting_is_whitelisted(self, config):
        config.set_pdf_page_size("a0")
        assert config.get_pdf_page_size() == "original"
        config.set_pdf_page_size("a4_portrait")
        assert config.get_pdf_page_size() == "a4_portrait"

    def test_the_image_is_scaled_to_fit_an_a4_page(self, qapp, tmp_path):
        """A4 页比图小的时候图要缩进去，而不是被裁掉。"""
        from PySide6.QtGui import QPdfWriter

        writer = QPdfWriter(str(tmp_path / "probe.pdf"))
        writer.setPageSize(pdf_page_size(sample(80, 40), "a4_portrait"))
        big = pdf_image_rect(sample(4000, 2000), writer)
        assert big.width() <= writer.width() and big.height() <= writer.height()
        small = pdf_image_rect(sample(80, 40), writer)
        assert small.width() < writer.width()


# ── 托盘滚轮动作 ──────────────────────────────────────

class TestTrayScrollAction:
    def _app(self, config):
        from types import SimpleNamespace

        fired = []
        app = SimpleNamespace(
            config_manager=config,
            start_screenshot=lambda: fired.append("screenshot"),
            history_window=None,
        )
        return app, fired

    def test_middle_click_runs_the_configured_action(self, config, monkeypatch):
        from PySide6.QtWidgets import QSystemTrayIcon

        import main_app
        import core.actions as actions_module

        config.set_tray_scroll_action("clipboard")
        app, _fired = self._app(config)
        app.open_clipboard_window = lambda: None
        seen = []
        monkeypatch.setattr(actions_module, "run_action",
                            lambda action_id, target: seen.append(action_id) or True)

        main_app.MainApp.on_tray_activated(app, QSystemTrayIcon.ActivationReason.MiddleClick)

        assert seen == ["clipboard"]

    def test_an_empty_binding_does_nothing(self, config, monkeypatch):
        from PySide6.QtWidgets import QSystemTrayIcon

        import main_app
        import core.actions as actions_module

        config.set_tray_scroll_action("")
        app, _fired = self._app(config)
        seen = []
        monkeypatch.setattr(actions_module, "run_action",
                            lambda action_id, target: seen.append(action_id) or True)

        main_app.MainApp.on_tray_activated(app, QSystemTrayIcon.ActivationReason.MiddleClick)
        assert seen == []

    def test_an_unknown_action_id_falls_back_to_nothing(self, config):
        config.set_app_setting("tray_scroll_action", "no_such_action")
        assert config.get_tray_scroll_action() == ""

    def test_the_left_click_action_is_unchanged(self, config, monkeypatch):
        from PySide6.QtWidgets import QSystemTrayIcon

        import main_app
        import core.actions as actions_module

        config.set_tray_click_action("screenshot")
        app, fired = self._app(config)
        monkeypatch.setattr(actions_module, "run_action", lambda *a: True)

        main_app.MainApp.on_tray_activated(app, QSystemTrayIcon.ActivationReason.Trigger)
        assert fired == ["screenshot"]


# ── 拖拽类鼠标手势 ────────────────────────────────────

class TestDragGestureVocabulary:
    def test_the_vocabulary_is_part_of_the_gesture_list(self):
        for gesture in platform_pointer.DRAG_GESTURES:
            assert gesture in platform_pointer.MOUSE_GESTURES

    def test_the_drag_gestures_are_unique(self):
        assert len(set(platform_pointer.MOUSE_GESTURES)) == len(platform_pointer.MOUSE_GESTURES)


class TestDragTracker:
    def test_a_horizontal_drag_reports_left_or_right(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("left", 100, 100)
        assert tracker.release("left", 300, 120) == "left_drag_right"

        tracker.press("left", 300, 100)
        assert tracker.release("left", 100, 90) == "left_drag_left"

    def test_a_vertical_drag_reports_up_or_down(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("left", 100, 100)
        assert tracker.release("left", 110, 300) == "left_drag_down"

        tracker.press("left", 100, 300)
        assert tracker.release("left", 90, 100) == "left_drag_up"

    def test_the_dominant_axis_wins(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("left", 0, 0)
        assert tracker.release("left", 200, 120) == "left_drag_right"

    def test_right_button_drags_are_tracked_too(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("right", 0, 0)
        assert tracker.release("right", -200, 0) == "right_drag_left"

    def test_a_short_move_is_not_a_drag(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("left", 0, 0)
        assert tracker.release("left", 20, 20) is None

    def test_buttons_that_do_not_drag_are_ignored(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("middle", 0, 0)
        assert tracker.release("middle", 500, 500) is None

    def test_releasing_a_different_button_does_nothing(self):
        tracker = platform_pointer.DragTracker(threshold=50)
        tracker.press("left", 0, 0)
        assert tracker.release("right", 500, 500) is None


class TestDragDispatch:
    def _manager(self, config, qapp):
        from core.shortcut_manager import ShortcutManager

        manager = ShortcutManager.instance()
        manager.set_mouse_gestures({})
        return manager

    def test_a_bound_drag_triggers_its_action(self, config, qapp, monkeypatch):
        """绑定「左键拖右」之后，合成按下→移动→松手要命中那个动作。"""
        from PySide6.QtWidgets import QSystemTrayIcon  # noqa: F401 - 触发 Qt 初始化


        manager = self._manager(config, qapp)
        manager.set_mouse_gestures({
            "clipboard": {"modifier": "", "gesture": "left_drag_right"},
        })
        assert manager._drag_enabled is True

        fired = []
        monkeypatch.setattr(manager, "_dispatch_mouse_gesture", fired.append)

        class _Button:
            name = "left"

        manager._on_gesture_click(100, 100, _Button(), True)
        manager._on_gesture_click(400, 120, _Button(), False)

        assert fired == ["left_drag_right"]
        manager.set_mouse_gestures({})

    def test_without_a_drag_binding_drags_are_not_tracked(self, config, qapp, monkeypatch):
        manager = self._manager(config, qapp)
        manager.set_mouse_gestures({
            "clipboard": {"modifier": "", "gesture": "wheel_up"},
        })
        assert manager._drag_enabled is False

        fired = []
        monkeypatch.setattr(manager, "_dispatch_mouse_gesture", fired.append)

        class _Button:
            name = "left"

        manager._on_gesture_click(100, 100, _Button(), True)
        manager._on_gesture_click(400, 120, _Button(), False)
        assert fired == []
        manager.set_mouse_gestures({})

    def test_clicks_still_work_while_drag_tracking_is_on(self, config, qapp, monkeypatch):
        manager = self._manager(config, qapp)
        manager.set_mouse_gestures({
            "clipboard": {"modifier": "", "gesture": "left_drag_right"},
            "open_history": {"modifier": "", "gesture": "middle_click"},
        })
        fired = []
        monkeypatch.setattr(manager, "_dispatch_mouse_gesture", fired.append)

        class _Middle:
            name = "middle"

        manager._on_gesture_click(10, 10, _Middle(), True)
        assert fired == ["middle_click"]
        manager.set_mouse_gestures({})

    def test_a_drag_gesture_can_be_bound_in_the_config(self, config):
        config.set_mouse_gesture("clipboard", "", "left_drag_left")
        bindings = config.get_mouse_gestures()
        assert bindings["clipboard"]["gesture"] == "left_drag_left"
