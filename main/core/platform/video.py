# -*- coding: utf-8 -*-
"""视频录制的编码后端：容器/编解码器协商，以及「一次录制会话」的 Qt 侧对象。

分工：录制本身的业务逻辑（选区、计时、帧率、落盘位置、状态机）在 ``video/`` 里；
这个文件只回答两件事——「当前平台能不能录视频」，以及「怎么把一次录制会话装起来」。
QtMultimedia 的类型与枚举收在这里，业务模块只调方法、只连信号。

编码走 Qt 多媒体自带的 FFmpeg 后端（PySide6 的轮子里就有，不需要外部 ffmpeg 可执行
文件），抓帧在 ``video/frame_source.py`` 里用 mss——也就是 GIF 抓帧在非 Windows 上用的
同一个依赖。两条路径都不分平台，所以这里没有按平台分派，只有能力登记
（``Capability.VIDEO_RECORDING``，见 ``capabilities.py``）。
"""

from PySide6.QtCore import QObject, QSize, Signal

from core.platform.capabilities import Capability, backend_name as _backend_name

#: 界面用的容器短名 → Qt 的 QMediaFormat.FileFormat 枚举名。
#: 只列这四种：它们在 Qt 的 FFmpeg 后端上都能承载 H.264/H.265。
_CONTAINER_FORMATS = {
    "mp4": "MPEG4",
    "mkv": "Matroska",
    "mov": "QuickTime",
    "avi": "AVI",
}

#: 界面用的视频编码短名 → Qt 的 QMediaFormat.VideoCodec 枚举名。
_VIDEO_CODECS = {
    "h264": "H264",
    "h265": "H265",
}

#: 录制状态（``RecordingSession.state_changed`` 的取值）
STATE_RECORDING = "recording"
STATE_PAUSED = "paused"
STATE_STOPPED = "stopped"


def backend_name() -> str:
    """当前平台的录制后端名（用于日志与诊断）。"""
    return _backend_name(Capability.VIDEO_RECORDING)


def _qt_multimedia():
    """QtMultimedia 模块；这个构建里没有它时返回 None（调用方按「不可用」处理）。"""
    try:
        from PySide6 import QtMultimedia
    except ImportError:
        return None
    return QtMultimedia


def _enum(family, name: str):
    """从 Qt 的枚举族里取一个成员；名字在这个版本里不存在时返回 None。"""
    return getattr(family, name, None)


def _encode_file_formats() -> set:
    """后端能编码的容器（Qt 枚举名集合）；查不到时返回空集。"""
    module = _qt_multimedia()
    if module is None:
        return set()
    try:
        fmt = module.QMediaFormat()
        return {item.name for item in fmt.supportedFileFormats(module.QMediaFormat.ConversionMode.Encode)}
    except Exception:
        return set()


def _encode_video_codecs() -> set:
    """后端能编码的视频编码（Qt 枚举名集合）；查不到时返回空集。"""
    module = _qt_multimedia()
    if module is None:
        return set()
    try:
        fmt = module.QMediaFormat()
        return {item.name for item in fmt.supportedVideoCodecs(module.QMediaFormat.ConversionMode.Encode)}
    except Exception:
        return set()


def is_available() -> bool:
    """当前进程能不能录视频：QtMultimedia 在、且后端至少支持一种我们要用的容器。

    判据刻意只到「有没有可用的编码后端」这一层：能不能真的录到画面取决于屏幕录制
    权限（macOS）与抓屏后端，那是运行时的事，由 ``core/platform/capture.py`` 负责说清楚。
    """
    return bool(_encode_file_formats() & set(_CONTAINER_FORMATS.values()))


def container_formats() -> list:
    """当前后端支持的容器短名（``_CONTAINER_FORMATS`` 里的子集，顺序固定）。"""
    supported = _encode_file_formats()
    return [name for name, enum_name in _CONTAINER_FORMATS.items() if enum_name in supported]


def video_codecs() -> list:
    """当前后端支持的视频编码短名（``_VIDEO_CODECS`` 里的子集，顺序固定）。"""
    supported = _encode_video_codecs()
    return [name for name, enum_name in _VIDEO_CODECS.items() if enum_name in supported]


def audio_input_device_names() -> list:
    """麦克风设备名（描述文本）；没有设备或查不到时返回空表。"""
    module = _qt_multimedia()
    if module is None:
        return []
    try:
        return [device.description() for device in module.QMediaDevices.audioInputs()]
    except Exception:
        return []


def _resolve_audio_device(name: str):
    """按描述名找麦克风；名字为空取系统默认，找不到具体设备也退回默认。"""
    module = _qt_multimedia()
    if module is None:
        return None
    try:
        if name:
            for device in module.QMediaDevices.audioInputs():
                if device.description() == name:
                    return device
        return module.QMediaDevices.defaultAudioInput()
    except Exception:
        return None


class RecordingSession(QObject):
    """一次录制会话：业务层只调这几个方法与信号，不碰 QtMultimedia 的类型。

    帧由调用方推进来（``push_image``）：录制区域的抓帧与缩放都在 ``video/`` 里，
    这里只负责把 ``QImage`` 变成编码器认的帧。
    """

    #: 编码器报错（文案是 Qt 给的描述；没有错误时不发）
    error_changed = Signal(str)
    #: 录制状态变化：recording / paused / stopped
    state_changed = Signal(str)
    #: 已录时长（毫秒），由编码器按它收到的帧时间戳给出
    duration_changed = Signal(int)

    def __init__(self, *, session, recorder, frame_input, audio_input, output_path, parent=None):
        super().__init__(parent)
        self._session = session
        self._recorder = recorder
        self._frame_input = frame_input
        self._audio_input = audio_input
        self._output_path = output_path
        # 编码器忙时（sendVideoFrame 返回 False）不再继续推，等它发 readyToSendVideoFrame；
        # 一直推只会把帧堆在内存里
        self._waiting_for_ready = False
        self._dropped = 0

        recorder.errorChanged.connect(self._on_error)
        recorder.recorderStateChanged.connect(self._on_recorder_state)
        # 经槽转发而不是把两个信号直连：QMediaRecorder.durationChanged 是 qlonglong，
        # 与 Signal(int) 直连会被 Qt 拒掉（"Failed to connect signal durationChanged"）
        recorder.durationChanged.connect(self._on_duration)
        # QVideoFrameInput.readyToSendVideoFrame() 是**无参**信号，槽不能多收一个参数
        ready = getattr(frame_input, "readyToSendVideoFrame", None)
        if ready is not None:
            ready.connect(self._on_ready)

    # ── 状态 ──

    def output_path(self) -> str:
        return self._output_path

    def duration_ms(self) -> int:
        try:
            return int(self._recorder.duration())
        except Exception:
            return 0

    def error_string(self) -> str:
        try:
            return str(self._recorder.errorString() or "")
        except Exception:
            return ""

    def dropped_frames(self) -> int:
        """因编码器忙而丢掉的帧数（录制结束时记一条日志用）。"""
        return self._dropped

    # ── 生命周期 ──

    def record(self) -> None:
        self._recorder.record()

    def stop(self) -> None:
        """停止编码。注意 Qt 的 stop 是异步的：StoppedState 之后文件才写完。"""
        self._recorder.stop()

    def push_image(self, image) -> bool:
        """把一帧画面交给编码器；编码器还没消费完上一帧时丢帧并返回 False。"""
        if image is None or image.isNull():
            return False
        if self._waiting_for_ready:
            self._dropped += 1
            return False
        module = _qt_multimedia()
        if module is None:
            return False
        try:
            frame = module.QVideoFrame(image)
            if not self._frame_input.sendVideoFrame(frame):
                # 编码器还没消费完上一帧：这一帧丢掉，等 readyToSendVideoFrame 再继续
                self._waiting_for_ready = True
                self._dropped += 1
                return False
            return True
        except Exception as e:
            from core.logger import log_exception, T

            log_exception(e, T("推送录制帧"))
            return False

    # ── 信号槽 ──

    def _on_ready(self) -> None:
        self._waiting_for_ready = False

    def _on_duration(self, value) -> None:
        self.duration_changed.emit(int(value))

    def _on_error(self, _error) -> None:
        message = self.error_string()
        if message:
            self.error_changed.emit(message)

    def _on_recorder_state(self, state) -> None:
        name = str(getattr(state, "name", state) or "")
        mapping = {
            "RecordingState": STATE_RECORDING,
            "PausedState": STATE_PAUSED,
            "StoppedState": STATE_STOPPED,
        }
        self.state_changed.emit(mapping.get(name, name.lower()))


def create_session(output_path: str, *, container: str = "mp4", video_codec: str = "h264",
                   fps: int = 30, size: QSize | None = None, bitrate_bps: int = 0,
                   audio_device: str | None = None, parent=None) -> RecordingSession | None:
    """装好一次录制会话；后端不可用或参数不成立时返回 None 并记一条日志。

    ``audio_device``：None 不录声音；空串取系统默认麦克风；其它值按描述名找设备
    （找不到时退回默认设备）。
    """
    module = _qt_multimedia()
    if module is None:
        from core.logger import T

        _log_unavailable(T("QtMultimedia 不在，无法录制视频"))
        return None

    file_format = _enum(module.QMediaFormat.FileFormat, _CONTAINER_FORMATS.get(container, ""))
    codec = _enum(module.QMediaFormat.VideoCodec, _VIDEO_CODECS.get(video_codec, ""))
    if file_format is None or codec is None:
        from core.logger import T

        _log_unavailable(T("录制参数不被编码后端支持: container={container}, codec={codec}",
                           container=container, codec=video_codec))
        return None

    try:
        media_format = module.QMediaFormat()
        media_format.setFileFormat(file_format)
        media_format.setVideoCodec(codec)
        # 音频编码不显式指定：Qt 的枚举里没有「不编码音频」，不接音频输入源就没有音轨
        media_format.setAudioCodec(module.QMediaFormat.AudioCodec.Unspecified)

        recorder = module.QMediaRecorder()
        recorder.setMediaFormat(media_format)
        recorder.setEncodingMode(module.QMediaRecorder.EncodingMode.ConstantQualityEncoding)
        if fps:
            recorder.setVideoFrameRate(float(fps))
        if size is not None and size.isValid() and size.width() > 0 and size.height() > 0:
            recorder.setVideoResolution(size)
        if bitrate_bps:
            recorder.setVideoBitRate(int(bitrate_bps))

        frame_input = module.QVideoFrameInput()
        session = module.QMediaCaptureSession()
        session.setVideoFrameInput(frame_input)
        session.setRecorder(recorder)

        audio_input = None
        if audio_device is not None:
            device = _resolve_audio_device(audio_device)
            if device is None or device.isNull():
                from core.logger import T

                _log_unavailable(T("没有可用的麦克风，本次录制不录声音"))
            else:
                audio_input = module.QAudioInput(device)
                session.setAudioInput(audio_input)

        from PySide6.QtCore import QUrl

        recorder.setOutputLocation(QUrl.fromLocalFile(output_path))
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("装配录制会话"))
        return None

    return RecordingSession(
        session=session,
        recorder=recorder,
        frame_input=frame_input,
        audio_input=audio_input,
        output_path=output_path,
        parent=parent,
    )


def _log_unavailable(message) -> None:
    """能力不可用时的降级日志（绝不静默返回 None）；``message`` 是已经翻译好的文案。"""
    from core.logger import log_warning

    log_warning(message, "Video")
