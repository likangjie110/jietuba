# -*- coding: utf-8 -*-
"""能力支持矩阵：把「这个平台能不能做这件事」变成一张可查询、可测试的表。

以前每个调用点各自判断平台，于是同一件事在不同地方得到不同答案：置顶在
``pin_manager`` 里按「有没有 user32」判断，智能选区在 ``window_finder`` 里按
「后端能不能导入」判断，开机自启在设置页里按 ``sys.platform == "win32"`` 判断。
结果是没法回答一个简单问题——「当前平台有哪些功能是缺的」。

这里按**能力**（而不是按平台）登记支持程度，调用方问「能不能」而不问「是不是 Windows」。
表是静态的，不需要真的去调用 API，所以任何平台都能在测试里断言另外两个平台的答案。
"""

from enum import Enum

from core.platform.detection import PLATFORM_NAME


class Support(Enum):
    """支持程度。

    三态而不是布尔：只用布尔会逼出两种谎言——置顶在 macOS/Linux 上走 Qt 标志也能用，
    但切一次会让窗口隐藏再显示（闪一下），报「支持」就掩盖了差异；点击穿透在 Linux
    上完全没有实现，报「不支持」才对。UI 隐藏只看 ``NONE``，日志与降级提示区分三态。
    """

    FULL = "full"          # 原生实现，与 Windows 行为对等
    DEGRADED = "degraded"  # 有替代实现，但有可感知的差异（闪烁、缺权限、能力子集）
    NONE = "none"          # 没有实现，调用方必须自己降级


class Capability(str, Enum):
    """需要平台实现的能力。字符串值用于日志与诊断输出。"""

    # 窗口
    WINDOW_ENUMERATION = "window_enumeration"
    WINDOW_TOPMOST = "window_topmost"
    WINDOW_CLICK_THROUGH = "window_click_through"
    WINDOW_EXCLUDE_FROM_CAPTURE = "window_exclude_from_capture"

    # 全局热键
    HOTKEY_KEYBOARD = "hotkey_keyboard"
    HOTKEY_MOUSE = "hotkey_mouse"

    # 指针与输入注入
    POINTER_POSITION = "pointer_position"
    POINTER_BUTTON_STATE = "pointer_button_state"
    SCROLL_LISTEN = "scroll_listen"
    SCROLL_INJECT = "scroll_inject"
    KEY_INJECT = "key_inject"

    # 剪贴板
    CLIPBOARD_IMAGE = "clipboard_image"
    PASTE_TO_APP = "paste_to_app"

    # 桌面集成
    AUTOSTART = "autostart"
    DESKTOP_SHORTCUT = "desktop_shortcut"

    # 进程
    PROCESS_CONTROL = "process_control"


_W = "windows"
_M = "macos"
_L = "linux"

# ── 支持矩阵 ──────────────────────────────────────────
# 每条 DEGRADED / NONE 都注明原因，它同时是「降级提示该怎么写」的依据。
_SUPPORT_TABLE: dict[Capability, dict[str, Support]] = {
    Capability.WINDOW_ENUMERATION: {
        # Windows 走 EnumWindows + DWM 去阴影，macOS 走 Quartz（天然按 Z 序、不含投影）。
        _W: Support.FULL,
        _M: Support.FULL,
        _L: Support.NONE,          # 没有窗口枚举后端
    },
    Capability.WINDOW_TOPMOST: {
        _W: Support.FULL,          # SetWindowPos 切置顶不会重绘、不激活窗口
        _M: Support.DEGRADED,      # 只能改 Qt 窗口标志，切一次会隐藏再显示
        _L: Support.DEGRADED,
    },
    Capability.WINDOW_CLICK_THROUGH: {
        _W: Support.FULL,          # WS_EX_TRANSPARENT
        _M: Support.FULL,          # NSWindow.setIgnoresMouseEvents_
        _L: Support.NONE,          # 需要 X11 SHAPE / input region，未实现
    },
    Capability.WINDOW_EXCLUDE_FROM_CAPTURE: {
        _W: Support.FULL,          # SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
        _M: Support.NONE,          # 没有等价 API，只能靠抓屏后自行裁掉
        _L: Support.NONE,
    },
    Capability.HOTKEY_KEYBOARD: {
        _W: Support.FULL,          # 系统级 RegisterHotKey，还能探测冲突
        _M: Support.DEGRADED,      # pynput 旁路监听：要辅助功能权限，且探测不出别的程序占用
        _L: Support.DEGRADED,
    },
    Capability.HOTKEY_MOUSE: {
        _W: Support.FULL,          # pynput 的 WH_MOUSE_LL 低级钩子 + WM_XBUTTON
        _M: Support.NONE,          # 侧键钩子是 pynput 的 Windows 专有接口
        _L: Support.NONE,
    },
    Capability.POINTER_POSITION: {
        _W: Support.FULL,
        _M: Support.FULL,
        _L: Support.FULL,          # QCursor.pos()，X11 下可靠
    },
    Capability.POINTER_BUTTON_STATE: {
        _W: Support.FULL,          # GetAsyncKeyState
        _M: Support.FULL,          # Quartz CGEventSourceButtonState
        _L: Support.NONE,          # 没有实现，键态恒为「未按下」
    },
    Capability.SCROLL_LISTEN: {
        _W: Support.FULL,          # pynput.mouse.Listener
        _M: Support.DEGRADED,      # 同上，需要辅助功能权限
        _L: Support.DEGRADED,
    },
    Capability.SCROLL_INJECT: {
        _W: Support.FULL,          # win32api.mouse_event(MOUSEEVENTF_HWHEEL)
        _M: Support.NONE,
        _L: Support.NONE,
    },
    Capability.KEY_INJECT: {
        _W: Support.FULL,          # keybd_event
        _M: Support.DEGRADED,      # pynput 合成 Cmd+V，需要辅助功能权限
        _L: Support.DEGRADED,
    },
    Capability.CLIPBOARD_IMAGE: {
        _W: Support.FULL,          # CF_DIBV5 + 注册 "PNG" 格式 + 剪贴板占用重试
        _M: Support.DEGRADED,      # 退回 Qt：无重试、无 "PNG" 格式注册
        _L: Support.DEGRADED,
    },
    Capability.PASTE_TO_APP: {
        # 「粘贴回原程序」= 记住前台窗口/应用 + 切回 + 注入粘贴键
        _W: Support.FULL,          # GetForegroundWindow/SetForegroundWindow + keybd_event
        _M: Support.DEGRADED,      # 记的是前台应用、发的是 Cmd+V，需要辅助功能权限
        _L: Support.NONE,          # 两半都没有实现
    },
    Capability.AUTOSTART: {
        _W: Support.FULL,          # 注册表 HKCU\Run
        _M: Support.NONE,          # 需要 LaunchAgent plist，未实现
        _L: Support.NONE,          # 需要 XDG autostart .desktop，未实现
    },
    Capability.DESKTOP_SHORTCUT: {
        _W: Support.FULL,          # PowerShell + WScript.Shell 生成 .lnk
        _M: Support.NONE,
        _L: Support.NONE,
    },
    Capability.PROCESS_CONTROL: {
        _W: Support.FULL,          # OpenProcess / GetProcessTimes / TerminateProcess
        _M: Support.NONE,          # 需要 libproc / kill 语义对齐，未实现
        _L: Support.NONE,
    },
}

# ── 各能力的后端名 ────────────────────────────────────
# 用于日志（例如「GIF 抓帧后端: mss」这类既有的后端说明）。
_BACKEND_NAMES: dict[Capability, dict[str, str]] = {
    Capability.WINDOW_ENUMERATION: {_W: "win32gui", _M: "quartz", _L: ""},
    Capability.WINDOW_TOPMOST: {_W: "win32", _M: "qt", _L: "qt"},
    Capability.WINDOW_CLICK_THROUGH: {_W: "win32", _M: "objc", _L: ""},
    Capability.WINDOW_EXCLUDE_FROM_CAPTURE: {_W: "win32", _M: "", _L: ""},
    Capability.HOTKEY_KEYBOARD: {_W: "win32", _M: "pynput", _L: "pynput"},
    Capability.HOTKEY_MOUSE: {_W: "pynput", _M: "", _L: ""},
    Capability.POINTER_POSITION: {_W: "win32", _M: "quartz", _L: "qt"},
    Capability.POINTER_BUTTON_STATE: {_W: "win32", _M: "quartz", _L: ""},
    Capability.SCROLL_LISTEN: {_W: "pynput", _M: "pynput", _L: "pynput"},
    Capability.SCROLL_INJECT: {_W: "win32", _M: "", _L: ""},
    Capability.KEY_INJECT: {_W: "win32", _M: "pynput", _L: "pynput"},
    Capability.CLIPBOARD_IMAGE: {_W: "win32", _M: "qt", _L: "qt"},
    Capability.PASTE_TO_APP: {_W: "win32", _M: "appkit+pynput", _L: ""},
    Capability.AUTOSTART: {_W: "winreg", _M: "", _L: ""},
    Capability.DESKTOP_SHORTCUT: {_W: "powershell", _M: "", _L: ""},
    Capability.PROCESS_CONTROL: {_W: "win32", _M: "", _L: ""},
}


def support(capability: Capability, platform: str | None = None) -> Support:
    """查询某个能力在当前（或指定）平台上的支持程度。"""
    table = _SUPPORT_TABLE.get(capability)
    if not table:
        return Support.NONE
    return table.get(platform or PLATFORM_NAME, Support.NONE)


def available(capability: Capability, platform: str | None = None) -> bool:
    """该能力在当前平台是否有实现（含降级实现）。

    只用来决定「要不要显示这个功能」；要区分降级程度（例如给用户提示闪烁或权限），
    用 ``support()``。
    """
    return support(capability, platform) is not Support.NONE


def backend_name(capability: Capability, platform: str | None = None) -> str:
    """该能力在当前平台由哪个后端提供；无实现时返回空串。"""
    if not available(capability, platform):
        return ""
    return _BACKEND_NAMES.get(capability, {}).get(platform or PLATFORM_NAME, "") or "native"


def unsupported(capability: Capability, platform: str | None = None) -> bool:
    """``available()`` 的反义，读起来更顺的写法（``if unsupported(X): 降级``）。"""
    return not available(capability, platform)
