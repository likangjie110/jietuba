# -*- coding: utf-8 -*-
"""``core/platform/video.py``：能力登记、后端能力查询、录制会话装配。

真实编码（Qt 的 FFmpeg 后端）在真机脚本里验证；这里只钉住「问平台拿到的答案」。
"""

import pytest
from PySide6.QtCore import QSize

from core.platform import video as platform_video
from core.platform.capabilities import Capability, Support, backend_name, support


def _captured_logs(monkeypatch) -> list:
    """把 log_* 收到的消息模板抓下来（与 test_platform_window_ops 同款做法）。"""
    captured: list = []

    def _record(message, *_args, **_kwargs):
        captured.append(getattr(message, "template", str(message)))

    for name in ("log_debug", "log_warning", "log_info", "log_exception"):
        monkeypatch.setattr(f"core.logger.{name}", _record)
    return captured


class TestCapabilityRegistration:
    def test_video_recording_is_registered_everywhere(self):
        for platform in ("windows", "macos", "linux"):
            assert support(Capability.VIDEO_RECORDING, platform) is not Support.NONE

    def test_backend_is_the_qt_multimedia_ffmpeg_backend(self):
        for platform in ("windows", "macos", "linux"):
            assert backend_name(Capability.VIDEO_RECORDING, platform) == "qtmultimedia"

    def test_macos_is_full_and_linux_is_degraded(self):
        """macOS 真机验证过；Linux 的 Wayland 下 Qt 抓屏/编码可能拿不到画面。"""
        assert support(Capability.VIDEO_RECORDING, "macos") is Support.FULL
        assert support(Capability.VIDEO_RECORDING, "linux") is Support.DEGRADED


class TestCapabilityQueries:
    def test_containers_are_a_subset_of_the_known_ones(self):
        assert set(platform_video.container_formats()) <= {"mp4", "mkv", "mov", "avi"}

    def test_codecs_are_a_subset_of_the_known_ones(self):
        assert set(platform_video.video_codecs()) <= {"h264", "h265"}

    def test_availability_matches_the_container_list(self):
        assert platform_video.is_available() == bool(platform_video.container_formats())

    def test_missing_qt_multimedia_means_unavailable(self, monkeypatch):
        monkeypatch.setattr(platform_video, "_qt_multimedia", lambda: None)
        assert platform_video.is_available() is False
        assert platform_video.container_formats() == []
        assert platform_video.video_codecs() == []
        assert platform_video.audio_input_device_names() == []

    def test_audio_devices_never_raise(self, monkeypatch):
        monkeypatch.setattr(platform_video, "_qt_multimedia", lambda: None)
        assert platform_video.audio_input_device_names() == []

    def test_unknown_enum_name_resolves_to_none(self):
        from PySide6.QtMultimedia import QMediaFormat

        assert platform_video._enum(QMediaFormat.FileFormat, "NotAFormat") is None
        assert platform_video._enum(QMediaFormat.FileFormat, "MPEG4") is not None


class TestCreateSession:
    def test_unknown_container_is_refused_with_a_log(self, monkeypatch, tmp_path):
        logged = _captured_logs(monkeypatch)
        session = platform_video.create_session(
            str(tmp_path / "out.webm"), container="webm", video_codec="h264")
        assert session is None
        assert any("编码后端支持" in message for message in logged), logged

    def test_missing_backend_is_refused_with_a_log(self, monkeypatch, tmp_path, qapp):
        logged = _captured_logs(monkeypatch)
        monkeypatch.setattr(platform_video, "_qt_multimedia", lambda: None)
        session = platform_video.create_session(str(tmp_path / "out.mp4"))
        assert session is None
        assert any("QtMultimedia" in message for message in logged), logged

    @pytest.mark.skipif(not platform_video.is_available(), reason="这个构建没有编码后端")
    def test_session_wraps_the_encoder_and_accepts_frames(self, qapp, tmp_path):
        """真后端上装一次会话：能建起来、能推一帧、能报出输出路径。"""
        from PySide6.QtGui import QImage

        out = str(tmp_path / "session.mp4")
        session = platform_video.create_session(
            out, container="mp4", video_codec="h264", fps=30, size=QSize(320, 240))
        assert session is not None
        assert session.output_path() == out

        image = QImage(320, 240, QImage.Format.Format_RGB32)
        image.fill(0)
        session.record()
        session.push_image(image)
        session.stop()
        assert session.dropped_frames() >= 0            # 只要求不抛异常

    def test_audio_without_a_device_degrades_with_a_log(self, monkeypatch, qapp, tmp_path):
        """要求录声音但机器上没有麦克风：退回无声录制，并说清楚。"""
        logged = _captured_logs(monkeypatch)
        monkeypatch.setattr(platform_video, "_resolve_audio_device", lambda _name: None)
        session = platform_video.create_session(
            str(tmp_path / "silent.mp4"), audio_device="不存在的麦克风")
        if session is None:                             # 构建里没有后端时走不到这一步
            pytest.skip("这个构建没有编码后端")
        assert any("麦克风" in message for message in logged), logged
