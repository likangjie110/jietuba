# -*- coding: utf-8 -*-
"""桌面外壳集成：用系统默认程序打开文件/文件夹、在文件管理器里定位、创建桌面快捷方式。

迁移前这几件事在 UI 里各写一份，而且有两处**只写了 Windows 分支、没有 else**：
``clipboard/ui/windows/clipboard_window.py`` 的「打开文件项」直接用 ``os.startfile``
（非 Windows 上 ``os`` 根本没有这个名字），「打开文件位置」用
``explorer /select,``——两者在 macOS/Linux 上必抛异常，用户点一次就报一次错。
"""

import os
import subprocess

from core.platform.detection import IS_MACOS, IS_WINDOWS
from core.platform.paths import desktop_dir


def open_path(path: str) -> bool:
    """用系统默认程序打开文件或文件夹。

    成功返回 True。失败只记日志并返回 False——调用方都是「点了没反应」的交互，
    不该因为打不开一个文件就把异常抛进 Qt 事件循环。
    """
    from core.logger import log_debug, log_exception, log_warning, T

    if not path:
        return False
    try:
        if IS_WINDOWS:
            os.startfile(path)
        elif IS_MACOS:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except FileNotFoundError as e:
        # Linux 上常见：没装 xdg-open，或路径已被移动/删除
        log_warning(T("打开失败，系统没有可用的打开方式: {path}", path=path), "Shell")
        log_debug(str(e), "Shell")
        return False
    except Exception as e:
        log_exception(e, T("用默认程序打开 {path}", path=path))
        return False


def reveal_path(path: str) -> bool:
    """在文件管理器里定位到该项（能选中就选中，不能就打开所在目录）。

    Windows 用 ``explorer /select,``；macOS 用 ``open -R``；Linux 没有统一的
    「选中」接口，退化为打开所在目录。
    """
    from core.logger import log_exception, T

    if not path:
        return False
    path = os.path.normpath(path)
    try:
        if IS_WINDOWS:
            if os.path.isdir(path):
                subprocess.Popen(["explorer", path])
            else:
                subprocess.Popen(["explorer", "/select,", path])
        elif IS_MACOS:
            if os.path.isdir(path):
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["open", "-R", path])
        else:
            target = path if os.path.isdir(path) else os.path.dirname(path)
            if not target:
                return False
            subprocess.Popen(["xdg-open", target])
        return True
    except Exception as e:
        log_exception(e, T("在文件管理器中定位 {path}", path=path))
        return False


# ── 桌面快捷方式（仅 Windows）──────────────────────────
# macOS/Linux 的实现（.app 别名 / XDG .desktop 文件）尚未提供，因此能力矩阵里
# DESKTOP_SHORTCUT 对这两个平台是 NONE，UI 不应展示对应开关。

_SHORTCUT_SUFFIX = ".lnk"


def desktop_shortcut_path(name: str) -> str:
    """桌面快捷方式的完整路径。"""
    return os.path.join(desktop_dir(), f"{name}{_SHORTCUT_SUFFIX}")


def create_desktop_shortcut(
    name: str,
    target: str,
    arguments: str = "",
    working_dir: str = "",
    icon: str = "",
) -> str | None:
    """在桌面创建指向 target 的快捷方式；成功返回落盘路径，失败返回 None。

    Windows 走 PowerShell 的 WScript.Shell：脚本用 UTF-16LE + Base64 传给
    ``-EncodedCommand``，避免非 ASCII 路径经命令行编码后变成乱码。
    """
    from core.logger import log_exception, log_info, T

    if not IS_WINDOWS:
        log_info(T("桌面快捷方式尚未支持当前平台，跳过"), "Shell")
        return None

    import base64

    shortcut_path = desktop_shortcut_path(name)

    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = "\n".join([
        f"$shortcutPath = {_ps_quote(shortcut_path)}",
        f"$targetPath = {_ps_quote(target)}",
        f"$workingDirectory = {_ps_quote(working_dir)}",
        f"$arguments = {_ps_quote(arguments)}",
        f"$iconLocation = {_ps_quote(icon or target)}",
        "$shell = New-Object -ComObject WScript.Shell",
        "$shortcut = $shell.CreateShortcut($shortcutPath)",
        "$shortcut.TargetPath = $targetPath",
        "$shortcut.WorkingDirectory = $workingDirectory",
        "if ($arguments.Length -gt 0) { $shortcut.Arguments = $arguments }",
        "$shortcut.IconLocation = $iconLocation",
        "$shortcut.Save()",
    ])
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(error_text or f"PowerShell exited with code {result.returncode}")
    except Exception as e:
        log_exception(e, T("创建桌面快捷方式"))
        return None

    log_info(T("已创建桌面快捷方式: {shortcut_path}", shortcut_path=shortcut_path), "Shell")
    return shortcut_path
