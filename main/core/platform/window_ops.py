# -*- coding: utf-8 -*-
"""窗口级原生操作：置顶、鼠标穿透、从截图中排除、任务栏/标题栏图标。

这些是「拿到窗口句柄之后对窗口本身做的事」，与 ``window.py``（枚举窗口、判断鼠标
下面是哪个窗口）区分开。原先它们散在 6 个文件里，同名 Win32 常量抄了 4 份，平台
分派方式也各不相同（``hasattr(ctypes, "windll")``、``sys.platform``、直接调 ctypes
再用 except 兜住）。

多数操作只有 Windows 有等价 API；能力矩阵里各自登记，调用方按 ``available()`` 决定
是否展示对应功能，而不是靠试一次看有没有异常。
"""

import ctypes
import os
import tempfile

from core.platform.detection import IS_MACOS, IS_WINDOWS

# ── Win32 常量（原先在四个文件里各抄一份）────────────────
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_TOPLEVEL_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+

WM_SETICON = 0x0080
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10


def _user32():
    """取 user32（仅 Windows 可用，调用方需自行确保平台）。"""
    return ctypes.windll.user32


def _native_handle(window) -> int | None:
    """取窗口的原生句柄；取不到返回 None。

    失败是常见情形而不是异常：窗口可能已经销毁、控件还没创建，
    也可能调用方传进来的对象根本没有 winId（例如长截图窗口的透明区尚未建好）。
    平台层的契约是「失败返回 False / None，绝不抛异常」——调用方不该再各自兜一遍。
    """
    try:
        return int(window.winId())
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("获取窗口原生句柄"))
        return None


# ──────────────────────────────────────────────
# 置顶
# ──────────────────────────────────────────────

def keep_visible_when_inactive(window) -> bool:
    """让窗口在应用不在前台时也保持可见；返回是否走了原生实现。

    只在 macOS 上需要：Qt 的 Tool 窗口在那里是 NSPanel，默认 ``hidesOnDeactivate``
    为真——应用一失去前台，系统就把这类窗口藏起来。贴图（以及任何要长期留在屏幕上的
    窗口）必须把这条关掉，否则用户切到别的应用时贴图会凭空消失。Windows / Linux 的
    窗口不因失焦隐藏，这里直接返回 False（能力矩阵登记为 NONE）。
    """
    from core.platform.capabilities import Capability, available

    if not available(Capability.WINDOW_KEEP_VISIBLE_WHEN_INACTIVE):
        return False

    from core.logger import log_exception, T

    try:
        import objc

        ns_window = objc.objc_object(c_void_p=int(window.winId())).window()
        if ns_window is None:
            return False
        if ns_window.hidesOnDeactivate():
            ns_window.setHidesOnDeactivate_(False)
        return True
    except Exception as e:
        log_exception(e, T("保持窗口可见（不随失焦隐藏）"))
        return False


def _dwmapi():
    """取 dwmapi（仅 Windows 可用，调用方需自行确保平台）。"""
    return ctypes.windll.dwmapi


#: Win11 起用 DwmSetWindowAttribute 指定「系统背景材质」
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_TRANSIENTWINDOW = 3          # 亚克力：轻微模糊 + 噪点

#: Win10 1803+ 的 SetWindowCompositionAttribute 路径
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4


def _apply_acrylic_windows(hwnd: int, tint_argb: int) -> bool:
    """Windows：先试 Win11 的系统背景材质，退回 Win10 的 SetWindowCompositionAttribute。

    两条路都不可用才返回 False。Windows 路径在本机（macOS）无法真机验证，只由假 DLL
    单测断言「确实发出了什么原生调用」。
    """
    try:
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW)
        hr = _dwmapi().DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )
        if hr == 0:
            return True
    except Exception:
        hr = -1       # 老系统没有这个属性：当作 Win11 材质不可用，继续走 accent 路径

    from core.logger import log_debug, T

    log_debug(T("Win11 系统背景材质不可用（hr={hr}），改试 accent 路径", hr=hr), "WindowOps")

    try:
        class _AccentPolicy(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class _WindowCompositionAttributeData(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(_AccentPolicy)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        policy = _AccentPolicy(ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, tint_argb, 0)
        data = _WindowCompositionAttributeData(
            WCA_ACCENT_POLICY, ctypes.pointer(policy), ctypes.sizeof(policy)
        )
        if _user32().SetWindowCompositionAttribute(hwnd, ctypes.byref(data)):
            return True
        log_debug(T("Windows 的亚克力两条路都不通，退回不透明外观"), "WindowOps")
        return False
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("应用亚克力背景（Windows）"))
        return False


def _apply_acrylic_macos(window) -> bool:
    """macOS：把 NSVisualEffectView 插到内容视图**下面**，让窗口背后的内容透出来。

    必须放在 contentView 的 superview 里、相对 contentView 置于 ``NSWindowBelow``：
    Qt 自己画在 contentView 上，而子视图一律盖在父视图的绘制之上——只把它加成
    contentView 的子视图会挡住按钮。
    """
    if not IS_MACOS:
        return False

    try:
        import objc
        from AppKit import (
            NSVisualEffectBlendingModeBehindWindow, NSVisualEffectMaterialHUDWindow,
            NSVisualEffectStateActive, NSVisualEffectView,
        )

        ns_window = objc.objc_object(c_void_p=int(window.winId())).window()
        if ns_window is None:
            return False
        content = ns_window.contentView()
        frame_view = content.superview() if content is not None else None
        if frame_view is None:
            return False

        # 幂等：同一个窗口重复应用时不重复挂视图（窗口还在、视图还在就直接复用）
        existing = getattr(window, "_acrylic_effect_view", None)
        if existing is not None and existing.superview() is frame_view:
            return True

        effect = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setAutoresizingMask_(18)      # NSViewWidthSizable | NSViewHeightSizable
        frame_view.addSubview_positioned_relativeTo_(effect, -1, content)  # NSWindowBelow
        window._acrylic_effect_view = effect
        return True
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("应用亚克力背景（macOS）"))
        return False


def apply_acrylic_background(window, tint_argb: int = 0x99000000) -> bool:
    """给窗口加上「毛玻璃 / 亚克力」背景；返回是否走了原生实现。

    原生调用只在平台层做（Qt 没有可移植的背景模糊）。失败一律返回 False 并记日志，
    调用方据此退回不透明外观——「透明到看不见」比没有效果糟得多。

    - macOS：``NSVisualEffectView`` 插到内容视图之下
    - Windows：Win11 系统背景材质，退回 Win10 的 SetWindowCompositionAttribute
    - Linux：登记为 NONE（合成器模糊各桌面环境差异太大，不做）
    """
    from core.platform.capabilities import Capability, available

    from core.logger import log_debug, log_exception, T

    if not available(Capability.WINDOW_ACRYLIC):
        log_debug(T("平台不支持亚克力背景，跳过"), "WindowOps")
        return False

    # 子部件没有自己的原生窗口：``winId()`` 会返回最近的原生祖先，把模糊挂上去等于给
    # 整个截图窗口加背景（尺寸也不对）。这种情况如实返回 False，让调用方保持原外观。
    try:
        if not window.isWindow():
            log_debug(T("窗口是子部件，没有独立原生窗口，跳过亚克力背景"), "WindowOps")
            return False
    except Exception:
        return False

    hwnd = _native_handle(window)
    if hwnd is None:
        return False

    # 平台层的契约是「失败返回 False，绝不抛异常」：分支里的原生调用各自也兜了一层，
    # 这里再兜一次是给「分支被替换/未来新增分支」留的保险。
    try:
        if IS_MACOS:
            return _apply_acrylic_macos(window)
        if IS_WINDOWS:
            return _apply_acrylic_windows(hwnd, tint_argb)
    except Exception as e:
        log_exception(e, T("应用亚克力背景"))
        return False

    return False


def set_topmost(window, enabled: bool) -> bool:
    """切换窗口置顶，返回是否走了原生实现。

    Windows 用 ``SetWindowPos(HWND_TOPMOST)``：不激活窗口、不重绘，所以钉图切置顶
    时不会闪。其它平台退回 Qt 的 ``WindowStaysOnTopHint``，代价是窗口会重新显示
    一次（能力矩阵里 WINDOW_TOPMOST 对这两个平台是 DEGRADED）。
    """
    if IS_WINDOWS:
        hwnd = _native_handle(window)
        if hwnd is not None:
            _user32().SetWindowPos(
                hwnd,
                HWND_TOPMOST if enabled else HWND_NOTOPMOST,
                0, 0, 0, 0, SWP_TOPLEVEL_FLAGS,
            )
            return True
        # 拿不到句柄就退回 Qt 标志那条路（它不需要句柄）

    from PySide6.QtCore import Qt

    was_visible = window.isVisible()
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
    if was_visible:
        window.show()  # 改窗口标志会让窗口隐藏，这里补一次显示
    return False


# ──────────────────────────────────────────────
# 鼠标穿透
# ──────────────────────────────────────────────

def set_click_through(
    window,
    enabled: bool,
    *,
    layered: bool = False,
    force_frame_change: bool = False,
) -> bool:
    """让窗口不接收鼠标事件（点击落到下面的窗口上）。返回是否设置成功。

    两个关键字参数对应各调用点历史上各自的做法，不是为灵活而设的开关：

    - ``layered``：是否连同 ``WS_EX_LAYERED`` 一起置上。选区覆盖层与长截图窗口需要
      （它们是分层窗口，靠逐像素 alpha 显示），绘制层与光标层原先没有置。
    - ``force_frame_change``：改完样式后是否用 ``SetWindowPos(SWP_FRAMECHANGED)``
      强制重算非客户区。只有绘制层原先这么做。

    在把这些差异统一之前需要 Windows 上的实机验证（改变窗口样式可能影响分层窗口的
    渲染），本仓库的发行版只有 Windows 而开发机是 macOS，所以先如实保留。

    macOS 走 NSWindow 的 ``setIgnoresMouseEvents_``；Linux 没有实现，返回 False
    （能力矩阵里 WINDOW_CLICK_THROUGH 为 NONE）——调用方需要知道自己没穿透成功。
    """
    hwnd = _native_handle(window)
    if hwnd is None:
        return False

    if IS_WINDOWS:
        try:
            user32 = _user32()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                style |= WS_EX_TRANSPARENT
                if layered:
                    style |= WS_EX_LAYERED
            else:
                style &= ~WS_EX_TRANSPARENT
                if layered:
                    style |= WS_EX_LAYERED
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            if force_frame_change:
                user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
                )
            return True
        except Exception as e:
            from core.logger import log_exception, T

            log_exception(e, T("设置窗口鼠标穿透"))
            return False

    if IS_MACOS:
        return _set_click_through_macos(hwnd, enabled)

    return False


def _set_click_through_macos(hwnd: int, enable: bool) -> bool:
    """macOS：让窗口忽略鼠标事件（等价于 WS_EX_TRANSPARENT）。

    刻意不用 Qt 的 ``WindowTransparentForInput`` 标志：改窗口标志会让窗口重新显示
    一次，全屏覆盖层会闪一下，而这个属性可以就地改。
    """
    from core.logger import log_exception, T

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


# ──────────────────────────────────────────────
# 从截图中排除
# ──────────────────────────────────────────────

def set_exclude_from_capture(window, exclude: bool) -> bool:
    """让窗口不出现在屏幕截图里（屏幕上仍正常显示）。

    只有 Windows 有等价 API（``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)``，
    Win10 2004+）。其它平台返回 False，调用方需要自己保证自绘的工具栏不被拍进截图
    ——长截图就是这么用的。失败原因可以用 ``last_error()`` 取。
    """
    if not IS_WINDOWS:
        return False

    from core.logger import log_exception

    hwnd = _native_handle(window)
    if hwnd is None:
        return False

    try:
        affinity = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
        result = _user32().SetWindowDisplayAffinity(hwnd, affinity)
        return bool(result)
    except Exception as e:
        log_exception(e, "SetWindowDisplayAffinity")
        return False


def last_error() -> int:
    """取当前线程的 Win32 LastError，用于诊断上面这类调用为什么失败。"""
    if not IS_WINDOWS:
        return -1

    from core.logger import log_exception

    try:
        return ctypes.windll.kernel32.GetLastError()
    except Exception as e:
        log_exception(e, "GetLastError")
        return -1


# ──────────────────────────────────────────────
# 窗口图标
# ──────────────────────────────────────────────

def set_application_icon(icon_path: str) -> bool:
    """设置「整个应用」的图标（macOS 的 Dock）；返回是否走了原生实现。

    Windows / Linux 登记为 NONE：那边没有独立的「应用图标」，能设的就是各窗口的任务栏
    图标与托盘那一枚（分别由 ``set_taskbar_icon`` / ``ResourceManager`` 负责），所以
    这里如实返回 False 并记一条降级日志。
    """
    from core.platform.capabilities import Capability, available

    from core.logger import log_debug, log_exception, T

    if not available(Capability.APPLICATION_ICON):
        log_debug(T("当前平台没有独立的「应用图标」，跳过"), "WindowOps")
        return False

    if not icon_path or not os.path.exists(icon_path):
        return False

    try:
        if not IS_MACOS:
            return False

        from AppKit import NSApplication, NSImage

        image = NSImage.alloc().initWithContentsOfFile_(icon_path)
        if image is None:
            return False
        NSApplication.sharedApplication().setApplicationIconImage_(image)
        return True
    except Exception as e:
        log_exception(e, T("设置应用图标"))
        return False


def set_taskbar_icon(window, icon_path: str, size: int = 32) -> bool:
    """把图标设成该窗口在任务栏/标题栏上显示的那个（仅 Windows）。

    Qt 的 ``setWindowIcon`` 在 Windows 上只影响窗口图标，任务栏用的是另一套
    （``WM_SETICON``）——需要先把图片落成 .ico 再 LoadImageW。非 Windows 返回
    False：那边 Qt 的窗口图标就是任务栏图标，不需要额外处理。
    """
    if not IS_WINDOWS:
        return False

    from core.logger import log_exception, log_warning, T

    if not icon_path or not os.path.exists(icon_path):
        return False

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon, QPainter, QPixmap

        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        QIcon(icon_path).paint(painter, 0, 0, size, size)
        painter.end()

        tmp_ico = os.path.join(tempfile.gettempdir(), "jietuba_win_icon.ico")
        if not pix.save(tmp_ico, "ICO"):
            log_warning(T("任务栏图标写入 .ico 失败: {path}", path=tmp_ico), "Window")
            return False

        hwnd = _native_handle(window)
        if hwnd is None:
            return False
        user32 = _user32()
        hicon = user32.LoadImageW(None, tmp_ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if not hicon:
            return False
        user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)  # ICON_BIG
        user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)  # ICON_SMALL
        return True
    except Exception as e:
        log_exception(e, T("设置任务栏图标"))
        return False
