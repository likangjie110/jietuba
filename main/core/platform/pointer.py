# -*- coding: utf-8 -*-
"""指针与输入：光标位置、鼠标键状态、全局滚轮/键盘监听、横向滚动与复制快捷键注入。

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

def pressed_modifiers() -> frozenset[str]:
    """当前按下的修饰键集合（``ctrl`` / ``alt`` / ``shift`` / ``cmd``）。

    全局鼠标动作要在「按键那一刻」知道修饰键状态，而 pynput 的鼠标回调不带这个信息：
    现读一次系统状态比另起一个键盘监听去追状态更可靠（漏一个事件就永久错位）。

    查不到的平台上返回空集合——调用方按「没有修饰键」处理，而不是让动作静默失效。
    """
    if IS_MACOS:
        return _pressed_modifiers_macos()
    if IS_WINDOWS:
        return _pressed_modifiers_windows()
    return frozenset()


def _pressed_modifiers_macos() -> frozenset[str]:
    """macOS：读当前事件源的修饰键标志位（Quartz 的 CGEventSourceFlagsState）。"""
    from core.logger import log_debug

    try:
        from Quartz import (
            CGEventSourceFlagsState,
            kCGEventFlagMaskAlternate,
            kCGEventFlagMaskCommand,
            kCGEventFlagMaskControl,
            kCGEventFlagMaskShift,
            kCGEventSourceStateCombinedSessionState,
        )

        flags = CGEventSourceFlagsState(kCGEventSourceStateCombinedSessionState)
    except Exception as e:
        log_debug(f"读取修饰键状态失败: {e}", "Pointer")
        return frozenset()

    pressed = set()
    if flags & kCGEventFlagMaskControl:
        pressed.add("ctrl")
    if flags & kCGEventFlagMaskAlternate:
        pressed.add("alt")
    if flags & kCGEventFlagMaskShift:
        pressed.add("shift")
    if flags & kCGEventFlagMaskCommand:
        pressed.add("cmd")
    return frozenset(pressed)


def _pressed_modifiers_windows() -> frozenset[str]:
    """Windows：GetAsyncKeyState 的高位表示当前按下。"""
    from core.logger import log_debug

    virtual_keys = {
        "ctrl": (0x11,),
        "alt": (0x12,),
        "shift": (0x10,),
        "cmd": (0x5B, 0x5C),      # 左右 Win 键
    }
    try:
        user32 = ctypes.windll.user32
    except Exception as e:
        log_debug(f"读取修饰键状态失败: {e}", "Pointer")
        return frozenset()

    pressed = set()
    for name, codes in virtual_keys.items():
        if any(user32.GetAsyncKeyState(code) & 0x8000 for code in codes):
            pressed.add(name)
    return frozenset(pressed)


# ── 全局鼠标手势的词汇表 ─────────────────────────────
# 「修饰键 + 鼠标动作」里的鼠标动作。只收不会抢走正常操作的：滚轮与中键/侧键。
# 不做左键双击、右键拖动这类——全局绑定会把普通操作也吃掉。
GESTURE_WHEEL_UP = "wheel_up"
GESTURE_WHEEL_DOWN = "wheel_down"
GESTURE_MIDDLE_CLICK = "middle_click"
GESTURE_BACK_CLICK = "back_click"
GESTURE_FORWARD_CLICK = "forward_click"
# 「按住键拖动」类手势：按下某个键、移动够远、松手，方向由位移决定。
# 与滚轮/中键互不冲突（那三个是「点一下」），但拖动会占用该键的正常拖拽——
# 因此默认只在用户显式绑定之后才参与匹配（见 core/shortcut_manager 的启停）。
GESTURE_LEFT_DRAG_UP = "left_drag_up"
GESTURE_LEFT_DRAG_DOWN = "left_drag_down"
GESTURE_LEFT_DRAG_LEFT = "left_drag_left"
GESTURE_LEFT_DRAG_RIGHT = "left_drag_right"
GESTURE_RIGHT_DRAG_UP = "right_drag_up"
GESTURE_RIGHT_DRAG_DOWN = "right_drag_down"
GESTURE_RIGHT_DRAG_LEFT = "right_drag_left"
GESTURE_RIGHT_DRAG_RIGHT = "right_drag_right"
DRAG_GESTURES = (
    GESTURE_LEFT_DRAG_UP,
    GESTURE_LEFT_DRAG_DOWN,
    GESTURE_LEFT_DRAG_LEFT,
    GESTURE_LEFT_DRAG_RIGHT,
    GESTURE_RIGHT_DRAG_UP,
    GESTURE_RIGHT_DRAG_DOWN,
    GESTURE_RIGHT_DRAG_LEFT,
    GESTURE_RIGHT_DRAG_RIGHT,
)

MOUSE_GESTURES = (
    GESTURE_WHEEL_UP,
    GESTURE_WHEEL_DOWN,
    GESTURE_MIDDLE_CLICK,
    GESTURE_BACK_CLICK,
    GESTURE_FORWARD_CLICK,
) + DRAG_GESTURES

#: 拖动判定：位移超过这个像素数才算一次拖动（避免把抖动当成手势）
DRAG_THRESHOLD = 60


class DragTracker:
    """把「按下 → 移动 → 松手」判成四个方向之一。

    纯状态机，不碰 pynput：喂进来的是按键名、坐标与按下/松开，因此可以穷举测试。
    判定用**位移的主轴**：横向位移更大就判左右，否则判上下——斜着拖时给一个确定答案，
    而不是要求用户拖出精确的水平线。
    """

    #: 参与拖动的按键名（pynput 的 Button.name）
    SUPPORTED_BUTTONS = ("left", "right")

    def __init__(self, threshold: int = DRAG_THRESHOLD):
        self.threshold = max(4, int(threshold))
        self._button = ""
        self._origin = (0, 0)

    def press(self, button_name: str, x: int, y: int) -> None:
        name = (button_name or "").lower()
        if name in self.SUPPORTED_BUTTONS:
            self._button = name
            self._origin = (int(x), int(y))

    def move(self, x: int, y: int) -> None:
        """移动不需要处理：方向在松手时用「起点 → 终点」算，中途抖动不影响结果。"""

    def release(self, button_name: str, x: int, y: int) -> str | None:
        """松手时给出方向手势名；没超过阈值或不是拖动中的键则返回 None。"""
        name = (button_name or "").lower()
        if name != self._button:
            return None
        self._button = ""
        dx = int(x) - self._origin[0]
        dy = int(y) - self._origin[1]
        if max(abs(dx), abs(dy)) < self.threshold:
            return None
        if abs(dx) >= abs(dy):
            horizontal = "right" if dx > 0 else "left"
            return f"{name}_drag_{horizontal}"
        vertical = "down" if dy > 0 else "up"
        return f"{name}_drag_{vertical}"

# pynput 的 Button 名字 → 手势名（侧键在 pynput 里叫 x1/x2）
BUTTON_NAME_TO_GESTURE = {
    "middle": GESTURE_MIDDLE_CLICK,
    "x1": GESTURE_BACK_CLICK,
    "x2": GESTURE_FORWARD_CLICK,
}

# 修饰键：空字符串表示「不要求修饰键」
MODIFIER_NONE = ""
MOUSE_MODIFIERS = (MODIFIER_NONE, "ctrl", "alt", "shift", "cmd")


def gesture_of_scroll(dx: int, dy: int) -> str | None:
    """滚轮事件 → 手势名；横向滚动不参与（留给长截图那种场景）。"""
    if dy > 0:
        return GESTURE_WHEEL_UP
    if dy < 0:
        return GESTURE_WHEEL_DOWN
    return None


def gesture_of_button(button_name: str) -> str | None:
    """鼠标按键名（pynput 的 ``Button.name``）→ 手势名；不参与全局手势的返回 None。"""
    return BUTTON_NAME_TO_GESTURE.get((button_name or "").lower())


def matches_modifiers(required: str, pressed) -> bool:
    """当前按下的修饰键是否满足绑定要求。

    要求「无修饰键」时必须真的没有修饰键：否则 Ctrl+滚轮既能触发绑定在「无」上的动作，
    又会顺带触发绑在 Ctrl 上的动作。
    """
    pressed = frozenset(pressed or ())
    if not required:
        return not pressed
    return required in pressed


def create_mouse_listener(on_click=None, on_scroll=None, on_error=None):
    """创建并启动全局鼠标监听器（按键 + 滚轮 + 移动）；pynput 不可用时返回 None。

    返回的对象只保证有 ``stop()``。回调都在 pynput 的监听线程里执行，调用方负责把
    结果排队回主线程。``on_click`` 的签名与 pynput 一致：``(x, y, button, pressed)``；
    ``on_scroll`` 是 ``(x, y, dx, dy)``（dy>0 向上、dy<0 向下）。

    监听是全局的、与窗口焦点无关——长截图与 GIF 录制只订阅滚轮（用
    ``create_scroll_listener``），「全局鼠标动作」还会订阅按键。
    """
    from core.logger import log_error, T

    if not available(Capability.SCROLL_LISTEN):
        log_error(T("当前平台无法监听全局鼠标事件"), "Pointer")
        return None

    try:
        from pynput import mouse

        listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
        listener.start()
        return listener
    except Exception as e:
        if on_error is not None:
            on_error(e)
        else:
            log_error(T("启动鼠标监听失败: {e}", e=e), "Pointer")
        return None


def create_scroll_listener(on_scroll, on_error=None):
    """只订阅滚轮的便捷入口（长截图 / GIF 录制用）。

    ``on_scroll`` 的签名是 ``(x, y, dx, dy)``，与 pynput 一致。实现与全局鼠标监听
    共用一份，免得两个入口各自维护 pynput 的启动与降级。
    """
    return create_mouse_listener(on_scroll=on_scroll, on_error=on_error)


def create_key_listener(on_press=None, on_release=None, on_error=None):
    """创建并启动全局键盘监听器；pynput 不可用时返回 None。

    只把按键原样交给回调，不做组合键匹配：热键匹配用 ``hotkey.start_keyboard_listener``，
    这里给的是「按下某个键就做点什么」这类用法（长截图横向模式要在按 Shift 时滚一屏）。
    返回的对象只保证有 ``stop()``；回调运行在 pynput 的监听线程里。

    键盘监听三平台都有实现，因此不查能力矩阵——pynput 是必需依赖，装不上属于环境问题，
    按导入失败记日志。
    """
    from core.logger import log_error, T

    try:
        # macOS：pynput 的监听线程一启动就读键盘布局，那一步在非主线程上会崩进程
        # （见 core/platform/pynput_macos.py）
        from core.platform.pynput_macos import install as install_macos_keyboard_fix

        install_macos_keyboard_fix()

        from pynput import keyboard

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        return listener
    except Exception as e:
        if on_error is not None:
            on_error(e)
        else:
            log_error(T("启动键盘监听失败: {e}", e=e), "Pointer")
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
    # macOS：Controller 构造时会读键盘布局，非主线程上同样会崩进程
    from core.platform.pynput_macos import install as install_macos_keyboard_fix

    install_macos_keyboard_fix()

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
