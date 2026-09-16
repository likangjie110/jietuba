# -*- coding: utf-8 -*-
"""
剪贴板「粘贴回原程序」的跨平台路径

Windows 用 SetForegroundWindow 记住窗口、keybd_event 发 Ctrl+V；macOS 没有这两个
API，焦点记的是前台应用（NSRunningApplication）、粘贴发的是 Cmd+V。

两条分支在这里都钉住：Windows 那条通过注入假的 user32 在 macOS 上也能验证，macOS
那条通过假的 AppKit/pynput 验证。真按键不发——那会往当前应用里粘东西。
"""
import sys

import pytest

from clipboard.controllers import clipboard_controller as cc


class _FakeTarget:
    """冒充 NSRunningApplication：只关心 activateWithOptions_ 收到了什么。"""

    def __init__(self):
        self.activate_calls = []

    def activateWithOptions_(self, options):  # noqa: N802 —— 冒充 Cocoa 的选择器名
        self.activate_calls.append(options)
        return True


def _fake_appkit(monkeypatch, frontmost=None, activate_constant=2):
    """装一个假的 AppKit。"""

    class _Workspace:
        @staticmethod
        def frontmostApplication():  # noqa: N802 —— 冒充 Cocoa 的选择器名
            return frontmost

    class _NSWorkspace:
        @staticmethod
        def sharedWorkspace():  # noqa: N802
            return _Workspace()

    module = type(sys)("AppKit")
    module.NSWorkspace = _NSWorkspace
    module.NSApplicationActivateIgnoringOtherApps = activate_constant
    monkeypatch.setitem(sys.modules, "AppKit", module)
    return module


@pytest.fixture
def macos(monkeypatch):
    """把模块切到 macOS 分支。"""
    monkeypatch.setattr(cc, "_IS_MACOS", True)
    monkeypatch.setattr(cc, "_user32", None)
    return cc


class TestForegroundTarget:

    def test_macos_captures_the_frontmost_application(self, monkeypatch, macos):
        target = _FakeTarget()
        _fake_appkit(monkeypatch, frontmost=target)

        assert cc.get_foreground_window() is target

    def test_unavailable_appkit_is_not_an_error(self, monkeypatch, macos):
        """取不到就当没记过——粘贴时会退回"只复制不粘贴"，不该崩。"""
        monkeypatch.setitem(sys.modules, "AppKit", None)

        assert cc.get_foreground_window() is None

    def test_macos_activates_with_ignoring_other_apps(self, monkeypatch, macos):
        constant = 2
        _fake_appkit(monkeypatch, activate_constant=constant)
        target = _FakeTarget()

        assert cc.set_foreground_window(target) is True
        # 不带这个选项的话，我们自己的窗口可能又把焦点抢回去
        assert target.activate_calls == [constant]

    def test_macos_does_nothing_without_a_target(self, macos):
        assert cc.set_foreground_window(None) is False

    def test_windows_branch_still_drives_user32(self, monkeypatch, macos):
        """注入假 user32 就能在非 Windows 上验证 Windows 那条路。"""
        calls = []
        fake_user32 = type(sys)("user32")
        fake_user32.GetForegroundWindow = staticmethod(lambda: 4242)
        fake_user32.SetForegroundWindow = staticmethod(lambda hwnd: calls.append(hwnd) or True)
        fake_user32.keybd_event = staticmethod(lambda *args: calls.append(args))
        monkeypatch.setattr(cc, "_user32", fake_user32)

        assert cc.get_foreground_window() == 4242
        assert cc.set_foreground_window(4242) is True
        assert cc.send_ctrl_v() is True
        assert calls[0] == 4242

        # Ctrl 按下、V 按下、V 抬起、Ctrl 抬起
        key_events = calls[1:]
        assert [event[0] for event in key_events] == [
            cc.VK_CONTROL, cc.VK_V, cc.VK_V, cc.VK_CONTROL]
        assert key_events[0][2] == 0 and key_events[-1][2] == cc.KEYEVENTF_KEYUP


class TestPasteShortcut:

    @pytest.fixture
    def fake_keyboard(self, monkeypatch):
        """冒充 pynput 的键盘控制器，记录按下/释放的键。"""
        events = []

        class _Controller:
            def pressed(self, *keys):
                events.append(("down", keys))
                return _Pressed(events)

            def press(self, key):
                events.append(("press", key))

            def release(self, key):
                events.append(("release", key))

        class _Pressed:
            def __init__(self, sink):
                self._sink = sink

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                events.append(("up", ()))
                return False

        module = type(sys)("pynput.keyboard")
        module.Controller = _Controller
        module.Key = type(sys)("Key")
        module.Key.cmd = "<cmd>"
        monkeypatch.setitem(sys.modules, "pynput.keyboard", module)
        monkeypatch.setitem(sys.modules, "pynput", type(sys)("pynput"))
        return events

    def test_macos_sends_cmd_v(self, macos, fake_keyboard):
        assert cc.send_ctrl_v() is True

        kinds = [kind for kind, _ in fake_keyboard]
        assert kinds == ["down", "press", "release", "up"]
        assert fake_keyboard[0][1][0] == "<cmd>"      # 先按住 Cmd
        assert fake_keyboard[1][1] == "v"             # 再敲 v

    def test_paste_failure_is_reported_not_raised(self, macos, monkeypatch):
        class _Boom:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("没有辅助功能权限")

        module = type(sys)("pynput.keyboard")
        module.Controller = _Boom
        module.Key = type(sys)("Key")
        monkeypatch.setitem(sys.modules, "pynput.keyboard", module)

        assert cc.send_ctrl_v() is False

    def test_non_macos_without_user32_sends_nothing(self, monkeypatch, macos):
        """既不是 Windows 也不是 macOS（比如 Linux 开发机）：明确返回 False。"""
        monkeypatch.setattr(cc, "_IS_MACOS", False)

        assert cc.send_ctrl_v() is False
