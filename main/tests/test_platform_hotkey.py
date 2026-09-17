# -*- coding: utf-8 -*-
"""平台层热键（core/platform/hotkey 与 hotkey_win32）单元测试。

重点是原先完全没人测的两块：Windows 的 RegisterHotKey 注册/探测与 ``WM_HOTKEY``
消息解码（要在真实 Windows 会话上才能跑），以及鼠标侧键钩子的消息解码。都用假
``ctypes.windll`` / 假 listener 在本机（macOS）驱动。

另外钉住热键字符串解析与 pynput 写法的一致性：两边对同一个字符串必须给出一致的
判断，否则用户在 macOS 上能录进去的键在 Windows 上注册不了（或反过来）。
"""

from types import SimpleNamespace

import pytest

from core.platform import hotkey, hotkey_win32


# ── 解析 ──────────────────────────────────────────────

class TestParseHotkey:
    @pytest.mark.parametrize("raw, mods, vk", [
        ("ctrl+a", hotkey.MOD_CONTROL, 0x41),
        ("ctrl+shift+a", hotkey.MOD_CONTROL | hotkey.MOD_SHIFT, 0x41),
        ("alt+f4", hotkey.MOD_ALT, 0x73),
        ("ctrl+1", hotkey.MOD_CONTROL, 0x31),
        ("ctrl+printscreen", hotkey.MOD_CONTROL, 0x2C),
        ("ctrl+esc", hotkey.MOD_CONTROL, 0x1B),
        ("ctrl+-", hotkey.MOD_CONTROL, 0xBD),
        ("ctrl+/", hotkey.MOD_CONTROL, 0xBF),
    ])
    def test_modifiers_and_virtual_keys(self, raw, mods, vk):
        parsed_mods, parsed_vk = hotkey.parse_hotkey(raw)
        assert parsed_mods == mods | hotkey.MOD_NOREPEAT
        assert parsed_vk == vk

    def test_always_sets_norepeat(self):
        """按住不放时系统会连续发 WM_HOTKEY，NOREPEAT 是必须的。"""
        assert hotkey.parse_hotkey("ctrl+a")[0] & hotkey.MOD_NOREPEAT

    def test_case_and_spacing_are_ignored(self):
        assert hotkey.parse_hotkey(" Ctrl + SHIFT + A ") == hotkey.parse_hotkey("ctrl+shift+a")

    @pytest.mark.parametrize("bad", ["", "   ", "ctrl", "ctrl+", "ctrl+nosuchkey", None, 123])
    def test_rejects_invalid(self, bad):
        with pytest.raises((TypeError, ValueError)):
            hotkey.parse_hotkey(bad)

    def test_f24_is_the_last_function_key(self):
        assert hotkey.parse_hotkey("ctrl+f24")[1] == 0x70 + 23
        with pytest.raises(ValueError):
            hotkey.parse_hotkey("ctrl+f25")


class TestPynputMappingAgreesWithTheParser:
    """同一个字符串在两个平台要么都能用，要么都不能——否则录进去的键在另一端失效。"""

    @pytest.mark.parametrize("raw", [
        "ctrl+a", "ctrl+shift+a", "alt+f4", "cmd+esc",
        "ctrl+printscreen", "shift+1", "ctrl+-", "ctrl+/",
    ])
    def test_both_parsers_accept(self, raw):
        hotkey.parse_hotkey(raw)
        assert hotkey.to_pynput_hotkey(raw)

    @pytest.mark.parametrize("raw", [
        "ctrl+space", "ctrl+tab", "ctrl+enter", "ctrl+backspace", "ctrl+delete",
        "ctrl+insert", "ctrl+home", "ctrl+end", "ctrl+pageup", "ctrl+pagedown",
        "ctrl+up", "ctrl+down", "ctrl+left", "ctrl+right",
    ])
    def test_pynput_only_named_keys_are_a_known_cross_platform_gap(self, raw):
        """已知的跨平台不一致（本次重构只搬移，未改行为）。

        pynput 那条路认这些「命名键」，而 Win32 的 VK 表只覆盖字母/数字/F1-F24 与少量
        符号。结果是同一个热键字符串在 macOS 上能录进去、在 Windows 上注册失败
        （``parse_hotkey`` 抛「不支持的键」）。

        真机上的表现因此不同：Windows 用户录不到这些键，macOS 用户能录。要修就得给
        ``parse_hotkey`` 补上对应的 VK 码，同时更新欢迎页的键位表（它断言「可绑的键
        就是解析器接受的键」）——那是产品决策，不是这次重构的范围。这条用例把现状钉住，
        免得以后被误当成新引入的回归。
        """
        assert hotkey.to_pynput_hotkey(raw)
        with pytest.raises(ValueError):
            hotkey.parse_hotkey(raw)

    @pytest.mark.parametrize("raw", ["ctrl+nosuchkey", "ctrl+f25", "ctrl+повер"])
    def test_both_parsers_reject(self, raw):
        with pytest.raises(ValueError):
            hotkey.parse_hotkey(raw)
        with pytest.raises(ValueError):
            hotkey.to_pynput_hotkey(raw)

    @pytest.mark.parametrize("raw", ["a", "ctrl", "", "ctrl+"])
    def test_a_lone_key_is_never_a_global_hotkey(self, raw):
        """没有修饰键会吃掉用户所有正常打字。"""
        with pytest.raises(ValueError):
            hotkey.to_pynput_hotkey(raw)

    def test_win_maps_to_command_on_other_platforms(self):
        assert hotkey.to_pynput_hotkey("win+space") == "<cmd>+<space>"


class TestMouseTokens:
    def test_recognised(self):
        assert hotkey.is_mouse_button_hotkey("mouseback")
        assert hotkey.is_mouse_button_hotkey("MouseForward")
        assert hotkey.is_mouse_button_hotkey("  mouseback  ")

    def test_not_a_keyboard_combo(self):
        for value in ("ctrl+shift+a", "f1", "", None, 123):
            assert hotkey.is_mouse_button_hotkey(value) is False

    def test_pynput_button_names_map_to_tokens(self):
        assert hotkey.PYNPUT_BUTTON_NAME_TOKENS["x1"] == hotkey.MOUSE_BUTTON_BACK
        assert hotkey.PYNPUT_BUTTON_NAME_TOKENS["x2"] == hotkey.MOUSE_BUTTON_FORWARD


# ── Windows：RegisterHotKey ───────────────────────────

class _FakeUser32:
    def __init__(self, register_result=True):
        self.register_result = register_result
        self.calls = []

    def RegisterHotKey(self, hwnd, hotkey_id, mods, vk):  # noqa: N802
        self.calls.append(("register", hotkey_id, mods, vk))
        return 1 if self.register_result else 0

    def UnregisterHotKey(self, hwnd, hotkey_id):  # noqa: N802
        self.calls.append(("unregister", hotkey_id))


@pytest.fixture
def win32(monkeypatch):
    fake = _FakeUser32()
    monkeypatch.setattr(hotkey_win32, "IS_WINDOWS", True)
    monkeypatch.setattr(hotkey_win32.ctypes, "windll", SimpleNamespace(user32=fake),
                        raising=False)
    return fake


@pytest.fixture
def off_windows(monkeypatch):
    monkeypatch.setattr(hotkey_win32, "IS_WINDOWS", False)
    return hotkey_win32


class TestWin32Registration:
    def test_registers_with_the_system(self, win32):
        assert hotkey_win32.register(3, hotkey.MOD_CONTROL, 0x41) is True
        assert win32.calls == [("register", 3, hotkey.MOD_CONTROL, 0x41)]

    def test_failure_is_reported_as_false(self, win32):
        """占用返回 0；调用方据此判断「这个组合被别的程序占了」。"""
        win32.register_result = False
        assert hotkey_win32.register(3, hotkey.MOD_CONTROL, 0x41) is False

    def test_never_raises(self, win32, monkeypatch):
        monkeypatch.setattr(hotkey_win32.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(
            RegisterHotKey=lambda *a: (_ for _ in ()).throw(OSError("boom")))),
            raising=False)
        assert hotkey_win32.register(3, 0, 0) is False

    def test_unregisters(self, win32):
        hotkey_win32.unregister(3)
        assert win32.calls == [("unregister", 3)]

    @pytest.mark.parametrize("method", ["register", "unregister", "check_availability"])
    def test_off_windows_is_a_noop(self, off_windows, method):
        if method == "register":
            assert off_windows.register(1, 0, 0) is False
        elif method == "unregister":
            assert off_windows.unregister(1) is None
        else:
            assert off_windows.check_availability(0, 0) is True


class TestWin32AvailabilityCheck:
    def test_available_when_the_probe_registers(self, win32):
        assert hotkey_win32.check_availability(hotkey.MOD_CONTROL, 0x41) is True
        # 探测必须自己收拾干净，否则会把真热键的位置一直占着
        assert win32.calls == [("register", hotkey_win32._PROBE_HOTKEY_ID,
                                hotkey.MOD_CONTROL, 0x41),
                               ("unregister", hotkey_win32._PROBE_HOTKEY_ID)]

    def test_unavailable_when_the_probe_fails(self, win32):
        win32.register_result = False
        assert hotkey_win32.check_availability(hotkey.MOD_CONTROL, 0x41) is False

    def test_probe_id_does_not_collide_with_real_ones(self):
        """真实注册的 id 从 1 开始递增，探测用固定 id 且足够远。"""
        assert hotkey_win32._PROBE_HOTKEY_ID > 1000


# ── Windows：WM_HOTKEY 过滤器 ─────────────────────────

class TestNativeEventFilter:
    """过滤器只做两件事：解出 WM_HOTKEY 的热键 id，并始终消费这条消息。"""

    def _make_filter(self, received):
        return hotkey_win32._Win32HotkeyFilter.build(received.append)

    def _message(self, message, w_param):
        from ctypes import wintypes

        msg = wintypes.MSG()
        msg.message = message
        msg.wParam = w_param
        return msg

    def test_decodes_the_hotkey_id(self, qapp):
        received = []
        flt = self._make_filter(received)
        msg = self._message(hotkey_win32.WM_HOTKEY, 7)
        import ctypes

        result = flt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
        assert received == [7]
        assert result == (True, 0), "WM_HOTKEY 必须消费掉，不能让系统再分发给别的窗口"

    def test_ignores_other_messages(self, qapp):
        received = []
        flt = self._make_filter(received)
        msg = self._message(0x0100, 7)  # WM_KEYDOWN
        import ctypes

        result = flt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
        assert received == []
        assert result == (False, 0)

    def test_ignores_other_event_types(self, qapp):
        received = []
        flt = self._make_filter(received)
        assert flt.nativeEventFilter(b"xcb_generic_event_t", 0) == (False, 0)
        assert received == []

    def test_callback_error_does_not_escape(self, qapp):
        """回调抛异常不能让过滤器崩掉——那会打断整个事件循环。"""
        def _boom(_hotkey_id):
            raise RuntimeError("回调炸了")

        flt = hotkey_win32._Win32HotkeyFilter.build(_boom)
        msg = self._message(hotkey_win32.WM_HOTKEY, 1)
        import ctypes

        assert flt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)


# ── 鼠标侧键：消息解码 ─────────────────────────────────

class _FakeListener:
    def __init__(self):
        self.X_BUTTONS = {0x020B: {1: (SimpleNamespace(name="x1"), True),
                                   2: (SimpleNamespace(name="x2"), True)}}
        self.suppressed = 0

    def suppress_event(self):
        self.suppressed += 1
        raise RuntimeError("pynput 的 SuppressException")


class TestMouseButtonDecoding:
    def test_decodes_the_button_and_state(self):
        listener = _FakeListener()
        data = SimpleNamespace(mouseData=1 << 16)  # XBUTTON1 在高 16 位
        assert hotkey_win32.decode_mouse_button(listener, 0x020B, data) == ("x1", True)

        data = SimpleNamespace(mouseData=2 << 16)
        assert hotkey_win32.decode_mouse_button(listener, 0x020B, data) == ("x2", True)

    def test_unknown_message_is_none(self):
        assert hotkey_win32.decode_mouse_button(_FakeListener(), 0x0201,
                                                SimpleNamespace(mouseData=0)) is None

    def test_unknown_button_index_is_none(self):
        data = SimpleNamespace(mouseData=9 << 16)
        assert hotkey_win32.decode_mouse_button(_FakeListener(), 0x020B, data) is None

    def test_facade_returns_none_off_windows(self, monkeypatch):
        """守卫在门面上（后端只被 Windows 那条路径调用到）。"""
        monkeypatch.setattr(hotkey, "IS_WINDOWS", False)
        assert hotkey.decode_mouse_button(_FakeListener(), 0x020B,
                                          SimpleNamespace(mouseData=1 << 16)) is None

    def test_suppressing_delegates_to_the_listener(self):
        listener = _FakeListener()
        with pytest.raises(RuntimeError):
            hotkey_win32.suppress_mouse_event(listener)
        assert listener.suppressed == 1

    def test_facade_suppression_is_a_noop_off_windows(self, monkeypatch):
        monkeypatch.setattr(hotkey, "IS_WINDOWS", False)
        hotkey.suppress_mouse_event(_FakeListener())  # 不抛异常


# ── 门面 ──────────────────────────────────────────────

class TestFacade:
    def test_backend_choice_follows_the_platform(self, monkeypatch):
        monkeypatch.setattr(hotkey, "IS_WINDOWS", True)
        assert hotkey.keyboard_backend_is_native() is True
        assert hotkey.mouse_hotkey_supported() is True

        monkeypatch.setattr(hotkey, "IS_WINDOWS", False)
        assert hotkey.keyboard_backend_is_native() is False
        assert hotkey.mouse_hotkey_supported() is False

    def test_native_filter_is_none_off_windows(self, monkeypatch):
        monkeypatch.setattr(hotkey, "IS_WINDOWS", False)
        assert hotkey.create_native_event_filter(lambda _i: None) is None

    def test_listener_startup_failure_is_reported_not_raised(self, monkeypatch):
        import sys

        monkeypatch.setattr(hotkey, "IS_WINDOWS", True)
        monkeypatch.setitem(sys.modules, "pynput.mouse", None)

        # hotkey 走门面 -> hotkey_win32.start_mouse_listener -> pynput
        assert hotkey.start_mouse_listener(lambda *a: None) is None

    def test_stop_listener_tolerates_none_and_dead_listeners(self):
        hotkey.stop_listener(None)

        class _Dead:
            def stop(self):
                raise RuntimeError("already stopped")

        hotkey.stop_listener(_Dead())
