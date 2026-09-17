# -*- coding: utf-8 -*-
"""Windows 的全局热键机制：RegisterHotKey、WM_HOTKEY 原生过滤器、鼠标侧键钩子原语。

从 ``core/shortcut_manager.py`` 搬过来，逻辑逐字保留。搬家的理由与别处一致：那里同时
装着应用级的 handler 链、应用内 Qt 快捷键分发和这套 Win32 机制，而这三件事的变动原因
完全不同。

只负责「怎么和系统打交道」；「哪个热键该干什么」仍由 ``ShortcutManager`` 决定——过滤器
把 ``WM_HOTKEY`` 解成热键 id 后回调给它，侧键消息解成「哪个键、按下还是抬起」后也交给它。
"""

import ctypes

from ctypes import wintypes

from core.platform.detection import IS_WINDOWS

WM_HOTKEY = 0x0312

# 临时探测冲突用的 id，与真实注册的 id 分开，避免探测时把真热键顶掉
_PROBE_HOTKEY_ID = 9999


def register(hotkey_id: int, mods: int, vk: int) -> bool:
    """注册系统级热键。返回是否成功（失败通常是该组合已被别的程序占用）。"""
    from core.logger import log_error

    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.user32.RegisterHotKey(None, hotkey_id, mods, vk))
    except Exception as e:
        log_error(f"Error registering hotkey id={hotkey_id}: {e}", module="Hotkey")
        return False


def unregister(hotkey_id: int) -> None:
    """注销系统级热键。"""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("注销热键 id={hotkey_id}", hotkey_id=hotkey_id))


def check_availability(mods: int, vk: int) -> bool:
    """用临时注册探测组合键是否可用。

    RegisterHotKey 会失败当且仅当该组合已被占用，因此「注册再注销」就是最可靠的探测；
    探测用的 id 与真实注册分开，避免抢占。
    """
    from core.logger import log_exception, T

    if not IS_WINDOWS:
        return True
    try:
        if ctypes.windll.user32.RegisterHotKey(None, _PROBE_HOTKEY_ID, mods, vk):
            ctypes.windll.user32.UnregisterHotKey(None, _PROBE_HOTKEY_ID)
            return True
        return False
    except Exception as e:
        log_exception(e, T("检查快捷键可用性"))
        return False


class _Win32HotkeyFilter:
    """原生事件过滤器：只做消息解码，把热键 id 交给回调。

    继承 QAbstractNativeEventFilter 需要 Qt，因此延迟到构造时组合出一个子类，
    免得这个模块在导入期就把 Qt 拉进来。
    """

    @staticmethod
    def build(on_hotkey):
        from PySide6.QtCore import QAbstractNativeEventFilter

        from core.logger import log_exception

        class _Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, eventType, message):
                try:
                    if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                        msg = wintypes.MSG.from_address(int(message))
                        if msg.message == WM_HOTKEY:
                            on_hotkey(msg.wParam)
                            # WM_HOTKEY 始终消费掉：它本来就是我们自己注册的
                            return True, 0
                except Exception as e:
                    log_exception(e, "nativeEventFilter")
                return False, 0

        return _Filter()


def create_event_filter(on_hotkey):
    """创建拦截 WM_HOTKEY 的过滤器；``on_hotkey(hotkey_id)`` 在过滤器里被同步调用。"""
    if not IS_WINDOWS:
        return None
    return _Win32HotkeyFilter.build(on_hotkey)


# ──────────────────────────────────────────────
# 鼠标侧键：WH_MOUSE_LL 低级钩子
# ──────────────────────────────────────────────
#
# 侧键不走 Qt 的焦点链（Qt 只在指针悬停于控件上时才派发 mousePressEvent），只能靠
# pynput 的低级鼠标钩子。这里是钩子用到的 pynput-Windows 专有接口：
# ``win32_event_filter=``、``X_BUTTONS``、``mouseData``、``suppress_event()``。

def start_mouse_listener(event_filter):
    """启动侧键监听；失败返回 None。"""
    from core.logger import log_error

    if not IS_WINDOWS:
        return None
    try:
        from pynput import mouse

        listener = mouse.Listener(win32_event_filter=event_filter)
        # filter 在 start() 之后随时可能被钩子线程调用，因此调用方要先拿到对象再启动
        listener.start()
        return listener
    except Exception as e:
        log_error(f"鼠标侧键监听启动失败: {e}", module="Hotkey")
        return None


def decode_mouse_button(listener, msg, data):
    """把钩子消息解成 ``(button_name, pressed)``；不是侧键返回 None。

    ``msg`` 是 Windows 消息号，``data.mouseData`` 的高 16 位标识是哪一个 XBUTTON。
    查表按 name 而不是枚举对象，因此调用方不需要持有 pynput 模块的引用。
    """
    by_index = getattr(listener, "X_BUTTONS", {}).get(msg)
    if by_index is None:
        return None
    entry = by_index.get(data.mouseData >> 16)
    if entry is None:
        return None
    button, pressed = entry
    return button.name, pressed


def suppress_mouse_event(listener) -> None:
    """把当前事件从系统里吞掉。

    抛出 pynput 的 SuppressException，就此结束本次回调与后续派发——被抑制的事件根本
    到不了 on_click，这是 pynput 的既定行为。
    """
    listener.suppress_event()
