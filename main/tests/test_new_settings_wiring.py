# -*- coding: utf-8 -*-
"""2026-09-19 按参考清单补的设置：取值兜底、保存/重置/快照接线、以及行为是否真的生效。

驱动发布实现：配置读写的兜底逻辑在 `settings.tool_settings`，保存路径是对话框真实的
`_save_integration_settings`，行为侧是 `core.net`、`core.updates`、
`clipboard.core.manager` 的真实方法（只在网络/剪贴板这类**边界**上换替身）。
"""

import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from clipboard.core.manager import ClipboardManager
from clipboard.core.models import ClipboardItem

@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    manager = get_tool_settings_manager()
    # 这些键会被用例改来改去，跑完还原，免得上一个用例的选择漏到下一个
    keys = ("ocr_text_layout", "ocr_punctuation", "ocr_language", "ocr_dialog_triggers",
            "desktop_toolbar_mode", "desktop_toolbar_position", "tray_click_action",
            "clipboard_image_copy_mode", "clipboard_ignore_own_copy", "proxy_mode",
            "proxy_host", "proxy_port", "formula_engine", "formula_service_url",
            "formula_service_api_key", "formula_service_timeout", "update_source_url")
    snapshot = {key: manager.get_app_setting(key) for key in keys}
    yield manager
    for key, value in snapshot.items():
        manager.set_app_setting(key, value)


class TestConfigFallbacks:
    def test_unknown_values_fall_back_to_defaults(self, config):
        config.set_app_setting("ocr_text_layout", "nonsense")
        config.set_app_setting("ocr_punctuation", "???")
        config.set_app_setting("ocr_language", "klingon")
        config.set_app_setting("desktop_toolbar_mode", "hologram")
        config.set_app_setting("clipboard_image_copy_mode", "magic")
        config.set_app_setting("formula_engine", "gpt")

        assert config.get_ocr_text_layout() == "auto"
        assert config.get_ocr_punctuation() == "none"
        assert config.get_ocr_language() == "follow_app"
        assert config.get_desktop_toolbar_mode() == "none"
        assert config.get_clipboard_image_copy_mode() == "auto"
        assert config.get_formula_engine() == "ppocr_formula"

    def test_tray_click_action_must_exist_in_the_registry(self, config):
        from core import actions

        config.set_tray_click_action("not_an_action")
        assert config.get_tray_click_action() == "screenshot"

        assert "clipboard" in actions.ACTIONS_BY_ID
        config.set_tray_click_action("clipboard")
        assert config.get_tray_click_action() == "clipboard"

    def test_proxy_needs_a_complete_host_and_port(self, config):
        config.set_proxy_config("manual", "", 8080)
        assert config.get_proxy_config()["mode"] == "none"

        config.set_proxy_config("manual", "127.0.0.1", 0)
        assert config.get_proxy_config()["mode"] == "none"

        config.set_proxy_config("manual", "127.0.0.1", 8080)
        assert config.get_proxy_config() == {"mode": "manual", "host": "127.0.0.1", "port": 8080}

    def test_toolbar_position_survives_garbage(self, config):
        config.set_app_setting("desktop_toolbar_position", "abc")
        assert config.get_desktop_toolbar_position() is None

        config.set_desktop_toolbar_position(120, 340)
        assert config.get_desktop_toolbar_position() == (120, 340)

    def test_formula_service_timeout_is_clamped(self, config):
        config.set_app_setting("formula_service_timeout", 9999)
        assert config.get_formula_service_config()["timeout"] == 120

        config.set_app_setting("formula_service_timeout", "壞值")
        assert config.get_formula_service_config()["timeout"] == 15

    def test_update_source_defaults_to_the_github_releases_page(self, config):
        from core.updates import DEFAULT_UPDATE_SOURCE
        from core.constants import PROJECT_GITHUB_URL

        config.set_update_source_url("")
        assert config.get_update_source_url() == DEFAULT_UPDATE_SOURCE
        assert DEFAULT_UPDATE_SOURCE == f"{PROJECT_GITHUB_URL}/releases"

        config.set_update_source_url("https://example.com/releases")
        assert config.get_update_source_url() == "https://example.com/releases"


class TestNetworkProxy:
    def test_manual_proxy_is_applied_to_the_process(self, config, qapp):
        from core.net import apply_proxy_from_settings
        from PySide6.QtNetwork import QNetworkProxy

        config.set_proxy_config("manual", "127.0.0.1", 8118)
        try:
            assert apply_proxy_from_settings() == "manual"
            applied = QNetworkProxy.applicationProxy()
            assert applied.type() == QNetworkProxy.ProxyType.HttpProxy
            assert applied.hostName() == "127.0.0.1"
            assert applied.port() == 8118
        finally:
            config.set_proxy_config("none", "", 0)
            assert apply_proxy_from_settings() == "none"

    def test_direct_connection_clears_the_proxy(self, config, qapp):
        from core.net import apply_proxy_from_settings
        from PySide6.QtNetwork import QNetworkProxy

        assert apply_proxy_from_settings() == "none"
        assert QNetworkProxy.applicationProxy().type() == QNetworkProxy.ProxyType.NoProxy

    def test_verify_reports_a_missing_host(self, qapp):
        from core.net import verify_proxy

        ok, reason = verify_proxy("", 0)
        assert ok is False and "空" in reason


class TestUpdateSource:
    def test_open_uses_the_system_browser(self, qapp, monkeypatch):
        from core import updates

        opened = []
        monkeypatch.setattr("core.platform.shell.open_url", lambda url: opened.append(url) or True)

        assert updates.check_for_updates() is True
        assert opened == [updates.update_source_url()]
        assert opened[0].startswith("https://github.com/")

    def test_the_registry_action_reaches_the_same_entry(self, qapp, monkeypatch):
        """动作注册表里的 check_updates 走的就是这条入口（热键/手势/托盘都能绑它）。"""
        from core import actions

        opened = []
        monkeypatch.setattr("core.platform.shell.open_url", lambda url: opened.append(url) or True)

        assert "check_updates" in actions.ACTIONS_BY_ID
        assert "check_updates" in actions.APP_ENTRY_ACTIONS
        assert actions.run_action("check_updates", SimpleNamespace()) is True
        assert opened and opened[0].endswith("/releases")


class TestIgnoreOwnClipboardWrite:
    @staticmethod
    def _item(content="hello", content_type="text"):
        return ClipboardItem(id=1, content=content, content_type=content_type)

    def test_the_same_content_is_ignored(self, config, qapp):
        manager = ClipboardManager()
        manager.mark_own_write(self._item())

        assert manager._consume_own_write(self._item()) is True
        # 标记是一次性的：紧接着又来一条同样的内容，就不是「自己回写」了
        assert manager._consume_own_write(self._item()) is False

    def test_other_content_is_not_ignored(self, config, qapp):
        manager = ClipboardManager()
        manager.mark_own_write(self._item("aaa"))

        assert manager._consume_own_write(self._item("bbb")) is False

    def test_the_setting_can_turn_the_guard_off(self, config, qapp):
        manager = ClipboardManager()
        config.set_clipboard_ignore_own_copy(False)
        manager.mark_own_write(self._item())

        assert manager._consume_own_write(self._item()) is False

    def test_a_stale_marker_expires(self, config, qapp):
        manager = ClipboardManager()
        manager.mark_own_write(self._item())
        manager._own_write_until = time.monotonic() - 1

        assert manager._consume_own_write(self._item()) is False


class TestImageFileOnClipboard:
    def _image(self) -> QImage:
        image = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#123456"))
        return image

    def test_auto_puts_both_the_file_and_the_image(self, qapp):
        from core.clipboard_utils import attach_image_file_to_clipboard

        assert attach_image_file_to_clipboard(self._image(), keep_image=True) is True

        mime = QApplication.clipboard().mimeData()
        urls = mime.urls()
        assert urls, "剪贴板里没有文件 URL"
        path = urls[0].toLocalFile()
        assert path.endswith(".png")
        from pathlib import Path

        assert Path(path).exists(), "文件 URL 指向的 PNG 不存在"
        assert mime.hasImage(), "自动档应当同时保留图片数据"

    def test_file_only_drops_the_image_data(self, qapp):
        from core.clipboard_utils import attach_image_file_to_clipboard

        assert attach_image_file_to_clipboard(self._image(), keep_image=False) is True

        mime = QApplication.clipboard().mimeData()
        assert mime.urls()
        assert not mime.hasImage(), "只给文件档不该再放图片数据"

    def test_empty_image_is_refused(self, qapp):
        from core.clipboard_utils import attach_image_file_to_clipboard

        assert attach_image_file_to_clipboard(QImage(), keep_image=True) is False


class _FakeDialog(SimpleNamespace):
    """只放被读写到的控件：保存/重置/快照都是 hasattr 分支，缺了就走不到。"""


def _make_widgets(config):
    from ui.fluent_lite import ComboBox, SwitchSettingCard, FluentIcon
    from PySide6.QtWidgets import QCheckBox, QLineEdit, QSpinBox

    dialog = _FakeDialog(config_manager=config)
    dialog.desktop_toolbar_combo = ComboBox()
    for value in ("none", "floating_ball"):
        dialog.desktop_toolbar_combo.addItem(value, userData=value)
    dialog.tray_click_combo = ComboBox()
    for value in ("screenshot", "clipboard"):
        dialog.tray_click_combo.addItem(value, userData=value)
    dialog.proxy_mode_combo = ComboBox()
    for value in ("none", "manual"):
        dialog.proxy_mode_combo.addItem(value, userData=value)
    dialog.proxy_host_input = QLineEdit()
    dialog.proxy_port_spin = QSpinBox()
    dialog.proxy_port_spin.setRange(0, 65535)
    dialog.update_source_input = QLineEdit()
    dialog.clipboard_image_mode_combo = ComboBox()
    for value in ("auto", "file_only", "image_only"):
        dialog.clipboard_image_mode_combo.addItem(value, userData=value)
    dialog.clipboard_ignore_own_toggle = SwitchSettingCard(
        FluentIcon.PASTE, "x", "y")
    dialog.ocr_layout_combo = ComboBox()
    for value in ("auto", "lines", "single"):
        dialog.ocr_layout_combo.addItem(value, userData=value)
    dialog.ocr_punctuation_combo = ComboBox()
    for value in ("none", "strip_trailing", "to_halfwidth"):
        dialog.ocr_punctuation_combo.addItem(value, userData=value)
    dialog.ocr_language_combo = ComboBox()
    for value in ("follow_app", "zh", "en", "ja", "ko"):
        dialog.ocr_language_combo.addItem(value, userData=value)
    dialog.ocr_dialog_checks = {t: QCheckBox() for t in ("capture", "copy_selection", "copy_all")}
    dialog.formula_engine_combo = ComboBox()
    for value in ("ppocr_formula", "external_service"):
        dialog.formula_engine_combo.addItem(value, userData=value)
    dialog.formula_url_input = QLineEdit()
    dialog.formula_key_input = QLineEdit()
    return dialog


def _select(combo, value):
    combo.setCurrentIndex(combo.findData(value))


class TestDialogWiring:
    def test_save_writes_every_new_setting(self, config, qapp, monkeypatch):
        from ui.settings_ui.dialog import SettingsDialog

        dialog = _make_widgets(config)
        applied = SimpleNamespace(toolbar_calls=0)
        monkeypatch.setattr(
            "main_app.main_app_instance",
            lambda: SimpleNamespace(
                apply_desktop_toolbar_mode=lambda: setattr(applied, "toolbar_calls",
                                                           applied.toolbar_calls + 1)),
        )
        proxy_calls = []
        monkeypatch.setattr("core.net.apply_proxy_from_settings",
                            lambda: proxy_calls.append(True))

        _select(dialog.desktop_toolbar_combo, "floating_ball")
        _select(dialog.tray_click_combo, "clipboard")
        _select(dialog.proxy_mode_combo, "manual")
        dialog.proxy_host_input.setText("10.0.0.1")
        dialog.proxy_port_spin.setValue(7890)
        dialog.update_source_input.setText("https://example.com/rel")
        _select(dialog.clipboard_image_mode_combo, "file_only")
        dialog.clipboard_ignore_own_toggle.setChecked(False)
        _select(dialog.ocr_layout_combo, "lines")
        _select(dialog.ocr_punctuation_combo, "strip_trailing")
        _select(dialog.ocr_language_combo, "ja")
        dialog.ocr_dialog_checks["copy_all"].setChecked(True)
        _select(dialog.formula_engine_combo, "external_service")
        dialog.formula_url_input.setText("https://api.example.com/formula")
        dialog.formula_key_input.setText("sk-test")

        SettingsDialog._save_integration_settings(dialog)

        assert config.get_desktop_toolbar_mode() == "floating_ball"
        assert config.get_tray_click_action() == "clipboard"
        assert config.get_proxy_config() == {"mode": "manual", "host": "10.0.0.1", "port": 7890}
        assert config.get_app_setting("update_source_url") == "https://example.com/rel"
        assert config.get_clipboard_image_copy_mode() == "file_only"
        assert config.get_clipboard_ignore_own_copy() is False
        assert config.get_ocr_text_layout() == "lines"
        assert config.get_ocr_punctuation() == "strip_trailing"
        assert config.get_ocr_language() == "ja"
        assert config.get_ocr_dialog_triggers() == ["copy_all"]
        assert config.get_formula_engine() == "external_service"
        assert config.get_formula_service_config()["url"] == "https://api.example.com/formula"
        assert config.get_formula_service_config()["api_key"] == "sk-test"
        assert proxy_calls == [True], "保存后没有把代理套上"
        assert applied.toolbar_calls == 1, "保存后没有按新模式显示/隐藏悬浮球"

    def test_save_without_a_running_app_still_persists(self, config, qapp, monkeypatch):
        from ui.settings_ui.dialog import SettingsDialog

        monkeypatch.setattr("main_app.main_app_instance", lambda: None)
        monkeypatch.setattr("core.net.apply_proxy_from_settings", lambda: "none")
        dialog = _make_widgets(config)

        SettingsDialog._save_integration_settings(dialog)

        assert config.get_desktop_toolbar_mode() == "none"

    def test_reset_restores_the_defaults_in_the_ui(self, config, qapp):
        from ui.settings_ui.dialog import _reset_integration_controls

        dialog = _make_widgets(config)
        _select(dialog.desktop_toolbar_combo, "floating_ball")
        _select(dialog.ocr_layout_combo, "single")
        dialog.ocr_dialog_checks["capture"].setChecked(True)
        dialog.formula_url_input.setText("https://example.com")
        dialog.proxy_host_input.setText("1.2.3.4")
        dialog.proxy_port_spin.setValue(999)
        dialog.update_source_input.setText("https://example.com/rel")

        _reset_integration_controls(dialog)

        assert dialog.desktop_toolbar_combo.currentData() == "none"
        assert dialog.ocr_layout_combo.currentData() == "auto"
        assert not any(check.isChecked() for check in dialog.ocr_dialog_checks.values())
        assert dialog.formula_url_input.text() == ""
        assert dialog.proxy_host_input.text() == ""
        assert dialog.proxy_port_spin.value() == 0
        assert dialog.update_source_input.text() == ""

    def test_snapshot_notices_a_new_setting_change(self, config, qapp):
        from ui.settings_ui.dialog import _snapshot_integration_controls

        dialog = _make_widgets(config)
        before = {}
        _snapshot_integration_controls(dialog, before)

        _select(dialog.desktop_toolbar_combo, "floating_ball")
        dialog.ocr_dialog_checks["capture"].setChecked(True)
        dialog.formula_key_input.setText("sk-new")
        after = {}
        _snapshot_integration_controls(dialog, after)

        assert before != after, "改了新设置却没被算成未保存变更"
        assert after["desktop_toolbar_combo"] != before["desktop_toolbar_combo"]
        assert after["ocr_dialog_triggers"] == ["capture"]
        assert after["formula_key_input"] == "sk-new"

    def test_refresh_reads_the_config_back(self, config, qapp):
        from ui.settings_ui.dialog import _refresh_integration_controls

        config.set_desktop_toolbar_mode("floating_ball")
        config.set_ocr_text_layout("single")
        config.set_ocr_dialog_triggers(["copy_selection"])
        config.set_proxy_config("manual", "127.0.0.1", 1080)
        dialog = _make_widgets(config)

        _refresh_integration_controls(dialog)

        assert dialog.desktop_toolbar_combo.currentData() == "floating_ball"
        assert dialog.ocr_layout_combo.currentData() == "single"
        assert dialog.ocr_dialog_checks["copy_selection"].isChecked()
        assert dialog.proxy_mode_combo.currentData() == "manual"
        assert dialog.proxy_host_input.text() == "127.0.0.1"
        assert dialog.proxy_port_spin.value() == 1080
