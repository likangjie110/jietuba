# -*- coding: utf-8 -*-
"""
WindowFinder 单元测试

覆盖 main/capture/window_finder.py 中不依赖真实 Windows 桌面枚举的纯逻辑部分：
- find_window_at_point 的 Z-order 命中测试与降级矩形
- set_screen_offset 的偏移记录
- clear() 状态复位
- _get_virtual_desktop_rect 的降级链（ctypes 失败 -> Qt primaryScreen -> 硬编码默认值）
- is_smart_selection_available 的可用性开关

find_windows()（EnumWindows 真实枚举）和 get_window_rect_no_shadow()（DWM API）
依赖真实 win32 会话，在无桌面的 CI runner 上不可测，不在本文件覆盖范围内。
"""
import sys

import pytest
from unittest.mock import patch, MagicMock

import capture.window_finder as window_finder_module
from capture.window_finder import WindowFinder, is_smart_selection_available


def _finder_or_skip():
    """拿一个 WindowFinder；两个平台的窗口枚举都不可用时跳过。"""
    if not is_smart_selection_available():
        pytest.skip("当前平台没有可用的窗口枚举接口")
    return WindowFinder()


windows_only = pytest.mark.skipif(
    not window_finder_module.WINDOWS_API_AVAILABLE,
    reason="这条测的是 Windows 的 GetSystemMetrics 分支",
)


class TestWindowFinderInit:
    def test_default_offset_is_zero(self):
        finder = _finder_or_skip()
        assert finder.screen_offset_x == 0
        assert finder.screen_offset_y == 0
        assert finder.windows == []

    def test_custom_offset(self):
        finder = _finder_or_skip()
        finder.set_screen_offset(100, -50)
        assert finder.screen_offset_x == 100
        assert finder.screen_offset_y == -50

    def test_raises_when_no_platform_api_is_available(self):
        with patch.object(window_finder_module, "WINDOWS_API_AVAILABLE", False), \
             patch.object(window_finder_module, "MACOS_API_AVAILABLE", False):
            with pytest.raises(RuntimeError):
                WindowFinder()


class TestSetScreenOffset:
    def test_updates_offsets(self):
        finder = _finder_or_skip()
        finder.set_screen_offset(200, 300)
        assert finder.screen_offset_x == 200
        assert finder.screen_offset_y == 300


class TestFindWindowAtPoint:
    def test_returns_topmost_window_containing_point(self):
        """windows 列表按 Z-order 排列，第一个命中的矩形应被返回"""
        finder = WindowFinder()
        # 两个重叠窗口：hwnd 1 在最上层（列表首位），hwnd 2 在其下
        finder.windows = [
            (1, [0, 0, 500, 500], "Top Window"),
            (2, [0, 0, 1000, 1000], "Bottom Window"),
        ]
        result = finder.find_window_at_point(100, 100)
        assert result == [0, 0, 500, 500]

    def test_skips_non_containing_window_and_finds_next(self):
        finder = WindowFinder()
        finder.windows = [
            (1, [0, 0, 100, 100], "Top Left"),
            (2, [200, 200, 800, 800], "Bottom Right"),
        ]
        # 点(300, 300) 不在第一个窗口内，但在第二个窗口内
        result = finder.find_window_at_point(300, 300)
        assert result == [200, 200, 800, 800]

    def test_point_on_boundary_is_inclusive(self):
        """边界值应被视为命中（<=判断）"""
        finder = WindowFinder()
        finder.windows = [(1, [10, 10, 110, 110], "Box")]
        assert finder.find_window_at_point(10, 10) == [10, 10, 110, 110]
        assert finder.find_window_at_point(110, 110) == [10, 10, 110, 110]

    def test_no_match_returns_fallback_rect(self):
        finder = WindowFinder()
        finder.windows = [(1, [0, 0, 50, 50], "Small Window")]
        fallback = [0, 0, 1920, 1080]
        result = finder.find_window_at_point(9999, 9999, fallback_rect=fallback)
        assert result == fallback

    def test_no_match_no_fallback_returns_virtual_desktop_rect(self):
        finder = WindowFinder()
        finder.windows = []
        with patch.object(
            finder, "_get_virtual_desktop_rect", return_value=[0, 0, 3840, 1080]
        ) as mock_vd:
            result = finder.find_window_at_point(500, 500)
        mock_vd.assert_called_once()
        assert result == [0, 0, 3840, 1080]

    def test_empty_windows_list_falls_back(self):
        finder = WindowFinder()
        finder.windows = []
        fallback = [1, 2, 3, 4]
        assert finder.find_window_at_point(0, 0, fallback_rect=fallback) == fallback


class TestGetVirtualDesktopRect:
    @windows_only
    def test_uses_system_metrics_when_available(self):
        finder = WindowFinder()
        fake_user32 = MagicMock()
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
        fake_user32.GetSystemMetrics.side_effect = lambda idx: {
            76: -1920,
            77: 0,
            78: 3840,
            79: 1080,
        }[idx]

        with patch.object(window_finder_module.ctypes, "windll") as mock_windll:
            mock_windll.user32 = fake_user32
            result = finder._get_virtual_desktop_rect()

        assert result == [-1920, 0, -1920 + 3840, 0 + 1080]

    @windows_only
    def test_falls_back_to_hardcoded_default_on_total_failure(self):
        """ctypes 和 Qt primaryScreen 都失败时，应返回硬编码的 1920x1080 默认值"""
        finder = WindowFinder()

        with patch.object(window_finder_module.ctypes, "windll") as mock_windll:
            mock_windll.user32.GetSystemMetrics.side_effect = OSError("no display")
            with patch("PySide6.QtGui.QGuiApplication.primaryScreen", return_value=None):
                result = finder._get_virtual_desktop_rect()

        assert result == [0, 0, 1920, 1080]


class TestClear:
    def test_clear_resets_windows_list(self):
        finder = _finder_or_skip()
        finder.windows = [(1, [0, 0, 10, 10], "Something")]
        finder.clear()
        assert finder.windows == []


class TestIsSmartSelectionAvailable:
    def test_available_when_either_platform_api_is_present(self):
        with patch.object(window_finder_module, "WINDOWS_API_AVAILABLE", True), \
             patch.object(window_finder_module, "MACOS_API_AVAILABLE", False):
            assert is_smart_selection_available() is True

        with patch.object(window_finder_module, "WINDOWS_API_AVAILABLE", False), \
             patch.object(window_finder_module, "MACOS_API_AVAILABLE", True):
            assert is_smart_selection_available() is True

    def test_unavailable_when_neither_platform_api_is_present(self):
        with patch.object(window_finder_module, "WINDOWS_API_AVAILABLE", False), \
             patch.object(window_finder_module, "MACOS_API_AVAILABLE", False):
            assert is_smart_selection_available() is False


class TestScreenOffsetOnMacOS:
    """偏移量必须真的从枚举结果里减掉——多屏坐标就靠这一步。"""

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS 的窗口来源")
    def test_offset_is_subtracted_from_enumerated_windows(self, monkeypatch):
        from capture import window_finder_macos

        monkeypatch.setattr(window_finder_macos, "enumerate_windows",
                            lambda exclude_pid=None, debug=False: [(7, [100, 200, 300, 400], "标题")])
        finder = WindowFinder(50, 20)

        windows = finder._find_windows_macos()

        assert windows == [(7, [50, 180, 250, 380], "标题")]


class TestMacOSEnumeration:
    """真机枚举：结构正确、坐标自洽（CI 在 Windows 上，这部分跳过）。"""

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS 的 Quartz 枚举")
    def test_finds_real_windows_with_sane_rects(self):
        from capture import window_finder_macos

        windows = window_finder_macos.enumerate_windows(exclude_pid=-1)

        assert windows, "桌面上总该有至少一个窗口"
        for window_id, rect, title in windows:
            assert isinstance(window_id, int)
            assert len(rect) == 4 and all(isinstance(v, int) for v in rect)
            assert rect[2] > rect[0] and rect[3] > rect[1], rect
            assert title.strip()

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS 的 Quartz 枚举")
    def test_own_process_is_excluded_by_default(self):
        """截图时我们自己的全屏遮罩必须在列表外。"""
        from capture import window_finder_macos

        assert all(info[0] != 0 for info in window_finder_macos.enumerate_windows())
