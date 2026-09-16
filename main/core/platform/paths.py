# -*- coding: utf-8 -*-
"""应用数据目录与默认保存目录。

这两个路径原先都是硬编码的 Windows 目录：应用数据固定在 ``%LOCALAPPDATA%\\Jietuba``，
环境变量取不到就回落 ``~/AppData/Local/Jietuba``。macOS/Linux 上根本没有
``%LOCALAPPDATA%``，于是日志、崩溃记录、离线翻译模型全都落进一个伪造的
``~/AppData/Local`` 目录里——能写、能跑，但没有人会去那里找它们。

Windows 的取值保持逐字不变（域环境下用户目录可能被重定向，硬拼 home 会落到
错误位置，而崩溃日志恰恰是出问题时最需要能找到的东西）。
"""

import os
from pathlib import Path

from core.platform.detection import IS_MACOS, IS_WINDOWS

APP_DIR_NAME = "Jietuba"


def app_data_dir() -> Path:
    """应用数据根目录（日志、崩溃记录、离线模型都在它下面）。"""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    # 其它 POSIX 平台走 XDG：XDG_DATA_HOME 未设置时，规范规定的默认值就是 ~/.local/share
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def log_dir() -> Path:
    """日志目录（与崩溃记录的落点保持一致）。"""
    return app_data_dir() / "Logs"


def default_screenshot_dir() -> str:
    """默认截图保存目录。

    Windows/macOS 用 ``~/Pictures/jietuba_photos``；Linux 优先读 XDG_PICTURES_DIR，
    因为发行版会把目录名本地化（中文环境通常是 ``~/图片``），硬拼 ``~/Pictures``
    会在用户家目录里凭空造出第二个图片目录。解析 user-dirs.dirs 需要额外依赖，
    取不到就回落 ``~/Pictures``——用户在设置里可以随时改。
    """
    if not IS_WINDOWS and not IS_MACOS:
        pictures = os.environ.get("XDG_PICTURES_DIR")
        if pictures:
            return os.path.join(pictures, "jietuba_photos")
    return os.path.join(os.path.expanduser("~"), "Pictures", "jietuba_photos")


def desktop_dir() -> str:
    """桌面目录（创建快捷方式、GIF 导出默认位置用）。

    同样优先 XDG_DESKTOP_DIR，理由与截图目录一致。
    """
    if not IS_WINDOWS and not IS_MACOS:
        desktop = os.environ.get("XDG_DESKTOP_DIR")
        if desktop:
            return desktop
    return os.path.join(os.path.expanduser("~"), "Desktop")
