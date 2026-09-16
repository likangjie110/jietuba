# -*- coding: utf-8 -*-
"""欢迎向导第 6 页（完成页）的平台相关行为。

迁移前这一页在 macOS/Linux 上仍然显示「开机自启」和「快捷方式」两个开关，且都默认
勾选：前者走到 ``sys.platform != "win32"`` 直接 return，后者直接去跑 powershell.exe
（必然 FileNotFoundError）。用户看到的是一个点了没反应的开关加一条错误日志。

现在这两个开关只在平台层声明支持时才创建；不支持时整行不出现，也不会去调注册表或
PowerShell。这里按平台把两种情形都钉住。
"""

import threading

import pytest

from ui.welcome import page6_finish
from ui.welcome.page6_finish import FinishPage


class _Config:
    """完成页用到的最小配置接口。"""

    def __init__(self, show_main_window=False):
        self._show_main = show_main_window
        self.app_settings = {}
        self.show_main_writes = []

    def get_show_main_window(self):
        return self._show_main

    def set_show_main_window(self, value):
        self.show_main_writes.append(value)

    def set_app_setting(self, key, value):
        self.app_settings[key] = value


@pytest.fixture
def synchronous_threads(monkeypatch):
    """把 save() 里的后台线程改成同步执行，避免断言与线程赛跑。"""

    class _InlineThread:
        def __init__(self, target=None, daemon=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", _InlineThread)


class TestCapabilityGatesTheToggles:
    def test_toggles_are_absent_when_platform_lacks_the_capability(self, qapp, monkeypatch):
        monkeypatch.setattr(page6_finish, "available", lambda *_a, **_k: False)
        page = FinishPage(_Config())
        try:
            assert not hasattr(page, "_autostart_switch")
            assert not hasattr(page, "_desktop_switch")
            # 跨平台的开关不受影响
            assert hasattr(page, "_show_main_switch")
        finally:
            page.close()

    def test_toggles_are_present_when_supported(self, qapp, monkeypatch):
        monkeypatch.setattr(page6_finish, "available", lambda *_a, **_k: True)
        monkeypatch.setattr(page6_finish.startup, "is_autostart_enabled", lambda: False)
        page = FinishPage(_Config())
        try:
            assert page._autostart_switch.isChecked() is True
            assert page._desktop_switch.isChecked() is True
        finally:
            page.close()

    def test_capabilities_are_queried_by_name(self, qapp, monkeypatch):
        """两个开关分别对应两个能力，不能共用一个判断。"""
        asked = []

        def _available(capability=None, *_a, **_k):
            asked.append(capability)
            return capability is page6_finish.Capability.AUTOSTART

        monkeypatch.setattr(page6_finish, "available", _available)
        monkeypatch.setattr(page6_finish.startup, "is_autostart_enabled", lambda: False)
        page = FinishPage(_Config())
        try:
            assert asked == [
                page6_finish.Capability.AUTOSTART,
                page6_finish.Capability.DESKTOP_SHORTCUT,
            ]
            assert hasattr(page, "_autostart_switch")
            assert not hasattr(page, "_desktop_switch")
        finally:
            page.close()


class TestSave:
    def test_saves_preferences_without_the_platform_toggles(self, qapp, monkeypatch, synchronous_threads):
        monkeypatch.setattr(page6_finish, "available", lambda *_a, **_k: False)
        config = _Config(show_main_window=True)
        page = FinishPage(config)
        try:
            page._show_main_switch.setChecked(False)
            page.save()
        finally:
            page.close()

        assert config.app_settings["welcome_wizard_done"] == "1"
        assert config.show_main_writes == [False]

    def test_only_calls_autostart_when_the_toggle_was_shown(self, qapp, monkeypatch, synchronous_threads):
        monkeypatch.setattr(page6_finish, "available", lambda *_a, **_k: False)
        called = []
        monkeypatch.setattr(page6_finish.startup, "set_autostart", lambda v: called.append(v))
        page = FinishPage(_Config())
        try:
            page.save()
        finally:
            page.close()
        assert called == []

    def test_writes_autostart_and_shortcut_when_shown(self, qapp, monkeypatch, synchronous_threads):
        monkeypatch.setattr(page6_finish, "available", lambda *_a, **_k: True)
        monkeypatch.setattr(page6_finish.startup, "is_autostart_enabled", lambda: False)
        autostart_calls, shortcut_calls = [], []
        monkeypatch.setattr(page6_finish.startup, "set_autostart", autostart_calls.append)
        monkeypatch.setattr(
            page6_finish.FinishPage, "_create_desktop_shortcut",
            lambda _cls: shortcut_calls.append(True),
        )
        page = FinishPage(_Config())
        try:
            page.save()
        finally:
            page.close()
        assert autostart_calls == [True]
        assert shortcut_calls == [True]


class TestDelegationToPlatformLayer:
    """完成页不再自己碰注册表或 PowerShell，只转发给平台层。"""

    def test_autostart_delegates(self, monkeypatch):
        monkeypatch.setattr(page6_finish.startup, "is_autostart_enabled", lambda: True)
        assert FinishPage._get_autostart() is True

        written = []
        monkeypatch.setattr(page6_finish.startup, "set_autostart", written.append)
        FinishPage._set_autostart(False)
        assert written == [False]

    def test_shortcut_uses_platform_shell(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            page6_finish.shell, "create_desktop_shortcut",
            lambda name, **kwargs: calls.append((name, kwargs)),
        )
        FinishPage._create_desktop_shortcut()
        assert len(calls) == 1
        name, kwargs = calls[0]
        assert name == page6_finish.PRODUCT_NAME
        assert kwargs["target"]
        assert kwargs["working_dir"]

    def test_module_has_no_registry_or_powershell_left(self):
        """这些实现必须只剩平台层一份，页面里不该再出现。"""
        import inspect

        source = inspect.getsource(page6_finish)
        assert "winreg" not in source
        assert "powershell" not in source
        assert "_AUTOSTART_REG_KEY" not in source
