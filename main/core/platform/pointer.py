# -*- coding: utf-8 -*-
"""指针与输入：光标位置、鼠标键状态、全局滚轮监听、横向滚动与复制快捷键注入。

迁移前这些能力散在四个文件里，各自判断平台，而且有几处是硬编码 Windows 调用：

- ``stitch/scroll_window.py`` 把「窗口鼠标穿透」和「启动 pynput 滚轮监听」写在同一个
  try 块里，Win32 调用在前。非 Windows 上第一句就抛 AttributeError 直接跳到 except，
  **监听器永远不会启动**——长截图在 macOS/Linux 上对滚动毫无反应。
- 同文件的横向滚动直接用 ``import win32api``，非 Windows 上 ModuleNotFoundError，
  横向模式的核心动作不可用。
- ``translation/smart_translation_controller.py`` 直接 ``ctypes.windll.user32.keybd_event``，
  非 Windows 上抛异常，智能翻译退化成手动输入原文。
- ``gif/frame_recorder.py`` 的鼠标键状态只在 Windows/macOS 有实现，Linux 上恒为
  「未按下」，且没有任何日志说明为什么 GIF 里不出现按键特效。

这里把「平台怎么做」收口，调用方只表达意图（取光标、键是否按下、监听滚轮、注入滚动、
发送复制快捷键）。每个平台的实现差异与缺失都在能力矩阵里登记，见 capabilities.py。
"""

import ctypes

from core.platform.capabilities import Capability, available
from core.platform.detection import IS_MACOS, IS_WINDOWS

# ── 输入注入用到的 Windows 常量 ─────────────────────────
VK_CONTROL = 0x11
VK_V = 0x56
VK_INSERT = 0x2D
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_HWHEEL = 0x01000
WHEEL_DELTA = 120

# 按键枚举沿用原来的整数约定（0=左键，1=右键），避免调用方改索引
BUTTON_LEFT = 0
BUTTON_RIGHT = 1


# ──────────────────────────────────────────────
# 光标与键状态
# ──────────────────────────────────────────────

def cursor_position() -> tuple[int, int]:
    """鼠标的屏幕绝对坐标。

    Windows 用 GetCursorPos，其它平台用 Qt 的 QCursor——后者在三平台上都是
    屏幕绝对坐标，与 mss/QScreen 的坐标系一致。
    """
    if IS_WINDOWS:
        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    from PySide6.QtGui import QCursor

    pos = QCursor.pos()
    return pos.x(), pos.y()


def is_button_pressed(button: int) -> bool:
    """鼠标键当前是否按下。``button``：0=左键，1=右键。

    Windows 用 GetAsyncKeyState；macOS 用 Quartz 的 CGEventSourceButtonState，
    读的是全局按键状态、不需要辅助功能权限；Linux 没有实现（能力矩阵里
    POINTER_BUTTON_STATE 为 NONE），返回 False 并记一条日志——迁移前这里同样
    恒返回 False，但没有任何线索解释原因。
    """
    if IS_WINDOWS:
        vk = VK_LBUTTON if button == 0 else VK_RBUTTON
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)

    if IS_MACOS:
        try:
            from Quartz import (
                CGEventSourceButtonState,
                kCGEventSourceStateCombinedSessionState,
            )

            return bool(CGEventSourceButtonState(
                kCGEventSourceStateCombinedSessionState, button))
        except Exception:
            return False

    _log_once_unsupported(
        "pointer_button_state",
        "当前平台读不到鼠标键状态，录制中的按键特效不会出现",
    )
    return False


# ──────────────────────────────────────────────
# 全局滚轮监听
# ──────────────────────────────────────────────

def create_scroll_listener(on_scroll, on_error=None):
    """创建并启动全局滚轮监听器；pynput 不可用时返回 None。

    返回的对象只保证有 ``stop()``（pynput 的 Listener 接口），调用方不需要知道
    底层是什么。``on_scroll`` 的签名是 ``(x, y, dx, dy)``，与 pynput 一致：
    dy>0 向上、dy<0 向下。

    监听是全局的，与窗口焦点无关——长截图与 GIF 录制都依赖这一点。
    """
    from core.logger import log_error, T

    if not available(Capability.SCROLL_LISTEN):
        log_error(T("当前平台无法监听全局滚轮事件"), "Pointer")
        return None

    try:
        from pynput import mouse

        listener = mouse.Listener(on_scroll=on_scroll)
        listener.start()
        return listener
    except Exception as e:
        if on_error is not None:
            on_error(e)
        else:
            log_error(T("启动滚轮监听失败: {e}", e=e), "Pointer")
        return None


def stop_listener(listener) -> None:
    """停止监听器，忽略重复停止与已失效的对象。"""
    if listener is None:
        return
    try:
        listener.stop()
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("停止输入监听器"))


# ──────────────────────────────────────────────
# 输入注入
# ──────────────────────────────────────────────

def scroll_horizontal(clicks: int = 1) -> bool:
    """向右滚动若干格（横向滚动事件）。返回是否注入成功。

    只有 Windows 有直接可用的接口（``mouse_event(MOUSEEVENTF_HWHEEL)``）。
    macOS 需要 CGEvent 合成、Linux 需要 XTest/ydotool，都尚未实现，因此能力矩阵里
    SCROLL_INJECT 对这两个平台是 NONE——长截图的横向模式在那些平台上只能用鼠标手势。
    """
    from core.logger import log_error, log_exception, T

    if not IS_WINDOWS:
        _log_once_unsupported(
            "scroll_inject",
            "当前平台暂不支持自动横向滚动，请用 Shift+滚轮或横向滚轮",
        )
        return False

    try:
        import win32api
        import win32con

        # MOUSEEVENTF_HWHEEL: 横向滚动事件；amount * WHEEL_DELTA 是标准滚动量
        win32api.mouse_event(
            win32con.MOUSEEVENTF_HWHEEL, 0, 0, clicks * WHEEL_DELTA, 0
        )
        return True
    except Exception as e:
        log_error(T("发送横向滚动失败: {e}", e=e), "Pointer")
        log_exception(e, T("发送横向滚动"))
        return False


def send_copy_shortcut() -> bool:
    """发送「复制选中内容」的快捷键。返回是否注入成功。

    各平台的复制快捷键不同，调用方只表达「复制」，映射由平台层负责：
    Windows 是 Ctrl+Insert（与原有的智能翻译实现一致），macOS 是 Cmd+C
    （macOS 没有 Ctrl+Insert 这个概念），Linux 是 Ctrl+C。

    迁移前这一步在 ``translation/smart_translation_controller.py`` 里直接
    ``ctypes.windll.user32.keybd_event``，非 Windows 上抛 AttributeError，智能翻译
    退化成手动输入原文。
    """
    from core.logger import log_error, log_exception, T

    try:
        if IS_WINDOWS:
            return _inject_windows(VK_CONTROL, VK_INSERT)
        return _inject_pynput("c")
    except Exception as e:
        log_error(T("发送复制快捷键失败: {e}", e=e), "Pointer")
        log_exception(e, T("发送复制快捷键"))
        return False


def send_paste_shortcut() -> bool:
    """发送「粘贴」的快捷键。返回是否注入成功。

    Windows 是 Ctrl+V，macOS 是 Cmd+V，Linux 是 Ctrl+V。调用方只表达「粘贴」，
    映射由平台层负责——剪贴板历史窗口的自动粘贴就靠它把内容送回原程序。
    """
    from core.logger import log_error, log_exception, T

    try:
        if IS_WINDOWS:
            return _inject_windows(VK_CONTROL, VK_V)
        return _inject_pynput("v")
    except Exception as e:
        log_error(T("发送粘贴快捷键失败: {e}", e=e), "Pointer")
        log_exception(e, T("发送粘贴快捷键"))
        return False


def _inject_windows(modifier_vk: int, key_vk: int) -> bool:
    """Windows：keybd_event 按下修饰键与目标键，再逆序抬起。"""
    ctypes.windll.user32.keybd_event(modifier_vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key_vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key_vk, 0, KEYEVENTF_KEYUP, 0)
    ctypes.windll.user32.keybd_event(modifier_vk, 0, KEYEVENTF_KEYUP, 0)
    return True


def _inject_pynput(letter: str) -> bool:
    """非 Windows：用 pynput 合成 Cmd/Ctrl + 字母。需要辅助功能权限。"""
    from pynput.keyboard import Controller, Key

    modifier = Key.cmd if IS_MACOS else Key.ctrl
    with Controller() as keyboard:
        keyboard.press(modifier)
        keyboard.press(letter)
        keyboard.release(letter)
        keyboard.release(modifier)
    return True


# ──────────────────────────────────────────────
# 能力缺失只提醒一次
# ──────────────────────────────────────────────

_warned: set[str] = set()


def _log_once_unsupported(key: str, message: str) -> None:
    """同一件「本平台做不到的事」只记一次日志。

    这些回调可能每帧、每次滚动都被调到（GIF 录制按帧采集键状态），每次都写日志会
    把日志冲垮；但完全不写又会让功能静默失效，查问题时无从下手。
    """
    if key in _warned:
        return
    _warned.add(key)
    from core.logger import log_warning

    log_warning(message, "Pointer")


def reset_warnings() -> None:
    """清掉「已提醒」标记（测试用）。"""
    _warned.clear()
