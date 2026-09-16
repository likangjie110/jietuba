# -*- coding: utf-8 -*-
"""窗口级平台操作。

这个文件正在被拆分：进程级的部分（工作集、进程身份/终止、DPI、AppUserModelID）
已经搬到 ``core/platform/process.py``；剩下的「从截图中排除某个窗口」连同
``get_last_error`` 属于窗口级操作，会并入 ``core/platform/window_ops.py``，
之后本文件删除。暂时保留在这里，是为了让调用点与迁移分两步走、每步都能单独验证。
"""

import ctypes

from core.platform.detection import IS_WINDOWS


WDA_NONE             = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+


def set_window_exclude_from_capture(hwnd: int, exclude: bool) -> bool:
    """设置窗口是否从屏幕截图中排除（mss/BitBlt/DXGI 均生效）。

    窗口在屏幕上仍正常显示，仅对截图不可见。返回 Win32 调用是否成功。
    只有 Windows 有等价 API，其它平台返回 False——调用方需要自己确保自绘的
    工具栏不会出现在截图里（``stitch/scroll_window.py`` 就是这么用的）。
    """
    if not IS_WINDOWS:
        return False

    from core.logger import log_exception

    try:
        affinity = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
        result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, affinity)
        return bool(result)
    except Exception as e:
        log_exception(e, "SetWindowDisplayAffinity")
        return False


def get_last_error() -> int:
    """返回当前线程的 Win32 LastError 值（用于诊断 Win32 API 失败原因）。"""
    if not IS_WINDOWS:
        return -1

    from core.logger import log_exception

    try:
        return ctypes.windll.kernel32.GetLastError()
    except Exception as e:
        log_exception(e, "GetLastError")
        return -1
