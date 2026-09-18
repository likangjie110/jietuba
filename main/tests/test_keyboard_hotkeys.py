# -*- coding: utf-8 -*-
"""
非 Windows 平台的键盘全局热键

Windows 走系统级 RegisterHotKey，其它平台只能靠 pynput 的键盘监听自己匹配。这里测
后者：热键串到 pynput 写法的映射、登记与注销，以及「事件进来 → 主线程执行回调」
这条链路。

监听器不会真的启动（monkeypatch 掉 start/stop，免得测试机上挂一个全局键盘钩子），
但匹配用的确实是 pynput 的 GlobalHotKeys 对象，喂进去的是真实按键会产生的那组
事件对象（Key.ctrl / Key.alt / 'j'）。

注意 pynput 会忽略「注入」的事件（injected=True，也就是自己合成的按键）——那是防
热键反馈循环的设计，所以这里只能用 injected=False 模拟真实按键。
"""
import sys

import pytest
from pynput.keyboard import Key, KeyCode

from core import shortcut_manager as sm
from core.platform import hotkey as platform_hotkey
from core.shortcut_manager import ShortcutHandler, ShortcutManager


@pytest.fixture
def manager(monkeypatch):
    """一个不走单例、也不真的挂系统钩子的 ShortcutManager。"""
    # 键盘热键的分支判断已搬到平台层（core/platform/hotkey）
    monkeypatch.setattr(sm.platform_hotkey, "keyboard_backend_is_native", lambda: False)
    monkeypatch.setattr("pynput.keyboard.GlobalHotKeys.start", lambda self: None)
    monkeypatch.setattr("pynput.keyboard.GlobalHotKeys.stop", lambda self: None)
    mgr = ShortcutManager()
    yield mgr
    mgr.unregister_all_hotkeys()


def _char(letter: str) -> KeyCode:
    """监听器对普通字符键给出的是 KeyCode 对象，不是裸字符串。

    裸字符串塞进匹配器是匹配不上的（pynput 按对象相等性比较），所以测试里也必须
    用和真实按键一样的对象。
    """
    return KeyCode.from_char(letter)


def _press(mgr, *keys):
    """把真实按键的事件序列喂给监听器（injected=False 即非合成事件）。"""
    for key in keys:
        mgr._keyboard_listener._on_press(key, False)


class TestHotkeySyntax:

    @pytest.mark.parametrize("raw, expected", [
        ("ctrl+1", "<ctrl>+1"),
        ("ctrl+shift+a", "<ctrl>+<shift>+a"),
        ("alt+f4", "<alt>+<f4>"),
        ("win+space", "<cmd>+<space>"),          # win 在 macOS 上就是 Command
        ("cmd+esc", "<cmd>+<esc>"),
        ("ctrl+printscreen", "<ctrl>+<print_screen>"),
    ])
    def test_maps_app_syntax_to_pynput(self, raw, expected):
        assert platform_hotkey.to_pynput_hotkey(raw) == expected

    @pytest.mark.parametrize("bad", ["a", "ctrl", "", "ctrl+", "ctrl+повер", "nosuchmod+a"])
    def test_rejects_what_cannot_be_a_global_hotkey(self, bad):
        """没有修饰键的热键会吃掉用户所有正常打字，必须拒绝。"""
        with pytest.raises(ValueError):
            platform_hotkey.to_pynput_hotkey(bad)


class TestRegistration:

    def test_registering_starts_the_listener_and_shows_up(self, manager):
        assert manager.register_hotkey("ctrl+alt+j", lambda: None) is True

        assert manager.has_registered_hotkeys() is True
        assert manager._keyboard_listener is not None

    def test_unparsable_hotkey_is_refused(self, manager):
        assert manager.register_hotkey("no-modifier", lambda: None) is False
        assert manager.has_registered_hotkeys() is False

    def test_unregistering_clears_registrations(self, manager):
        manager.register_hotkey("ctrl+alt+j", lambda: None)

        manager.unregister_all_hotkeys()

        assert manager.has_registered_hotkeys() is False
        assert manager._keyboard_listener is None

    def test_availability_is_always_true_without_a_system_api(self, manager):
        """macOS 上 pynput 是旁路监听，别的程序占了同一个组合也照样能收到。"""
        assert manager.check_hotkey_availability("ctrl+alt+j") is True


class TestDispatch:

    def test_real_key_sequence_fires_the_callback_on_the_main_thread(self, qapp, manager):
        calls = []
        manager.register_hotkey("ctrl+alt+j", lambda: calls.append(True))

        _press(manager, Key.ctrl, Key.alt, _char("j"))
        qapp.processEvents()          # 信号是排队投递的，和真实情况一样

        assert calls == [True]

    def test_a_different_main_key_does_not_fire(self, qapp, manager):
        calls = []
        manager.register_hotkey("ctrl+alt+j", lambda: calls.append(True))

        _press(manager, Key.ctrl, Key.alt, _char("k"))
        qapp.processEvents()

        assert calls == []

    def test_suppressed_hotkeys_do_not_fire(self, qapp, manager):
        calls = []
        manager.register_hotkey("ctrl+alt+j", lambda: calls.append(True))
        manager.set_global_hotkeys_suppressed(True)

        _press(manager, Key.ctrl, Key.alt, _char("j"))
        qapp.processEvents()

        assert calls == []

    def test_handler_chain_can_intercept_before_the_callback(self, qapp, manager):
        """截图模式之类的界面要能在回调执行前把热键接管过去。"""
        calls = []

        class _Interceptor(ShortcutHandler):
            @property
            def priority(self) -> int:
                return 200

            @property
            def handler_name(self) -> str:
                return "Interceptor"

            def is_active(self) -> bool:
                return True

            def handle_key(self, event) -> bool:
                return False

            def handle_hotkey(self, hotkey_id: int, callback) -> bool:
                calls.append("intercepted")
                return True

        manager.register(_Interceptor())
        manager.register_hotkey("ctrl+alt+j", lambda: calls.append("callback"))

        _press(manager, Key.ctrl, Key.alt, _char("j"))
        qapp.processEvents()

        assert calls == ["intercepted"]

    def test_callback_exception_does_not_escape(self, qapp, manager):
        def _boom():
            raise RuntimeError("回调炸了")

        manager.register_hotkey("ctrl+alt+j", _boom)

        _press(manager, Key.ctrl, Key.alt, _char("j"))
        qapp.processEvents()          # 崩出去的话这里就会红


class TestInputPermission:
    """macOS 的「辅助功能」权限：没授权时 pynput 收不到任何事件，平台层负责查与要。"""

    @staticmethod
    def _fake_services(monkeypatch, *, trusted, calls=None):
        """假的 pyobjc ApplicationServices 模块。"""
        module = type(sys)("ApplicationServices")
        module.kAXTrustedCheckOptionPrompt = "AXTrustedCheckOptionPrompt"

        def with_options(options):
            if calls is not None:
                calls.append(options)
            return trusted

        module.AXIsProcessTrustedWithOptions = with_options
        monkeypatch.setitem(sys.modules, "ApplicationServices", module)

    def test_trusted_on_platforms_without_the_restriction(self, monkeypatch):
        monkeypatch.setattr(platform_hotkey, "IS_MACOS", False)
        assert platform_hotkey.input_monitoring_trusted() is True
        assert platform_hotkey.request_input_monitoring_permission() is True

    def test_reports_the_missing_permission(self, monkeypatch):
        calls = []
        self._fake_services(monkeypatch, trusted=False, calls=calls)
        monkeypatch.setattr(platform_hotkey, "IS_MACOS", True)

        assert platform_hotkey.input_monitoring_trusted() is False
        # 查询用 prompt=False，别在「只是看看」的时候弹窗
        assert calls == [{"AXTrustedCheckOptionPrompt": False}]

    def test_never_resolves_pynputs_own_symbol(self):
        """pynput 在监听线程里解析 HIServices.AXIsProcessTrusted；pyobjc 的惰性函数表
        在两个线程并发解析同名函数时会 KeyError，把监听线程打死。我们的检查必须换名字。"""
        import inspect

        source = inspect.getsource(platform_hotkey)
        assert "AXIsProcessTrusted(" not in source
        assert "AXIsProcessTrustedWithOptions" in source

    def test_request_asks_macos_to_prompt(self, monkeypatch):
        """必须带 prompt 选项：不带的话系统不会弹窗，用户根本不知道该去授权。"""
        calls = []
        self._fake_services(monkeypatch, trusted=False, calls=calls)
        monkeypatch.setattr(platform_hotkey, "IS_MACOS", True)

        assert platform_hotkey.request_input_monitoring_permission() is False
        assert calls == [{"AXTrustedCheckOptionPrompt": True}]

    def test_unavailable_api_is_treated_as_trusted(self, monkeypatch):
        """pyobjc 缺失时查不了——监听这条路本来也不通，别在这里再叠一层误导。"""
        monkeypatch.setitem(sys.modules, "ApplicationServices", None)
        monkeypatch.setattr(platform_hotkey, "IS_MACOS", True)

        assert platform_hotkey.input_monitoring_trusted() is True
        assert platform_hotkey.request_input_monitoring_permission() is True


class TestPermissionRecovery:
    """没权限时快捷键完全没反应：要主动弹窗要权限，授权后自己恢复，不用重启程序。"""

    def _fake_platform(self, monkeypatch, *, trusted=False):
        state = {"trusted": trusted, "prompts": 0, "listeners": []}

        class _Listener:
            def stop(self):
                pass

        def start(mapping):
            state["listeners"].append(mapping)
            return _Listener()

        monkeypatch.setattr(platform_hotkey, "input_monitoring_trusted",
                            lambda: state["trusted"])
        monkeypatch.setattr(platform_hotkey, "request_input_monitoring_permission",
                            lambda: state.__setitem__("prompts", state["prompts"] + 1) or False)
        monkeypatch.setattr(platform_hotkey, "start_keyboard_listener", start)
        return state

    def test_missing_permission_prompts_and_starts_watching(self, qapp, manager, monkeypatch):
        state = self._fake_platform(monkeypatch)

        manager.register_hotkey("ctrl+1", lambda: None)

        assert state["prompts"] == 1
        assert manager._input_permission_watch is not None
        assert manager._input_permission_watch.isActive() is True

    def test_prompt_is_not_repeated_when_the_listener_is_rebuilt(self, qapp, manager, monkeypatch):
        """改热键会重建监听器，但用户只该被打扰一次。"""
        state = self._fake_platform(monkeypatch)

        manager.register_hotkey("ctrl+1", lambda: None)
        manager.register_hotkey("ctrl+2", lambda: None)

        assert state["prompts"] == 1
        assert len(state["listeners"]) == 2

    def test_granting_the_permission_restarts_the_listener(self, qapp, manager, monkeypatch):
        """授权后必须重建监听器：没权限时那次连事件 tap 都没建出来，线程早就退出了。"""
        state = self._fake_platform(monkeypatch)
        manager.register_hotkey("ctrl+1", lambda: None)
        assert len(state["listeners"]) == 1

        state["trusted"] = True
        manager._on_input_permission_tick()

        assert len(state["listeners"]) == 2
        assert manager._input_permission_watch is None

    def test_still_untrusted_keeps_watching(self, qapp, manager, monkeypatch):
        state = self._fake_platform(monkeypatch)
        manager.register_hotkey("ctrl+1", lambda: None)

        manager._on_input_permission_tick()

        assert len(state["listeners"]) == 1
        assert manager._input_permission_watch is not None

    def test_still_untrusted_asks_the_app_to_tell_the_user(self, qapp, manager, monkeypatch):
        """轮询到还没授权就发信号：启动时那个系统对话框用户可能已经关掉或没看见。"""
        self._fake_platform(monkeypatch)
        manager.register_hotkey("ctrl+1", lambda: None)
        signals = []
        manager.input_permission_missing.connect(lambda: signals.append(True))

        manager._on_input_permission_tick()

        assert signals == [True]

    def test_granting_stops_asking_for_a_prompt(self, qapp, manager, monkeypatch):
        state = self._fake_platform(monkeypatch)
        manager.register_hotkey("ctrl+1", lambda: None)
        signals = []
        manager.input_permission_missing.connect(lambda: signals.append(True))

        state["trusted"] = True
        manager._on_input_permission_tick()

        assert signals == []

    def test_unregistering_stops_watching(self, qapp, manager, monkeypatch):
        self._fake_platform(monkeypatch)
        manager.register_hotkey("ctrl+1", lambda: None)

        manager.unregister_all_hotkeys()

        assert manager._input_permission_watch is None

    def test_granted_permission_needs_no_watch(self, qapp, manager, monkeypatch):
        state = self._fake_platform(monkeypatch, trusted=True)

        manager.register_hotkey("ctrl+1", lambda: None)

        assert state["prompts"] == 0
        assert manager._input_permission_watch is None
