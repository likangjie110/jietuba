# -*- coding: utf-8 -*-
"""视频录制的接线：动作注册表、截图/工具栏入口、设置页与保存路径。

真实编码在真机脚本里验证；这里钉的是「按钮/动作/配置三者连起来了吗」。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from core import actions
from settings.tool_settings import ToolSettingsManager
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_misc import create_misc_page


@pytest.fixture
def manager(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "video.ini"), QSettings.Format.IniFormat))


def _fake_dialog(manager, **extra):
    """只够建页面用的假对话框（页面里会用到 config_manager / tr）。"""
    base = dict(config_manager=manager, tr=lambda text: text)
    base.update(extra)
    return SimpleNamespace(**base)


# ── 动作注册表 ────────────────────────────────────────

class TestActionRegistry:
    def test_video_action_opens_the_editor_in_video_mode(self):
        action = actions.ACTIONS_BY_ID["video_capture"]
        assert action.editor_mode == actions.EDITOR_MODE_VIDEO
        assert action.silent_capture is False
        assert action.tray is True

    def test_video_action_shows_up_in_the_tray_defaults(self, manager):
        flags = manager.get_action_tray_flags()
        assert flags["video_capture"] is True

    def test_run_action_opens_the_editor_with_the_detected_rect(self, monkeypatch):
        called = []

        app = SimpleNamespace(
            start_screenshot_for_gesture=lambda mode, rect: called.append((mode, rect)) or True)
        monkeypatch.setattr(actions, "_target_rect", lambda _app: [10, 20, 110, 220])

        assert actions.run_action("video_capture", app) is True
        assert called == [(actions.EDITOR_MODE_VIDEO, [10, 20, 110, 220])]


class TestToolbarEntry:
    def test_video_button_is_in_the_default_layout_after_gif(self):
        from ui import toolbar_layout

        order = list(toolbar_layout.DEFAULT_ORDER)
        assert order[order.index("gif") + 1] == "video"

    def test_video_button_starts_collapsed(self):
        """新按钮默认收进「…」，免得把工具栏默认宽度撑宽。"""
        from ui import toolbar_layout

        layout = dict(toolbar_layout.normalize_layout([]))
        assert layout["video"] == toolbar_layout.MORE

    def test_screenshot_window_routes_the_mode_to_the_video_window(self, monkeypatch):
        from ui.screenshot_window import ScreenshotWindow

        started = []
        scene = SimpleNamespace(
            selection_model=SimpleNamespace(is_confirmed=True),
            confirm_selection=lambda: None,
        )
        fake_self = SimpleNamespace(_is_closing=False, scene=scene,
                                    start_video_record_mode=lambda: started.append(True))

        assert ScreenshotWindow.apply_editor_mode(fake_self, "video") is True
        assert started == [True]


class TestScreenshotWindowEntry:
    def test_start_video_record_mode_uses_the_confirmed_selection(self, monkeypatch):
        from PySide6.QtCore import QRect

        import video
        from ui.screenshot_window import ScreenshotWindow

        opened, closed = [], []
        monkeypatch.setattr(video, "start_video_record_window",
                            lambda rect: opened.append(rect))

        selection = SimpleNamespace(
            is_confirmed=True,
            rect=lambda: QRect(30, 40, 320, 240),
        )
        fake_self = SimpleNamespace(
            scene=SimpleNamespace(selection_model=selection),
            cleanup_and_close=lambda: closed.append(True),
        )

        ScreenshotWindow.start_video_record_mode(fake_self)
        assert opened == [QRect(30, 40, 320, 240)]
        assert closed == [True], "录制窗口起来之后截图窗口该关掉"

    def test_without_a_selection_it_warns_and_does_not_start(self, monkeypatch):
        import ui.screenshot_window as screenshot_window
        import video
        from ui.screenshot_window import ScreenshotWindow

        opened, warned = [], []
        monkeypatch.setattr(video, "start_video_record_window",
                            lambda rect: opened.append(rect))
        monkeypatch.setattr(screenshot_window, "show_modeless_warning_dialog",
                            lambda *args: warned.append(args))
        selection = SimpleNamespace(is_confirmed=False)
        fake_self = SimpleNamespace(
            scene=SimpleNamespace(selection_model=selection), cleanup_and_close=lambda: None)

        ScreenshotWindow.start_video_record_mode(fake_self)
        assert opened == []
        assert warned, "没有选区时要给一条提示"


# ── 设置页 ────────────────────────────────────────────

class TestSettingsPage:
    def test_page_builds_every_video_control(self, qapp, manager):
        dialog, page = _build_page(manager)
        try:
            for attribute in ("video_container_combo", "video_codec_combo",
                              "video_quality_combo", "video_fps_combo", "video_bitrate_spin",
                              "video_audio_combo", "video_audio_device_combo",
                              "video_duration_spin", "video_save_path_input"):
                assert hasattr(dialog, attribute), attribute
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_controls_are_filled_from_the_config(self, qapp, manager):
        manager.set_video_container("mkv")
        manager.set_video_codec("h265")
        manager.set_video_quality("720p")
        manager.set_video_fps(60)
        manager.set_video_bitrate_mbps(9)
        manager.set_video_max_duration_s(45)
        manager.set_video_save_path("/tmp/videos")

        dialog, page = _build_page(manager)
        try:
            assert dialog.video_container_combo.currentData() == "mkv"
            assert dialog.video_codec_combo.currentData() == "h265"
            assert dialog.video_quality_combo.currentData() == "720p"
            assert dialog.video_fps_combo.currentData() == 60
            assert dialog.video_bitrate_spin.value() == 9
            assert dialog.video_duration_spin.value() == 45
            assert dialog.video_save_path_input.text() == "/tmp/videos"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_microphone_device_row_is_disabled_without_audio(self, qapp, manager):
        manager.set_video_audio("none")
        dialog, page = _build_page(manager)
        try:
            assert dialog.video_audio_device_combo.isEnabled() is False

            dialog.video_audio_combo.setCurrentIndex(
                dialog.video_audio_combo.findData("microphone"))
            assert dialog.video_audio_device_combo.isEnabled() is True
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_save_writes_every_video_setting(self, manager, qapp, monkeypatch):
        dialog, page = _build_page(manager)
        try:
            _select(dialog.video_container_combo, "mov")
            _select(dialog.video_codec_combo, "h265")
            _select(dialog.video_quality_combo, "480p")
            _select(dialog.video_fps_combo, 15)
            dialog.video_bitrate_spin.setValue(7)
            dialog.video_duration_spin.setValue(90)
            dialog.video_save_path_input.setText("  /tmp/rec  ")

            monkeypatch.setattr("main_app.main_app_instance", lambda: None)
            SettingsDialog._save_integration_settings(dialog)

            assert manager.get_video_container() == "mov"
            assert manager.get_video_codec() == "h265"
            assert manager.get_video_quality() == "480p"
            assert manager.get_video_fps() == 15
            assert manager.get_video_bitrate_mbps() == 7
            assert manager.get_video_max_duration_s() == 90
            assert manager.get_video_save_path() == "/tmp/rec"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_reset_restores_the_defaults_on_the_widgets(self, manager, qapp):
        from ui.settings_ui.dialog import _reset_integration_controls

        manager.set_video_container("mkv")
        manager.set_video_fps(60)
        manager.set_video_max_duration_s(300)
        dialog, page = _build_page(manager)
        try:
            # 页面按配置填好之后，重置按钮会把控件拨回默认值（真正落盘仍走保存）
            _reset_integration_controls(dialog)
            assert dialog.video_container_combo.currentData() == "mp4"
            assert dialog.video_fps_combo.currentData() == 30
            assert dialog.video_duration_spin.value() == 0
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_snapshot_remembers_the_video_controls(self, qapp, manager):
        from ui.settings_ui.dialog import _snapshot_integration_controls

        dialog, page = _build_page(manager)
        try:
            snap: dict = {}
            _snapshot_integration_controls(dialog, snap)
            for attribute in ("video_container_combo", "video_codec_combo",
                              "video_quality_combo", "video_fps_combo",
                              "video_audio_combo", "video_audio_device_combo"):
                assert attribute in snap, attribute
            assert "video_save_path_input" in snap
            assert "video_bitrate_spin" in snap and "video_duration_spin" in snap
        finally:
            page.deleteLater()
            qapp.processEvents()


def _build_page(manager):
    """建一次真实的「其它设置」页，返回 (假对话框, 页面)。"""
    dialog = _fake_dialog(manager)
    return dialog, create_misc_page(dialog)


def _select(combo, value):
    combo.setCurrentIndex(combo.findData(value))


# ── 保存路径 ──────────────────────────────────────────

class TestRecorderRect:
    def test_geometry_sync_gives_the_recorder_the_selection(self, qapp, manager, monkeypatch):
        """选区一变，录制器拿到的区域也要跟着变。

        真机上踩过一次：窗口只同步了遮罩，录制器的区域始终是空的，点「开始」只会报
        「录制区域太小」——所以这条钉住 ``_sync_geometry`` 的这一步。
        """
        from PySide6.QtCore import QRect

        from video.record_window import VideoRecordWindow

        monkeypatch.setattr(VideoRecordWindow, "__init__", lambda self, *a, **k: None)
        window = VideoRecordWindow.__new__(VideoRecordWindow)
        window._recorder = SimpleNamespace(set_rect=lambda rect: setattr(window, "_seen", rect))
        window._overlay = SimpleNamespace(update_rect=lambda rect: None)
        window._toolbar = SimpleNamespace(move=lambda *a: None)
        window._rect = QRect()

        VideoRecordWindow._sync_geometry(window, QRect(10, 20, 300, 200), reposition=False)
        assert window._seen == QRect(10, 20, 300, 200)

class TestOutputPath:
    def _window(self, manager, monkeypatch, *, video_path="", screenshot_path=""):
        """只取录制窗口里与路径/参数有关的那部分行为：不建任何窗口。"""
        from video.record_window import VideoRecordWindow

        manager.set_video_save_path(video_path)
        monkeypatch.setattr(manager, "get_screenshot_save_path", lambda: screenshot_path)
        monkeypatch.setattr(VideoRecordWindow, "__init__", lambda self, *a, **k: None)

        window = VideoRecordWindow.__new__(VideoRecordWindow)
        window._config = manager
        return window

    def test_video_folder_wins(self, manager, monkeypatch, tmp_path):
        window = self._window(manager, monkeypatch,
                              video_path=str(tmp_path / "videos"),
                              screenshot_path=str(tmp_path / "shots"))
        path = window._next_output_path("mp4")
        assert Path(path).parent == tmp_path / "videos"
        assert Path(path).name.startswith("jietuba_video_")
        assert Path(path).suffix == ".mp4"

    def test_falls_back_to_the_screenshot_folder(self, manager, monkeypatch, tmp_path):
        window = self._window(manager, monkeypatch,
                              screenshot_path=str(tmp_path / "shots"))
        assert Path(window._next_output_path("mkv")).parent == tmp_path / "shots"

    def test_extension_follows_the_container(self, manager, monkeypatch, tmp_path):
        window = self._window(manager, monkeypatch, video_path=str(tmp_path))
        for container, suffix in (("mp4", ".mp4"), ("mkv", ".mkv"),
                                 ("mov", ".mov"), ("avi", ".avi")):
            assert Path(window._next_output_path(container)).suffix == suffix

    def test_unknown_container_falls_back_to_mp4(self, manager, monkeypatch, tmp_path):
        window = self._window(manager, monkeypatch, video_path=str(tmp_path))
        assert Path(window._next_output_path("webm")).suffix == ".mp4"


class TestOptionsFromSettings:
    def test_build_options_reads_config_and_toolbar(self, manager, monkeypatch, tmp_path):
        from video.record_window import VideoRecordWindow

        manager.set_video_container("mkv")
        manager.set_video_codec("h265")
        manager.set_video_bitrate_mbps(11)
        manager.set_video_audio("microphone")
        manager.set_video_audio_device("外接麦克风")
        manager.set_video_max_duration_s(60)
        manager.set_video_save_path(str(tmp_path))

        monkeypatch.setattr(VideoRecordWindow, "__init__", lambda self, *a, **k: None)
        window = VideoRecordWindow.__new__(VideoRecordWindow)
        window._config = manager
        window._toolbar = SimpleNamespace(current_fps=lambda: 24,
                                          current_quality=lambda: "720p")

        options = window._build_options()
        assert options.container == "mkv"
        assert options.codec == "h265"
        assert options.fps == 24
        assert options.quality == "720p"
        assert options.bitrate_mbps == 11
        assert options.audio is True
        assert options.audio_device == "外接麦克风"
        assert options.max_duration_s == 60
        assert Path(options.output_path).parent == tmp_path

class TestWindowKeepsShowing:
    def test_record_window_keeps_overlay_and_toolbar_visible_when_inactive(
            self, monkeypatch):
        """macOS 上 Qt.Tool 窗口失焦会被系统藏起来：遮罩与工具栏都要显式保住。

        真机上踩过：录屏开始后切到别的程序，遮罩与工具栏一起消失，而录制还在跑。
        """
        from video import record_window as record_window_module
        from video.record_window import VideoRecordWindow

        called = []
        monkeypatch.setattr("core.platform.window_ops.keep_visible_when_inactive",
                            lambda widget: called.append(widget))
        overlay, toolbar = object(), object()

        window = VideoRecordWindow.__new__(VideoRecordWindow)
        window._overlay = overlay
        window._toolbar = toolbar
        record_window_module.VideoRecordWindow._keep_windows_visible(window)

        assert called == [overlay, toolbar]
