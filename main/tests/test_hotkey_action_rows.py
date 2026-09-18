# -*- coding: utf-8 -*-
"""「快捷键/动作」页的全局动作部分：行增删、托盘开关、清除全部与保存。

动作清单来自 ``core.actions``，热键与托盘开关来自 ``app/action_hotkeys`` /
``app/action_tray``；这一页只负责把两边对上，所以这里也断言「注册表少一项就少一个选项」。
"""
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from core import actions
from settings.tool_settings import ToolSettingsManager
from ui.fluent_lite.switch import SwitchButton
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_hotkey import (
    _add_action_row, _iter_global_hotkey_edits, _remove_action_row, collect_action_hotkeys,
    collect_action_tray_flags, create_hotkey_page, refresh_action_rows, reset_action_rows,
)


def _manager(tmp_path):
    settings = QSettings(str(tmp_path / "hotkeys.ini"), QSettings.Format.IniFormat)
    return ToolSettingsManager(qsettings=settings)


def _dialog(manager):
    return SimpleNamespace(
        config_manager=manager,
        tr=lambda text: text,
        _get_input_style=lambda: "",
    )


def _page(qapp, tmp_path):
    manager = _manager(tmp_path)
    dialog = _dialog(manager)
    page = create_hotkey_page(dialog)
    return manager, dialog, page


def _row_ids(dialog):
    return [row["action"].currentData() for row in dialog._action_hotkey_rows]


class TestActionRows:
    def test_defaults_become_rows(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            assert _row_ids(dialog) == ["screenshot", "clipboard", "open_translation"]
            assert dialog._action_hotkey_rows[0]["primary"].text() == "ctrl+1"
            assert dialog._action_hotkey_rows[0]["tray"].isChecked() is True
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_action_combo_lists_the_registry(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            combo = dialog._action_hotkey_rows[0]["action"]
            assert [combo.itemData(i) for i in range(combo.count())] == [
                action.id for action in actions.ACTIONS
            ]
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_add_prefers_an_action_that_is_not_listed_yet(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            row = _add_action_row(dialog)

            assert row["action"].currentData() == "screenshot_copy"
            # 托盘开关的默认值来自注册表，这里不写死方向（注册表调整默认值时用例仍成立）
            assert row["tray"].isChecked() is actions.ACTIONS_BY_ID["screenshot_copy"].tray
            assert isinstance(row["tray"], SwitchButton)
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_remove_drops_the_row_and_its_hotkeys(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            row = dialog._action_hotkey_rows[1]
            _remove_action_row(dialog, row)

            assert _row_ids(dialog) == ["screenshot", "open_translation"]
            assert "clipboard" not in collect_action_hotkeys(dialog)
            assert "clipboard" not in collect_action_tray_flags(dialog)
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_unknown_action_in_config_is_not_shown(self, qapp, tmp_path):
        """配置里留着已删掉的动作时不能把页面卡住，也不该悄悄换成别的动作。"""
        manager = _manager(tmp_path)
        manager.set_action_hotkeys({"no_such_action": ["ctrl+9", ""]})
        dialog = _dialog(manager)

        page = create_hotkey_page(dialog)
        try:
            assert dialog._action_hotkey_rows == []
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_every_row_hotkey_edit_is_in_the_conflict_domain(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            edits = list(_iter_global_hotkey_edits(dialog))

            assert len(edits) == len(dialog._action_hotkey_rows) * 2
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_configured_hotkeys_and_tray_flags_come_back(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_action_hotkeys({"screenshot": ["ctrl+alt+1", "ctrl+alt+2"]})
        manager.set_action_tray_flags({"screenshot": False})
        dialog = _dialog(manager)

        page = create_hotkey_page(dialog)
        try:
            row = dialog._action_hotkey_rows[0]
            assert (row["primary"].text(), row["secondary"].text()) == ("ctrl+alt+1", "ctrl+alt+2")
            assert row["tray"].isChecked() is False
            assert collect_action_hotkeys(dialog) == {
                "screenshot": ["ctrl+alt+1", "ctrl+alt+2"]
            }
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestClearAndReset:
    def test_reset_goes_back_to_the_defaults(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_action_hotkeys({"gif_capture": ["alt+g", ""]})
        dialog = _dialog(manager)

        page = create_hotkey_page(dialog)
        try:
            assert _row_ids(dialog) == ["gif_capture"]

            reset_action_rows(dialog)

            assert _row_ids(dialog) == ["screenshot", "clipboard", "open_translation"]
            assert dialog._action_hotkey_rows[0]["primary"].text() == "ctrl+1"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_refresh_rereads_the_config(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        dialog = _dialog(manager)
        page = create_hotkey_page(dialog)
        try:
            manager.set_action_hotkeys({"long_screenshot": ["alt+l", ""]})
            refresh_action_rows(dialog)

            assert _row_ids(dialog) == ["long_screenshot"]
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestDialogPersistence:
    def test_accept_writes_both_tables(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            row = dialog._action_hotkey_rows[1]          # clipboard
            row["primary"].setText("ctrl+alt+5")
            row["tray"].setChecked(False)
            _add_action_row(dialog, "gif_capture", "alt+g", "")
            dialog._action_hotkey_rows[-1]["tray"].setChecked(True)

            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)
            dialog.accept()

            assert manager.get_action_hotkeys()["clipboard"] == ["ctrl+alt+5", ""]
            assert manager.get_action_hotkeys()["gif_capture"] == ["alt+g", ""]
            assert manager.get_action_tray_flags()["clipboard"] is False
            assert manager.get_action_tray_flags()["gif_capture"] is True
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_accept_refuses_to_persist_a_duplicate(self, monkeypatch, qapp, tmp_path):
        """冲突值不落盘，窗口也不关闭。"""
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "ui.settings_ui.dialog.show_warning_dialog",
            lambda *_a, **_k: None,
        )
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            dialog._action_hotkey_rows[0]["secondary"].setText("ctrl+1")
            accepted = []
            monkeypatch.setattr(SettingsDialog, "close", lambda _self: accepted.append(True))

            dialog.accept()

            assert accepted == []
            assert manager.get_action_hotkeys()["screenshot"] == ["ctrl+1", ""]
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_snapshot_notices_row_changes(self, qapp, tmp_path):
        """加/删行动作也要算未保存变更，否则关窗口时会被静默丢掉。"""
        manager = _manager(tmp_path)
        dialog = _dialog(manager)
        page = create_hotkey_page(dialog)
        try:
            before = SettingsDialog._snapshot_settings(dialog)
            _add_action_row(dialog)
            after = SettingsDialog._snapshot_settings(dialog)

            assert before != after
            assert len(after["action_hotkeys"]) == len(before["action_hotkeys"]) + 1
        finally:
            page.deleteLater()
            qapp.processEvents()
