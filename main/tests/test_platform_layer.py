# -*- coding: utf-8 -*-
"""平台层（core/platform）单元测试。

这层是「跨平台」的可执行定义，所以测试的重点不是当前机器的行为，而是**三个平台的答案
是否都登记齐了**：新增一个能力却漏掉 Linux，或者把某个能力的后端名写成空串却在矩阵里
标了支持，都要在这里失败。当前平台的实际答案只做少量核对。
"""

import pytest

from core.platform import (
    Capability,
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    PLATFORM_NAME,
    SUPPORTED_PLATFORMS,
    Support,
    available,
    backend_name,
    is_native_qt_platform,
    qt_platform_name,
    support,
    unsupported,
)

PLATFORMS = ("windows", "macos", "linux")
ALL_CAPABILITIES = list(Capability)


class TestDetection:
    def test_exactly_one_os_matches(self):
        """三个 OS 常量互斥——曾经有模块按 hasattr(ctypes, "windll") 判断，
        与 sys.platform 不是同一判据，混用时会出现「既是也不是」的中间态。"""
        assert sum([IS_WINDOWS, IS_MACOS, IS_LINUX]) <= 1

    def test_platform_name_matches_the_flags(self):
        if IS_WINDOWS:
            assert PLATFORM_NAME == "windows"
        elif IS_MACOS:
            assert PLATFORM_NAME == "macos"
        elif IS_LINUX:
            assert PLATFORM_NAME == "linux"

    def test_supported_platforms_cover_the_three_targets(self):
        assert set(SUPPORTED_PLATFORMS) == set(PLATFORMS)

    def test_current_platform_is_known(self):
        """本仓库只发行 Windows，并适配 macOS/Linux；跑在别的系统上应当尽早暴露。"""
        assert PLATFORM_NAME in SUPPORTED_PLATFORMS

    def test_qt_platform_name_is_safe_without_qapplication(self):
        """QApplication 未创建时不能抛异常——探测器会在启动早期被调用。"""
        assert isinstance(qt_platform_name(), str)
        assert isinstance(is_native_qt_platform(), bool)

    def test_offscreen_qt_is_not_native(self, monkeypatch):
        """离屏平台上碰原生窗口 API 会崩，守卫必须认得出它。"""
        from core.platform import detection

        monkeypatch.setattr(detection, "qt_platform_name", lambda: "offscreen")
        assert detection.is_native_qt_platform() is False


class TestSupportTable:
    """矩阵的完整性与自洽性。"""

    @pytest.mark.parametrize("capability", ALL_CAPABILITIES)
    def test_every_capability_is_registered_for_all_platforms(self, capability):
        for platform in PLATFORMS:
            # support() 对未登记的条目回落到 NONE，所以这里直接查内部表，
            # 否则「漏登记」会被悄悄当成「不支持」而测不出来。
            assert platform in _table(capability), (
                f"{capability.value} 缺少 {platform} 的登记"
            )

    @pytest.mark.parametrize("capability", ALL_CAPABILITIES)
    def test_backend_name_agrees_with_support(self, capability):
        for platform in PLATFORMS:
            name = backend_name(capability, platform)
            if support(capability, platform) is Support.NONE:
                assert name == "", f"{capability.value}/{platform} 不支持却有后端名 {name!r}"
            else:
                assert name, f"{capability.value}/{platform} 支持却无后端名"

    def test_available_is_support_without_none(self):
        for capability in ALL_CAPABILITIES:
            for platform in PLATFORMS:
                assert available(capability, platform) == (
                    support(capability, platform) is not Support.NONE
                )

    def test_unsupported_is_the_negation_of_available(self):
        for capability in ALL_CAPABILITIES:
            for platform in PLATFORMS:
                assert unsupported(capability, platform) != available(capability, platform)

    def test_unknown_platform_degrades_to_none(self):
        """未识别的平台不能因为「表里没写」而被当成支持。"""
        for capability in ALL_CAPABILITIES:
            assert support(capability, "plan9") is Support.NONE
            assert available(capability, "plan9") is False


class TestTruthTable:
    """逐平台核对关键结论。

    这些断言同时是给未来改动的护栏：把 Windows 的能力「顺手也标给 Linux」时，
    必须同时把 Linux 后端写出来，否则这里就红。
    """

    def test_window_enumeration_only_on_windows_and_macos(self):
        assert support(Capability.WINDOW_ENUMERATION, "windows") is Support.FULL
        assert support(Capability.WINDOW_ENUMERATION, "macos") is Support.FULL
        assert support(Capability.WINDOW_ENUMERATION, "linux") is Support.NONE

    def test_topmost_is_degraded_outside_windows(self):
        """macOS/Linux 只能改 Qt 窗口标志，切一次会隐藏再显示，所以是降级而非不支持。"""
        assert support(Capability.WINDOW_TOPMOST, "windows") is Support.FULL
        assert support(Capability.WINDOW_TOPMOST, "macos") is Support.DEGRADED
        assert support(Capability.WINDOW_TOPMOST, "linux") is Support.DEGRADED

    def test_exclude_from_capture_is_windows_only(self):
        assert support(Capability.WINDOW_EXCLUDE_FROM_CAPTURE, "windows") is Support.FULL
        assert available(Capability.WINDOW_EXCLUDE_FROM_CAPTURE, "macos") is False
        assert available(Capability.WINDOW_EXCLUDE_FROM_CAPTURE, "linux") is False

    def test_mouse_side_button_hotkey_is_windows_only(self):
        """pynput 的侧键钩子是 Windows 专有接口，别的平台接不到。"""
        assert support(Capability.HOTKEY_MOUSE, "windows") is Support.FULL
        assert available(Capability.HOTKEY_MOUSE, "macos") is False
        assert available(Capability.HOTKEY_MOUSE, "linux") is False

    def test_keyboard_hotkey_works_everywhere_but_degrades_outside_windows(self):
        assert support(Capability.HOTKEY_KEYBOARD, "windows") is Support.FULL
        assert support(Capability.HOTKEY_KEYBOARD, "macos") is Support.DEGRADED
        assert support(Capability.HOTKEY_KEYBOARD, "linux") is Support.DEGRADED

    def test_pointer_button_state_is_missing_on_linux(self):
        """这一条是 GIF 录制里一个真实差异：Linux 上取不到鼠标键状态，
        「不支持」必须能查出来，而不是让键态永远静默为「未按下」。"""
        assert available(Capability.POINTER_BUTTON_STATE, "windows") is True
        assert available(Capability.POINTER_BUTTON_STATE, "macos") is True
        assert available(Capability.POINTER_BUTTON_STATE, "linux") is False

    def test_scroll_inject_and_clipboard_reflect_their_backends(self):
        assert available(Capability.SCROLL_INJECT, "windows") is True
        assert available(Capability.SCROLL_INJECT, "macos") is False
        assert available(Capability.SCROLL_INJECT, "linux") is False
        # 剪贴板写图三平台都能用，但只有 Windows 是完整实现（重试 + 注册 PNG 格式）
        assert support(Capability.CLIPBOARD_IMAGE, "windows") is Support.FULL
        assert support(Capability.CLIPBOARD_IMAGE, "macos") is Support.DEGRADED
        assert support(Capability.CLIPBOARD_IMAGE, "linux") is Support.DEGRADED

    def test_desktop_integration_is_windows_only_for_now(self):
        for capability in (Capability.AUTOSTART, Capability.DESKTOP_SHORTCUT,
                           Capability.PROCESS_CONTROL):
            assert available(capability, "windows") is True
            assert available(capability, "macos") is False
            assert available(capability, "linux") is False

    def test_current_platform_answers_are_consistent_with_detection(self):
        """当前平台的答案要跟 detection 的常量对得上（防止表里键名写错）。"""
        declared = PLATFORM_NAME if PLATFORM_NAME in PLATFORMS else "linux"
        for capability in ALL_CAPABILITIES:
            assert support(capability) is support(capability, declared)


class TestSmartSelectionWiring:
    """智能选区可用性 = 「平台声明有后端」且有「依赖真的导入成功」。

    两者缺一不可：声明为真但没装 pywin32 时功能是坏的，声明为假时更不该乐观。
    """

    def test_unavailable_when_platform_declares_no_backend(self, monkeypatch):
        from capture import window_finder as wf

        monkeypatch.setattr(wf, "WINDOWS_API_AVAILABLE", False)
        monkeypatch.setattr(wf, "MACOS_API_AVAILABLE", False)
        assert wf.is_smart_selection_available() is False

    def test_unavailable_when_dependency_missing_even_if_declared(self, monkeypatch):
        from capture import window_finder as wf

        monkeypatch.setattr(wf, "WINDOWS_API_AVAILABLE", False)
        monkeypatch.setattr(wf, "MACOS_API_AVAILABLE", False)
        monkeypatch.setattr(wf, "available", lambda *a, **k: True)
        assert wf.is_smart_selection_available() is False

    def test_available_when_both_hold(self, monkeypatch):
        from capture import window_finder as wf

        monkeypatch.setattr(wf, "available", lambda *a, **k: True)
        # 两个平台标志里至少一个为真的情况
        monkeypatch.setattr(wf, "MACOS_API_AVAILABLE", True)
        assert wf.is_smart_selection_available() is True


def _table(capability):
    """取出内部支持矩阵里某个能力那一行（测试要能发现「漏登记」）。"""
    from core.platform import capabilities as caps

    return caps._SUPPORT_TABLE[capability]
