# -*- coding: utf-8 -*-
"""录制抓帧后端：Windows 走 Rust 的 GDI BitBlt，其它平台由 Python 用 mss 抓帧。

两条路径的差别只在「谁在抓帧」：Windows 是 Rust 线程（零 GIL 争用），其它平台是
Python 线程用 mss 抓，抓到之后都交给同一个 Rust FrameStore 压缩与存储。门面把
「用哪条」收在这里，业务模块（``gif/frame_recorder.py``）只问后端名、拿会话，
不判断平台。
"""

from core.platform.capabilities import Capability, backend_name as _backend_name
from core.platform.detection import IS_WINDOWS

try:
    import gifrecorder
    _gifrecorder_available = True
except ImportError:
    gifrecorder = None
    _gifrecorder_available = False


def backend_name() -> str:
    """当前平台的抓帧后端名（``gdi`` / ``mss``），用于日志与诊断。"""
    return _backend_name(Capability.FRAME_CAPTURE)


def create_session(store, left: int, top: int, width: int, height: int, fps: int,
                   *, elapsed_ms=None, parent=None):
    """创建并启动抓帧会话；没有可用后端时返回 None。

    返回对象提供 ``stop()`` / ``pause()`` / ``resume()``，与 ``gifrecorder.RecordSession``
    同名，因此上层的暂停/恢复/停止不用分平台写两遍。``elapsed_ms`` 只有 mss 后端用得到
    （它得自己给每帧打时间戳），``parent`` 用来把抓帧线程挂到调用方的 QObject 上。
    """
    if IS_WINDOWS:
        if not _gifrecorder_available:
            return None
        return gifrecorder.RecordSession(store, left, top, width, height, fps)

    from core.platform.capture_mss import MssCaptureThread

    session = MssCaptureThread(store, left, top, width, height, fps,
                               elapsed_ms=elapsed_ms, parent=parent)
    session.start()
    return session
