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
    UI_ELEMENT_DETECTION = "ui_element_detection"
    WINDOW_TOPMOST = "window_topmost"
    WINDOW_KEEP_VISIBLE_WHEN_INACTIVE = "window_keep_visible_when_inactive"
    WINDOW_ACRYLIC = "window_acrylic"
    APPLICATION_ICON = "application_icon"
    WINDOW_CLICK_THROUGH = "window_click_through"
    WINDOW_EXCLUDE_FROM_CAPTURE = "window_exclude_from_capture"
    WINDOW_SYSTEM_MOVE = "window_system_move"
    WINDOW_FOLLOW_MOVE = "window_follow_move"

    # 全局热键
    HOTKEY_KEYBOARD = "hotkey_keyboard"
    HOTKEY_MOUSE = "hotkey_mouse"

    # 指针与输入注入
    POINTER_POSITION = "pointer_position"
    POINTER_BUTTON_STATE = "pointer_button_state"
    MODIFIER_STATE = "modifier_state"
    SCROLL_LISTEN = "scroll_listen"
    SCROLL_INJECT = "scroll_inject"
    KEY_INJECT = "key_inject"

    # 剪贴板
    CLIPBOARD_IMAGE = "clipboard_image"
    PASTE_TO_APP = "paste_to_app"

    # GIF 录制
    FRAME_CAPTURE = "frame_capture"

    # 视频录制
    VIDEO_RECORDING = "video_recording"

    # 桌面集成
    AUTOSTART = "autostart"
    DESKTOP_SHORTCUT = "desktop_shortcut"

    # 进程
    PROCESS_CONTROL = "process_control"
    FOREGROUND_APP = "foreground_app"

    # 凭据存储
    SECRET_STORE = "secret_store"


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
    Capability.UI_ELEMENT_DETECTION: {
        _W: Support.NONE,          # UIA 后端未实现（Windows 实机上才能验证），会回退到窗口级
        _M: Support.FULL,          # AXUIElementCopyElementAtPosition（需要辅助功能权限）
        _L: Support.NONE,          # 没有等价接口
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
    Capability.WINDOW_SYSTEM_MOVE: {
        # 把拖动交给窗口管理器（Qt 的 QWindow.startSystemMove）：窗口由系统搬，
        # 应用侧每帧不做任何窗口操作，拖动因此与原生窗口一样跟手
        _W: Support.FULL,          # 未在 Windows 实机验证（本机是 macOS）
        _M: Support.FULL,          # performWindowDragWithEvent（阻塞到松开鼠标）
        _L: Support.DEGRADED,      # X11 一般可以；Wayland 合成器可能拒绝，届时退回自搬
    },
    Capability.WINDOW_FOLLOW_MOVE: {
        # 「跟随窗口」：父窗口移动时系统带着子窗口一起走（AppKit 的 addChildWindow:）。
        # 贴图工具栏是独立顶层窗口，靠这条才能在不逐个搬它的前提下跟住贴图
        _W: Support.NONE,          # 未实现（Windows 的 owned window 语义未验证）
        _M: Support.FULL,
        _L: Support.NONE,
    },
    Capability.WINDOW_ACRYLIC: {
        # 窗口的毛玻璃/亚克力背景：把「背后的内容模糊」交给系统原生实现
        _W: Support.FULL,          # Win11 DwmSetWindowAttribute；退回 Win10 的 accent 路径
        _M: Support.FULL,          # NSVisualEffectView 插到内容视图之下
        _L: Support.NONE,          # 合成器模糊各桌面环境差异太大，不做
    },
    Capability.WINDOW_KEEP_VISIBLE_WHEN_INACTIVE: {
        # macOS 的 Qt Tool 窗口是 NSPanel，默认 hidesOnDeactivate=True：应用失去前台就被
        # 系统藏起来（贴图会「消失」）
        _W: Support.NONE,          # Windows 的窗口不因失焦隐藏，无需处理
        _M: Support.FULL,          # 关掉 NSWindow.hidesOnDeactivate
        _L: Support.NONE,
    },
    Capability.APPLICATION_ICON: {
        # 整个应用的图标（macOS 的 Dock）：Windows 侧没有这个概念——那边「应用图标」就是
        # 各窗口的任务栏图标与托盘那一枚，分别由 set_taskbar_icon / get_app_icon 负责
        _W: Support.NONE,
        _M: Support.FULL,          # NSApplication.setApplicationIconImage
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
    Capability.MODIFIER_STATE: {
        _W: Support.FULL,          # GetAsyncKeyState 读 Ctrl/Alt/Shift/Win
        _M: Support.FULL,          # Quartz CGEventSourceFlagsState
        _L: Support.NONE,          # 没有实现，恒为空集合（全局鼠标动作会按「无修饰键」处理）
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
    Capability.FRAME_CAPTURE: {
        # GIF 录制的抓帧：Windows 是 Rust 线程里的 GDI BitBlt，零 GIL 争用；
        # 其它平台由 Python 线程用 mss 抓帧再喂给同一个 FrameStore。
        _W: Support.FULL,
        _M: Support.DEGRADED,
        _L: Support.DEGRADED,
    },
    Capability.VIDEO_RECORDING: {
        # 视频录制：编码走 Qt 多媒体自带的 FFmpeg 后端（PySide6 轮子里就有，不需要外部
        # ffmpeg 可执行文件），抓帧用 mss，三平台是同一套代码。差异只在抓屏能否拿到画面。
        _W: Support.FULL,          # 未在 Windows 实机验证（本机是 macOS）
        _M: Support.FULL,          # 需要「屏幕录制」权限，缺失时抓到的是空桌面
        _L: Support.DEGRADED,      # Wayland 下 Qt 的抓屏与编码可能拿不到画面
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
    Capability.FOREGROUND_APP: {
        # 「当前前台是哪个程序」——全局鼠标动作的「忽略程序列表」用它判断该不该触发
        _W: Support.FULL,          # GetForegroundWindow + QueryFullProcessImageNameW
        _M: Support.FULL,          # NSWorkspace.frontmostApplication
        _L: Support.NONE,          # 没有实现（需要 X11 _NET_ACTIVE_WINDOW + WM_CLASS）
    },
    Capability.SECRET_STORE: {
        # API key 这类凭据的存放处：有了它就不必把明文写进 QSettings（macOS 的 plist、
        # Windows 的注册表都是人人可读的普通文件）
        _W: Support.FULL,          # 凭据管理器 CredReadW/CredWriteW/CredDeleteW（未在实机验证）
        _M: Support.FULL,          # Keychain generic password（Security.framework）
        _L: Support.NONE,          # 要 libsecret 依赖，不做；调用方回退明文存储
    },
}

# ── 各能力的后端名 ────────────────────────────────────
# 用于日志（例如「GIF 抓帧后端: mss」这类既有的后端说明）。
_BACKEND_NAMES: dict[Capability, dict[str, str]] = {
    Capability.WINDOW_ENUMERATION: {_W: "win32gui", _M: "quartz", _L: ""},
    Capability.UI_ELEMENT_DETECTION: {_W: "", _M: "ax", _L: ""},
    Capability.WINDOW_TOPMOST: {_W: "win32", _M: "qt", _L: "qt"},
    Capability.WINDOW_KEEP_VISIBLE_WHEN_INACTIVE: {_W: "", _M: "appkit", _L: ""},
    Capability.WINDOW_ACRYLIC: {_W: "dwm", _M: "appkit", _L: ""},
    Capability.APPLICATION_ICON: {_W: "", _M: "appkit", _L: ""},
    Capability.WINDOW_CLICK_THROUGH: {_W: "win32", _M: "objc", _L: ""},
    Capability.WINDOW_EXCLUDE_FROM_CAPTURE: {_W: "win32", _M: "", _L: ""},
    Capability.WINDOW_SYSTEM_MOVE: {_W: "qt", _M: "appkit", _L: "qt"},
    Capability.WINDOW_FOLLOW_MOVE: {_W: "", _M: "appkit", _L: ""},
    Capability.HOTKEY_KEYBOARD: {_W: "win32", _M: "pynput", _L: "pynput"},
    Capability.HOTKEY_MOUSE: {_W: "pynput", _M: "", _L: ""},
    Capability.POINTER_POSITION: {_W: "win32", _M: "quartz", _L: "qt"},
    Capability.POINTER_BUTTON_STATE: {_W: "win32", _M: "quartz", _L: ""},
    Capability.SCROLL_LISTEN: {_W: "pynput", _M: "pynput", _L: "pynput"},
    Capability.MODIFIER_STATE: {_W: "win32", _M: "quartz", _L: ""},
    Capability.SCROLL_INJECT: {_W: "win32", _M: "", _L: ""},
    Capability.KEY_INJECT: {_W: "win32", _M: "pynput", _L: "pynput"},
    Capability.CLIPBOARD_IMAGE: {_W: "win32", _M: "qt", _L: "qt"},
    Capability.PASTE_TO_APP: {_W: "win32", _M: "appkit+pynput", _L: ""},
    Capability.FRAME_CAPTURE: {_W: "gdi", _M: "mss", _L: "mss"},
    Capability.VIDEO_RECORDING: {_W: "qtmultimedia", _M: "qtmultimedia", _L: "qtmultimedia"},
    Capability.AUTOSTART: {_W: "winreg", _M: "", _L: ""},
    Capability.DESKTOP_SHORTCUT: {_W: "powershell", _M: "", _L: ""},
    Capability.PROCESS_CONTROL: {_W: "win32", _M: "", _L: ""},
    Capability.FOREGROUND_APP: {_W: "win32", _M: "appkit", _L: ""},
    Capability.SECRET_STORE: {_W: "credential-manager", _M: "keychain", _L: ""},
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
