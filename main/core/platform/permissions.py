# -*- coding: utf-8 -*-
"""系统权限清单：当前平台上本程序要哪些系统授权、怎么查、怎么要、怎么跳到面板前。

「查权限」和「要权限」原先各自待在 ``hotkey``（辅助功能）与 ``capture``（屏幕录制）里，
界面想问「一共有哪些权限要用户授权」只能自己再列一遍——那是把平台差异写进 UI：加一个
平台或加一条权限，界面都得跟着改。这里把它们收成一张按平台声明的表，界面遍历表渲染；
没有这层系统门槛的平台（Windows/Linux）表是空的，页面自然不出现。

文案不在这里，这里只给稳定的 key：人类可读的标题与说明由 UI 按 key 取，翻译仍走
``tr()`` 与 app_*.xml，平台层不必背 i18n 包袱。

``trusted()`` 顺带记住本次运行**第一次**看到的状态：屏幕录制权限是进程启动时读一次的，
用户在设置页授权后 preflight 立刻变成 True，可抓屏依然只有桌面。``pending_restart()``
把这个「已授权但要重启才生效」的中间态说出来，否则用户只会觉得授权没用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.platform import capture as platform_capture
from core.platform import hotkey as platform_hotkey
from core.platform.detection import PLATFORM_NAME

# 稳定标识：界面按它取文案，日志按它点名
ACCESSIBILITY = "accessibility"
SCREEN_RECORDING = "screen_recording"

# macOS「系统设置 → 隐私与安全性」里对应面板的深链。Ventura 之后系统设置换了实现，
# 这组旧 scheme 仍会被重定向到对应面板。
_MACOS_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?"


@dataclass(frozen=True)
class Permission:
    """一条需要用户授权的系统权限。

    ``trusted`` / ``request`` 都是平台层既有实现（辅助功能在 ``hotkey``、屏幕录制在
    ``capture``）。``request`` 会弹系统对话框，必须在主线程调用。
    """

    key: str
    trusted: Callable[[], bool]
    request: Callable[[], bool]
    settings_url: str
    requires_restart: bool = False


def _macos_requirements() -> tuple[Permission, ...]:
    """现取平台层函数，而不是在 import 时把引用钉死——测试与替换实现都依赖这一点。"""
    return (
        Permission(
            key=ACCESSIBILITY,
            trusted=platform_hotkey.input_monitoring_trusted,
            request=platform_hotkey.request_input_monitoring_permission,
            settings_url=_MACOS_SETTINGS + "Privacy_Accessibility",
        ),
        Permission(
            key=SCREEN_RECORDING,
            trusted=platform_capture.screen_capture_trusted,
            request=platform_capture.request_screen_capture_permission,
            settings_url=_MACOS_SETTINGS + "Privacy_ScreenCapture",
            requires_restart=True,
        ),
    )


# 按平台声明的清单。缺失即「这个平台没有系统权限门槛」，返回空元组。
_REQUIREMENTS: dict[str, Callable[[], tuple[Permission, ...]]] = {
    "macos": _macos_requirements,
}

# 权限 key -> 本次运行第一次查到的状态（见模块开头关于 pending_restart 的说明）
_session_initial: dict[str, bool] = {}


def requirements(platform: str | None = None) -> tuple[Permission, ...]:
    """当前（或指定）平台上需要用户授权的权限清单。"""
    builder = _REQUIREMENTS.get(platform or PLATFORM_NAME)
    return builder() if builder else ()


def trusted(permission: Permission) -> bool:
    """当前状态；顺带记下本次运行第一次看到的状态。"""
    granted = bool(permission.trusted())
    _session_initial.setdefault(permission.key, granted)
    return granted


def note_startup_state() -> None:
    """记下进程启动时的权限状态（启动链调用一次）。

    只有知道「启动时是什么状态」，才能在用户本次运行里授权之后说出「已授权，但要重启才
    生效」。界面自己第一次查到的状态不算数：用户完全可能先授权、后打开设置页。
    """
    for permission in requirements():
        _session_initial.setdefault(permission.key, bool(permission.trusted()))


def pending_restart(permission: Permission) -> bool:
    """本次运行里才拿到、但因为要重启才生效而暂时用不上的权限。"""
    if not permission.requires_restart:
        return False
    if _session_initial.get(permission.key, True):
        return False
    return trusted(permission)


def request(permission: Permission) -> bool:
    """触发系统授权对话框，返回请求之后的状态。

    macOS 上系统对话框只在本程序还没登记进列表时弹；已经拒绝过就不会再弹，所以界面
    除了这个按钮，还要给一个直接打开系统设置面板的入口（``open_settings``）。
    """
    return bool(permission.request())


def open_settings(permission: Permission) -> bool:
    """打开系统设置里这条权限对应的面板。"""
    if not permission.settings_url:
        return False

    from core.platform import shell

    return shell.open_url(permission.settings_url)


def reset_session_state() -> None:
    """清掉会话态（测试用）。"""
    _session_initial.clear()
