# -*- coding: utf-8 -*-
"""
click_through.py - 鼠标穿透（覆盖层不吃鼠标事件）

GIF 录制有两个覆盖层要"看得见但不挡路"：选区覆盖层（穿透状态下让点击落到下面的
程序上）和绘制层（录制中不抢焦点）。Windows 用 WS_EX_TRANSPARENT 实现。

macOS 的等价物是 NSWindow 的 setIgnoresMouseEvents_。这里刻意不用 Qt 的
WindowTransparentForInput 标志：改窗口标志会让窗口重新显示一次，全屏覆盖层会闪
一下，而这个属性可以就地改。

hwnd 参数在两个平台含义不同但来源相同——都是 QWidget.winId()：Windows 上是窗口
句柄，macOS 上是 NSView 指针。
"""
import ctypes

from core.logger import T, log_exception

IS_WINDOWS = hasattr(ctypes, "windll")


def set_click_through_macos(hwnd: int, enable: bool) -> bool:
    """macOS：让窗口忽略鼠标事件（等价于 WS_EX_TRANSPARENT）。

    返回是否设置成功；失败只记日志，不影响录制本身。
    """
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
