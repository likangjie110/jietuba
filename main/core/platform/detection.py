# -*- coding: utf-8 -*-
"""平台探测：全项目唯一的「现在跑在哪个平台」判断点。

这些判断以前散在十几个模块里，而且判据互相矛盾——有的用 ``sys.platform == "win32"``，
有的用 ``hasattr(ctypes, "windll")``，有的用 ``QGuiApplication.platformName() == "cocoa"``。
同一台机器上它们偶尔给出不同答案，排查问题时没有一处可以对齐，所以统一到这里。

OS 判断与 Qt 平台插件名是两件事，刻意分开而不合并：macOS 上测试会跑在 offscreen 平台、
Windows 上可能跑在 minimal，此时 OS 仍是 win32/darwin 但不能碰原生窗口 API。
"""

import sys

# ── OS ──────────────────────────────────────────────

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# 后端模块与日志里用的短名；未识别的平台直接回落到 sys.platform 原值
PLATFORM_NAME = (
    "windows" if IS_WINDOWS
    else "macos" if IS_MACOS
    else "linux" if IS_LINUX
    else sys.platform
)

SUPPORTED_PLATFORMS = ("windows", "macos", "linux")


def qt_platform_name() -> str:
    """Qt 平台插件名（``windows`` / ``cocoa`` / ``xcb`` / ``wayland`` / ``offscreen``）。

    需要真正碰原生窗口句柄的地方要额外看这个，不能只看 OS：``gif/click_through.py``
    就踩过一次——拿到非 cocoa 的插件名却仍然去构造 objc 对象，在离屏平台上直接段错误。
    QApplication 尚未创建时返回空串，调用方按"不确定"处理。
    """
    try:
        from PySide6.QtGui import QGuiApplication

        return (QGuiApplication.platformName() or "").lower()
    except Exception:
        return ""


def is_native_qt_platform() -> bool:
    """Qt 是否跑在真实窗口系统上（而非 offscreen/minimal 这类无原生窗口的插件）。

    探针类代码（原生句柄、屏幕坐标）在离屏平台上要么无效要么会崩，用它做前置守卫。
    """
    name = qt_platform_name()
    return name not in ("", "offscreen", "minimal", "minimalegl")
