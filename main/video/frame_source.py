# -*- coding: utf-8 -*-
"""录制抓帧：后台线程用 mss 按帧率抓录制区域，抓到的一帧交给主线程的编码器。

为什么不让 Qt 抓：``QScreen.grabWindow`` 要在 GUI 线程调，1280×720 的区域在 Retina 上
实测约 17ms（抓的是 2560×1440 物理像素）——30fps 就会把主线程占掉一半。mss 可以在普通
线程里抓，主线程只剩「缩放 + 推给编码器」。

区域坐标用**窗口坐标**（与 GIF 录制那条链路一致）：macOS 上 mss 按「点」取坐标、返回
同样大小的像素，Windows 上按进程的 DPI 感知空间取——两头都是「坐标进、同尺寸帧出」。
Retina 屏上这等于逻辑分辨率（mss 拿不到 2 倍的物理像素），缩放交给 ``VideoRecorder``
按清晰度档位决定。帧是值类型（``QImage``），跨线程走信号是安全的。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class FrameSourceThread(QThread):
    """按帧率抓录制区域的线程；区域坐标用窗口坐标（见模块说明）。"""

    #: 抓到一帧（QImage 是值类型，队列连接会跨线程传一份）
    frame_ready = Signal(QImage)
    #: 抓帧失败（文案已经翻译好）；发一次就退出循环
    failed = Signal(str)

    def __init__(self, region: dict, fps: int, parent=None):
        super().__init__(parent)
        self._region = dict(region)
        self._fps = max(1, int(fps))
        self._stopped = threading.Event()
        self._paused = threading.Event()

    def stop(self):
        self._stopped.set()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def run(self):
        # 平台层与业务层一律在函数内导入 core.logger：模块级导入会与 core.constants 成环
        from core.logger import log_error, log_exception, T

        try:
            import mss
        except ImportError as e:
            # failed 是给界面看的信号：必须是渲染好的字符串（T() 返回的是待翻译对象，
            # Qt 信号跨不过去）
            message = T("缺少 mss，无法录制视频: {e}", e=e).render()
            log_error(message, "Video")
            self.failed.emit(message)
            return

        interval = 1.0 / self._fps
        with mss.mss() as sct:
            while not self._stopped.is_set():
                if self._paused.is_set():
                    time.sleep(0.01)
                    continue
                started = time.perf_counter()
                try:
                    shot = sct.grab(self._region)
                    # mss 的 bgra 生命周期跟着它自己，构造出来的 QImage 必须拷一份
                    frame = QImage(
                        shot.bgra, shot.width, shot.height, shot.width * 4,
                        QImage.Format.Format_RGB32,
                    ).copy()
                    self.frame_ready.emit(frame)
                except Exception as e:
                    log_exception(e, T("录制抓帧失败"))
                    self.failed.emit(T("录制抓帧失败: {e}", e=e).render())
                    return
                spent = time.perf_counter() - started
                if spent < interval:
                    time.sleep(interval - spent)
