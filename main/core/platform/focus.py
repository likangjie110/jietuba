# -*- coding: utf-8 -*-
"""前台窗口/应用的记录与切回。

「记住粘贴前谁在前台，粘完再切回去」是多平台行为差异最明显的一处：

- Windows 记的是窗口句柄（``GetForegroundWindow``），切回用 ``SetForegroundWindow``。
- macOS 没有窗口句柄这一层，记的是**前台应用**（``NSRunningApplication``），切回用
  ``activateWithOptions_``，并且必须带上 ``NSApplicationActivateIgnoringOtherApps``，
  否则我们自己的窗口可能又把焦点抢回去。
- Linux 没有实现（``Capability.PASTE_TO_APP`` 为 NONE）。

因此这里返回的对象对调用方是**不透明令牌**：只负责原样保管、再原样传回来，不要
假设它是整数或某个具体类型。
"""

import ctypes

from core.platform.capabilities import Capability, available
from core.platform.detection import IS_MACOS, IS_WINDOWS


def _user32():
    """取 user32（仅 Windows 可用，调用方需自行确保平台）。

    ``ctypes`` 本身三平台都有，只有 ``ctypes.windll`` 是 Windows 专有；因此可以在
    模块顶层 import ctypes，但不能在导入期取 windll。
    """
    return ctypes.windll.user32


def capture_foreground():
    """返回当前前台窗口/应用的不透明令牌；拿不到返回 None。"""
    from core.logger import log_exception, T

    if IS_WINDOWS:
        try:
            return _user32().GetForegroundWindow()
        except Exception as e:
            log_exception(e, T("获取前台窗口"))
            return None

    if IS_MACOS:
        try:
            from AppKit import NSWorkspace

            return NSWorkspace.sharedWorkspace().frontmostApplication()
        except Exception as e:
            log_exception(e, T("获取前台应用"))
            return None

    return None


def foreground_app_name() -> str | None:
    """当前前台程序的可读名字；没有实现或取不到时返回 None。

    用来判断「这个程序在忽略列表里」：macOS 取的是 ``localizedName``
    （「访达」这类本地化名字），Windows 取可执行文件名并去掉 ``.exe``。
    名字本身不可比较（本地化、大小写、.exe 后缀都随平台变），所以比较规则由
    调用方统一做（见 ``shortcut_manager._is_ignored_app``）。
    """
    from core.logger import log_exception, T

    if not available(Capability.FOREGROUND_APP):
        return None

    if IS_WINDOWS:
        try:
            from ctypes import byref, wintypes

            from core.platform.process import get_process_identity

            hwnd = _user32().GetForegroundWindow()
            if not hwnd:
                return None
            pid = wintypes.DWORD()
            _user32().GetWindowThreadProcessId(hwnd, byref(pid))
            identity = get_process_identity(int(pid.value))
            if not identity:
                return None
            image_name = identity[1] or ""
            if image_name.lower().endswith(".exe"):
                image_name = image_name[:-4]
            return image_name or None
        except Exception as e:
            log_exception(e, T("获取前台程序名"))
            return None

    if IS_MACOS:
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            name = app.localizedName()
            return str(name) if name else None
        except Exception as e:
            log_exception(e, T("获取前台程序名"))
            return None

    return None


def activate_foreground(target) -> bool:
    """把焦点切回之前记下的窗口/应用。返回是否成功。"""
    from core.logger import log_exception, T

    if target is None:
        return False

    if IS_WINDOWS:
        try:
            _user32().SetForegroundWindow(target)
            return True
        except Exception as e:
            log_exception(e, T("设置前台窗口"))
            return False

    if IS_MACOS:
        try:
            from AppKit import NSApplicationActivateIgnoringOtherApps

            return bool(
                target.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            )
        except Exception as e:
            log_exception(e, T("设置前台应用"))
            return False

    return False
