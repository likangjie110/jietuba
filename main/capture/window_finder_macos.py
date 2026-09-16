# -*- coding: utf-8 -*-
"""
window_finder_macos.py - macOS 的窗口枚举（智能选区用）

Windows 那边是 EnumWindows + DWM 去阴影；macOS 用 Quartz 的 CGWindowListCopyWindowInfo，
它返回的列表本身就是**从前到后的 Z 序**，所以"第一个命中即返回"这套逻辑可以直接沿用。

几个平台差异值得记一笔：

- CGWindowBounds 天然不含投影，不需要 Windows 那套 DwmGetWindowAttribute。
- 没有窗口句柄，用 kCGWindowNumber（窗口 id）当标识。
- 读窗口**标题**需要「屏幕录制」权限，没授权时 kCGWindowName 是空的；但窗口的
  位置/大小/所属应用不需要任何权限。所以标题退化为应用名——不然没授权时列表里
  所有窗口都没名字，调试和日志都没法看。

坐标：CGWindowBounds 与 Qt 的全局坐标同一套（主屏左上角为原点，单位为点），
多屏偏移由调用方按老规矩相减。
"""
import os
from typing import List, Optional, Tuple

from core.logger import T

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

# 普通应用窗口都在 layer 0；Dock(20)、菜单栏(25)、浮动面板等都在别的层上
_NORMAL_WINDOW_LAYER = 0
# 与 Windows 实现一致的最小尺寸阈值
_MIN_WINDOW_SIZE = 30


def enumerate_windows(exclude_pid: Optional[int] = None,
                      debug: bool = False) -> List[Tuple[int, List[int], str]]:
    """枚举屏幕上的应用窗口，返回 [(window_id, [x1,y1,x2,y2], 标题), ...]。

    顺序即 Z 序（前 → 后），调用方按顺序做命中测试。
    """
    if not MACOS_API_AVAILABLE:
        return []
    if exclude_pid is None:
        exclude_pid = os.getpid()          # 截图时我们自己的全屏遮罩要排除掉

    options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    try:
        window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
    except Exception as e:
        from core.logger import log_error
        log_error(T("枚举窗口失败: {e}", e=e), module="SmartSelection")
        return []

    windows: List[Tuple[int, List[int], str]] = []
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
            # 完全跑到屏幕外的窗口（与 Windows 实现同样的粗筛）
            if x2 < -1000 or y2 < -1000 or x > 10000 or y > 10000:
                continue

            title = (info.get("kCGWindowName") or "").strip()
            if not title:
                # 没授权屏幕录制时标题读不到，退化成应用名，至少日志里认得出是谁
                title = (info.get("kCGWindowOwnerName") or "").strip()
            if not title:
                continue

            windows.append((int(info.get("kCGWindowNumber", 0)), [x, y, x2, y2], title))
        except Exception as e:
            if debug:
                from core.logger import log_warning
                log_warning(T("处理窗口时出错: {e}", e=e), module="SmartSelection")
            continue

    if debug:
        from core.logger import log_debug
        log_debug(T("找到 {count} 个有效窗口", count=len(windows)), module="SmartSelection")
        for index, (_, rect, title) in enumerate(windows[:5]):
            log_debug(
                T("{index}. 标题: {title}, 大小: {width}x{height}, 位置: ({x}, {y})",
                  index=index + 1, title=title[:30],
                  width=rect[2] - rect[0], height=rect[3] - rect[1],
                  x=rect[0], y=rect[1]),
                module="SmartSelection",
            )
    return windows


def virtual_desktop_rect() -> Optional[List[int]]:
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
        from core.logger import log_exception
        log_exception(e, T("获取虚拟屏幕尺寸"))
        return None


def frontmost_window_rect() -> Optional[List[int]]:
    """最前面那个应用窗口的矩形（按 Z 序取第一个）。"""
    windows = enumerate_windows()
    return windows[0][1] if windows else None
