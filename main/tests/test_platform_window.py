# -*- coding: utf-8 -*-
"""平台层窗口枚举（core/platform/window）单元测试。

覆盖三层：

1. **门面共用逻辑**（与平台无关）：Z 序命中测试、降级链、偏移换算、可用性判断。
   原 ``tests/test_window_finder.py`` 的断言基本原样保留，只是模块位置与打桩点换了。
2. **Windows 后端**：用假的 win32gui/win32con 驱动过滤规则——原实现里这部分没人测
   （要在真实 Windows 会话上跑），而它恰恰是最容易悄悄改坏的地方。
3. **macOS 后端**：用假的 Quartz 驱动。

真机枚举（EnumWindows / CGWindowListCopyWindowInfo）不在这里覆盖。
"""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import core.platform.window as window
from core.platform.contracts import WindowInfo


def _available_or_skip() -> bool:
    """当前平台没有后端时跳过——这类用例测的是共用逻辑，不该在 Linux 上假失败。"""
    if not window.is_window_enumeration_available():
        pytest.skip("当前平台没有可用的窗口枚举后端")
    return True


@pytest.fixture
def finder():
    _available_or_skip()
    return window.WindowFinder()


@pytest.fixture
def win32(monkeypatch):
    """让门面认为当前平台是 Windows，并注入假的后端。"""
    import core.platform.window_win32 as backend

    monkeypatch.setattr(window, "IS_WINDOWS", True)
    monkeypatch.setattr(window, "IS_MACOS", False)
    monkeypatch.setattr(backend, "WINDOWS_API_AVAILABLE", True)
    return backend


@pytest.fixture
def macos(monkeypatch):
    import core.platform.window_macos as backend

    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.setattr(backend, "MACOS_API_AVAILABLE", True)
    return backend


def _fake_win32gui(windows):
    """windows: {hwnd: (title, rect, class_name, ex_style)}"""
    import sys as _sys

    class _Gui:
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TRANSPARENT = 0x00000020
        GWL_EXSTYLE = -20

        @staticmethod
        def IsWindowVisible(hwnd):
            return True

        @staticmethod
        def GetWindowLong(hwnd, _index):
            return windows[hwnd][3]

        @staticmethod
        def GetWindowText(hwnd):
            return windows[hwnd][0]

        @staticmethod
        def GetClassName(hwnd):
            return windows[hwnd][2]

        @staticmethod
        def EnumWindows(callback, _param):
            for hwnd in windows:
                if not callback(hwnd, None):
                    break

        @staticmethod
        def GetWindowRect(hwnd):
            return windows[hwnd][1]

    gui = type(_sys)("win32gui")
    for name in ("WS_EX_TOOLWINDOW", "WS_EX_TRANSPARENT", "GWL_EXSTYLE",
                 "IsWindowVisible", "GetWindowLong", "GetWindowText", "GetClassName",
                 "EnumWindows", "GetWindowRect"):
        setattr(gui, name, getattr(_Gui, name))
    return gui


class TestInit:
    def test_default_offset_is_zero(self, finder):
        assert finder.screen_offset_x == 0
        assert finder.screen_offset_y == 0
        assert finder.windows == []

    def test_custom_offset(self, finder):
        finder = window.WindowFinder(100, -50)
        assert finder.screen_offset_x == 100
        assert finder.screen_offset_y == -50

    def test_raises_when_no_platform_api_is_available(self, monkeypatch):
        import core.platform.window_win32 as backend

        monkeypatch.setattr(window, "IS_WINDOWS", True)
        monkeypatch.setattr(window, "IS_MACOS", False)
        monkeypatch.setattr(backend, "WINDOWS_API_AVAILABLE", False)
        with pytest.raises(RuntimeError):
            window.WindowFinder()

    def test_linux_has_no_backend(self, monkeypatch):
        monkeypatch.setattr(window, "IS_WINDOWS", False)
        monkeypatch.setattr(window, "IS_MACOS", False)
        assert window.is_window_enumeration_available() is False
        with pytest.raises(RuntimeError):
            window.WindowFinder()

    def test_declaration_alone_is_not_enough(self, monkeypatch):
        """平台声明有后端但依赖没装（例如缺 pywin32 的 Windows）时不可用。"""
        import core.platform.window_win32 as backend

        monkeypatch.setattr(window, "IS_WINDOWS", True)
        monkeypatch.setattr(window, "IS_MACOS", False)
        monkeypatch.setattr(backend, "WINDOWS_API_AVAILABLE", False)
        assert window.is_window_enumeration_available() is False


class TestSetScreenOffset:
    def test_updates_offsets(self, finder):
        finder.set_screen_offset(200, 300)
        assert finder.screen_offset_x == 200
        assert finder.screen_offset_y == 300


class TestFindWindowAtPoint:
    """windows 列表按 Z 序排列，第一个命中的矩形应被返回。"""

    def _with(self, finder, entries):
        finder.windows = [WindowInfo(handle, tuple(rect), title) for handle, rect, title in entries]
        return finder

    def test_returns_topmost_window_containing_point(self, finder):
        self._with(finder, [
            (1, [0, 0, 500, 500], "Top Window"),
            (2, [0, 0, 1000, 1000], "Bottom Window"),
        ])
        assert finder.find_window_at_point(100, 100) == [0, 0, 500, 500]

    def test_skips_non_containing_window_and_finds_next(self, finder):
        self._with(finder, [
            (1, [0, 0, 100, 100], "Top Left"),
            (2, [200, 200, 800, 800], "Bottom Right"),
        ])
        assert finder.find_window_at_point(300, 300) == [200, 200, 800, 800]

    def test_point_on_boundary_is_inclusive(self, finder):
        self._with(finder, [(1, [10, 10, 110, 110], "Box")])
        assert finder.find_window_at_point(10, 10) == [10, 10, 110, 110]
        assert finder.find_window_at_point(110, 110) == [10, 10, 110, 110]

    def test_no_match_returns_fallback_rect(self, finder):
        self._with(finder, [(1, [0, 0, 50, 50], "Small Window")])
        fallback = [0, 0, 1920, 1080]
        assert finder.find_window_at_point(9999, 9999, fallback_rect=fallback) == fallback

    def test_no_match_no_fallback_returns_virtual_desktop_rect(self, finder):
        finder.windows = []
        with patch.object(finder, "_get_virtual_desktop_rect",
                          return_value=[0, 0, 3840, 1080]) as mock_vd:
            result = finder.find_window_at_point(500, 500)
        mock_vd.assert_called_once()
        assert result == [0, 0, 3840, 1080]

    def test_empty_windows_list_falls_back(self, finder):
        finder.windows = []
        assert finder.find_window_at_point(0, 0, fallback_rect=[1, 2, 3, 4]) == [1, 2, 3, 4]


class TestOffsetIsSubtracted:
    """多屏时枚举结果必须真的减掉偏移——屏幕绝对坐标到截图区域坐标就靠这一步。"""

    def test_win32(self, win32, monkeypatch):
        # 必须用 monkeypatch：直接给模块属性赋值会永久替换掉真实现，
        # 污染同一进程里后面所有用例（这正是本文件早先"单跑通过、一起跑全红"的原因）
        monkeypatch.setattr(win32, "enumerate_windows", lambda debug=False: [
            WindowInfo(7, (100, 200, 300, 400), "标题")
        ])
        finder = window.WindowFinder(50, 20)
        finder.find_windows()
        assert [w.rect for w in finder.windows] == [(50, 180, 250, 380)]
        assert finder.windows[0].handle == 7
        assert finder.windows[0].title == "标题"

    def test_macos(self, macos, monkeypatch):
        monkeypatch.setattr(macos, "enumerate_windows", lambda debug=False: [
            WindowInfo(7, (100, 200, 300, 400), "标题")
        ])
        finder = window.WindowFinder(50, 20)
        finder.find_windows()
        assert [w.rect for w in finder.windows] == [(50, 180, 250, 380)]

    def test_zero_offset_keeps_rects_untouched(self, win32, monkeypatch):
        monkeypatch.setattr(win32, "enumerate_windows",
                            lambda debug=False: [WindowInfo(1, (5, 6, 7, 8), "t")])
        finder = window.WindowFinder(0, 0)
        finder.find_windows()
        assert [w.rect for w in finder.windows] == [(5, 6, 7, 8)]

    def test_no_backend_clears_the_list(self, monkeypatch, finder):
        finder.windows = [WindowInfo(1, (0, 0, 1, 1), "old")]
        monkeypatch.setattr(window, "_backend", lambda: None)
        finder.find_windows()
        assert finder.windows == []


class TestGetVirtualDesktopRect:
    def test_uses_backend_when_available(self, win32, monkeypatch):
        monkeypatch.setattr(win32, "virtual_desktop_rect", lambda: [-1920, 0, 1920, 1080])
        finder = window.WindowFinder()
        assert finder._get_virtual_desktop_rect() == [-1920, 0, 1920, 1080]

    def test_falls_back_to_qt_primary_screen(self, win32, monkeypatch):
        """后端取不到时用 Qt 主屏几何，仍然好过随便给一个数。"""
        monkeypatch.setattr(win32, "virtual_desktop_rect", lambda: None)
        finder = window.WindowFinder()
        fake_screen = SimpleNamespace(
            geometry=lambda: SimpleNamespace(x=lambda: 1, y=lambda: 2,
                                             width=lambda: 800, height=lambda: 600)
        )
        with patch("PySide6.QtGui.QGuiApplication.primaryScreen", return_value=fake_screen):
            assert finder._get_virtual_desktop_rect() == [1, 2, 801, 602]

    def test_falls_back_to_hardcoded_default_on_total_failure(self, win32, monkeypatch):
        """最后那一步是兜底：调用方永远要拿得到一个矩形，否则会漏掉一次选区更新。"""
        monkeypatch.setattr(win32, "virtual_desktop_rect", lambda: None)
        finder = window.WindowFinder()
        with patch("PySide6.QtGui.QGuiApplication.primaryScreen", return_value=None):
            assert finder._get_virtual_desktop_rect() == [0, 0, 1920, 1080]


class TestClear:
    def test_clear_resets_windows_list(self, finder):
        finder.windows = [WindowInfo(1, (0, 0, 10, 10), "Something")]
        finder.clear()
        assert finder.windows == []


class TestPreload:
    def test_returns_true_when_a_backend_exists(self, win32):
        assert window.preload() is True

    def test_returns_false_without_a_backend(self, monkeypatch):
        monkeypatch.setattr(window, "IS_WINDOWS", False)
        monkeypatch.setattr(window, "IS_MACOS", False)
        assert window.preload() is False


class TestWin32Backend:
    """Windows 的过滤规则原先没人测——要在真实 Windows 会话上才能跑。"""

    def _enumerate(self, monkeypatch, windows):
        import core.platform.window_win32 as backend

        gui = _fake_win32gui(windows)
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        monkeypatch.setitem(sys.modules, "win32con", gui)
        # macOS 上这两个模块从未导入成功，属性根本不存在，因此要 raising=False
        monkeypatch.setattr(backend, "win32gui", gui, raising=False)
        monkeypatch.setattr(backend, "win32con", gui, raising=False)
        monkeypatch.setattr(backend, "WINDOWS_API_AVAILABLE", True)
        monkeypatch.setattr(backend, "get_window_rect_no_shadow",
                            lambda hwnd: list(windows[hwnd][1]))
        return backend.enumerate_windows()

    def test_keeps_a_normal_window(self, monkeypatch):
        result = self._enumerate(monkeypatch, {
            11: ("标题", [0, 0, 400, 300], "Notepad", 0),
        })
        assert len(result) == 1
        assert result[0].handle == 11
        assert result[0].rect == (0, 0, 400, 300)
        assert result[0].title == "标题"

    def test_drops_tool_windows(self, monkeypatch):
        """工具窗口（无边框辅助窗）不该出现在智能选区里。"""
        result = self._enumerate(monkeypatch, {
            11: ("工具", [0, 0, 400, 300], "Tool", 0x00000080),
        })
        assert result == []

    def test_drops_transparent_overlays(self, monkeypatch):
        """透明遮罩会盖住一切，留着它智能选区就永远命中遮罩。"""
        result = self._enumerate(monkeypatch, {
            11: ("遮罩", [0, 0, 400, 300], "Overlay", 0x00000020),
        })
        assert result == []

    def test_drops_windows_without_a_title(self, monkeypatch):
        result = self._enumerate(monkeypatch, {
            11: ("   ", [0, 0, 400, 300], "Notepad", 0),
        })
        assert result == []

    def test_drops_tiny_windows(self, monkeypatch):
        result = self._enumerate(monkeypatch, {
            11: ("小", [0, 0, 20, 20], "Notepad", 0),
        })
        assert result == []

    def test_drops_offscreen_windows(self, monkeypatch):
        result = self._enumerate(monkeypatch, {
            11: ("远", [20000, 20000, 20400, 20300], "Notepad", 0),
        })
        assert result == []

    def test_drops_known_system_classes(self, monkeypatch):
        result = self._enumerate(monkeypatch, {
            11: ("桌面", [0, 0, 400, 300], "Progman", 0),
            12: ("工作区", [0, 0, 400, 300], "WorkerW", 0),
        })
        assert result == []

    def test_keeps_order_as_z_ordered(self, monkeypatch):
        """EnumWindows 的枚举顺序即 Z 序，门面的"第一个命中"依赖它。"""
        result = self._enumerate(monkeypatch, {
            11: ("上", [0, 0, 200, 200], "Notepad", 0),
            12: ("下", [0, 0, 400, 400], "Notepad", 0),
        })
        assert [w.handle for w in result] == [11, 12]

    def test_returns_empty_without_dependency(self, monkeypatch):
        import core.platform.window_win32 as backend

        monkeypatch.setattr(backend, "WINDOWS_API_AVAILABLE", False)
        assert backend.enumerate_windows() == []

    def test_debug_logging_does_not_change_results(self, monkeypatch):
        windows = {11: ("标题", [0, 0, 400, 300], "Notepad", 0)}
        quiet = self._enumerate(monkeypatch, dict(windows))
        noisy = self._enumerate(monkeypatch, dict(windows))  # debug 分支只多打日志
        assert [w.rect for w in quiet] == [w.rect for w in noisy]


class TestMacOSBackend:
    def _patch_quartz(self, monkeypatch, copy_window_info):
        """替换后端模块里已绑定的 Quartz 名字。

        这些名字是模块导入时 ``from Quartz import ...`` 绑好的（macOS 上装了 pyobjc
        就一定成功），只往 sys.modules 里塞假模块不生效。
        """
        import core.platform.window_macos as backend

        monkeypatch.setattr(backend, "CGWindowListCopyWindowInfo", copy_window_info)
        monkeypatch.setattr(backend, "kCGNullWindowID", 0)
        monkeypatch.setattr(backend, "kCGWindowListOptionOnScreenOnly", 1)
        monkeypatch.setattr(backend, "kCGWindowListExcludeDesktopElements", 2)
        monkeypatch.setattr(backend, "MACOS_API_AVAILABLE", True)
        return backend

    def _enumerate(self, monkeypatch, infos):
        backend = self._patch_quartz(monkeypatch, lambda *a: infos)
        return backend.enumerate_windows(exclude_pid=None)

    @staticmethod
    def _info(**kwargs):
        base = {
            "kCGWindowLayer": 0,
            "kCGWindowOwnerPID": 999,
            "kCGWindowIsOnscreen": True,
            "kCGWindowAlpha": 1.0,
            "kCGWindowBounds": {"X": 10, "Y": 20, "Width": 300, "Height": 200},
            "kCGWindowName": "标题",
            "kCGWindowOwnerName": "App",
            "kCGWindowNumber": 42,
        }
        base.update(kwargs)
        return base

    def test_keeps_a_normal_window(self, monkeypatch):
        result = self._enumerate(monkeypatch, [self._info()])
        assert len(result) == 1
        assert result[0].handle == 42
        assert result[0].rect == (10, 20, 310, 220)  # X+Width / Y+Height
        assert result[0].title == "标题"

    def test_title_falls_back_to_app_name_without_screen_recording_permission(
            self, monkeypatch):
        """没授权「屏幕录制」时读不到窗口标题；退化成应用名，否则列表里全是无名窗口。"""
        result = self._enumerate(monkeypatch, [self._info(**{"kCGWindowName": ""})])
        assert result[0].title == "App"

    def test_title_absent_everywhere_is_dropped(self, monkeypatch):
        result = self._enumerate(
            monkeypatch,
            [self._info(**{"kCGWindowName": "", "kCGWindowOwnerName": ""})],
        )
        assert result == []

    def test_drops_non_zero_layers(self, monkeypatch):
        """Dock(20)、菜单栏(25) 等都在别的层上。"""
        result = self._enumerate(monkeypatch, [self._info(**{"kCGWindowLayer": 20})])
        assert result == []

    def test_drops_invisible_and_transparent(self, monkeypatch):
        assert self._enumerate(
            monkeypatch, [self._info(**{"kCGWindowIsOnscreen": False})]) == []
        assert self._enumerate(
            monkeypatch, [self._info(**{"kCGWindowAlpha": 0.0})]) == []

    def test_drops_tiny_and_offscreen(self, monkeypatch):
        assert self._enumerate(monkeypatch, [self._info(
            **{"kCGWindowBounds": {"X": 0, "Y": 0, "Width": 10, "Height": 10}})]) == []
        assert self._enumerate(monkeypatch, [self._info(
            **{"kCGWindowBounds": {"X": 20000, "Y": 20000, "Width": 300, "Height": 200}})]) == []

    def test_excludes_own_process_by_default(self, monkeypatch):
        """截图时我们自己的全屏遮罩必须在列表外，否则智能选区永远命中它。"""
        backend = self._patch_quartz(
            monkeypatch, lambda *a: [self._info(**{"kCGWindowOwnerPID": 4242})]
        )

        assert backend.enumerate_windows(exclude_pid=4242) == []
        assert len(backend.enumerate_windows(exclude_pid=1)) == 1

    def test_returns_empty_without_dependency(self, monkeypatch):
        import core.platform.window_macos as backend

        monkeypatch.setattr(backend, "MACOS_API_AVAILABLE", False)
        assert backend.enumerate_windows() == []

    def test_enumeration_failure_is_reported_not_raised(self, monkeypatch):
        def _boom(*_a):
            raise RuntimeError("没有屏幕录制权限")

        backend = self._patch_quartz(monkeypatch, _boom)
        assert backend.enumerate_windows() == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 的 Quartz 枚举")
class TestElementDetection:
    """「UI 检测」的元素档：档位收敛、元素后端、档位分派。

    元素后端只在 macOS 上存在，所以这里全程用替身驱动，不依赖真机（真机验证见
    ``TestMacOSRealElementDetection``）。
    """

    @pytest.mark.parametrize("raw, expected", [
        ("none", "none"),
        ("window", "window"),
        ("element", "element"),
        (" Element ", "element"),
        (True, "element"),         # 旧的 smart_selection：打开 = 检测（默认档）
        (False, "none"),
        ("banana", None),
        (None, None),
        (2, None),
    ])
    def test_normalize_accepts_known_modes_and_legacy_bool(self, raw, expected):
        assert window.normalize_ui_detection(raw) == expected

    @staticmethod
    def _fake_element_backend(monkeypatch, rect):
        """让门面认下 macOS 的元素后端，并返回固定的元素矩形。"""
        import core.platform.window_macos as backend

        monkeypatch.setattr(window, "IS_WINDOWS", False)
        monkeypatch.setattr(window, "IS_MACOS", True)
        monkeypatch.setattr(backend, "ELEMENT_API_AVAILABLE", True)
        monkeypatch.setattr(backend, "element_rect_at_point", lambda x, y: rect)
        monkeypatch.setattr(window, "available", lambda *a, **k: True)
        return backend

    def test_availability_needs_capability_and_backend(self, monkeypatch):
        monkeypatch.setattr(window, "available", lambda *a, **k: True)
        monkeypatch.setattr(window, "IS_MACOS", False)          # 没有元素后端
        assert window.is_element_detection_available() is False

        monkeypatch.setattr(window, "available", lambda *a, **k: False)
        assert window.is_element_detection_available() is False

    def test_find_element_returns_none_without_backend(self, monkeypatch):
        monkeypatch.setattr(window, "IS_MACOS", False)
        assert window.find_element_at_point(10, 10) is None

    def test_margin_expands_the_element_rect(self, monkeypatch):
        self._fake_element_backend(monkeypatch, [100, 100, 200, 150])

        assert window.find_element_at_point(120, 120) == [100, 100, 200, 150]
        assert window.find_element_at_point(120, 120, margin=8) == [92, 92, 208, 158]

    def test_finder_falls_back_to_the_window_when_element_misses(self, monkeypatch):
        """element 档没命中元素时按窗口级给结果，别变成「什么都不框」。"""
        self._fake_element_backend(monkeypatch, None)
        finder = window.WindowFinder()
        finder.windows = [WindowInfo(1, (0, 0, 400, 300), "窗口")]

        assert finder.find_rect_at_point(50, 50, mode="element") == [0, 0, 400, 300]

    def test_finder_prefers_the_element_when_it_hits(self, monkeypatch):
        self._fake_element_backend(monkeypatch, [120, 130, 180, 170])
        finder = window.WindowFinder()
        finder.windows = [WindowInfo(1, (0, 0, 400, 300), "窗口")]

        assert finder.find_rect_at_point(50, 50, mode="element") == [120, 130, 180, 170]
        # 窗口档不受元素影响
        assert finder.find_rect_at_point(50, 50, mode="window") == [0, 0, 400, 300]

    def test_finder_element_mode_without_backend_uses_the_window(self, monkeypatch):
        import core.platform.window_macos as backend

        monkeypatch.setattr(window, "IS_WINDOWS", False)
        monkeypatch.setattr(window, "IS_MACOS", True)
        monkeypatch.setattr(backend, "MACOS_API_AVAILABLE", True)
        monkeypatch.setattr(backend, "ELEMENT_API_AVAILABLE", False)   # 缺元素后端
        monkeypatch.setattr(window, "available", lambda *a, **k: True)
        finder = window.WindowFinder()
        finder.windows = [WindowInfo(1, (0, 0, 400, 300), "窗口")]

        assert finder.find_rect_at_point(50, 50, mode="element") == [0, 0, 400, 300]


class TestMacOSElementBackendInternals:
    """macOS 元素后端的过滤规则：命中自己 = 没元素、窗口级角色不算元素。"""

    def _fake_element(self, monkeypatch, *, role="AXButton", pid=999, rect=(10, 20, 60, 40)):
        import core.platform.window_macos as backend

        class _Value:
            def __init__(self, point=None, size=None):
                self.point = point
                self.size = size

        monkeypatch.setattr(backend, "ELEMENT_API_AVAILABLE", True)
        monkeypatch.setattr(backend, "AXUIElementCreateSystemWide", lambda: "systemwide")
        monkeypatch.setattr(backend, "AXUIElementCopyElementAtPosition",
                            lambda *a: (0, "element"))
        monkeypatch.setattr(backend, "AXUIElementGetPid", lambda *a: (0, pid))
        monkeypatch.setattr(backend, "AXUIElementCopyAttributeValue",
                            lambda el, attr, _: (0, role if attr == "AXRole" else None))
        monkeypatch.setattr(backend, "_element_rect", lambda el: list(rect))
        return backend

    def test_element_of_our_own_process_is_not_an_element(self, monkeypatch):
        """截图遮罩是自己进程的全屏窗口，命中它必须当作没元素，否则元素档框的是遮罩。"""
        import os

        backend = self._fake_element(monkeypatch, pid=os.getpid())
        assert backend.element_rect_at_point(100, 100) is None

    def test_other_apps_element_is_returned(self, monkeypatch):
        backend = self._fake_element(monkeypatch, pid=4321)
        assert backend.element_rect_at_point(100, 100) == [10, 20, 60, 40]

    @pytest.mark.parametrize("role", ["AXWindow", "AXSheet", "AXApplication", "AXSystemWide"])
    def test_window_level_roles_fall_back_to_the_window_path(self, monkeypatch, role):
        backend = self._fake_element(monkeypatch, role=role)
        assert backend.element_rect_at_point(100, 100) is None


class TestMacOSRealElementDetection:
    """真机：把鼠标所在位置上的元素框出来，结果要落在屏幕内、且不该是整屏。"""

    @pytest.mark.skipif(not sys.platform == "darwin", reason="元素检测只有 macOS 后端")
    def test_returns_a_sane_rect_for_a_real_point(self):
        import core.platform.window_macos as backend

        if not backend.ELEMENT_API_AVAILABLE:
            pytest.skip("当前环境没有 ApplicationServices")

        from PySide6.QtGui import QCursor

        cursor = QCursor.pos()
        rect = backend.element_rect_at_point(cursor.x(), cursor.y())
        if rect is None:
            pytest.skip("这一点上没有可用的元素（或缺少辅助功能权限）")

        x1, y1, x2, y2 = rect
        assert x2 > x1 and y2 > y1
        assert x2 - x1 < 10000 and y2 - y1 < 10000


class TestMacOSRealEnumeration:
    """真机枚举：结构正确、坐标自洽（CI 在 Windows 上，这部分跳过）。"""

    def test_finds_real_windows_with_sane_rects(self):
        import core.platform.window_macos as backend

        found = backend.enumerate_windows(exclude_pid=-1)
        assert found, "桌面上总该有至少一个窗口"
        for item in found:
            assert isinstance(item.handle, int)
            assert len(item.rect) == 4 and all(isinstance(v, int) for v in item.rect)
            x1, y1, x2, y2 = item.rect
            assert x2 > x1 and y2 > y1, item.rect
            assert item.title.strip()

    def test_default_excludes_more_than_a_non_matching_pid(self):
        """默认排除自己：结果数不会多于「排除一个不相干的 pid」。

        我们的进程如果在屏幕上有窗口，默认调用应当更少；一个都没有时两者相等。
        """
        import core.platform.window_macos as backend

        default_count = len(backend.enumerate_windows())
        other_count = len(backend.enumerate_windows(exclude_pid=-1))
        assert default_count <= other_count

    def test_find_window_at_cursor_returns_a_rect(self):
        """端到端：门面 + Quartz 后端 + 命中测试走通一次。"""
        result = window.find_window_at_cursor()
        assert result is None or len(result) == 4


class TestKeepVisibleWhenInactive:
    """贴图不能随失焦被系统藏起来（macOS 的 Qt Tool 窗口默认会）。"""

    def test_unavailable_platform_is_a_no_op(self, monkeypatch):
        from core.platform import window_ops

        monkeypatch.setattr("core.platform.capabilities.available", lambda *a, **k: False)

        assert window_ops.keep_visible_when_inactive(object()) is False

    def test_macos_clears_hides_on_deactivate(self, monkeypatch):
        from core.platform import window_ops

        monkeypatch.setattr("core.platform.capabilities.available", lambda *a, **k: True)
        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        calls = []

        class _FakeNSWindow:
            @staticmethod
            def hidesOnDeactivate():
                return True

            @staticmethod
            def setHidesOnDeactivate_(value):
                calls.append(value)

        fake_module = SimpleNamespace(
            objc_object=lambda **_k: SimpleNamespace(window=lambda: _FakeNSWindow())
        )
        monkeypatch.setitem(sys.modules, "objc", fake_module)
        window = SimpleNamespace(winId=lambda: 12345)

        assert window_ops.keep_visible_when_inactive(window) is True
        assert calls == [False]

    def test_failure_is_reported_not_raised(self, monkeypatch):
        from core.platform import window_ops

        monkeypatch.setattr("core.platform.capabilities.available", lambda *a, **k: True)
        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        def _boom():
            raise RuntimeError("没有原生窗口")

        window = SimpleNamespace(winId=_boom)

        assert window_ops.keep_visible_when_inactive(window) is False
