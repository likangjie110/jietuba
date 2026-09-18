# -*- coding: utf-8 -*-
"""录制抓帧后端：Windows 走 Rust 的 GDI BitBlt，其它平台由 Python 用 mss 抓帧。

两条路径的差别只在「谁在抓帧」：Windows 是 Rust 线程（零 GIL 争用），其它平台是
Python 线程用 mss 抓，抓到之后都交给同一个 Rust FrameStore 压缩与存储。门面把
「用哪条」收在这里，业务模块（``gif/frame_recorder.py``）只问后端名、拿会话，
不判断平台。

另外这里也管「当前进程能不能抓屏」：macOS 的「屏幕录制」权限缺失时，抓屏调用照样
返回成功，但画面里只有桌面壁纸、所有窗口都消失——看起来像截图坏了，其实是权限。
"""

from core.platform.capabilities import Capability, backend_name as _backend_name
from core.platform.detection import IS_MACOS, IS_WINDOWS

try:
    import gifrecorder
    _gifrecorder_available = True
except ImportError:
    gifrecorder = None
    _gifrecorder_available = False


def backend_name() -> str:
    """当前平台的抓帧后端名（``gdi`` / ``mss``），用于日志与诊断。"""
    return _backend_name(Capability.FRAME_CAPTURE)


def screen_capture_trusted() -> bool:
    """当前进程能不能抓到窗口内容（而不是只有桌面壁纸）。

    macOS 上是「屏幕录制」权限：没授权时 CoreGraphics 的抓屏接口仍然成功返回，
    只是画面里没有任何窗口，用户看到的是"截图只截到桌面"。其它平台没有这层门槛。
    """
    if not IS_MACOS:
        return True
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        return bool(CGPreflightScreenCaptureAccess())
    except Exception as e:
        # 查不了（pyobjc 缺失）时报「有权限」：抓屏本身会用不了，别在这里再叠一层误导
        from core.logger import log_debug

        log_debug(f"检查屏幕录制权限失败: {e}", "Capture")
        return True


def request_screen_capture_permission() -> bool:
    """请求屏幕录制权限（macOS 会弹系统对话框），返回请求之后的状态。

    必须在主线程调用——它会弹窗。注意与「辅助功能」不同：屏幕录制权限是**进程启动时**
    读一次的，用户勾上之后必须重启本程序才生效。
    """
    if not IS_MACOS:
        return True
    try:
        from Quartz import CGRequestScreenCaptureAccess

        return bool(CGRequestScreenCaptureAccess())
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("请求屏幕录制权限"))
        return screen_capture_trusted()


_warned_missing_screen_capture = False


def warn_missing_screen_capture_permission() -> None:
    """把「截图里只有桌面」这件事说清楚：每进程只留一条，抓屏是高频动作。

    启动时（请求过权限之后）与每次抓屏前都会走这里，所以去重放在这儿而不是调用方。
    """
    global _warned_missing_screen_capture

    if _warned_missing_screen_capture:
        return
    _warned_missing_screen_capture = True

    from core.logger import log_warning, T

    log_warning(
        T(
            "截图里只有桌面、窗口都不见了：macOS 需要「屏幕录制」权限（系统设置 → 隐私与安全性 → "
            "录屏与系统录音）。授权后要重启本程序；重新打包过的应用请先用「−」移除旧条目再重新添加"
        ),
        "Capture",
    )


def reset_permission_warning() -> None:
    """清掉「已经提醒过缺权限」的标记（测试用）。"""
    global _warned_missing_screen_capture
    _warned_missing_screen_capture = False


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
