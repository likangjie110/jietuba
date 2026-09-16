# -*- coding: utf-8 -*-
"""窗口级原生操作：置顶、鼠标穿透、从截图中排除、任务栏/标题栏图标。

这些是「拿到窗口句柄之后对窗口本身做的事」，与 ``window.py``（枚举窗口、判断鼠标
下面是哪个窗口）区分开。原先它们散在 6 个文件里，同名 Win32 常量抄了 4 份，平台
分派方式也各不相同（``hasattr(ctypes, "windll")``、``sys.platform``、直接调 ctypes
再用 except 兜住）。

多数操作只有 Windows 有等价 API；能力矩阵里各自登记，调用方按 ``available()`` 决定
是否展示对应功能，而不是靠试一次看有没有异常。
"""

import ctypes
import os
import tempfile

from core.platform.detection import IS_MACOS, IS_WINDOWS

# ── Win32 常量（原先在四个文件里各抄一份）────────────────
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_TOPLEVEL_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+

WM_SETICON = 0x0080
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10


def _user32():
    """取 user32（仅 Windows 可用，调用方需自行确保平台）。"""
    return ctypes.windll.user32


def _native_handle(window) -> int | None:
    """取窗口的原生句柄；取不到返回 None。

    失败是常见情形而不是异常：窗口可能已经销毁、控件还没创建，
    也可能调用方传进来的对象根本没有 winId（例如长截图窗口的透明区尚未建好）。
    平台层的契约是「失败返回 False / None，绝不抛异常」——调用方不该再各自兜一遍。
    """
    try:
        return int(window.winId())
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("获取窗口原生句柄"))
        return None


# ──────────────────────────────────────────────
# 置顶
# ──────────────────────────────────────────────

def set_topmost(window, enabled: bool) -> bool:
    """切换窗口置顶，返回是否走了原生实现。

    Windows 用 ``SetWindowPos(HWND_TOPMOST)``：不激活窗口、不重绘，所以钉图切置顶
    时不会闪。其它平台退回 Qt 的 ``WindowStaysOnTopHint``，代价是窗口会重新显示
    一次（能力矩阵里 WINDOW_TOPMOST 对这两个平台是 DEGRADED）。
    """
    if IS_WINDOWS:
        hwnd = _native_handle(window)
        if hwnd is not None:
            _user32().SetWindowPos(
                hwnd,
                HWND_TOPMOST if enabled else HWND_NOTOPMOST,
                0, 0, 0, 0, SWP_TOPLEVEL_FLAGS,
            )
            return True
        # 拿不到句柄就退回 Qt 标志那条路（它不需要句柄）

    from PySide6.QtCore import Qt

    was_visible = window.isVisible()
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
    if was_visible:
        window.show()  # 改窗口标志会让窗口隐藏，这里补一次显示
    return False


# ──────────────────────────────────────────────
# 鼠标穿透
# ──────────────────────────────────────────────

def set_click_through(
    window,
    enabled: bool,
    *,
    layered: bool = False,
    force_frame_change: bool = False,
) -> bool:
    """让窗口不接收鼠标事件（点击落到下面的窗口上）。返回是否设置成功。

    两个关键字参数对应各调用点历史上各自的做法，不是为灵活而设的开关：

    - ``layered``：是否连同 ``WS_EX_LAYERED`` 一起置上。选区覆盖层与长截图窗口需要
      （它们是分层窗口，靠逐像素 alpha 显示），绘制层与光标层原先没有置。
    - ``force_frame_change``：改完样式后是否用 ``SetWindowPos(SWP_FRAMECHANGED)``
      强制重算非客户区。只有绘制层原先这么做。

    在把这些差异统一之前需要 Windows 上的实机验证（改变窗口样式可能影响分层窗口的
    渲染），本仓库的发行版只有 Windows 而开发机是 macOS，所以先如实保留。

    macOS 走 NSWindow 的 ``setIgnoresMouseEvents_``；Linux 没有实现，返回 False
    （能力矩阵里 WINDOW_CLICK_THROUGH 为 NONE）——调用方需要知道自己没穿透成功。
    """
    hwnd = _native_handle(window)
    if hwnd is None:
        return False

    if IS_WINDOWS:
        try:
            user32 = _user32()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                style |= WS_EX_TRANSPARENT
                if layered:
                    style |= WS_EX_LAYERED
            else:
                style &= ~WS_EX_TRANSPARENT
                if layered:
                    style |= WS_EX_LAYERED
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            if force_frame_change:
                user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
                )
            return True
        except Exception as e:
            from core.logger import log_exception, T

            log_exception(e, T("设置窗口鼠标穿透"))
            return False

    if IS_MACOS:
        return _set_click_through_macos(hwnd, enabled)

    return False


def _set_click_through_macos(hwnd: int, enable: bool) -> bool:
    """macOS：让窗口忽略鼠标事件（等价于 WS_EX_TRANSPARENT）。

    刻意不用 Qt 的 ``WindowTransparentForInput`` 标志：改窗口标志会让窗口重新显示
    一次，全屏覆盖层会闪一下，而这个属性可以就地改。
    """
    from core.logger import log_exception, T

    # 必须先确认跑在 Cocoa 平台上：offscreen 之类的平台插件没有真实 NSWindow，
    # 拿 winId 去构造 objc 对象会直接段错误——那是信号，try/except 拦不住。
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != "cocoa":
            return False
    except Exception:
        return False

    try:
        import objc

        ns_window = objc.objc_object(c_void_p=int(hwnd)).window()
        if ns_window is None:
            return False
        ns_window.setIgnoresMouseEvents_(bool(enable))
        return True
    except Exception as e:
        log_exception(e, T("设置鼠标穿透"))
        return False


# ──────────────────────────────────────────────
# 从截图中排除
# ──────────────────────────────────────────────

def set_exclude_from_capture(window, exclude: bool) -> bool:
    """让窗口不出现在屏幕截图里（屏幕上仍正常显示）。

    只有 Windows 有等价 API（``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)``，
    Win10 2004+）。其它平台返回 False，调用方需要自己保证自绘的工具栏不被拍进截图
    ——长截图就是这么用的。失败原因可以用 ``last_error()`` 取。
    """
    if not IS_WINDOWS:
        return False

    from core.logger import log_exception

    hwnd = _native_handle(window)
    if hwnd is None:
        return False

    try:
        affinity = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
        result = _user32().SetWindowDisplayAffinity(hwnd, affinity)
        return bool(result)
    except Exception as e:
        log_exception(e, "SetWindowDisplayAffinity")
        return False


def last_error() -> int:
    """取当前线程的 Win32 LastError，用于诊断上面这类调用为什么失败。"""
    if not IS_WINDOWS:
        return -1

    from core.logger import log_exception

    try:
        return ctypes.windll.kernel32.GetLastError()
    except Exception as e:
        log_exception(e, "GetLastError")
        return -1


# ──────────────────────────────────────────────
# 窗口图标
# ──────────────────────────────────────────────

def set_taskbar_icon(window, icon_path: str, size: int = 32) -> bool:
    """把图标设成该窗口在任务栏/标题栏上显示的那个（仅 Windows）。

    Qt 的 ``setWindowIcon`` 在 Windows 上只影响窗口图标，任务栏用的是另一套
    （``WM_SETICON``）——需要先把图片落成 .ico 再 LoadImageW。非 Windows 返回
    False：那边 Qt 的窗口图标就是任务栏图标，不需要额外处理。
    """
    if not IS_WINDOWS:
        return False

    from core.logger import log_exception, log_warning, T

    if not icon_path or not os.path.exists(icon_path):
        return False

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon, QPainter, QPixmap

        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        QIcon(icon_path).paint(painter, 0, 0, size, size)
        painter.end()

        tmp_ico = os.path.join(tempfile.gettempdir(), "jietuba_win_icon.ico")
        if not pix.save(tmp_ico, "ICO"):
            log_warning(T("任务栏图标写入 .ico 失败: {path}", path=tmp_ico), "Window")
            return False

        hwnd = _native_handle(window)
        if hwnd is None:
            return False
        user32 = _user32()
        hicon = user32.LoadImageW(None, tmp_ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if not hicon:
            return False
        user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)  # ICON_BIG
        user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)  # ICON_SMALL
        return True
    except Exception as e:
        log_exception(e, T("设置任务栏图标"))
        return False
