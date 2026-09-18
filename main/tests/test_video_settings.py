# -*- coding: utf-8 -*-
"""视频录制设置项的取值兜底与落盘（``settings/tool_settings.py``）。

驱动的是真实读写路径：所有键都走 ``app/`` 前缀，读音非法时退回默认值。
"""

import pytest

from video.video_recorder import QUALITY_LIMITS, VideoOptions


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    manager = get_tool_settings_manager()
    keys = ("video_container", "video_codec", "video_fps", "video_quality",
            "video_bitrate_mbps", "video_audio", "video_audio_device",
            "video_max_duration_s", "video_save_path")
    snapshot = {key: manager.get_app_setting(key) for key in keys}
    yield manager
    for key, value in snapshot.items():
        manager.set_app_setting(key, value)


class TestDefaults:
    def test_every_key_has_a_default(self):
        from settings.tool_settings import ToolSettingsManager

        defaults = ToolSettingsManager.APP_DEFAULT_SETTINGS
        for key in ("video_container", "video_codec", "video_fps", "video_quality",
                    "video_bitrate_mbps", "video_audio", "video_audio_device",
                    "video_max_duration_s", "video_save_path"):
            assert key in defaults, key

    def test_default_quality_follows_the_selection(self):
        from settings.tool_settings import ToolSettingsManager

        assert ToolSettingsManager.APP_DEFAULT_SETTINGS["video_quality"] == "source"

    def test_quality_limits_cover_every_choice_but_source(self):
        from settings.tool_settings import ToolSettingsManager

        qualities = set(ToolSettingsManager.VIDEO_QUALITIES)
        assert set(QUALITY_LIMITS) == qualities - {"source"}


class TestRoundTrip:
    def test_values_survive_a_write_and_read(self, config):
        config.set_video_container("mkv")
        config.set_video_codec("h265")
        config.set_video_fps(60)
        config.set_video_quality("720p")
        config.set_video_bitrate_mbps(12)
        config.set_video_audio("microphone")
        config.set_video_audio_device("外接麦克风")
        config.set_video_max_duration_s(120)
        config.set_video_save_path("/tmp/videos")

        assert config.get_video_container() == "mkv"
        assert config.get_video_codec() == "h265"
        assert config.get_video_fps() == 60
        assert config.get_video_quality() == "720p"
        assert config.get_video_bitrate_mbps() == 12
        assert config.get_video_audio() == "microphone"
        assert config.get_video_audio_device() == "外接麦克风"
        assert config.get_video_max_duration_s() == 120
        assert config.get_video_save_path() == "/tmp/videos"

    def test_unknown_choices_fall_back_to_defaults(self, config):
        config.set_video_container("webm")
        config.set_video_codec("av1")
        config.set_video_quality("4k")
        config.set_video_audio("系統音")

        assert config.get_video_container() == "mp4"
        assert config.get_video_codec() == "h264"
        assert config.get_video_quality() == "source"
        assert config.get_video_audio() == "none"

    def test_unknown_frame_rate_falls_back_to_the_default(self, config):
        config.set_video_fps(27)
        assert config.get_video_fps() == 30
        config.set_video_fps("每秒三十帧")
        assert config.get_video_fps() == 30

    def test_bitrate_and_duration_are_clamped(self, config):
        low, high = config.VIDEO_BITRATE_RANGE
        config.set_video_bitrate_mbps(high + 100)
        assert config.get_video_bitrate_mbps() == high
        config.set_video_bitrate_mbps(-5)
        assert config.get_video_bitrate_mbps() == low

        low, high = config.VIDEO_MAX_DURATION_RANGE
        config.set_video_max_duration_s(high + 100)
        assert config.get_video_max_duration_s() == high

    def test_text_fields_are_stripped(self, config):
        config.set_video_save_path("  /tmp/videos  ")
        assert config.get_video_save_path() == "/tmp/videos"
        config.set_video_audio_device("   ")
        assert config.get_video_audio_device() == ""


class TestOptionsBridging:
    def test_with_output_keeps_everything_else(self):
        options = VideoOptions(container="mkv", fps=15)
        moved = options.with_output("/tmp/x.mkv")
        assert moved.output_path == "/tmp/x.mkv"
        assert moved.container == "mkv" and moved.fps == 15
