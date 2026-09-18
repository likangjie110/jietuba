# -*- coding: utf-8 -*-
"""视频录制模块入口。

对外暴露的主要接口：
    VideoRecordWindow      — 录制窗口（选区边框 + 工具栏 + 录制器）
    start_video_record_window — 创建窗口并挂到 QApplication 上

典型调用方：
    from video import VideoRecordWindow
    win = VideoRecordWindow(capture_rect)
"""

from .record_window import VideoRecordWindow, start_video_record_window
from .video_recorder import VideoOptions, VideoRecorder, VideoState

__all__ = [
    "VideoOptions",
    "VideoRecorder",
    "VideoRecordWindow",
    "VideoState",
    "start_video_record_window",
]
