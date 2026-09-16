# -*- coding: utf-8 -*-
"""平台层窗口操作（core/platform/window_ops）单元测试。

这些是从 6 个文件里收拢来的同名 Win32 调用，原先常量抄了 4 份、平台分派方式还各不
相同。Windows 分支在本机（macOS）用假的 user32 驱动，断言真的发出去了什么。

其中 ``set_click_through`` 的两个关键字参数对应各调用点历史上各自的做法（是否有
WS_EX_LAYERED、是否强制重算非客户区）——本仓库发行版只有 Windows，开发机是 macOS，
在没有实机验证前不合并这些差异，因此这里也把它们钉住。
"""

import pytest

from core.platform import window_ops


class _FakeUser32:
    def __init__(self, style=0, load_image=0):
        self.style = style
        self.calls = []
        self._load_image = load_image

    def GetWindowLongW(self, hwnd, index):  # noqa: N802
        self.calls.append(("GetWindowLongW", hwnd, index))
        return self.style

    def SetWindowLongW(self, hwnd, index, value):  # noqa: N802
        self.calls.append(("SetWindowLongW", hwnd, index, value))
        self.style = value

    def SetWindowPos(self, *args):  # noqa: N802
        self.calls.append(("SetWindowPos",) + args)

    def SetWindowDisplayAffinity(self, hwnd, affinity):  # noqa: N802
        self.calls.append(("SetWindowDisplayAffinity", hwnd, affinity))
        return 1

    def LoadImageW(self, *args):  # noqa: N802
        self.calls.append(("LoadImageW",) + args)
        return self._load_image

    def SendMessageW(self, *args):  # noqa: N802
        self.calls.append(("SendMessageW",) + args)

    def method_names(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def windows(monkeypatch):
    """切到 Windows 分支并换上假的 user32。"""
    fake = _FakeUser32()
    monkeypatch.setattr(window_ops, "IS_WINDOWS", True)
    monkeypatch.setattr(window_ops, "IS_MACOS", False)
    monkeypatch.setattr(window_ops, "_user32", lambda: fake)
    return fake


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(window_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(window_ops, "IS_MACOS", True)
    return window_ops


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(window_ops, "IS_WINDOWS", False)
    monkeypatch.setattr(window_ops, "IS_MACOS", False)
    return window_ops


class _FakeWindow:
    def __init__(self, hwnd=4242, visible=True):
        self._hwnd = hwnd
        self._visible = visible
        self.flags = {}
        self.shown = 0

    def winId(self):  # noqa: N802
        return self._hwnd

    def isVisible(self):  # noqa: N802
        return self._visible

    def setWindowFlag(self, flag, on):  # noqa: N802
        self.flags[flag] = on

    def show(self):
        self.shown += 1


class TestTopmost:
    def test_windows_uses_set_window_pos(self, windows):
        win = _FakeWindow()
        assert window_ops.set_topmost(win, True) is True
        assert windows.calls == [
            ("SetWindowPos", 4242, window_ops.HWND_TOPMOST, 0, 0, 0, 0,
             window_ops.SWP_TOPLEVEL_FLAGS)
        ]

    def test_windows_demote_uses_notopmost(self, windows):
        window_ops.set_topmost(_FakeWindow(), False)
        assert windows.calls[0][2] == window_ops.HWND_NOTOPMOST

    def test_swp_flags_keep_the_window_in_place_and_unfocused(self, windows):
        """切置顶不能移动窗口、不能抢焦点，否则钉图会跳一下。"""
        flags = window_ops.SWP_TOPLEVEL_FLAGS
        assert flags & window_ops.SWP_NOMOVE
        assert flags & window_ops.SWP_NOSIZE
        assert flags & window_ops.SWP_NOACTIVATE

    def test_other_platforms_fall_back_to_qt_flags(self, as_linux):
        win = _FakeWindow()
        assert window_ops.set_topmost(win, True) is False  # 表示没走原生实现
        from PySide6.QtCore import Qt

        assert win.flags[Qt.WindowType.WindowStaysOnTopHint] is True

    def test_fallback_re_shows_only_visible_windows(self, as_macos):
        """改窗口标志会让窗口隐藏，可见的才需要补一次 show。"""
        visible_win = _FakeWindow(visible=True)
        hidden_win = _FakeWindow(visible=False)
        window_ops.set_topmost(visible_win, True)
        window_ops.set_topmost(hidden_win, True)
        assert visible_win.shown == 1
        assert hidden_win.shown == 0


class TestClickThrough:
    def test_sets_transparent_bit(self, windows):
        window_ops.set_click_through(_FakeWindow(), True)
        written = [c for c in windows.calls if c[0] == "SetWindowLongW"][0]
        assert written[3] & window_ops.WS_EX_TRANSPARENT

    def test_clears_transparent_bit(self, windows):
        windows.style = window_ops.WS_EX_TRANSPARENT
        window_ops.set_click_through(_FakeWindow(), False)
        written = [c for c in windows.calls if c[0] == "SetWindowLongW"][0]
        assert not (written[3] & window_ops.WS_EX_TRANSPARENT)

    def test_layered_flag_is_opt_in(self, windows):
        """选区覆盖层是分层窗口，需要 LAYERED；绘制层与光标层原先没有置。"""
        window_ops.set_click_through(_FakeWindow(), True, layered=True)
        with_layered = [c for c in windows.calls if c[0] == "SetWindowLongW"][0][3]
        assert with_layered & window_ops.WS_EX_LAYERED

        windows.calls.clear()
        windows.style = 0  # 换一个干净的窗口样式，否则上一步置上的 LAYERED 会留着
        window_ops.set_click_through(_FakeWindow(), True, layered=False)
        without = [c for c in windows.calls if c[0] == "SetWindowLongW"][0][3]
        assert not (without & window_ops.WS_EX_LAYERED)

    def test_frame_change_is_opt_in(self, windows):
        """只有绘制层需要强制重算非客户区。"""
        window_ops.set_click_through(_FakeWindow(), True)
        assert "SetWindowPos" not in windows.method_names()

        window_ops.set_click_through(_FakeWindow(), True, force_frame_change=True)
        pos_call = [c for c in windows.calls if c[0] == "SetWindowPos"][0]
        assert pos_call[-1] & window_ops.SWP_FRAMECHANGED

    def test_macos_dispatch(self, as_macos, monkeypatch):
        called = []
        monkeypatch.setattr(
            window_ops, "_set_click_through_macos",
            lambda hwnd, enable: called.append((hwnd, enable)) or True,
        )
        assert window_ops.set_click_through(_FakeWindow(hwnd=99), True) is True
        assert called == [(99, True)]

    def test_linux_reports_failure(self, as_linux):
        """Linux 没有实现：必须返回 False，调用方要知道自己没穿透成功。"""
        assert window_ops.set_click_through(_FakeWindow(), True) is False

    def test_windows_errors_are_reported_not_raised(self, monkeypatch, windows):
        class _Boom:
            def GetWindowLongW(self, *_a):
                raise OSError("句柄失效")

        monkeypatch.setattr(window_ops, "_user32", lambda: _Boom())
        assert window_ops.set_click_through(_FakeWindow(), True) is False


class TestExcludeFromCapture:
    def test_windows_sets_the_affinity(self, windows):
        assert window_ops.set_exclude_from_capture(_FakeWindow(), True) is True
        assert windows.calls == [("SetWindowDisplayAffinity", 4242,
                                  window_ops.WDA_EXCLUDEFROMCAPTURE)]

    def test_disable_restores_none(self, windows):
        window_ops.set_exclude_from_capture(_FakeWindow(), False)
        assert windows.calls[0][2] == window_ops.WDA_NONE

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_unsupported_platforms_return_false(self, platform, monkeypatch):
        monkeypatch.setattr(window_ops, "IS_WINDOWS", False)
        monkeypatch.setattr(window_ops, "IS_MACOS", platform == "macos")
        assert window_ops.set_exclude_from_capture(_FakeWindow(), True) is False

    def test_last_error_is_minus_one_off_windows(self, as_linux):
        assert window_ops.last_error() == -1


class TestTaskbarIcon:
    def test_windows_sets_both_icon_sizes(self, windows, tmp_path, qapp):
        icon = tmp_path / "icon.svg"
        icon.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            '<rect width="32" height="32" fill="#123456"/></svg>',
            encoding="utf-8",
        )
        windows._load_image = 777

        assert window_ops.set_taskbar_icon(_FakeWindow(), str(icon)) is True
        names = windows.method_names()
        assert "LoadImageW" in names
        sends = [c for c in windows.calls if c[0] == "SendMessageW"]
        # ("SendMessageW", hwnd, WM_SETICON, icon_kind, hicon)
        # ICON_BIG(1) 与 ICON_SMALL(0) 都要设，否则任务栏与标题栏会不一致
        assert [c[3] for c in sends] == [1, 0]
        assert all(c[4] == 777 for c in sends)

    def test_missing_file_is_a_noop(self, windows, tmp_path):
        window_ops.set_taskbar_icon(_FakeWindow(), str(tmp_path / "nope.svg"))
        assert windows.calls == []

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_unsupported_platforms_do_nothing(self, platform, monkeypatch, tmp_path):
        """迁移前这里每次 showEvent 都会在非 Windows 上记一条异常日志。"""
        monkeypatch.setattr(window_ops, "IS_WINDOWS", False)
        monkeypatch.setattr(window_ops, "IS_MACOS", platform == "macos")
        icon = tmp_path / "icon.svg"
        icon.write_text("<svg/>", encoding="utf-8")
        assert window_ops.set_taskbar_icon(_FakeWindow(), str(icon)) is False


class TestRetiredModules:
    """两个旧模块已并入平台层，不该再被引用。"""

    def test_platform_utils_is_gone(self):
        import importlib

        with pytest.raises(ImportError):
            importlib.import_module("core.platform_utils")

    def test_gif_click_through_is_gone(self):
        import importlib

        with pytest.raises(ImportError):
            importlib.import_module("gif.click_through")

    def test_callers_use_the_platform_layer(self):
        import inspect

        import gif.cursor_overlay as cursor_overlay
        import gif.overlay as overlay
        import stitch.scroll_window as scroll_window
        import ui.screenshot_window as screenshot_window

        assert "window_ops" in inspect.getsource(overlay)
        assert "window_ops" in inspect.getsource(cursor_overlay)
        assert "window_ops" in inspect.getsource(scroll_window)
        assert "window_ops" in inspect.getsource(screenshot_window)

    def test_no_platform_utils_references_left_in_source(self):
        import pathlib

        offenders = []
        for path in pathlib.Path("main").rglob("*.py"):
            if path.name in ("process.py",) or path.parts[-2] == "tests":
                continue
            text = path.read_text(encoding="utf-8")
            if "platform_utils" in text:
                offenders.append(path.as_posix())
        assert offenders == []


class TestWindowOpsConstantsAreShared:
    """同名 Win32 常量原先在 4 个文件里各抄一份，现在只有平台层一处定义。

    只扫生产代码：测试里的假 win32con 需要自带常量表（模拟的就是那个模块），
    脚本也不随发行版走。
    """

    def test_no_duplicate_definitions_left(self):
        import pathlib
        import re

        pattern = re.compile(r"^\s*WS_EX_TRANSPARENT\s*=\s*0x", re.M)
        hits = []
        for path in pathlib.Path("main").rglob("*.py"):
            if set(path.parts) & {"tests", "scripts"}:
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(path.as_posix())
        assert hits == ["main/core/platform/window_ops.py"], hits


class TestHandleAcquisition:
    """句柄取不到是常见情形（窗口已销毁、控件未建好），不能变成异常。

    这是本次重构里真实踩到的一个坑：set_click_through 的 macOS 分支原先在 try
    之外调用 winId()，长截图窗口的透明区尚未建好时会把 AttributeError 抛到调用方。
    平台层的契约是「失败返回 False / None，绝不抛异常」。
    """

    def test_click_through_returns_false_instead_of_raising(self, as_macos):
        assert window_ops.set_click_through(None, True) is False

    def test_exclude_from_capture_returns_false(self, windows):
        assert window_ops.set_exclude_from_capture(None, True) is False

    def test_topmost_falls_back_to_qt_instead_of_raising(self, windows):
        """句柄拿不到时仍要能用 Qt 标志顶上——不能因为句柄问题丢掉置顶。"""
        win = _FakeWindow()

        class _NoHandle:
            def __init__(self):
                self.flags = {}
                self.shown = 0

            def winId(self):  # noqa: N802
                raise RuntimeError("控件还没建好")

            def isVisible(self):  # noqa: N802
                return True

            def setWindowFlag(self, flag, on):  # noqa: N802
                self.flags[flag] = on

            def show(self):
                self.shown += 1

        host = _NoHandle()
        assert window_ops.set_topmost(host, True) is False
        from PySide6.QtCore import Qt

        assert host.flags[Qt.WindowType.WindowStaysOnTopHint] is True
        assert win.shown == 0

    def test_taskbar_icon_returns_false(self, windows, tmp_path):
        icon = tmp_path / "i.svg"
        icon.write_text("<svg/>", encoding="utf-8")
        assert window_ops.set_taskbar_icon(None, str(icon)) is False
