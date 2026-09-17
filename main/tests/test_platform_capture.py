# -*- coding: utf-8 -*-
"""平台层的抓帧后端：``core/platform/capture.py`` 与 ``capture_mss.py``。

Windows 上抓帧在 Rust 里（GDI BitBlt，零 GIL 争用）；其它平台由这里的 mss 线程抓帧，
两条路径都往同一个 Rust FrameStore 里喂帧。这里测三件事：能力表怎么说、门面怎么选
后端、mss 线程的行为（推帧、暂停、停止、节流）。

真机不依赖：gifrecorder 与 mss 都是替身。
"""
import sys
import time

import pytest

from core.platform import capture, capture_mss, capabilities
from core.platform.capabilities import Capability, Support


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
    thread = capture_mss.MssCaptureThread(store, 10, 20, 64, 48, fps,
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
        thread = capture_mss.MssCaptureThread(store, 0, 0, 32, 32, 30, elapsed_ms=lambda: 0)
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


class TestCapability:

    def test_backend_name_comes_from_the_capability_table(self):
        assert capture.backend_name() == capabilities.backend_name(Capability.FRAME_CAPTURE)
        assert capture.backend_name() in ("gdi", "mss")

    @pytest.mark.parametrize("platform_name, expected", [
        ("windows", Support.FULL),      # Rust 线程里的 GDI BitBlt
        ("macos", Support.DEGRADED),    # Python 线程用 mss，有 GIL 争用
        ("linux", Support.DEGRADED),
    ])
    def test_native_capture_is_windows_only(self, platform_name, expected):
        assert capabilities.support(Capability.FRAME_CAPTURE, platform_name) is expected
        assert capabilities.available(Capability.FRAME_CAPTURE, platform_name) is True


class TestCreateSession:

    @pytest.fixture
    def fake_gifrecorder(self, monkeypatch):
        """冒充 gifrecorder：RecordSession 记录构造参数。"""

        class _RecordSession:
            def __init__(self, *args):
                self.args = args

        module = type(sys)("gifrecorder")
        module.RecordSession = _RecordSession
        monkeypatch.setattr(capture, "gifrecorder", module)
        monkeypatch.setattr(capture, "_gifrecorder_available", True)
        return module

    def test_windows_uses_the_rust_record_session(self, monkeypatch, fake_gifrecorder):
        module = fake_gifrecorder
        store = object()
        monkeypatch.setattr(capture, "IS_WINDOWS", True)

        session = capture.create_session(store, 1, 2, 640, 480, 16)

        assert isinstance(session, module.RecordSession)
        assert session.args == (store, 1, 2, 640, 480, 16)

    def test_windows_without_the_extension_returns_none(self, monkeypatch, fake_gifrecorder):
        """缺 gifrecorder 时给 None 让调用方降级，而不是炸在构造函数里。"""
        monkeypatch.setattr(capture, "IS_WINDOWS", True)
        monkeypatch.setattr(capture, "_gifrecorder_available", False)

        assert capture.create_session(object(), 0, 0, 10, 10, 16) is None

    def test_other_platforms_start_the_mss_thread(self, monkeypatch):
        started = []

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def start(self):
                started.append(self)

        monkeypatch.setattr(capture, "IS_WINDOWS", False)
        monkeypatch.setattr(capture_mss, "MssCaptureThread", _FakeThread)
        store = object()
        marker = object()

        session = capture.create_session(store, 10, 20, 64, 48, 20,
                                         elapsed_ms=marker, parent=None)

        assert started == [session], "会话由门面启动，上层不用记两条路径的差别"
        assert session.args == (store, 10, 20, 64, 48, 20)
        assert session.kwargs == {"elapsed_ms": marker, "parent": None}
