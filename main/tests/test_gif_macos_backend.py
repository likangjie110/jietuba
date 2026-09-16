# -*- coding: utf-8 -*-
"""
GIF 录制的非 Windows 后端

Windows 上抓帧在 Rust 里（GDI BitBlt）；其它平台没有对应实现，改由 Python 用 mss
抓帧、把 BGRA 交给同一个 FrameStore——压缩、存储、导出仍然全在 Rust。这里测新增的
那半边：抓帧线程的行为（推帧、暂停、停止、节流）和鼠标穿透的 macOS 实现。

真机不依赖：mss 和 objc 都是替身。
"""
import sys
import time

import pytest

from gif import click_through, frame_recorder
from gif.frame_recorder import FrameRecorder, _MssCaptureThread


class _FakeStore:
    """只记录 push_bgra 调用，is_paused 是属性（和 Rust 那边的签名一致）。"""

    def __init__(self, paused=False):
        self.pushes = []
        self.is_paused = paused

    def push_bgra(self, buffer, elapsed_ms):
        self.pushes.append((bytes(buffer), elapsed_ms))
        return True


@pytest.fixture
def fake_mss(monkeypatch):
    """假的 mss：grab() 返回带 raw 的截图对象。"""
    state = {"grabs": 0, "regions": []}

    class _Shot:
        raw = bytearray(b"\x10\x20\x30\xff" * 4)

    class _Sct:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, region):
            state["grabs"] += 1
            state["regions"].append(region)
            return _Shot()

    module = type(sys)("mss")
    module.mss = _Sct
    monkeypatch.setitem(sys.modules, "mss", module)
    return state


def _run_thread(store, fps=20, seconds=0.25, paused=False, **kwargs):
    """跑一小段抓帧线程，返回它。"""
    thread = _MssCaptureThread(store, 10, 20, 64, 48, fps,
                               elapsed_ms=lambda: 123, **kwargs)
    if paused:
        thread.pause()
    thread.start()
    time.sleep(seconds)
    thread.stop()
    return thread


class TestCaptureThread:

    def test_pushes_frames_with_the_elapsed_time(self, fake_mss):
        store = _FakeStore()

        _run_thread(store)

        assert store.pushes, "应该抓到帧"
        assert all(elapsed == 123 for _, elapsed in store.pushes)
        assert fake_mss["regions"][0] == {"left": 10, "top": 20, "width": 64, "height": 48}

    def test_stop_ends_the_loop(self, fake_mss):
        store = _FakeStore()

        _run_thread(store, seconds=0.15)
        grabbed_at_stop = fake_mss["grabs"]
        time.sleep(0.15)

        assert fake_mss["grabs"] == grabbed_at_stop

    def test_paused_thread_does_not_capture(self, fake_mss):
        store = _FakeStore()

        _run_thread(store, paused=True)

        assert store.pushes == []

    def test_store_paused_state_is_respected(self, fake_mss):
        """录制暂停时 FrameStore 自己也会置位，两条路都不该抓帧。"""
        store = _FakeStore(paused=True)

        _run_thread(store)

        assert store.pushes == []

    def test_resume_after_pause_captures_again(self, fake_mss):
        store = _FakeStore()
        thread = _MssCaptureThread(store, 0, 0, 32, 32, 30, elapsed_ms=lambda: 0)
        thread.pause()
        thread.start()
        time.sleep(0.1)
        assert store.pushes == []

        thread.resume()
        time.sleep(0.2)
        thread.stop()

        assert store.pushes, "恢复后应该继续抓帧"

    def test_frame_rate_is_throttled(self, fake_mss):
        """节流得当真：10fps 跑 0.4 秒不该抓到 40 帧。"""
        store = _FakeStore()

        _run_thread(store, fps=10, seconds=0.4)

        assert 2 <= len(store.pushes) <= 8, f"抓到 {len(store.pushes)} 帧，节流没生效"


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
        assert click_through.set_click_through_macos(0x1234, True) is True
        assert click_through.set_click_through_macos(0x1234, False) is True
        assert fake_objc == [True, False]

    def test_offscreen_platform_is_skipped_without_touching_objc(self, monkeypatch, qapp):
        """回归：离屏平台没有真实 NSWindow，去碰 objc 会直接段错误（整进程崩）。"""
        monkeypatch.setattr("PySide6.QtGui.QGuiApplication.platformName",
                            staticmethod(lambda: "offscreen"))
        touched = []
        module = type(sys)("objc")
        module.objc_object = staticmethod(lambda **kwargs: touched.append(1))
        monkeypatch.setitem(sys.modules, "objc", module)

        assert click_through.set_click_through_macos(0x1234, True) is False
        assert touched == [], "不该去碰 objc"

    def test_missing_window_is_not_an_error(self, monkeypatch):
        class _NoWindow:
            def window(self):
                return None

        module = type(sys)("objc")
        module.objc_object = staticmethod(lambda **kwargs: _NoWindow())
        monkeypatch.setitem(sys.modules, "objc", module)

        assert click_through.set_click_through_macos(1, True) is False

    def test_failure_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "objc", None)   # import 会失败

        assert click_through.set_click_through_macos(1, True) is False


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
        monkeypatch.setattr(frame_recorder, "_IS_WINDOWS", False)
        monkeypatch.setattr(frame_recorder, "_MssCaptureThread", _FakeThread)
        monkeypatch.setattr(FrameRecorder, "_start_scroll_listener", lambda self: None)

        recorder = FrameRecorder()
        recorder.set_rect(recorder._rect.__class__(0, 0, 100, 80))
        recorder.start()

        assert "started" in started, "非 Windows 应该启动 Python 抓帧线程"
        assert callable(started[0]), "计时要复用录制器自己的 start_time/pause_offset"
