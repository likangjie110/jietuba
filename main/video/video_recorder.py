# -*- coding: utf-8 -*-
"""视频录制：把一块屏幕区域录成 MP4/MKV 等容器里的视频文件。

链路：``FrameSourceThread``（mss，后台线程按帧率抓帧）→ 本类按清晰度档位缩放 →
``core/platform/video.py`` 的 ``RecordingSession``（Qt 多媒体的 FFmpeg 后端编码）。

录制区域用的是**应用自己的窗口坐标**（与 GIF 录制一模一样，不做设备像素比换算）：
mss 在 macOS 上按「点」取坐标、返回同样大小的像素，在 Windows 上按进程的 DPI 感知
空间取坐标——两头都是「窗口坐标进、同尺寸帧出」。所以输出分辨率 = 录制区域的大小，
「跟随区域」档就是帧的原样；Retina 屏上这等于逻辑分辨率（mss 拿不到 2x 的物理像素）。

为什么不做「暂停时也把编码器停住」：推帧是编码器唯一的数据来源，不推帧就等于暂停，
落盘的时长里也就没有暂停那段——而 Qt 的 ``QMediaRecorder.pause()`` 在各后端上的行为
不一致（有的后端会让时间轴继续走）。所以暂停只停抓帧线程。

编码收尾是异步的：``stop()`` 之后要等编码器报 ``stopped`` 才算文件写完，
``finished`` 信号因此比 ``stop()`` 晚。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from core.logger import T, log_debug, log_info, log_warning
from core.platform import video as platform_video

#: 清晰度档位 → 输出尺寸上限（``source`` 不在表里：跟随所选区域的物理像素）
QUALITY_LIMITS = {
    "1080p": QSize(1920, 1080),
    "720p": QSize(1280, 720),
    "480p": QSize(854, 480),
}

#: 选区边缘留 1px，免得把录制框自己的边线录进去（与 GIF 录制一致）
_RECT_INSET = 1

#: 编码器迟迟不报「已停止」时的兜底时长：宁可给一个可能没写完的文件路径，
#: 也不要让界面永远停在「正在保存」
_FINALIZE_TIMEOUT_MS = 5000


class VideoState(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class VideoOptions:
    """一次录制的参数（由调用方从设置里读好传进来）。"""

    container: str = "mp4"
    codec: str = "h264"
    fps: int = 30
    quality: str = "source"
    bitrate_mbps: int = 0
    audio: bool = False
    audio_device: str = ""
    max_duration_s: int = 0
    output_path: str = ""

    def with_output(self, path: str) -> "VideoOptions":
        return replace(self, output_path=str(path))


def even(value: int) -> int:
    """取偶数（不小于 2）：H.264/H.265 的编码器要求宽高对齐到偶数。"""
    value = int(value)
    return max(2, value - value % 2)


def target_size(source: QSize, quality: str) -> QSize:
    """按清晰度档位算输出尺寸：只缩不放，宽高取偶数。"""
    if source.width() <= 0 or source.height() <= 0:
        return QSize(0, 0)
    limit = QUALITY_LIMITS.get(quality)
    size = QSize(source)
    if limit is not None and (source.width() > limit.width() or source.height() > limit.height()):
        size = source.scaled(limit, Qt.AspectRatioMode.KeepAspectRatio)
    return QSize(even(size.width()), even(size.height()))


class VideoRecorder(QObject):
    """录制一块屏幕区域 → 视频文件。抓帧在后台线程，编码在主线程。"""

    frame_count_changed = Signal(int)
    duration_changed = Signal(int)      # 毫秒（不含暂停时间）
    state_changed = Signal(str)         # VideoState 的 name
    failed = Signal(str)                # 已翻译的失败文案
    finished = Signal(str)              # 输出文件路径（编码器收尾之后）
    limit_reached = Signal()            # 到达最长录制时长，已自动停止

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rect = QRect()
        self._options = VideoOptions()
        self._state = VideoState.IDLE
        self._session = None
        self._source = None
        self._target = QSize(0, 0)

        self._frames = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(200)
        self._elapsed_timer.timeout.connect(self._on_tick)

        self._started_at = 0.0
        self._paused_total = 0.0
        self._pause_started = 0.0
        self._finalized = False
        self._finalize_timer = QTimer(self)
        self._finalize_timer.setSingleShot(True)
        self._finalize_timer.timeout.connect(self._finalize)

    # ── 属性 ──

    @property
    def state(self) -> VideoState:
        return self._state

    @property
    def options(self) -> VideoOptions:
        return self._options

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def target_resolution(self) -> QSize:
        """本次录制的输出分辨率（``start`` 之后才有意义）。"""
        return QSize(self._target)

    @property
    def output_path(self) -> str:
        return self._options.output_path

    def set_rect(self, rect: QRect) -> None:
        self._rect = QRect(rect)

    def duration_ms(self) -> int:
        """已录时长（毫秒），不含暂停那段。"""
        import time

        if self._state not in (VideoState.RECORDING, VideoState.PAUSED):
            return 0
        now = time.perf_counter()
        paused = self._paused_total
        if self._state == VideoState.PAUSED and self._pause_started:
            paused += now - self._pause_started
        return int(max(0.0, now - self._started_at - paused) * 1000)

    # ── 生命周期 ──

    def start(self, options: VideoOptions) -> bool:
        """开始录制；返回是否真的开起来了。"""
        import time

        if self._state not in (VideoState.IDLE, VideoState.STOPPED):
            return False
        if not platform_video.is_available():
            self._fail("视频录制不可用：这个构建里没有可用的编码后端")
            return False
        if not options.output_path:
            self._fail("没有指定视频保存路径")
            return False

        area = self._recording_rect()
        if area.width() < 2 or area.height() < 2:
            self._fail("录制区域太小，无法录制视频")
            return False

        self._options = options
        self._frames = 0
        self._finalized = False
        self._paused_total = 0.0
        self._pause_started = 0.0
        # 输出分辨率要等第一帧才知道（抓帧后端决定帧的实际像素），第一帧到达前是空的
        self._target = QSize(0, 0)
        self._target_resolved = False

        source_region = area
        audio_device = options.audio_device if options.audio else None
        self._session = platform_video.create_session(
            options.output_path,
            container=options.container,
            video_codec=options.codec,
            fps=options.fps,
            size=None,
            bitrate_bps=int(options.bitrate_mbps) * 1_000_000,
            audio_device=audio_device,
            parent=self,
        )
        if self._session is None:
            self._fail("录制会话创建失败：编码后端不支持这组参数")
            return False

        self._session.error_changed.connect(self._on_session_error)
        self._session.state_changed.connect(self._on_session_state)

        from .frame_source import FrameSourceThread

        self._source = FrameSourceThread(
            {"left": source_region.x(), "top": source_region.y(),
             "width": source_region.width(), "height": source_region.height()},
            options.fps,
            parent=self,
        )
        self._source.frame_ready.connect(self._on_frame)
        self._source.failed.connect(self._on_source_failed)
        self._source.start()

        self._session.record()
        self._started_at = time.perf_counter()
        self._elapsed_timer.start()
        self._set_state(VideoState.RECORDING)
        log_info(T("视频录制开始: {w}x{h} @ {fps}fps ({container}/{codec}, {quality}, {backend})",
                   w=source_region.width(), h=source_region.height(),
                   fps=options.fps, container=options.container, codec=options.codec,
                   quality=options.quality, backend=platform_video.backend_name()), "Video")
        return True

    def pause(self) -> None:
        if self._state != VideoState.RECORDING:
            return
        import time

        self._pause_started = time.perf_counter()
        if self._source is not None:
            self._source.pause()
        self._set_state(VideoState.PAUSED)

    def resume(self) -> None:
        if self._state != VideoState.PAUSED:
            return
        import time

        self._paused_total += time.perf_counter() - self._pause_started
        self._pause_started = 0.0
        if self._source is not None:
            self._source.resume()
        self._set_state(VideoState.RECORDING)

    def stop(self) -> None:
        """停止录制；文件真正写完时发 ``finished``。"""
        if self._state not in (VideoState.RECORDING, VideoState.PAUSED):
            return
        self._elapsed_timer.stop()
        self._stop_source()
        session = self._session
        if session is None:
            self._finalize()
            return
        log_debug(T("停止视频录制，共 {frames} 帧", frames=self._frames), "Video")
        session.stop()
        # 正常路径由编码器的 stopped 状态触发收尾，这里只是兜底
        self._finalize_timer.start(_FINALIZE_TIMEOUT_MS)
        self._set_state(VideoState.STOPPED)

    # ── 内部 ──

    def _recording_rect(self) -> QRect:
        """实际录制范围：选区四边各内缩 1px（避开录制框自己的边线）。"""
        rect = self._rect
        if rect.width() - _RECT_INSET * 2 < 2 or rect.height() - _RECT_INSET * 2 < 2:
            return QRect(rect)
        return QRect(rect.x() + _RECT_INSET, rect.y() + _RECT_INSET,
                     rect.width() - _RECT_INSET * 2, rect.height() - _RECT_INSET * 2)

    def _on_frame(self, image: QImage) -> None:
        """抓到一帧：按目标分辨率缩放后推给编码器。

        目标分辨率从**第一帧**算出来（抓帧后端决定帧的实际像素），之后的帧沿用；
        暂停期间到达的帧直接丢掉——抓帧线程的暂停有几十毫秒延迟，这几帧不该进文件。
        """
        if self._state != VideoState.RECORDING or self._session is None:
            return
        if not self._target_resolved:
            self._target = target_size(image.size(), self._options.quality)
            self._target_resolved = True
            log_debug(T("录制输出分辨率: {w}x{h}（区域 {region_w}x{region_h}）",
                        w=self._target.width(), h=self._target.height(),
                        region_w=image.width(), region_h=image.height()), "Video")
        if self._target.isValid() and self._target != image.size():
            scaled = image.scaled(self._target, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            if not scaled.isNull():
                image = scaled
        if not self._session.push_image(image):
            return
        self._frames += 1
        self.frame_count_changed.emit(self._frames)

    def _on_source_failed(self, message: str) -> None:
        """抓帧挂了：停掉这次录制并把原因说清楚（不静默留一个空文件）。"""
        log_warning(T("录制抓帧线程失败，停止录制: {message}", message=message), "Video")
        self.stop()
        self.failed.emit(message)

    def _on_session_error(self, message: str) -> None:
        """编码器报错：这次录制已经不可信，停掉并告诉用户（收尾仍等编码器或兜底超时）。"""
        log_warning(T("录制编码器报错: {message}", message=message), "Video")
        self.failed.emit(message)
        self.stop()

    def _on_session_state(self, state: str) -> None:
        if state == platform_video.STATE_STOPPED and not self._finalized:
            self._finalize()

    def _on_tick(self) -> None:
        self.duration_changed.emit(self.duration_ms())
        limit = int(self._options.max_duration_s or 0)
        if limit > 0 and self.duration_ms() >= limit * 1000:
            log_info(T("达到最长录制时长 {limit}s，自动停止", limit=limit), "Video")
            self.limit_reached.emit()
            self.stop()

    def _stop_source(self) -> None:
        source = self._source
        self._source = None
        if source is None:
            return
        try:
            source.stop()
            source.wait(2000)
        except Exception as e:
            log_warning(T("停止抓帧线程失败: {e}", e=e), "Video")

    def _finalize(self) -> None:
        """编码收尾：发一次 ``finished``（编码器报停止、报错、兜底超时都会走到这）。"""
        if self._finalized:
            return
        self._finalized = True
        self._finalize_timer.stop()
        self._elapsed_timer.stop()
        self._stop_source()
        session = self._session
        self._session = None
        dropped = session.dropped_frames() if session is not None else 0
        path = self._options.output_path
        if dropped:
            log_debug(T("录制期间丢弃了 {dropped} 帧（编码器来不及消费）", dropped=dropped), "Video")
        log_info(T("视频录制完成: {path}", path=path), "Video")
        self.finished.emit(path)

    def _set_state(self, state: VideoState) -> None:
        self._state = state
        self.state_changed.emit(state.name)

    def _fail(self, template: str, **kwargs) -> None:
        """失败：记一条日志并通知界面。

        信号里必须是**渲染好的字符串**——``T()`` 返回的是待翻译的 ``LogMsg``，Qt 信号
        跨不过去（会报 "Cannot copy-convert LogMsg"）。所以日志与界面共用一份中文模板，
        界面拿到的是渲染结果。
        """
        message = T(template, **kwargs).render()
        log_warning(message, "Video")
        self.failed.emit(message)
