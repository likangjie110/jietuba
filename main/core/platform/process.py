# -*- coding: utf-8 -*-
"""进程与运行环境：工作集回收、进程身份/终止、DPI 感知、任务栏应用标识、重开本程序。

这些是从 ``core/platform_utils.py`` 拆出来的「进程级」部分（「窗口级」部分见
``window_ops.py``）。原文件 224 行全是 Win32 调用，却**一个平台守卫都没有**——
全靠 ``except Exception`` 吞掉 ``ctypes.windll`` 的 AttributeError 来「降级」。
后果有两个：非 Windows 上每次调用都走一遍异常路径，日志里留一串说不清原因的记录；
而且「这个平台到底能不能做这件事」在代码里读不出来。

现在非 Windows 直接早退，返回值与之前完全一致（None / False / 无副作用），
但不再制造异常，能力缺失也变成可查的事实（见 ``core/platform/capabilities.py``）。
"""

import ctypes
import ntpath
import os
import pathlib
import shlex
import subprocess
import sys

from core.platform.detection import IS_MACOS, IS_WINDOWS


# ──────────────────────────────────────────────
# 内存
# ──────────────────────────────────────────────

def trim_working_set():
    """释放进程工作集，降低任务管理器/活动监视器里显示的内存占用（Windows）。

    只对 Windows 有意义：SetProcessWorkingSetSize 把页换出到后备存储后，
    任务管理器读的 Working Set 会立刻下降。macOS/Linux 上驻留内存由内核按
    page reclaim 自己决定，没有等价且安全的「主动交还」接口。
    """
    if not IS_WINDOWS:
        return
    try:
        from ctypes import wintypes

        # 必须正确声明参数/返回类型，否则 64 位系统上句柄会被截断
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessWorkingSetSize.argtypes = [
            wintypes.HANDLE, ctypes.c_ssize_t, ctypes.c_ssize_t
        ]
        kernel32.SetProcessWorkingSetSize.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        kernel32.SetProcessWorkingSetSize(handle, -1, -1)
    except Exception as e:
        from core.logger import log_exception, T

        log_exception(e, T("释放工作集"))


_trim_timer = None  # 延迟初始化，避免在 QApplication 创建前导入时崩溃


def request_trim_working_set(delay_ms: int = 1500):
    """请求释放工作集（去抖）。多次调用只执行最后一次，避免 page fault 风暴。"""
    if not IS_WINDOWS:
        return

    global _trim_timer
    if _trim_timer is None:
        from PySide6.QtCore import QTimer

        _trim_timer = QTimer()
        _trim_timer.setSingleShot(True)
        _trim_timer.timeout.connect(trim_working_set)
    _trim_timer.start(delay_ms)


# ──────────────────────────────────────────────
# 启动期补丁
# ──────────────────────────────────────────────

def patch_platform_version_lookup() -> bool:
    """让 ``platform.version()`` 不再启动 cmd.exe，返回是否打了补丁。

    标准库的 ``platform.version()`` → ``uname()`` → ``_syscmd_ver()`` 会执行
    ``subprocess("ver", shell=True)``。PyInstaller onefile 打包后，「从 %TEMP% 解压
    再启动 cmd.exe」这个组合会被杀毒软件误报，而 ``sys.getwindowsversion()`` 是纯
    Win32 API，结果完全一致且不创建子进程。

    必须在任何代码调用 ``platform.version()`` 之前执行，因此 bootstrap 在导入期就调它。
    """
    if not IS_WINDOWS:
        return False

    import platform as platform_module

    original = getattr(platform_module, "_syscmd_ver", None)
    if original is None:
        # 标准库改了内部实现，补丁失效但不该让启动失败
        return False

    def _safe_syscmd_ver(system='', release='', version='',
                         supported_platforms=('win32', 'win16', 'dos')):
        wv = sys.getwindowsversion()
        return ('Microsoft Windows', str(wv.major),
                f'{wv.major}.{wv.minor}.{wv.build}')

    platform_module._syscmd_ver = _safe_syscmd_ver
    return True


# ──────────────────────────────────────────────
# DPI 感知
# ──────────────────────────────────────────────

def set_dpi_awareness():
    """设置进程 DPI 感知（必须在 QApplication 创建之前调用）。

    优先使用 Per-Monitor DPI Aware（PMv1，SetProcessDpiAwareness(2)），
    失败时回退到旧版 SetProcessDPIAware（System DPI Aware）。

    只有 Windows 需要显式声明：Qt 在 macOS/Linux 上走各自的 DPI 机制，
    没有这个进程级开关。
    """
    if not IS_WINDOWS:
        return
    from core.logger import log_exception

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception as e:
        log_exception(e, "SetProcessDpiAwareness")
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception as e2:
            log_exception(e2, "SetProcessDPIAware")


# ──────────────────────────────────────────────
# 任务栏
# ──────────────────────────────────────────────

def set_app_user_model_id(app_id: str = "jietuba.app"):
    """设置 AppUserModelID，确保任务栏图标正确分组（必须在 QApplication 创建之前调用）。

    只有 Windows 有这个概念（任务栏按 AppUserModelID 归组窗口）。
    """
    if not IS_WINDOWS:
        return
    from core.logger import log_exception

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception as e:
        log_exception(e, "SetAppUserModelID")


# ──────────────────────────────────────────────
# 进程管理
# ──────────────────────────────────────────────

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_kernel32_ref = None


def _kernel32():
    """惰性获取 kernel32 并声明函数签名（只做一次）。

    必须显式声明 argtypes/restype：ctypes 默认按 c_int 解释返回值，
    64 位下 HANDLE 会被截断，导致 CloseHandle 失败、句柄泄漏。
    """
    global _kernel32_ref
    if _kernel32_ref is not None:
        return _kernel32_ref

    from ctypes import wintypes

    k = ctypes.windll.kernel32
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k.GetProcessTimes.restype = wintypes.BOOL
    k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL

    _kernel32_ref = k
    return k


def get_process_identity(pid: int):
    """返回进程的 (创建时间, 可执行文件名)；进程不存在、无权访问或平台不支持时返回 None。

    Windows 会回收复用 PID，所以 PID 本身不足以标识一个进程，
    (PID, 创建时间) 才是唯一标识。创建时间取自 GetProcessTimes 的
    FILETIME，拼成 64 位整数返回，可直接用于相等比较。

    非 Windows 返回 None——注意调用方（``bootstrap.ensure_single_instance``）把
    None 当作「进程已不存在」处理，因此在那些平台上单实例检查会失效。
    """
    if not IS_WINDOWS or pid <= 0:
        return None

    from ctypes import wintypes

    from core.logger import log_exception, T

    try:
        k = _kernel32()
    except Exception as e:
        log_exception(e, T("加载 kernel32"))
        return None

    # 整个查询过程都不许抛：调用方在启动早期用它拿自身身份，那里没有兜底，
    # 抛出去会直接打断启动；而返回 None 只会让"终止旧实例"这一步被跳过，是安全方向。
    handle = None
    try:
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not k.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        create_time = (creation.dwHighDateTime << 32) | creation.dwLowDateTime

        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            # 路径是 Windows 形式，用 ntpath 而不是 os.path：后者的分隔符规则
            # 取决于进程跑在哪个系统上，在 macOS 上开发调试时会拆不开
            image_name = ntpath.basename(buffer.value)
        else:
            image_name = ""

        return create_time, image_name
    except Exception as e:
        log_exception(e, T("查询进程标识"))
        return None
    finally:
        if handle:
            try:
                k.CloseHandle(handle)
            except Exception as e:
                log_exception(e, T("关闭进程句柄"))


def terminate_process_by_pid(pid: int) -> bool:
    """终止指定 PID 的进程。成功返回 True，失败或平台不支持返回 False。

    本函数不校验目标身份。Windows 会回收复用 PID，调用方必须先用
    get_process_identity() 确认目标确实是预期的那个进程，否则可能误杀无关进程。
    """
    if not IS_WINDOWS:
        return False

    from core.logger import log_exception, T

    try:
        k = _kernel32()
        handle = k.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(k.TerminateProcess(handle, 0))
        finally:
            k.CloseHandle(handle)
    except Exception as e:
        log_exception(e, T("终止进程"))
        return False


# ──────────────────────────────────────────────
# 重开本程序
# ──────────────────────────────────────────────

def _macos_app_bundle() -> str | None:
    """当前进程所属的 .app bundle；没打包或不在 bundle 里时返回 None。

    冻结版的 ``sys.executable`` 形如 ``…/Jietuba.app/Contents/MacOS/Jietuba``，
    往上第三层就是 bundle 本身。
    """
    if not (IS_MACOS and getattr(sys, "frozen", False)):
        return None
    parents = pathlib.Path(sys.executable).resolve().parents
    if len(parents) < 3:
        return None
    bundle = parents[2]
    return str(bundle) if bundle.suffix == ".app" else None


def _spawn_detached(argv: list[str], **kwargs) -> None:
    """起一个与当前进程脱钩的子进程：父进程退出后它还要继续跑。"""
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def relaunch_application() -> bool:
    """重开本程序；调用方随后应正常退出，否则会同时存在两个实例。

    macOS 打包版必须让 LaunchServices 重开 **.app**（``open -a``，和双击 Dock 图标同
    一条路），不能直接 exec 内层二进制：TCC 按 bundle 的代码签名认应用，用内层二进制
    起来的那份对不上辅助功能/录屏里已有的授权条目，用户刚勾上的权限等于没给。

    重开的时机也要绕一下：新实例带单实例检查，本进程还活着时它一启动就会自己退出，
    所以真正干活的是一个脱离父进程的 shell——等本进程 pid 消失后再 open。

    源码运行（开发机上的 ``python main_app.py``）没有 bundle，重开同一个解释器和参数即可。
    返回值只表示「已经把重开动作交出去」，不保证新实例一定起得来。
    """
    from core.logger import log_exception, log_info, T

    try:
        bundle = _macos_app_bundle()
        if bundle:
            helper = (
                f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; "
                f"open -a {shlex.quote(bundle)}"
            )
            _spawn_detached(["/bin/sh", "-c", helper])
            log_info(T("已安排退出后重新打开 {path}", path=bundle), "Process")
            return True

        _spawn_detached([sys.executable, *sys.argv], cwd=os.getcwd())
        return True
    except Exception as e:
        log_exception(e, T("重开本程序"))
        return False
