# -*- coding: utf-8 -*-
"""窗口枚举与命中测试（智能选区）。

门面 + 共用逻辑，平台实现见 ``window_win32.py`` / ``window_macos.py``。原先是
``capture/window_finder.py`` 同时扮演门面与 Windows 实现，macOS 实现另放一个文件、
由它转发调用；两个文件里的过滤阈值、粗筛条件、虚拟桌面降级链各写一份。

Windows 与 macOS 的后端都按 **Z 序（前 → 后）** 返回窗口，所以"第一个命中即返回"
这套逻辑不需要分平台。Linux 没有后端，能力矩阵里 ``WINDOW_ENUMERATION`` 为 NONE。
"""

from core.platform.capabilities import Capability, available
from core.platform.contracts import WindowInfo
from core.platform.detection import IS_MACOS, IS_WINDOWS

# 窗口太小就不值得作为选区目标（两端后端都用这个阈值，避免结果口径不一致）
MIN_WINDOW_SIZE = 30
# 完全跑到屏幕外的窗口的粗筛边界
OFFSCREEN_LIMIT = 1000
OFFSCREEN_FAR = 10000


def _backend():
    """当前平台可用的枚举后端模块；没有就返回 None。

    延迟导入：``core.platform.window`` 在启动早期就会被调用，不该因为加载它而把
    pywin32 / Quartz 一起拉进来；后端模块本身也用 try-import 兜住缺依赖的情况。
    """
    if IS_WINDOWS:
        from core.platform import window_win32

        return window_win32 if window_win32.WINDOWS_API_AVAILABLE else None
    if IS_MACOS:
        from core.platform import window_macos

        return window_macos if window_macos.MACOS_API_AVAILABLE else None
    return None


def preload() -> bool:
    """预热窗口枚举后端，返回当前平台是否存在后端。

    导入后端模块本身就会把它依赖的库装进 import 缓存（Windows 上是
    win32gui/win32con，macOS 上是 Quartz），首次截图因此不必等 import。
    这里刻意不直接 import 那些库——那样在别的平台上必然失败，而"该预热什么"
    本来就该由后端自己决定。
    """
    return _backend() is not None


def is_window_enumeration_available() -> bool:
    """能否枚举窗口。

    两个条件同时成立：平台层声明这个平台有后端，且对应依赖真的导入成功。
    只看声明会在缺 pywin32 的 Windows 上误报可用；只看导入结果，则会把 Linux 的
    「根本没有实现」记成「依赖缺失」，排查时会往错误的方向找。
    """
    return available(Capability.WINDOW_ENUMERATION) and _backend() is not None


class WindowFinder:
    """枚举窗口并按 Z 序对鼠标位置做命中测试。

    ``windows`` 里的矩形已经减掉 ``screen_offset_*``——截图窗口可能只覆盖某一块
    屏幕区域，而枚举拿到的永远是屏幕绝对坐标，偏移在这里统一处理，调用方不必各自减。
    """

    def __init__(self, screen_offset_x: int = 0, screen_offset_y: int = 0):
        if not is_window_enumeration_available():
            raise RuntimeError("当前平台没有可用的窗口枚举接口，无法使用智能选区功能")

        self.windows: list[WindowInfo] = []
        self.screen_offset_x = screen_offset_x
        self.screen_offset_y = screen_offset_y
        self.debug = False

    def set_screen_offset(self, offset_x: int, offset_y: int):
        """设置屏幕偏移（多显示器时用于把屏幕绝对坐标换算到截图区域坐标）。"""
        from core.logger import log_debug, T

        self.screen_offset_x = offset_x
        self.screen_offset_y = offset_y
        if self.debug:
            log_debug(T("使用偏移: ({offset_x}, {offset_y})",
                        offset_x=self.screen_offset_x,
                        offset_y=self.screen_offset_y), module="SmartSelection")

    def find_windows(self) -> None:
        """枚举所有可见窗口并记录偏移后的矩形。"""
        backend = _backend()
        if backend is None:
            self.windows = []
            return

        enumerated = backend.enumerate_windows(debug=self.debug)
        offset_x, offset_y = self.screen_offset_x, self.screen_offset_y
        if offset_x or offset_y:
            enumerated = [
                WindowInfo(
                    window.handle,
                    (window.rect[0] - offset_x, window.rect[1] - offset_y,
                     window.rect[2] - offset_x, window.rect[3] - offset_y),
                    window.title,
                )
                for window in enumerated
            ]
        self.windows = enumerated

    def find_window_at_point(self, x: int, y: int,
                             fallback_rect: list[int] | None = None) -> list[int]:
        """返回包含该点的最顶层窗口矩形；没有命中时返回 ``fallback_rect``。

        第一个命中即返回——列表已按 Z 序从顶到底排列。
        """
        from core.logger import log_debug, T

        for index, window in enumerate(self.windows):
            x1, y1, x2, y2 = window.rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                if self.debug:
                    log_debug(
                        T(
                            "鼠标({x}, {y})处找到窗口: '{title}', 大小: {width}x{height}, Z-order: {idx}",
                            x=x, y=y, title=window.title[:30],
                            width=x2 - x1, height=y2 - y1, idx=index,
                        ),
                        module="SmartSelection",
                    )
                return [x1, y1, x2, y2]

        if self.debug:
            log_debug(T("在鼠标位置({x}, {y})未找到有效窗口，返回备选矩形", x=x, y=y),
                      module="SmartSelection")

        if fallback_rect:
            return fallback_rect
        return self._get_virtual_desktop_rect()

    def _get_virtual_desktop_rect(self) -> list[int]:
        """虚拟桌面尺寸（包含所有显示器）。

        降级链：平台后端的取法 → Qt 的主屏几何 → 硬编码 1920x1080。最后那一步是
        为了让调用方永远拿得到一个矩形，否则智能选区会因为「没有备选矩形」而漏掉
        一次更新。
        """
        from core.logger import log_exception, T

        backend = _backend()
        if backend is not None:
            rect = backend.virtual_desktop_rect()
            if rect:
                return rect

        try:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            if screen:
                geometry = screen.geometry()
                return [geometry.x(), geometry.y(),
                        geometry.x() + geometry.width(),
                        geometry.y() + geometry.height()]
        except Exception as e:
            log_exception(e, T("降级获取主显示器尺寸"))

        return [0, 0, 1920, 1080]

    def clear(self) -> None:
        """清除窗口列表。"""
        self.windows = []


def find_window_at_cursor(screen_offset_x: int = 0,
                          screen_offset_y: int = 0) -> list[int] | None:
    """快捷方式：返回当前鼠标位置下的窗口矩形；功能不可用时返回 None。"""
    from core.logger import log_error, T

    if not is_window_enumeration_available():
        return None

    try:
        from PySide6.QtGui import QCursor

        finder = WindowFinder(screen_offset_x, screen_offset_y)
        finder.find_windows()

        cursor_pos = QCursor.pos()
        x = cursor_pos.x() - screen_offset_x
        y = cursor_pos.y() - screen_offset_y

        return finder.find_window_at_point(x, y)
    except Exception as e:
        log_error(T("查找窗口失败: {e}", e=e), module="SmartSelection")
        return None
