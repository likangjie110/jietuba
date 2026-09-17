# -*- coding: utf-8 -*-
"""用 mss 抓帧的后端（非 Windows）。

Windows 上抓帧在 Rust 里（GDI BitBlt，零 GIL 争用）；其它平台没有对应实现，这里用
mss 抓帧后把 BGRA 原样交给同一个 Rust FrameStore——压缩、存储、导出仍然全在 Rust。
方法名刻意与 ``gifrecorder.RecordSession`` 一致（stop/pause/resume），这样上层的
「暂停 / 恢复 / 停止」三处调用不用分平台写两遍。
"""

import threading
import time

from PySide6.QtCore import QThread


class MssCaptureThread(QThread):
    """非 Windows 的抓帧线程，接口对齐 gifrecorder.RecordSession。"""

    def __init__(self, store, left: int, top: int, width: int, height: int,
                 fps: int, elapsed_ms, parent=None):
        super().__init__(parent)
        self._store = store
        self._region = {"left": left, "top": top, "width": width, "height": height}
        self._fps = max(1, fps)
        self._elapsed_ms = elapsed_ms       # 可调用对象，返回距开始录制的毫秒数
        self._stopped = threading.Event()
        self._paused = threading.Event()

    def stop(self):
        self._stopped.set()
        self.wait(2000)

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def run(self):
        # 平台层一律在函数内导入 core.logger：模块级导入会与 core.constants 成环
        from core.logger import log_error, log_exception, T

        try:
            import mss
        except ImportError as e:
            log_error(T("缺少 mss，非 Windows 平台无法抓帧: {e}", e=e), "GIF")
            return

        interval = 1.0 / self._fps
        with mss.mss() as sct:
            while not self._stopped.is_set():
                if self._paused.is_set() or self._store.is_paused:   # is_paused 是属性
                    time.sleep(0.01)
                    continue
                started = time.perf_counter()
                try:
                    shot = sct.grab(self._region)
                    # mss 给的 raw 就是 BGRA，直接喂给 Rust，不做像素转换
                    self._store.push_bgra(memoryview(shot.raw), int(self._elapsed_ms()))
                except Exception as e:
                    log_exception(e, T("mss 抓帧失败"))
                spent = time.perf_counter() - started
                if spent < interval:
                    time.sleep(interval - spent)
