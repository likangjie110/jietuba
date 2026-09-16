# -*- coding: utf-8 -*-
"""开机自启（仅 Windows）。

实现从 ``ui/welcome/page6_finish.py`` 原样搬过来——写注册表 HKCU\\Run。搬家的理由是
它当时散在欢迎向导页里，设置页的「开机自启」卡片、重置逻辑都要跨模块去调那个页面类
的私有 classmethod（``FinishPage._get_autostart``），而它其实与界面无关。

macOS（LaunchAgent plist）与 Linux（XDG autostart .desktop）尚未实现，能力矩阵里
AUTOSTART 对这两个平台是 NONE，UI 不应展示对应开关。
"""

import os
import sys

from core.platform.detection import IS_WINDOWS

_AUTOSTART_REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_APP_NAME = "Jietuba"


def app_launch_command() -> str:
    """返回开机自启要执行的命令行。

    打包后（PyInstaller frozen）就是可执行文件本身；开发模式下是
    ``python main_app.py``。注意注册表里的值带引号，用于容忍带空格的路径。
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    main_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "main_app.py")
    )
    return f'"{sys.executable}" "{main_script}"'


def is_autostart_enabled() -> bool:
    """注册表 HKCU\\Run 里是否已有本程序的启动项。"""
    if not IS_WINDOWS:
        return False

    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0, winreg.KEY_READ
        )
    except Exception:
        return False

    try:
        winreg.QueryValueEx(key, _AUTOSTART_APP_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
    finally:
        winreg.CloseKey(key)


def set_autostart(enabled: bool) -> bool:
    """启用：写入注册表 HKCU\\Run；禁用：删除对应值。返回是否写入成功。"""
    from core.logger import log_exception, log_info, T

    if not IS_WINDOWS:
        log_info(T("开机自启尚未支持当前平台，跳过"), "Startup")
        return False

    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE
        )
        try:
            if enabled:
                command = app_launch_command()
                winreg.SetValueEx(key, _AUTOSTART_APP_NAME, 0, winreg.REG_SZ, command)
                log_info(T("已写入开机自启注册表项: {command}", command=command), "Startup")
            else:
                try:
                    winreg.DeleteValue(key, _AUTOSTART_APP_NAME)
                    log_info(T("已删除开机自启注册表项"), "Startup")
                except FileNotFoundError:
                    pass  # 本来就不存在，视作已经是目标状态
        finally:
            winreg.CloseKey(key)
        return True
    except Exception as e:
        log_exception(e, T("设置开机自启"))
        return False
