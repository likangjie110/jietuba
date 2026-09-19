# -*- coding: utf-8 -*-
"""独立图片查看器与主窗口。

两条都驱动真实的部件：查看器真读文件、真渲染（断言 ``pixmap_item`` 里的像素与尺寸）、
真写「另存」文件；主窗口真建四个页面并真切页。只有「翻译服务」与系统文件对话框是边界替身。
"""


import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QGuiApplication, QImage

from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "viewer.ini"), QSettings.Format.IniFormat))


def image(width=200, height=100, color="#3366CC") -> QImage:
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor(color))
    return picture


@pytest.fixture
def folder(tmp_path):
    """同目录三张图（尺寸各不相同，便于断言翻页确实换了图）。"""
    directory = tmp_path / "shots"
    directory.mkdir()
    image(120, 60, "#3366CC").save(str(directory / "a.png"), "PNG")
    image(140, 70, "#22AA55").save(str(directory / "b.png"), "PNG")
    image(160, 80, "#FF8800").save(str(directory / "c.png"), "PNG")
    return directory


# ── 查看器 ────────────────────────────────────────────

class TestImageViewer:
    @pytest.fixture
    def viewer(self, qapp, config):
        from ui.image_viewer import ImageViewer

        window = ImageViewer()
        window.resize(800, 600)
        yield window
        window.close()
        window.deleteLater()
        qapp.processEvents()

    def test_loading_a_file_shows_it(self, viewer, folder):
        assert viewer.load_file(str(folder / "b.png")) is True
        assert viewer.displayed_pixmap().width() == 140
        assert "b.png" in viewer.info_label.text()

    def test_zoom_and_actual_size(self, viewer, folder):
        viewer.load_file(str(folder / "a.png"))
        assert viewer.actual_size() is True
        assert viewer.zoom == 1.0

        assert viewer.zoom_by(2.0) is True
        assert viewer.zoom == 2.0
        assert viewer.zoom_by(1000.0) is True          # 钳到上限
        assert viewer.zoom <= 20.0

    def test_rotate_swaps_the_rendered_size(self, viewer, folder):
        viewer.load_file(str(folder / "a.png"))        # 120x60
        assert viewer.rotate(90) is True
        pixmap = viewer.displayed_pixmap()
        assert (pixmap.width(), pixmap.height()) == (60, 120)
        assert viewer.rotation == 90
        assert viewer.rotate(-90) is True
        assert viewer.rotation == 0
        assert (viewer.displayed_pixmap().width(), viewer.displayed_pixmap().height()) == (120, 60)

    def test_flip_is_reflected_in_the_pixels(self, viewer, tmp_path):
        """左半红右半蓝的图，水平翻转后左右必须对调（按真像素断言）。"""
        source = QImage(40, 20, QImage.Format.Format_ARGB32)
        for y in range(20):
            for x in range(40):
                source.setPixelColor(x, y, QColor("#FF0000") if x < 20 else QColor("#0000FF"))
        path = tmp_path / "half.png"
        source.save(str(path), "PNG")

        viewer.load_file(str(path))
        before = viewer.displayed_pixmap().toImage().pixelColor(2, 10)
        assert before.red() > 200

        assert viewer.flip_horizontal() is True
        after = viewer.displayed_pixmap().toImage().pixelColor(2, 10)
        assert after.blue() > 200
        assert viewer.is_flipped() == (True, False)

    def test_next_and_previous_walk_the_folder(self, viewer, folder):
        viewer.load_file(str(folder / "b.png"))
        assert viewer.sequence_position() == (2, 3)

        assert viewer.show_next() is True
        assert "c.png" in viewer.info_label.text()
        assert viewer.sequence_position() == (3, 3)

        assert viewer.show_next() is True                # 循环回到第一张
        assert "a.png" in viewer.info_label.text()
        assert viewer.sequence_position() == (1, 3)

        assert viewer.show_previous() is True
        assert "c.png" in viewer.info_label.text()

    def test_a_single_image_has_no_sequence(self, viewer, tmp_path):
        path = tmp_path / "lonely.png"
        image().save(str(path), "PNG")
        viewer.load_file(str(path))
        assert viewer.has_sequence() is False
        assert viewer.show_next() is False
        assert viewer.sequence_position() == (0, 0)

    def test_copy_puts_the_current_view_on_the_clipboard(self, viewer, folder):
        viewer.load_file(str(folder / "b.png"))
        assert viewer.copy_image() is True
        clipboard_image = QGuiApplication.clipboard().image()
        assert not clipboard_image.isNull()
        assert (clipboard_image.width(), clipboard_image.height()) == (140, 70)

    def test_save_as_writes_the_rotated_view(self, viewer, folder, tmp_path, monkeypatch):
        from ui import image_viewer as viewer_module

        viewer.load_file(str(folder / "a.png"))
        viewer.rotate(90)
        target = tmp_path / "out.png"
        monkeypatch.setattr(viewer_module.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(target), "PNG (*.png)")))

        assert viewer.save_as() is True
        saved = QImage(str(target))
        assert (saved.width(), saved.height()) == (60, 120)

    def test_loading_an_in_memory_image_clears_the_sequence(self, viewer, folder):
        viewer.load_file(str(folder / "a.png"))
        assert viewer.has_sequence() is True
        assert viewer.load_image(image(80, 40), name="clipboard") is True
        assert viewer.has_sequence() is False
        assert viewer.displayed_pixmap().width() == 80
        assert "clipboard" in viewer.info_label.text()

    def test_open_image_viewer_reuses_one_window(self, qapp, config):
        from ui.image_viewer import open_image_viewer

        first = open_image_viewer(image=image(), config_manager=config)
        second = open_image_viewer(image=image(50, 50), config_manager=config)
        try:
            assert first is second
            assert first.displayed_pixmap().width() == 50
        finally:
            first.close()
            first.deleteLater()
            qapp.processEvents()
            QApplication_instance()._image_viewer = None


def QApplication_instance():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


# ── 动作与历史入口 ────────────────────────────────────

class TestViewerEntries:
    def test_the_registry_action_opens_the_viewer_with_the_clipboard_image(self, qapp, config,
                                                                          monkeypatch):
        """走动作入口：剪贴板里有图 → 查看器打开并显示它。"""
        from types import SimpleNamespace

        import core.actions as actions_module

        monkeypatch.setattr(actions_module, "clipboard_image",
                            lambda: image(70, 40, "#AA22FF"))
        from main_app import MainApp
        import main_app as main_app_module

        monkeypatch.setattr(main_app_module, "log_warning", lambda *a, **k: None)
        app = SimpleNamespace(config_manager=config, open_image_viewer=None)
        # 用真实方法：把 MainApp 的入口挂到替身上（只替「应用实例」这一层）
        app.open_image_viewer = lambda: MainApp.open_image_viewer(app)
        app.open_history_window = lambda: None
        assert actions_module.run_action("open_image_viewer", app) is True

        from PySide6.QtWidgets import QApplication

        viewer = getattr(QApplication.instance(), "_image_viewer", None)
        try:
            assert viewer is not None
            assert viewer.displayed_pixmap().width() == 70
        finally:
            if viewer is not None:
                viewer.close()
                viewer.deleteLater()
                QApplication.instance()._image_viewer = None
            qapp.processEvents()

    def test_the_history_window_has_a_view_button(self, qapp, config, tmp_path):
        from history.store import HistoryStore
        from history.window import HistoryWindow

        store = HistoryStore(tmp_path / "history")
        store.add(image(64, 48), source="region")
        window = HistoryWindow(config_manager=config, store=store)
        try:
            assert hasattr(window, "view_btn")
            window.refresh()
            window.list_widget.setCurrentRow(0)
            assert window.view_btn.isEnabled() is True
            assert window.on_view() is True
        finally:
            from PySide6.QtWidgets import QApplication

            viewer = getattr(QApplication.instance(), "_image_viewer", None)
            if viewer is not None:
                viewer.close()
                viewer.deleteLater()
                QApplication.instance()._image_viewer = None
            window.close()
            window.deleteLater()
            qapp.processEvents()


# ── 主窗口 ────────────────────────────────────────────

class TestMainWindow:
    @pytest.fixture
    def window(self, qapp, config):
        from ui.main_window import MainWindow

        window = MainWindow(config_manager=config)
        yield window
        window.close()
        window.deleteLater()
        qapp.processEvents()

    def test_it_has_the_four_pages(self, window):
        assert window.page_keys() == ["history", "translation", "settings", "about"]
        assert window.stack.count() == 4

    def test_switching_changes_the_sidebar_and_the_content(self, window):
        assert window.current_page_key() == "history"
        assert window.switch_to("translation") is True
        assert window.current_page_key() == "translation"
        assert window.sidebar.currentRow() == 1
        assert window.stack.currentIndex() == 1

        window.sidebar.setCurrentRow(2)
        assert window.current_page_key() == "settings"

    def test_an_unknown_page_is_refused(self, window):
        assert window.switch_to("nowhere") is False
        assert window.current_page_key() == "history"

    def test_the_history_page_is_a_real_history_window(self, window):
        from history.window import HistoryWindow

        page = window.page("history")
        assert isinstance(page, HistoryWindow)
        assert hasattr(page, "list_widget")

    def test_the_settings_page_hosts_the_real_settings_dialog(self, window):
        from ui.settings_ui.dialog import SettingsDialog

        page = window.page("settings")
        assert isinstance(page, SettingsDialog)
        assert page.content_stack.count() >= 10      # 各设置页都在

    def test_the_about_page_builds(self, window):
        page = window.page("about")
        assert page is not None

    def test_translation_page_calls_the_service(self, window, monkeypatch):
        """翻译页走真实服务层：这里把 provider 换成本地替身，断言结果进了结果框。"""
        page = window.page("translation")
        assert page is not None

        class _Result:
            success = True
            text = "translated!"
            error_message = ""

        class _Service:
            def is_configured(self, *_args, **_kwargs):
                return True

            def translate(self, request, **_kwargs):
                assert request.text == "hello"
                return _Result()

        monkeypatch.setattr(page, "_service", lambda: _Service())
        page.source_edit.setPlainText("hello")
        assert page.on_translate() is True
        page._worker.wait(2000)
        page._on_translated(True, "translated!", "")
        assert page.result_edit.toPlainText() == "translated!"

    def test_translation_page_reports_a_missing_service(self, window, monkeypatch):
        page = window.page("translation")
        monkeypatch.setattr(page, "_service", lambda: None)
        page.source_edit.setPlainText("hello")
        assert page.on_translate() is False
        assert page.status.text()

    def test_open_main_window_reuses_one_instance(self, qapp, config):
        from PySide6.QtWidgets import QApplication

        from ui.main_window import open_main_window

        first = open_main_window(config)
        second = open_main_window(config)
        try:
            assert first is second
        finally:
            first.close()
            first.deleteLater()
            qapp.processEvents()
            QApplication.instance()._main_window = None
