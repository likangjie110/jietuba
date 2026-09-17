# -*- coding: utf-8 -*-
"""
GIF 录制在非 Windows 上的接线

Windows 上抓帧在 Rust 里（GDI BitBlt）；其它平台改用 Python 的 mss 抓帧线程——线程
本身与后端选择都在平台层（``core/platform/capture*.py``，测试见
``test_platform_capture.py``）。这里只测 gif 侧的接线：FrameRecorder.start() 拿到
平台层给的会话，以及为 macOS 实现的鼠标穿透。

真机不依赖：gifrecorder 与 objc 都是替身。
"""
import sys

import pytest

from gif import frame_recorder
from core.platform import window_ops
from gif.frame_recorder import FrameRecorder
from core.platform import capture as capture_module, capture_mss


class TestClickThrough:

    @pytest.fixture
    def fake_objc(self, monkeypatch):
        """冒充 objc：objc_object(...).window() 返回一个记录调用的 NSWindow 替身。"""
        calls = []
        # 穿透只在 cocoa 平台插件下才有意义（见 set_click_through_macos 的守卫），
        # 测试里显式钉住，否则在 Windows CI 上这条会因为平台名不是 cocoa 而走空
        monkeypatch.setattr("PySide6.QtGui.QGuiApplication.platformName",
                            staticmethod(lambda: "cocoa"))

        class _Window:
            def setIgnoresMouseEvents_(self, value):  # noqa: N802 —— Cocoa 选择器名
                calls.append(value)
                return True

        window = _Window()

        class _NSView:
            def window(self):
                return window

        module = type(sys)("objc")
        module.objc_object = staticmethod(lambda **kwargs: _NSView())
        monkeypatch.setitem(sys.modules, "objc", module)
        return calls

    def test_toggles_ignores_mouse_events(self, fake_objc):
        assert window_ops._set_click_through_macos(0x1234, True) is True
        assert window_ops._set_click_through_macos(0x1234, False) is True
        assert fake_objc == [True, False]

    def test_offscreen_platform_is_skipped_without_touching_objc(self, monkeypatch, qapp):
        """回归：离屏平台没有真实 NSWindow，去碰 objc 会直接段错误（整进程崩）。"""
        monkeypatch.setattr("PySide6.QtGui.QGuiApplication.platformName",
                            staticmethod(lambda: "offscreen"))
        touched = []
        module = type(sys)("objc")
        module.objc_object = staticmethod(lambda **kwargs: touched.append(1))
        monkeypatch.setitem(sys.modules, "objc", module)

        assert window_ops._set_click_through_macos(0x1234, True) is False
        assert touched == [], "不该去碰 objc"

    def test_missing_window_is_not_an_error(self, monkeypatch):
        class _NoWindow:
            def window(self):
                return None

        module = type(sys)("objc")
        module.objc_object = staticmethod(lambda **kwargs: _NoWindow())
        monkeypatch.setitem(sys.modules, "objc", module)

        assert window_ops._set_click_through_macos(1, True) is False

    def test_failure_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "objc", None)   # import 会失败

        assert window_ops._set_click_through_macos(1, True) is False


class TestStartPicksTheRightBackend:

    def test_non_windows_starts_the_mss_thread(self, qapp, monkeypatch):
        started = []

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                started.append(kwargs.get("elapsed_ms"))
                self.args = args

            def start(self):
                started.append("started")

            def stop(self):
                pass

        class _FakeStoreFactory:
            frame_count = 0
            frame_timestamps = []
            total_duration_ms = 0

            def set_state(self, state):
                pass

        fake_module = type(sys)("gifrecorder")
        fake_module.STATE_RECORDING = 1
        fake_module.FrameStore = lambda **kwargs: _FakeStoreFactory()
        monkeypatch.setattr(frame_recorder, "gifrecorder", fake_module)
        monkeypatch.setattr(frame_recorder, "_gifrecorder_available", True)
        monkeypatch.setattr(capture_module, "IS_WINDOWS", False)
        monkeypatch.setattr(capture_mss, "MssCaptureThread", _FakeThread)
        monkeypatch.setattr(FrameRecorder, "_start_scroll_listener", lambda self: None)

        recorder = FrameRecorder()
        recorder.set_rect(recorder._rect.__class__(0, 0, 100, 80))
        recorder.start()

        assert "started" in started, "非 Windows 应该启动 Python 抓帧线程"
        assert callable(started[0]), "计时要复用录制器自己的 start_time/pause_offset"
