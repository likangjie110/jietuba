# -*- coding: utf-8 -*-
"""视频录制核心（``video/video_recorder.py``）。

这里全部用替身：编码后端由 ``core/platform/video.create_session`` 提供、抓帧由
``FrameSourceThread`` 提供，两者都在用例里换掉。真实编码在真机脚本里验证
（Qt 的编码器是异步的，放进单元测试只会得到一条随机红的用例）。
"""

import time

import pytest
from PySide6.QtCore import QObject, QRect, QSize, Signal
from PySide6.QtGui import QImage

from video import video_recorder as vr
from video.video_recorder import VideoOptions, VideoRecorder, VideoState


# ── 替身 ──────────────────────────────────────────────

class FakeSession(QObject):
    """假的录制会话：记录调用，允许用例手动发状态/错误信号。"""

    error_changed = Signal(str)
    state_changed = Signal(str)
    duration_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.recorded = False
        self.stopped = False
        self.pushed = []
        self.path = ""

    def record(self):
        self.recorded = True

    def stop(self):
        self.stopped = True

    def push_image(self, image):
        self.pushed.append(image)
        return True

    def dropped_frames(self):
        return 0


class FakeSource(QObject):
    """假的抓帧线程：不真抓，等用例手动发帧。"""

    frame_ready = Signal(QImage)
    failed = Signal(str)

    instances = []

    def __init__(self, region, fps, parent=None):
        super().__init__(parent)
        self.region = region
        self.fps = fps
        self.started = False
        self.stopped = False
        self.paused = False
        FakeSource.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait(self, _ms=0):
        return True

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


@pytest.fixture
def fake_backend(monkeypatch):
    """把编码后端与抓帧线程都换成替身，返回 (session, 取 source 的函数)。"""
    session = FakeSession()
    FakeSource.instances = []

    def create_session(_path, **_kwargs):
        session.path = _path
        return session

    monkeypatch.setattr(vr.platform_video, "is_available", lambda: True)
    monkeypatch.setattr(vr.platform_video, "create_session", create_session)
    monkeypatch.setattr(vr.platform_video, "backend_name", lambda: "qtmultimedia")
    monkeypatch.setattr("video.frame_source.FrameSourceThread", FakeSource)

    def last_source():
        return FakeSource.instances[-1] if FakeSource.instances else None

    return session, last_source


def make_image(width=64, height=48, color=0) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    return image


def options(**overrides) -> VideoOptions:
    base = dict(container="mp4", codec="h264", fps=30, quality="source",
                output_path="/tmp/jietuba_test_video.mp4")
    base.update(overrides)
    return VideoOptions(**base)


#: 录制器与它创建的抓帧线程是「无父对象的 QObject」：Python 侧没有引用就会被回收，
#: 于是信号源消失（"Signal source has been deleted"）。用列表钉住它们的生命周期。
_KEEPALIVE: list = []


def start_recorder(qapp, fake_backend, rect=None, **overrides) -> VideoRecorder:
    recorder = VideoRecorder()
    _KEEPALIVE.append(recorder)
    recorder.set_rect(rect or QRect(100, 100, 640, 480))
    assert recorder.start(options(**overrides))
    return recorder


# ── 纯函数 ────────────────────────────────────────────

class TestTargetSize:
    def test_source_keeps_the_physical_pixels(self):
        assert vr.target_size(QSize(2560, 1440), "source") == QSize(2560, 1440)

    def test_caps_at_the_quality_limit(self):
        assert vr.target_size(QSize(2560, 1440), "1080p") == QSize(1920, 1080)

    def test_keeps_aspect_ratio(self):
        # 16:10 的区域缩到 1080p 后应是 1728x1080，而不是被拉成 16:9
        assert vr.target_size(QSize(2560, 1600), "1080p") == QSize(1728, 1080)

    def test_never_upscales(self):
        assert vr.target_size(QSize(640, 360), "1080p") == QSize(640, 360)

    def test_dimensions_are_even(self):
        size = vr.target_size(QSize(641, 361), "source")
        assert (size.width(), size.height()) == (640, 360)

    def test_empty_source_is_zero(self):
        assert vr.target_size(QSize(0, 0), "source") == QSize(0, 0)


class TestRegionCoordinates:
    def test_region_is_passed_in_window_coordinates(self, qapp, fake_backend):
        """抓帧区域按窗口坐标给（与 GIF 录制一致），不做设备像素比换算。

        这条钉住的是一次真机踩坑：按 2 倍坐标抓会抓到偏掉的内容（mss 在 macOS 上按
        「点」取坐标、返回同尺寸像素）。
        """
        _session, last_source = fake_backend
        start_recorder(qapp, fake_backend, rect=QRect(400, 300, 640, 480))

        assert last_source().region == {"left": 401, "top": 301, "width": 638, "height": 478}


# ── 起步与停止 ────────────────────────────────────────

class TestStart:
    def test_start_reports_recording_and_starts_both_halves(self, qapp, fake_backend):
        session, last_source = fake_backend
        recorder = start_recorder(qapp, fake_backend)

        assert recorder.state is VideoState.RECORDING
        assert session.recorded is True
        assert last_source().started is True

    def test_region_is_inset_by_one_pixel(self, qapp, fake_backend):
        """录制区域四边各内缩 1px，免得把选区边框录进去。"""
        _session, last_source = fake_backend
        start_recorder(qapp, fake_backend, rect=QRect(100, 100, 640, 480))

        assert last_source().region == {"left": 101, "top": 101, "width": 638, "height": 478}

    def test_frames_are_scaled_to_the_quality_preset(self, qapp, fake_backend):
        session, last_source = fake_backend
        start_recorder(qapp, fake_backend, rect=QRect(0, 0, 1920, 1080), quality="480p")

        # 抓帧线程给的是录制区域（内缩 1px 后）的物理像素
        last_source().frame_ready.emit(make_image(1918, 1078))
        pushed = session.pushed[-1]
        assert pushed.width() <= 854 and pushed.height() <= 480
        assert pushed.width() % 2 == 0 and pushed.height() % 2 == 0

    def test_source_quality_pushes_the_frame_untouched(self, qapp, fake_backend):
        session, last_source = fake_backend
        start_recorder(qapp, fake_backend, rect=QRect(0, 0, 640, 480))

        last_source().frame_ready.emit(make_image(638, 478))
        assert session.pushed[-1].size() == QSize(638, 478)

    def test_unavailable_backend_does_not_start(self, qapp, monkeypatch):
        monkeypatch.setattr(vr.platform_video, "is_available", lambda: False)
        recorder = VideoRecorder()
        recorder.set_rect(QRect(0, 0, 640, 480))

        failures = []
        recorder.failed.connect(failures.append)
        assert recorder.start(options()) is False
        assert recorder.state is VideoState.IDLE
        assert failures and "编码后端" in failures[0]

    def test_missing_output_path_does_not_start(self, qapp, fake_backend):
        recorder = VideoRecorder()
        recorder.set_rect(QRect(0, 0, 640, 480))

        failures = []
        recorder.failed.connect(failures.append)
        assert recorder.start(options(output_path="")) is False
        assert failures

    def test_tiny_region_does_not_start(self, qapp, fake_backend):
        recorder = VideoRecorder()
        recorder.set_rect(QRect(0, 0, 1, 1))

        failures = []
        recorder.failed.connect(failures.append)
        assert recorder.start(options()) is False
        assert failures

    def test_session_failure_is_reported(self, qapp, monkeypatch):
        monkeypatch.setattr(vr.platform_video, "is_available", lambda: True)
        monkeypatch.setattr(vr.platform_video, "create_session", lambda *_a, **_k: None)
        recorder = VideoRecorder()
        recorder.set_rect(QRect(0, 0, 640, 480))

        failures = []
        recorder.failed.connect(failures.append)
        assert recorder.start(options()) is False
        assert failures and "录制会话" in failures[0]


class TestPauseAndStop:
    def test_pause_stops_the_capture_thread_and_drops_frames(self, qapp, fake_backend):
        session, last_source = fake_backend
        recorder = start_recorder(qapp, fake_backend)
        source = last_source()

        recorder.pause()
        assert recorder.state is VideoState.PAUSED
        assert source.paused is True

        pushed_before = len(session.pushed)
        source.frame_ready.emit(make_image())      # 暂停期间抓帧线程还可能送来几帧
        assert len(session.pushed) == pushed_before

    def test_resume_keeps_counting_frames(self, qapp, fake_backend):
        session, last_source = fake_backend
        recorder = start_recorder(qapp, fake_backend)
        recorder.pause()
        recorder.resume()

        assert recorder.state is VideoState.RECORDING
        last_source().frame_ready.emit(make_image())
        assert len(session.pushed) == 1

    def test_stop_stops_source_and_session(self, qapp, fake_backend):
        session, _last_source = fake_backend
        recorder = start_recorder(qapp, fake_backend)

        recorder.stop()
        assert recorder.state is VideoState.STOPPED
        assert session.stopped is True
        assert FakeSource.instances[-1].stopped is True

    def test_finished_is_emitted_once_when_the_encoder_reports_stopped(self, qapp, fake_backend):
        session, _source = fake_backend
        recorder = start_recorder(qapp, fake_backend)

        finished = []
        recorder.finished.connect(finished.append)
        recorder.stop()
        assert finished == []                      # 编码器还没收尾

        session.state_changed.emit("stopped")
        session.state_changed.emit("stopped")
        assert finished == [recorder.output_path]

    def test_encoder_error_is_reported_and_finalized(self, qapp, fake_backend):
        session, _source = fake_backend
        recorder = start_recorder(qapp, fake_backend)

        failures, finished = [], []
        recorder.failed.connect(failures.append)
        recorder.finished.connect(finished.append)

        session.error_changed.emit("位置不可写")
        assert failures == ["位置不可写"]
        assert recorder.state is VideoState.STOPPED


class TestDurationAndLimit:
    def test_duration_excludes_the_pause(self, qapp, fake_backend):
        recorder = start_recorder(qapp, fake_backend)
        time.sleep(0.05)
        recorder.pause()
        first = recorder.duration_ms()
        time.sleep(0.06)
        assert recorder.duration_ms() < first + 30    # 暂停期间几乎没涨

    def test_max_duration_auto_stops(self, qapp, fake_backend):
        from dataclasses import replace

        recorder = start_recorder(qapp, fake_backend)
        limits = []
        recorder.limit_reached.connect(lambda: limits.append(True))

        recorder._on_tick()
        assert limits == []                            # 默认不限制，不该触发

        recorder._options = replace(recorder.options, max_duration_s=1)
        recorder._started_at -= 1000                   # 假装已经录了 1000 秒
        recorder._on_tick()
        assert limits == [True]
        assert recorder.state is VideoState.STOPPED
