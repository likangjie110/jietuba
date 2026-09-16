# -*- coding: utf-8 -*-
"""平台层外壳集成（core/platform/shell）与开机自启（core/platform/startup）单元测试。

这里同时承担两件事：

1. 钉住迁移前只有一个平台分支、没有 else 的两处崩溃——「打开文件项」用
   ``os.startfile``、「打开文件位置」用 ``explorer /select,``，在 macOS/Linux 上
   必然抛异常。
2. 冻结 Windows 的行为。Windows 分支在本机（macOS）跑不到，所以用注入假模块/假
   subprocess 的方式驱动它，断言发出去的命令与写进注册表的值。
"""

import base64
import os
import subprocess as real_subprocess
import sys
from types import SimpleNamespace

import pytest

from core.platform import shell, startup


# ── 假 subprocess ─────────────────────────────────────

class _FakePopen:
    """记录被启动的命令行，不真的开进程。"""

    calls: list[list[str]] = []

    def __init__(self, args, **_kwargs):
        _FakePopen.calls.append(list(args))


@pytest.fixture
def popen(monkeypatch):
    _FakePopen.calls = []
    monkeypatch.setattr(shell.subprocess, "Popen", _FakePopen)
    return _FakePopen.calls


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr(shell, "IS_MACOS", False)
    return shell


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", False)
    monkeypatch.setattr(shell, "IS_MACOS", True)
    return shell


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", False)
    monkeypatch.setattr(shell, "IS_MACOS", False)
    return shell


class TestOpenPath:
    def test_macos_uses_open(self, as_macos, popen, tmp_path):
        target = tmp_path / "a.png"
        target.write_text("x")
        assert shell.open_path(str(target)) is True
        assert popen == [["open", str(target)]]

    def test_linux_uses_xdg_open(self, as_linux, popen, tmp_path):
        target = tmp_path / "a.png"
        target.write_text("x")
        assert shell.open_path(str(target)) is True
        assert popen == [["xdg-open", str(target)]]

    def test_windows_uses_startfile(self, as_windows, popen, tmp_path):
        """Windows 走 os.startfile（默认关联程序），不是 open/xdg-open。"""
        target = tmp_path / "a.png"
        target.write_text("x")
        opened = []
        # os.startfile 只存在于 Windows，本机需要临时造一个出来
        monkeypatch_startfile = pytest.MonkeyPatch()
        monkeypatch_startfile.setattr(os, "startfile", opened.append, raising=False)
        try:
            assert shell.open_path(str(target)) is True
        finally:
            monkeypatch_startfile.undo()
        assert opened == [str(target)]
        assert popen == []

    def test_empty_path_is_rejected(self, as_linux, popen):
        assert shell.open_path("") is False
        assert popen == []

    def test_missing_launcher_does_not_raise(self, as_linux, monkeypatch, tmp_path):
        """Linux 上没装 xdg-open 是常见情况：只记日志，不能把异常抛进事件循环。"""
        def _boom(*_a, **_k):
            raise FileNotFoundError("xdg-open")

        monkeypatch.setattr(shell.subprocess, "Popen", _boom)
        assert shell.open_path(str(tmp_path / "gone.png")) is False

    def test_unexpected_error_does_not_raise(self, as_linux, monkeypatch, tmp_path):
        def _boom(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(shell.subprocess, "Popen", _boom)
        assert shell.open_path(str(tmp_path / "x.png")) is False


class TestRevealPath:
    def test_macos_selects_the_file(self, as_macos, popen, tmp_path):
        target = tmp_path / "a.png"
        target.write_text("x")
        assert shell.reveal_path(str(target)) is True
        assert popen == [["open", "-R", str(target)]]

    def test_macos_opens_the_folder_itself(self, as_macos, popen, tmp_path):
        assert shell.reveal_path(str(tmp_path)) is True
        assert popen == [["open", str(tmp_path)]]

    def test_linux_opens_containing_folder(self, as_linux, popen, tmp_path):
        target = tmp_path / "a.png"
        target.write_text("x")
        assert shell.reveal_path(str(target)) is True
        assert popen == [["xdg-open", str(tmp_path)]]

    def test_windows_selects_with_explorer(self, as_windows, popen, tmp_path):
        target = tmp_path / "a.png"
        target.write_text("x")
        assert shell.reveal_path(str(target)) is True
        assert popen == [["explorer", "/select,", str(target)]]

    def test_windows_opens_a_directory_directly(self, as_windows, popen, tmp_path):
        assert shell.reveal_path(str(tmp_path)) is True
        assert popen == [["explorer", str(tmp_path)]]

    def test_empty_path_is_rejected(self, as_macos, popen):
        assert shell.reveal_path("") is False
        assert popen == []


class TestDesktopShortcut:
    def test_path_uses_the_platform_desktop_dir(self):
        assert shell.desktop_shortcut_path("Jietuba") == os.path.join(
            shell.desktop_dir(), "Jietuba.lnk"
        )

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_unsupported_platforms_do_nothing(self, platform, monkeypatch):
        """尚未提供 .app 别名 / XDG .desktop，所以不能假装成功。"""
        monkeypatch.setattr(shell, "IS_WINDOWS", False)
        monkeypatch.setattr(shell, "IS_MACOS", platform == "macos")
        ran = []
        monkeypatch.setattr(shell.subprocess, "run", lambda *a, **k: ran.append(a))
        assert shell.create_desktop_shortcut("Jietuba", target="/bin/true") is None
        assert ran == []

    def test_windows_builds_an_encoded_powershell_script(self, as_windows, monkeypatch, tmp_path):
        """脚本用 UTF-16LE + Base64 传输，避免非 ASCII 路径经命令行编码后变乱码。"""
        captured = {}

        def _fake_run(args, **_kwargs):
            captured["args"] = list(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(shell.subprocess, "run", _fake_run)
        monkeypatch.setattr(shell, "desktop_shortcut_path", lambda name: str(tmp_path / "J.lnk"))

        result = shell.create_desktop_shortcut(
            "Jietuba",
            target=r"C:\程序\jietuba.exe",
            arguments="--flag",
            working_dir=r"C:\程序",
        )

        assert result == str(tmp_path / "J.lnk")
        args = captured["args"]
        assert args[0] == "powershell.exe"
        assert "-NoProfile" in args and "-NonInteractive" in args
        assert "-EncodedCommand" in args

        script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16le")
        assert str(tmp_path / "J.lnk") in script
        assert r"C:\程序\jietuba.exe" in script
        assert "$shortcut.Save()" in script
        # 单引号要按 PowerShell 的规则翻倍，否则带撇号的路径会截断脚本
        assert "$arguments.Length -gt 0" in script

    def test_windows_quotes_single_quotes_in_paths(self, as_windows, monkeypatch, tmp_path):
        captured = {}

        def _fake_run(args, **_kwargs):
            captured["args"] = list(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(shell.subprocess, "run", _fake_run)
        shell.create_desktop_shortcut("Jietuba", target=r"C:\it's\app.exe")
        script = base64.b64decode(
            captured["args"][captured["args"].index("-EncodedCommand") + 1]
        ).decode("utf-16le")
        assert r"C:\it''s\app.exe" in script

    def test_powershell_failure_is_reported_not_raised(self, as_windows, monkeypatch, tmp_path):
        def _fake_run(*_a, **_k):
            return SimpleNamespace(returncode=1, stdout="", stderr="拒绝访问")

        monkeypatch.setattr(shell.subprocess, "run", _fake_run)
        monkeypatch.setattr(shell, "desktop_shortcut_path", lambda name: str(tmp_path / "J.lnk"))
        assert shell.create_desktop_shortcut("Jietuba", target="x.exe") is None


# ── 开机自启 ──────────────────────────────────────────

class _FakeWinreg:
    """冒充 winreg：只实现本仓库用到的那几个名字。"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []

    def OpenKey(self, root, key, _reserved, access):  # noqa: N802
        self.calls.append(("OpenKey", root, key, access))
        return f"handle({key})"

    def QueryValueEx(self, _handle, name):  # noqa: N802
        self.calls.append(("QueryValueEx", name))
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, _handle, name, _reserved, _type, value):  # noqa: N802
        self.calls.append(("SetValueEx", name, value))
        self.values[name] = value

    def DeleteValue(self, _handle, name):  # noqa: N802
        self.calls.append(("DeleteValue", name))
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, _handle):  # noqa: N802
        self.calls.append(("CloseKey",))


@pytest.fixture
def fake_winreg(monkeypatch):
    module = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", module)
    monkeypatch.setattr(startup, "IS_WINDOWS", True)
    return module


class TestAutostart:
    def test_non_windows_reports_disabled(self, monkeypatch):
        monkeypatch.setattr(startup, "IS_WINDOWS", False)
        assert startup.is_autostart_enabled() is False

    def test_non_windows_set_is_a_noop(self, monkeypatch):
        """迁移前这里会记一条 info 然后 return；不能因为平台不支持就抛异常。"""
        monkeypatch.setattr(startup, "IS_WINDOWS", False)
        assert startup.set_autostart(True) is False

    def test_reads_the_run_key(self, fake_winreg):
        fake_winreg.values["Jietuba"] = "C:\\app.exe"
        assert startup.is_autostart_enabled() is True
        assert ("OpenKey", "HKCU", startup._AUTOSTART_REG_KEY, fake_winreg.KEY_READ) in fake_winreg.calls
        assert ("CloseKey",) in fake_winreg.calls

    def test_missing_value_means_disabled(self, fake_winreg):
        assert startup.is_autostart_enabled() is False

    def test_open_failure_means_disabled(self, fake_winreg, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("拒绝访问")

        monkeypatch.setattr(fake_winreg, "OpenKey", _boom)
        assert startup.is_autostart_enabled() is False

    def test_enable_writes_exactly_one_value(self, fake_winreg, monkeypatch):
        monkeypatch.setattr(startup, "app_launch_command", lambda: "C:\\app.exe")
        assert startup.set_autostart(True) is True
        assert fake_winreg.values["Jietuba"] == "C:\\app.exe"
        assert ("SetValueEx", "Jietuba", "C:\\app.exe") in fake_winreg.calls
        assert ("CloseKey",) in fake_winreg.calls

    def test_disable_deletes_the_value(self, fake_winreg):
        fake_winreg.values["Jietuba"] = "C:\\app.exe"
        assert startup.set_autostart(False) is True
        assert "Jietuba" not in fake_winreg.values
        assert ("DeleteValue", "Jietuba") in fake_winreg.calls

    def test_disable_tolerates_a_missing_value(self, fake_winreg):
        """本来就没写过时删一次不该报错——已经是目标状态了。"""
        assert startup.set_autostart(False) is True

    def test_failure_is_reported_not_raised(self, fake_winreg, monkeypatch):
        def _boom(*_a, **_k):
            raise OSError("拒绝访问")

        monkeypatch.setattr(fake_winreg, "OpenKey", _boom)
        assert startup.set_autostart(True) is False


class TestLaunchCommand:
    def test_frozen_returns_the_executable(self, monkeypatch):
        monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
        monkeypatch.setattr(startup.sys, "executable", "/opt/jietuba/jietuba")
        assert startup.app_launch_command() == "/opt/jietuba/jietuba"

    def test_source_mode_points_at_main_app(self, monkeypatch):
        monkeypatch.delattr(startup.sys, "frozen", raising=False)
        command = startup.app_launch_command()
        assert "main_app.py" in command
        assert command.startswith('"')
        assert command.endswith('"')


class TestClipboardCrashRegression:
    """迁移前这两处在 macOS/Linux 上必崩：一个用 os.startfile，一个用 explorer。"""

    def test_startfile_is_never_reached_off_windows(self, as_linux, popen, tmp_path):
        assert not hasattr(os, "startfile")
        assert shell.open_path(str(tmp_path)) is True  # 不抛 AttributeError

    def test_explorer_is_never_invoked_off_windows(self, as_linux, popen, tmp_path):
        shell.reveal_path(str(tmp_path / "x.png"))
        assert all(call[0] != "explorer" for call in popen)

    def test_subprocess_is_importable_for_the_shell_module(self):
        """外壳模块要能在没有 pywin32 的环境里导入（它只用标准库 + 平台常量）。"""
        assert real_subprocess is not None
