# -*- coding: utf-8 -*-
"""平台层剪贴板写图（core/platform/clipboard）与前台焦点（core/platform/focus）单元测试。

这两块原先都在 ``clipboard/`` 里：写图的 Win32 实现在 ``core/clipboard_utils.py``，
前台焦点与粘贴注入在 ``clipboard/controllers/clipboard_controller.py``，各自判断平台。
Windows 分支在本机（macOS）用假 win32clipboard / 假 user32 驱动。
"""

from types import SimpleNamespace

import pytest

from core.platform import clipboard, focus


# ── 写图 ──────────────────────────────────────────────

class _NullishImage:
    def isNull(self):
        return True


def _image(width=4, height=3):
    from PySide6.QtGui import QImage

    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(0xFF3366AA)
    return img


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(clipboard, "IS_WINDOWS", True)
    return clipboard


@pytest.fixture
def as_macos(monkeypatch):
    """写图只有「Windows 的 Win32 写法」与「其它平台的 Qt 回退」两条路。"""
    monkeypatch.setattr(clipboard, "IS_WINDOWS", False)
    return clipboard


class TestBackendName:
    def test_windows(self, as_windows):
        assert clipboard.backend_name() == "win32"

    def test_others(self, as_macos):
        assert clipboard.backend_name() == "qt"


class TestCopyImage:
    def test_null_image_is_rejected_without_touching_the_platform(self, as_windows, monkeypatch):
        calls = []
        monkeypatch.setattr(clipboard, "_copy_win32_with_retry", calls.append)
        assert clipboard.copy_image(_NullishImage()) is False
        assert calls == []

    def test_windows_uses_win32(self, as_windows, monkeypatch):
        calls = []
        monkeypatch.setattr(clipboard, "_copy_win32_with_retry", lambda img: calls.append("win32"))
        monkeypatch.setattr(clipboard, "_copy_qt", lambda img: calls.append("qt"))
        assert clipboard.copy_image(_image()) is True
        assert calls == ["win32"]

    def test_windows_falls_back_to_qt_on_failure(self, as_windows, monkeypatch):
        """宁可少一点保真度，也不要让用户看到「复制了但剪贴板里没有」。"""
        calls = []

        def _boom(_img):
            calls.append("win32")
            raise RuntimeError("clipboard busy")

        monkeypatch.setattr(clipboard, "_copy_win32_with_retry", _boom)
        monkeypatch.setattr(clipboard, "_copy_qt", lambda img: calls.append("qt"))

        assert clipboard.copy_image(_image()) is True
        assert calls == ["win32", "qt"]

    def test_non_windows_goes_straight_to_qt(self, as_macos, monkeypatch):
        calls = []
        monkeypatch.setattr(clipboard, "_copy_win32_with_retry",
                            lambda img: calls.append("win32"))
        monkeypatch.setattr(clipboard, "_copy_qt", lambda img: calls.append("qt"))
        assert clipboard.copy_image(_image()) is True
        assert calls == ["qt"]

    def test_failure_of_both_paths_is_reported_not_raised(self, as_windows, monkeypatch):
        def _boom(_img):
            raise RuntimeError("no clipboard")

        monkeypatch.setattr(clipboard, "_copy_win32_with_retry", _boom)
        monkeypatch.setattr(clipboard, "_copy_qt", _boom)
        assert clipboard.copy_image(_image()) is False


class _ClipboardBusy(Exception):
    """模拟剪贴板被占用时 pywin32 抛出的异常。"""


class TestClipboardBusyRetry:
    def test_busy_detection_accepts_all_exception_shapes(self):
        """pywin32 抛的异常形状不止一种，只认一种会把偶发占用当成真失败。"""
        assert clipboard._is_clipboard_busy_error(
            SimpleNamespace(hresult=clipboard._CLIPBOARD_BUSY_HRESULT))
        assert clipboard._is_clipboard_busy_error(
            _ClipboardBusy(clipboard._CLIPBOARD_BUSY_HRESULT))
        assert clipboard._is_clipboard_busy_error(
            _ClipboardBusy((clipboard._CLIPBOARD_BUSY_HRESULT, "msg")))

    def test_other_errors_are_not_treated_as_busy(self):
        assert clipboard._is_clipboard_busy_error(RuntimeError("别的错")) is False

    def test_retries_until_success(self, monkeypatch):
        attempts = []

        def _flaky(_img):
            attempts.append(1)
            if len(attempts) < 3:
                raise _ClipboardBusy(clipboard._CLIPBOARD_BUSY_HRESULT)

        monkeypatch.setattr(clipboard, "_write_win32", _flaky)
        monkeypatch.setattr(clipboard, "_WRITE_RETRY_DELAYS", (0.0, 0.0, 0.0))
        clipboard._copy_win32_with_retry(_image())
        assert len(attempts) == 3

    def test_non_busy_error_is_raised_immediately(self, monkeypatch):
        attempts = []

        def _broken(_img):
            attempts.append(1)
            raise RuntimeError("别的错")

        monkeypatch.setattr(clipboard, "_write_win32", _broken)
        monkeypatch.setattr(clipboard, "_WRITE_RETRY_DELAYS", (0.0, 0.0, 0.0))
        with pytest.raises(RuntimeError):
            clipboard._copy_win32_with_retry(_image())
        assert len(attempts) == 1

    def test_gives_up_after_the_last_delay(self, monkeypatch):
        attempts = []

        def _always_busy(_img):
            attempts.append(1)
            raise _ClipboardBusy(clipboard._CLIPBOARD_BUSY_HRESULT)

        monkeypatch.setattr(clipboard, "_write_win32", _always_busy)
        monkeypatch.setattr(clipboard, "_WRITE_RETRY_DELAYS", (0.0, 0.0))
        with pytest.raises(_ClipboardBusy):
            clipboard._copy_win32_with_retry(_image())
        assert len(attempts) == 2

    def test_zero_delay_first_attempt_is_not_slept(self):
        """第一次不该 sleep：剪贴板通常没被占用，白等一次就多一次延迟。"""
        assert clipboard._WRITE_RETRY_DELAYS[0] == 0.0


class TestWin32Payloads:
    def test_dibv5_header_is_124_bytes_and_bottom_up(self):
        img = _image(5, 3)
        data = clipboard._build_dibv5(img)
        assert len(data) == 124 + 5 * 4 * 3
        import struct

        assert struct.unpack('<I', data[0:4])[0] == 124          # bV5Size
        assert struct.unpack('<i', data[4:8])[0] == 5            # bV5Width
        assert struct.unpack('<i', data[8:12])[0] == 3           # bV5Height（正 = bottom-up）
        assert struct.unpack('<H', data[14:16])[0] == 32         # bV5BitCount
        # 布局：size(0)/width(4)/height(8)/planes(12)/bitcount(14)/compression(16)
        assert struct.unpack('<I', data[16:20])[0] == 3          # BI_BITFIELDS
        assert struct.unpack('<I', data[20:24])[0] == 5 * 4 * 3   # bV5SizeImage

    def test_dibv5_is_vertically_flipped(self):
        """DIB 是 bottom-up：第一行像素来自图片的最后一行。"""
        from PySide6.QtGui import QImage

        img = QImage(1, 2, QImage.Format.Format_ARGB32)
        img.setPixelColor(0, 0, img.pixelColor(0, 0).fromRgb(255, 0, 0))
        img.setPixelColor(0, 1, img.pixelColor(0, 1).fromRgb(0, 0, 255))

        data = clipboard._build_dibv5(img)
        first_pixel = data[124:128]
        # 蓝色分量（BGRA 的第 0 字节）对应原图最后一行
        assert first_pixel[0] == 255

    def test_png_is_a_png(self):
        data = clipboard._build_png(_image(3, 3))
        assert data[:8] == b"\x89PNG\r\n\x1a\n"


# ── 前台焦点 ──────────────────────────────────────────

class _FakeAppTarget:
    """冒充 NSRunningApplication：只关心 activateWithOptions_ 收到了什么。"""

    def __init__(self):
        self.activate_calls = []

    def activateWithOptions_(self, options):  # noqa: N802 —— 冒充 Cocoa 选择器名
        self.activate_calls.append(options)
        return True


def _fake_appkit(monkeypatch, frontmost=None, activate_constant=2):
    import sys

    module = type(sys)("AppKit")

    class _Workspace:
        @staticmethod
        def frontmostApplication():  # noqa: N802
            return frontmost

    module.NSWorkspace = SimpleNamespace(sharedWorkspace=lambda: _Workspace())
    module.NSApplicationActivateIgnoringOtherApps = activate_constant
    monkeypatch.setitem(sys.modules, "AppKit", module)
    return module


@pytest.fixture
def focus_windows(monkeypatch):
    calls = []

    fake_user32 = type("user32", (), {})()
    fake_user32.GetForegroundWindow = staticmethod(lambda: 4242)
    fake_user32.SetForegroundWindow = staticmethod(lambda hwnd: calls.append(hwnd) or True)
    monkeypatch.setattr(focus, "IS_WINDOWS", True)
    monkeypatch.setattr(focus, "IS_MACOS", False)
    monkeypatch.setattr(focus, "_user32", lambda: fake_user32)
    return calls


@pytest.fixture
def focus_macos(monkeypatch):
    monkeypatch.setattr(focus, "IS_WINDOWS", False)
    monkeypatch.setattr(focus, "IS_MACOS", True)
    return focus


@pytest.fixture
def focus_linux(monkeypatch):
    monkeypatch.setattr(focus, "IS_WINDOWS", False)
    monkeypatch.setattr(focus, "IS_MACOS", False)
    return focus


class TestCaptureForeground:
    def test_windows_reads_the_foreground_window(self, focus_windows):
        assert focus.capture_foreground() == 4242

    def test_macos_reads_the_frontmost_application(self, monkeypatch, focus_macos):
        target = _FakeAppTarget()
        _fake_appkit(monkeypatch, frontmost=target)
        assert focus.capture_foreground() is target

    def test_missing_appkit_is_not_an_error(self, monkeypatch, focus_macos):
        """取不到就当没记过——粘贴时退回「只复制不粘贴」，不该崩。"""
        import sys

        monkeypatch.setitem(sys.modules, "AppKit", None)
        assert focus.capture_foreground() is None

    def test_linux_has_no_target(self, focus_linux):
        assert focus.capture_foreground() is None

    def test_windows_failure_is_reported_not_raised(self, monkeypatch, focus_macos):
        def _boom():
            raise OSError("no window")

        monkeypatch.setattr(focus, "IS_WINDOWS", True)
        monkeypatch.setattr(focus, "IS_MACOS", False)
        monkeypatch.setattr(focus, "_user32",
                            lambda: SimpleNamespace(GetForegroundWindow=_boom))
        assert focus.capture_foreground() is None


class TestActivateForeground:
    def test_windows_sets_the_foreground_window(self, focus_windows):
        assert focus.activate_foreground(4242) is True
        assert focus_windows == [4242]

    def test_macos_activates_with_ignoring_other_apps(self, monkeypatch, focus_macos):
        constant = 2
        _fake_appkit(monkeypatch, activate_constant=constant)
        target = _FakeAppTarget()

        assert focus.activate_foreground(target) is True
        # 不带这个选项的话，我们自己的窗口可能又把焦点抢回去
        assert target.activate_calls == [constant]

    def test_no_target_does_nothing(self, focus_macos):
        assert focus.activate_foreground(None) is False

    def test_linux_is_unsupported(self, focus_linux):
        assert focus.activate_foreground(object()) is False

    def test_failure_is_reported_not_raised(self, monkeypatch, focus_windows):
        from PySide6.QtWidgets import QApplication  # noqa: F401 —— 确保 Qt 已就绪

        monkeypatch.setattr(focus, "_user32", lambda: SimpleNamespace(
            SetForegroundWindow=lambda _hwnd: (_ for _ in ()).throw(OSError())))
        assert focus.activate_foreground(1) is False
