# -*- coding: utf-8 -*-
"""Windows 的窗口枚举后端（智能选区用）。

从 ``capture/window_finder.py`` 搬过来时保持逻辑逐字不变，只换了两件事：
返回 ``WindowInfo`` 而不是 ``(hwnd, rect, title)`` 元组；坐标偏移交给门面做，
这里只负责枚举。

原来这个模块同时是门面（选择后端、命中测试、虚拟桌面）和 Windows 实现，macOS 实现
在另一个文件里、由它转发调用，两边的过滤阈值与粗筛条件各写一份。
"""

import ctypes

from core.platform.contracts import WindowInfo

try:
    import win32gui
    import win32con
    WINDOWS_API_AVAILABLE = True
except ImportError:
    WINDOWS_API_AVAILABLE = False


def get_window_rect_no_shadow(hwnd):
    """获取去除阴影的真实窗口矩形（物理像素）。

    Windows 10/11 的窗口自带大投影，直接 GetWindowRect 会让选区框比窗口大一圈，
    看起来像"选歪了"。DWMWA_EXTENDED_FRAME_BOUNDS 给的是不含投影的边界。
    """
    try:
        rect = ctypes.wintypes.RECT()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.c_int(9),  # DWMWA_EXTENDED_FRAME_BOUNDS
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        return [rect.left, rect.top, rect.right, rect.bottom]
    except Exception:
        # 降级回退：少数窗口（如 UWP 外壳）拿不到 DWM 边界
        return win32gui.GetWindowRect(hwnd)


def enumerate_windows(debug: bool = False) -> list[WindowInfo]:
    """枚举所有可见的应用窗口，顺序即 Z 序（前 → 后）。

    过滤条件（与 macOS 后端保持一致的口径）：必须可见、必须有标题、不能是工具窗口
    或透明遮罩、必须有合理尺寸。
    """
    from core.logger import T, log_debug, log_error, log_exception, log_warning

    if not WINDOWS_API_AVAILABLE:
        return []

    windows: list[WindowInfo] = []

    # 获取桌面区域用于 ApplicationFrameWindow 特判
    try:
        user32 = ctypes.windll.user32
        desktop_width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        desktop_height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        desktop_area = desktop_width * desktop_height
    except Exception:
        desktop_area = 1920 * 1080  # 降级默认值

    def enum_windows_callback(hwnd, _):
        try:
            # 1. 只处理可见窗口
            if not win32gui.IsWindowVisible(hwnd):
                return True

            # 2. 检查窗口样式（排除工具窗口、消息窗口等）
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

            # 跳过工具窗口
            if ex_style & win32con.WS_EX_TOOLWINDOW:
                return True

            # 3. 必须有窗口标题
            title = win32gui.GetWindowText(hwnd)
            if not title or len(title.strip()) == 0:
                return True

            # 4. 检查窗口是否真的可以接收输入（不是透明遮罩）
            if ex_style & win32con.WS_EX_TRANSPARENT:
                return True

            # 5. 获取窗口矩形（使用 DWM API 去除阴影）
            x1, y1, x2, y2 = get_window_rect_no_shadow(hwnd)

            # 6. 窗口必须有合理的大小（排除太小的窗口）
            width = x2 - x1
            height = y2 - y1
            if width < 30 or height < 30:
                return True

            # 7. 窗口必须在屏幕可见区域内（至少部分可见）
            if x2 < -1000 or y2 < -1000 or x1 > 10000 or y1 > 10000:
                return True

            # 8. 检查窗口类名，排除一些特殊的系统窗口
            try:
                class_name = win32gui.GetClassName(hwnd)

                # ApplicationFrameWindow 特判：过滤掉占满屏幕的空壳
                if class_name == "ApplicationFrameWindow":
                    if width * height > 0.8 * desktop_area:
                        return True

                excluded_classes = [
                    'Windows.UI.Core.CoreWindow',  # UWP 内容窗口（会和 ApplicationFrameWindow 重复）
                    'WorkerW',                     # 桌面工作窗口
                    'Progman',                     # 程序管理器
                ]
                if class_name in excluded_classes:
                    return True
            except Exception as e:
                log_exception(e, T("获取窗口类名 hwnd={hwnd}", hwnd=hwnd))

            windows.append(WindowInfo(hwnd, (x1, y1, x2, y2), title))

        except Exception as e:
            # 静默处理异常，继续枚举下一个窗口
            if debug:
                log_warning(T("处理窗口时出错: {e}", e=e), module="SmartSelection")

        return True

    try:
        win32gui.EnumWindows(enum_windows_callback, None)
    except Exception as e:
        # EnumWindows 失败时回调可能已经收集了一部分窗口，但结果不完整，宁可不给
        log_error(T("枚举窗口失败: {e}", e=e), module="SmartSelection")
        windows = []

    if debug and windows:
        log_debug(T("找到 {count} 个有效窗口", count=len(windows)), module="SmartSelection")
        log_debug(T("检测到的窗口列表（前5个）:"), module="SmartSelection")
        for index, window in enumerate(windows[:5]):
            rect = window.rect
            log_debug(
                T(
                    "{index}. 标题: {title}, 大小: {width}x{height}, 位置: ({x}, {y})",
                    index=index + 1,
                    title=window.title[:30],
                    width=rect[2] - rect[0],
                    height=rect[3] - rect[1],
                    x=rect[0],
                    y=rect[1],
                ),
                module="SmartSelection",
            )

    return windows


def virtual_desktop_rect() -> list[int] | None:
    """所有显示器合并后的区域；拿不到返回 None。"""
    try:
        user32 = ctypes.windll.user32
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77
        # SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
        x = user32.GetSystemMetrics(76)
        y = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)
        return [x, y, x + width, y + height]
    except Exception:
        return None
