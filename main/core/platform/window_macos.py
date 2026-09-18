# -*- coding: utf-8 -*-
"""macOS 的窗口枚举后端（智能选区用）。

从 ``capture/window_finder_macos.py`` 搬过来，逻辑逐字保留，只换成返回 ``WindowInfo``。

Quartz 的 ``CGWindowListCopyWindowInfo`` 返回的列表本身就是**从前到后的 Z 序**，
所以"第一个命中即返回"这套逻辑与 Windows 后端共用。

几个平台差异值得记一笔：

- ``CGWindowBounds`` 天然不含投影，不需要 Windows 那套 ``DwmGetWindowAttribute``。
- 没有窗口句柄，用 ``kCGWindowNumber``（窗口 id）当标识。
- 读窗口**标题**需要「屏幕录制」权限，没授权时 ``kCGWindowName`` 是空的；但窗口的
  位置/大小/所属应用不需要任何权限。所以标题退化为应用名——不然没授权时列表里所有
  窗口都没名字，调试和日志都没法看。
"""

import os

from core.platform.contracts import WindowInfo

try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListExcludeDesktopElements,
        kCGWindowListOptionOnScreenOnly,
    )
    MACOS_API_AVAILABLE = True
except ImportError:
    MACOS_API_AVAILABLE = False

# 元素级命中（UI 检测的「检测元素」档）用无障碍接口，比窗口枚举多一个依赖：
# 辅助功能权限。没授权时 AXUIElementCopyElementAtPosition 返回 kAXErrorAPIDisabled，
# 这里把它当成「查不到」，由上层回退到窗口级。
try:
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCopyElementAtPosition,
        AXUIElementCreateSystemWide,
        AXUIElementGetPid,
        AXValueGetValue,
        kAXValueCGPointType,
        kAXValueCGSizeType,
    )
    ELEMENT_API_AVAILABLE = True
except ImportError:
    ELEMENT_API_AVAILABLE = False

# 普通应用窗口都在 layer 0；Dock(20)、菜单栏(25)、浮动面板等都在别的层上
_NORMAL_WINDOW_LAYER = 0
# 与 Windows 后端一致的最小尺寸阈值
_MIN_WINDOW_SIZE = 30

# 太小的元素多半是装饰/分隔线，命中它们反而不如回退到窗口级
_MIN_ELEMENT_SIZE = 8

# 命中到这些角色说明「命中的就是窗口/应用本身」，元素级没有更细的东西可选
_WINDOW_LEVEL_ROLES = ("AXWindow", "AXSheet", "AXApplication", "AXSystemWide", "AXDialog")


def enumerate_windows(exclude_pid: int | None = None,
                      debug: bool = False) -> list[WindowInfo]:
    """枚举屏幕上的应用窗口，顺序即 Z 序（前 → 后）。

    ``exclude_pid`` 默认排除自己：截图时我们自己的全屏遮罩必须在列表外，
    否则智能选区永远命中遮罩。
    """
    from core.logger import T

    if not MACOS_API_AVAILABLE:
        return []
    if exclude_pid is None:
        exclude_pid = os.getpid()

    options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    try:
        window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
    except Exception as e:
        from core.logger import log_error

        log_error(T("枚举窗口失败: {e}", e=e), module="SmartSelection")
        return []

    windows: list[WindowInfo] = []
    for info in window_list:
        try:
            if info.get("kCGWindowLayer") != _NORMAL_WINDOW_LAYER:
                continue
            if exclude_pid is not None and info.get("kCGWindowOwnerPID") == exclude_pid:
                continue
            if not info.get("kCGWindowIsOnscreen", True):
                continue
            if float(info.get("kCGWindowAlpha", 1.0)) <= 0:
                continue

            bounds = info.get("kCGWindowBounds")
            if not bounds:
                continue
            x = int(bounds["X"])
            y = int(bounds["Y"])
            width = int(bounds["Width"])
            height = int(bounds["Height"])

            if width < _MIN_WINDOW_SIZE or height < _MIN_WINDOW_SIZE:
                continue
            x2, y2 = x + width, y + height
            # 完全跑到屏幕外的窗口（与 Windows 后端同样的粗筛）
            if x2 < -1000 or y2 < -1000 or x > 10000 or y > 10000:
                continue

            title = (info.get("kCGWindowName") or "").strip()
            if not title:
                # 没授权屏幕录制时标题读不到，退化成应用名，至少日志里认得出是谁
                title = (info.get("kCGWindowOwnerName") or "").strip()
            if not title:
                continue

            windows.append(
                WindowInfo(int(info.get("kCGWindowNumber", 0)), (x, y, x2, y2), title)
            )
        except Exception as e:
            if debug:
                from core.logger import log_warning

                log_warning(T("处理窗口时出错: {e}", e=e), module="SmartSelection")
            continue

    if debug:
        from core.logger import log_debug

        log_debug(T("找到 {count} 个有效窗口", count=len(windows)), module="SmartSelection")
        for index, window in enumerate(windows[:5]):
            rect = window.rect
            log_debug(
                T("{index}. 标题: {title}, 大小: {width}x{height}, 位置: ({x}, {y})",
                  index=index + 1, title=window.title[:30],
                  width=rect[2] - rect[0], height=rect[3] - rect[1],
                  x=rect[0], y=rect[1]),
                module="SmartSelection",
            )
    return windows


def _element_attribute(element, attribute: str):
    """读一个 AX 属性；读不到返回 None。"""
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    return value if err == 0 else None


def _element_rect(element) -> list[int] | None:
    """把元素的 AXPosition/AXSize 拼成屏幕坐标矩形（原生坐标是左上原点，与窗口枚举一致）。"""
    position = _element_attribute(element, "AXPosition")
    size = _element_attribute(element, "AXSize")
    if position is None or size is None:
        return None

    try:
        position_ok, point = AXValueGetValue(position, kAXValueCGPointType, None)
        size_ok, dimensions = AXValueGetValue(size, kAXValueCGSizeType, None)
    except Exception:
        return None
    if not (position_ok and size_ok):
        return None

    x, y = int(point.x), int(point.y)
    width, height = int(dimensions.width), int(dimensions.height)
    return [x, y, x + width, y + height]


def element_rect_at_point(x: int, y: int) -> list[int] | None:
    """该点最深的 UI 元素矩形（屏幕坐标）；查不到或命中的是窗口本身时返回 None。

    ``None`` 不是错误，而是「这一档没东西可选」：调用方应当回退到窗口级命中。
    辅助功能权限缺失时 AX 返回 kAXErrorAPIDisabled，同样落到这里。
    """
    if not ELEMENT_API_AVAILABLE:
        return None

    try:
        err, element = AXUIElementCopyElementAtPosition(
            AXUIElementCreateSystemWide(), float(x), float(y), None)
    except Exception:
        return None
    if err != 0 or element is None:
        return None

    # 截图遮罩是我们自己的全屏窗口，它一定压在鼠标下面：命中它必须当成「没元素」，
    # 否则元素档框的就是遮罩自己（整屏），窗口枚举那边是靠 exclude_pid 排掉自己的。
    try:
        pid_err, pid = AXUIElementGetPid(element, None)
        if pid_err == 0 and pid == os.getpid():
            return None
    except Exception:
        pass

    role = _element_attribute(element, "AXRole")
    if isinstance(role, str) and role in _WINDOW_LEVEL_ROLES:
        return None

    rect = _element_rect(element)
    if rect is None:
        return None
    if rect[2] - rect[0] < _MIN_ELEMENT_SIZE or rect[3] - rect[1] < _MIN_ELEMENT_SIZE:
        return None
    return rect


def virtual_desktop_rect() -> list[int] | None:
    """所有显示器合并后的区域（Qt 的虚拟桌面几何）。拿不到返回 None。"""
    try:
        from PySide6.QtGui import QGuiApplication

        rect = None
        for screen in QGuiApplication.screens():
            geometry = screen.geometry()
            if rect is None:
                rect = [geometry.x(), geometry.y(),
                        geometry.x() + geometry.width(), geometry.y() + geometry.height()]
            else:
                rect[0] = min(rect[0], geometry.x())
                rect[1] = min(rect[1], geometry.y())
                rect[2] = max(rect[2], geometry.x() + geometry.width())
                rect[3] = max(rect[3], geometry.y() + geometry.height())
        return rect
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("获取虚拟屏幕尺寸"))
        return None
