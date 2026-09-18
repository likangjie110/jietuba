# -*- coding: utf-8 -*-
"""全局动作的快捷键与托盘（块④的数据层与分发层）。

三件事都在这里管：
- ``app/action_hotkeys`` / ``app/action_tray`` 两个键，以及旧六个热键键的迁移；
- ``MainApp.update_hotkey`` 按动作注册表注册（不再是三段硬编码）；
- 托盘菜单按注册表 + 托盘开关生成。
页面本身的交互在 test_hotkey_action_rows.py 里测。
"""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QSettings

from core import actions
from main_app import MainApp
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def config(tmp_path):
    settings = QSettings(str(tmp_path / "actions.ini"), QSettings.Format.IniFormat)
    return ToolSettingsManager(qsettings=settings)


class TestActionHotkeyTable:
    def test_defaults_mirror_the_registry(self, config):
        table = config.get_action_hotkeys()

        assert table["screenshot"] == ["ctrl+1", ""]
        assert table["clipboard"] == ["ctrl+2", ""]
        assert table["open_translation"] == ["", ""]
        assert set(table) <= set(actions.ACTIONS_BY_ID)

    def test_round_trip(self, config):
        config.set_action_hotkeys({"screenshot": ["ctrl+alt+1", "ctrl+alt+9"]})

        assert config.get_action_hotkeys() == {"screenshot": ["ctrl+alt+1", "ctrl+alt+9"]}

    def test_pairs_are_normalized(self, config):
        config.set_action_hotkeys({"screenshot": "ctrl+1", "clipboard": None})

        table = config.get_action_hotkeys()
        assert table["screenshot"] == ["ctrl+1", ""]
        assert table["clipboard"] == ["", ""]

    def test_broken_json_is_an_empty_table(self, config):
        config.qsettings.setValue("app/action_hotkeys", "{不是 json")

        assert config.get_action_hotkeys() == {}

    def test_migration_reads_the_legacy_keys_once(self, config):
        config.qsettings.setValue("app/hotkey", "ctrl+shift+1")
        config.qsettings.setValue("app/hotkey_2", "ctrl+shift+2")
        config.qsettings.setValue("clipboard/hotkey", "alt+1")
        config.qsettings.setValue("app/translation_hotkey", "alt+2")

        table = config.get_action_hotkeys()

        assert table["screenshot"] == ["ctrl+shift+1", "ctrl+shift+2"]
        assert table["clipboard"] == ["alt+1", ""]
        assert table["open_translation"] == ["alt+2", ""]

    def test_migration_writes_back_so_the_legacy_keys_are_no_longer_read(self, config):
        config.qsettings.setValue("app/hotkey", "ctrl+shift+1")
        config.get_action_hotkeys()

        # 改掉旧键不再影响结果——表已经建好了
        config.qsettings.setValue("app/hotkey", "ctrl+shift+9")
        assert config.get_action_hotkeys()["screenshot"][0] == "ctrl+shift+1"

    @pytest.mark.parametrize(
        ("legacy_read", "legacy_write", "action_id", "index"),
        [
            ("get_hotkey", "set_hotkey", "screenshot", 0),
            ("get_hotkey_2", "set_hotkey_2", "screenshot", 1),
            ("get_clipboard_hotkey", "set_clipboard_hotkey", "clipboard", 0),
            ("get_clipboard_hotkey_2", "set_clipboard_hotkey_2", "clipboard", 1),
            ("get_translation_hotkey", "set_translation_hotkey", "open_translation", 0),
            ("get_translation_hotkey_2", "set_translation_hotkey_2", "open_translation", 1),
        ],
    )
    def test_legacy_accessors_are_views_over_the_table(
        self, config, legacy_read, legacy_write, action_id, index
    ):
        """旧读写器不再是第二份存储：写进去要在表里看到。"""
        getattr(config, legacy_write)("ctrl+alt+7")

        pair = config.get_action_hotkeys()[action_id]
        assert pair[index] == "ctrl+alt+7"
        assert getattr(config, legacy_read)() == "ctrl+alt+7"

    def test_views_keep_the_other_slot(self, config):
        config.set_action_hotkey_pair = None if False else None
        config.set_hotkey_2("ctrl+alt+8")
        config.set_hotkey("ctrl+alt+7")

        assert config.get_action_hotkeys()["screenshot"] == ["ctrl+alt+7", "ctrl+alt+8"]


class TestActionTrayFlags:
    def test_defaults_are_the_registry_defaults(self, config):
        """默认托盘项 = 注册表里 tray=True 的那批（托盘菜单不再自己维护清单）。"""
        flags = config.get_action_tray_flags()
        expected = {action.id for action in actions.ACTIONS if action.tray}

        assert {action_id for action_id, on in flags.items() if on} == expected
        assert "open_save_folder" in flags          # 这一批新补的常用入口

    def test_round_trip(self, config):
        config.set_action_tray_flags({"gif_capture": True, "screenshot": False})

        assert config.get_action_tray_flags() == {"gif_capture": True, "screenshot": False}

    def test_broken_json_falls_back_to_defaults(self, config):
        config.qsettings.setValue("app/action_tray", json.dumps(["不是字典"]))

        assert config.get_action_tray_flags()["screenshot"] is True


class _HotkeySystem:
    """记录注册请求的假热键系统。"""

    def __init__(self, refuse=()):
        self.registered = []
        self.refused = set(refuse)
        self.unregistered = 0
        self.suppressed = None
        self.gestures = None

    def unregister_all(self):
        self.unregistered += 1
        self.registered.clear()

    def set_suppressed(self, value):
        self.suppressed = value

    def register_hotkey(self, hotkey, callback):
        if hotkey in self.refused:
            return False
        self.registered.append((hotkey, callback))
        return True

    def set_mouse_gestures(self, bindings):
        self.gestures = bindings


def _app(config, hotkey_system=None):
    system = hotkey_system or _HotkeySystem()
    return SimpleNamespace(
        config_manager=config,
        hotkey_system=system,
        tr=lambda text: text,
        _show_hotkey_error=lambda failed: None,
    )


class TestUpdateHotkeyRegistry:
    def test_registers_every_configured_action(self, config):
        config.set_action_hotkeys({
            "screenshot": ["ctrl+1", "ctrl+2"],
            "gif_capture": ["alt+g", ""],
            "open_translation": ["", ""],
        })
        app = _app(config)

        MainApp.update_hotkey(app)

        assert sorted(key for key, _cb in app.hotkey_system.registered) == [
            "alt+g", "ctrl+1", "ctrl+2",
        ]

    def test_callbacks_carry_the_matching_action_id(self, config):
        """回调是 partial(run_action, 动作 id, app)——键按下时不会再查表。"""
        import core.actions as actions_module

        config.set_action_hotkeys({"gif_capture": ["alt+g", ""], "screenshot": ["ctrl+1", ""]})
        app = _app(config)
        MainApp.update_hotkey(app)

        bound = {}
        for hotkey, callback in app.hotkey_system.registered:
            assert callback.func is actions_module.run_action
            assert callback.args[1] is app
            bound[hotkey] = callback.args[0]

        assert bound == {"alt+g": "gif_capture", "ctrl+1": "screenshot"}

    def test_unknown_action_ids_are_skipped(self, config, caplog):
        config.set_action_hotkeys({"no_such_action": ["ctrl+9", ""]})
        app = _app(config)

        MainApp.update_hotkey(app)

        assert app.hotkey_system.registered == []

    def test_clipboard_hotkey_is_skipped_while_monitoring_is_off(self, config):
        config.set_action_hotkeys({"clipboard": ["ctrl+2", ""]})
        config.set_clipboard_enabled(False)
        app = _app(config)

        MainApp.update_hotkey(app)

        assert app.hotkey_system.registered == []

    def test_failed_registration_is_collected(self, config):
        config.set_action_hotkeys({"screenshot": ["ctrl+1", ""]})
        app = _app(config, _HotkeySystem(refuse={"ctrl+1"}))
        failed = []
        app._show_hotkey_error = failed.append

        MainApp.update_hotkey(app, show_error=True)

        assert failed == [[("Screenshot", "ctrl+1")]]

    def test_backup_hotkey_is_labelled_separately(self, config):
        config.set_action_hotkeys({"screenshot": ["ctrl+1", "ctrl+2"]})
        app = _app(config, _HotkeySystem(refuse={"ctrl+2"}))
        failed = []
        app._show_hotkey_error = failed.append

        MainApp.update_hotkey(app, show_error=True)

        assert failed == [[("Screenshot (2)", "ctrl+2")]]

    def test_gestures_are_refreshed_too(self, config):
        config.set_mouse_gestures({"screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}})
        app = _app(config)

        MainApp.update_hotkey(app)

        assert app.hotkey_system.gestures == {
            "screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}
        }

    def test_existing_registrations_are_dropped_first(self, config):
        app = _app(config)
        app.hotkey_system.registered.append(("ctrl+9", lambda: None))

        MainApp.update_hotkey(app)

        assert all(key != "ctrl+9" for key, _cb in app.hotkey_system.registered)


class TestTrayRefreshOnSettingsAccepted:
    def test_settings_are_applied_then_the_tray_is_rebuilt(self):
        """「快捷键/动作」页改完托盘开关，保存后托盘菜单要立刻跟上（不用重启）。"""
        calls = []
        fake = SimpleNamespace(
            config_manager=SimpleNamespace(),
            set_clipboard_monitoring_enabled=lambda enabled: calls.append(("clipboard", enabled)),
            update_hotkey=lambda show_error=False: calls.append(("hotkeys", show_error)),
            _update_tray_menu=lambda: calls.append(("tray", None)),
            clipboard_window=None,
        )
        fake.config_manager = SimpleNamespace(get_clipboard_enabled=lambda: True)

        MainApp.on_settings_accepted(fake)

        assert ("tray", None) in calls
        assert calls.index(("tray", None)) > calls.index(("hotkeys", True))


class _FakeApp(QObject):
    """托盘菜单要一个 QObject 作为 QAction 的父对象。"""

    def __init__(self, config):
        super().__init__()
        self.config_manager = config

    def open_clipboard_window(self):
        self.called = "clipboard"

    def start_screenshot(self):
        self.called = "screenshot"

    def set_global_hotkeys_disabled(self, _disabled):
        pass

    def open_settings(self):
        pass

    def quit_app(self):
        pass


class TestTrayMenuActions:
    def _titles(self, menu):
        return [item.text() for item in menu.actions() if not item.isSeparator()]

    def test_default_items_follow_the_registry(self, qapp, config):
        from ui.tray_menu import create_tray_menu

        menu = create_tray_menu(_FakeApp(config))

        titles = self._titles(menu)
        # 顺序就是注册表顺序，默认项就是 tray=True 的那批
        assert titles[:len([a for a in actions.ACTIONS if a.tray])] == [
            a.label for a in actions.ACTIONS if a.tray
        ]
        assert "Screenshot" in titles and "Open Save Folder" in titles

    def test_turning_an_action_off_removes_its_item(self, qapp, config):
        from ui.tray_menu import create_tray_menu

        config.set_action_tray_flags({"screenshot": True, "clipboard": False,
                                      "open_translation": True})
        titles = self._titles(create_tray_menu(_FakeApp(config)))

        assert "Clipboard" not in titles
        assert "Screenshot" in titles

    def test_enabling_an_action_adds_its_item(self, qapp, config):
        from ui.tray_menu import create_tray_menu

        flags = config.get_action_tray_flags()
        flags["long_screenshot"] = True
        config.set_action_tray_flags(flags)

        titles = self._titles(create_tray_menu(_FakeApp(config)))

        assert "Long Screenshot" in titles

    def test_items_trigger_their_action(self, qapp, config):
        from ui.tray_menu import create_tray_menu

        config.set_action_tray_flags({"screenshot": False, "clipboard": True})
        menu = create_tray_menu(_FakeApp(config))
        fired = []
        import core.actions as actions_module

        original = actions_module.run_action
        actions_module.run_action = lambda action_id, _app: fired.append(action_id) or True
        try:
            for item in menu.actions():
                if item.text() == "Clipboard":
                    item.trigger()
        finally:
            actions_module.run_action = original

        assert fired == ["clipboard"]

    def test_static_entries_are_kept(self, qapp, config):
        from ui.tray_menu import create_tray_menu

        titles = self._titles(create_tray_menu(_FakeApp(config)))

        assert "Disable Global Hotkeys" in titles
        assert "Settings" in titles
        assert "Exit" in titles


class TestOpenSaveFolderAction:
    """托盘新增的「打开保存目录」：用系统文件管理器打开截图保存目录。"""

    def test_it_opens_the_configured_folder(self, monkeypatch, config):
        seen = {}
        monkeypatch.setattr("core.platform.shell.open_path",
                            lambda path: seen.setdefault("path", path) or True)
        config.set_screenshot_save_path("/tmp/shots")
        import core.actions as actions_module

        assert actions_module.run_action(
            "open_save_folder", SimpleNamespace(config_manager=config)) is True
        assert seen["path"] == "/tmp/shots"

    def test_no_configured_folder_is_reported(self, monkeypatch, config):
        monkeypatch.setattr("core.platform.shell.open_path",
                            lambda path: pytest.fail("没有目录时不该调用系统打开"))
        monkeypatch.setattr(config, "get_screenshot_save_path", lambda: "")
        import core.actions as actions_module

        assert actions_module.run_action(
            "open_save_folder", SimpleNamespace(config_manager=config)) is False

    def test_platform_failure_is_reported(self, monkeypatch, config):
        monkeypatch.setattr("core.platform.shell.open_path", lambda path: False)
        config.set_screenshot_save_path("/tmp/shots")
        import core.actions as actions_module

        assert actions_module.run_action(
            "open_save_folder", SimpleNamespace(config_manager=config)) is False


class TestTranslateClipboardImageAction:
    """「图片翻译」：剪贴板图片 → OCR → 翻译（复用截图翻译那条链路）。"""

    def _app(self, config):
        return SimpleNamespace(config_manager=config)

    def test_it_starts_the_translation_flow(self, monkeypatch, qapp, config):
        from PySide6.QtGui import QImage

        import core.actions as actions_module

        image = QImage(60, 40, QImage.Format.Format_ARGB32)
        image.fill(0xFF112233)
        monkeypatch.setattr(actions_module, "clipboard_image", lambda: image)
        calls = {}
        fake_manager = SimpleNamespace(
            translate_from_image=lambda **kwargs: calls.update(kwargs))
        monkeypatch.setattr("translation.TranslationManager.instance",
                            staticmethod(lambda: fake_manager))
        config.set_app_setting("translation_target_lang", "EN")

        assert actions_module.run_action(
            "translate_clipboard_image", self._app(config)) is True

        assert calls["pixmap"].width() == 60 and calls["pixmap"].height() == 40
        assert calls["target_lang"] == "EN"      # 参数来自配置，不是写死的

    def test_without_an_image_nothing_happens(self, monkeypatch, config):
        import core.actions as actions_module

        monkeypatch.setattr(actions_module, "clipboard_image", lambda: None)
        monkeypatch.setattr(
            "translation.TranslationManager.instance",
            staticmethod(lambda: pytest.fail("没有图片时不该启动翻译")),
        )

        assert actions_module.run_action(
            "translate_clipboard_image", self._app(config)) is False

    def test_the_action_is_registered_with_tray_and_entry(self):
        assert "translate_clipboard_image" in actions.APP_ENTRY_ACTIONS
        assert actions.ACTIONS_BY_ID["translate_clipboard_image"].tray is True

    def test_broken_params_fall_back_to_translate_defaults(self, monkeypatch, qapp, config):
        from PySide6.QtGui import QImage

        import core.actions as actions_module

        image = QImage(10, 10, QImage.Format.Format_ARGB32)
        monkeypatch.setattr(actions_module, "clipboard_image", lambda: image)
        monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)

        def _boom():
            raise RuntimeError("配置坏了")

        monkeypatch.setattr(config, "get_translation_request_params", _boom)
        calls = {}
        monkeypatch.setattr(
            "translation.TranslationManager.instance",
            staticmethod(lambda: SimpleNamespace(
                translate_from_image=lambda **kwargs: calls.update(kwargs))),
        )

        assert actions_module.run_action(
            "translate_clipboard_image", self._app(config)) is True
        # 参数读不出来也要能翻译（用 translate_from_image 自己的默认值）
        assert "target_lang" not in calls
