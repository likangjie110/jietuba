# -*- coding: utf-8 -*-
"""「全局鼠标」设置页：绑定行的增删、遮罩开关、忽略程序列表与保存/恢复默认。

界面只负责把配置表和界面控件对上——动作清单来自 ``core.actions``，手势与修饰键词汇
来自平台层，所以这里也断言「少了一项就该少一个下拉选项」，避免界面自己长出一份表。
"""
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from core import actions
from core.platform import pointer as platform_pointer
from settings.tool_settings import ToolSettingsManager
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_mouse import (
    _add_mouse_row, _render_ignored_rows, collect_mouse_gestures, create_mouse_page,
    read_ignored_apps, reset_mouse_page,
)


def _manager(tmp_path):
    qsettings = QSettings(str(tmp_path / "mouse_settings.ini"), QSettings.Format.IniFormat)
    return ToolSettingsManager(qsettings=qsettings)


def _dialog(manager):
    return SimpleNamespace(
        config_manager=manager,
        tr=lambda text: text,
        _get_input_style=lambda: "",
    )


def _page(qapp, tmp_path):
    manager = _manager(tmp_path)
    dialog = _dialog(manager)
    page = create_mouse_page(dialog)
    return manager, dialog, page


class TestGestureRows:
    def test_empty_config_shows_the_empty_hint(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            assert dialog._mouse_gesture_rows == []
            assert dialog._mouse_empty_hint.isVisible() is False  # 页面没显示过，只查状态标志
            assert collect_mouse_gestures(dialog) == {}
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_configured_bindings_become_rows(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_mouse_gesture("screenshot_copy", "ctrl", "wheel_up")
        dialog = _dialog(manager)

        page = create_mouse_page(dialog)
        try:
            assert len(dialog._mouse_gesture_rows) == 1
            row = dialog._mouse_gesture_rows[0]
            assert row["action"].currentData() == "screenshot_copy"
            assert row["modifier"].currentData() == "ctrl"
            assert row["gesture"].currentData() == "wheel_up"
            assert collect_mouse_gestures(dialog) == {
                "screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}
            }
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_unknown_action_in_config_is_not_bound(self, qapp, tmp_path):
        """配置里留着已删掉的动作时不能把页面卡住（下拉没有该项就退回第一项）。"""
        manager = _manager(tmp_path)
        manager.set_mouse_gestures({"no_such_action": {"modifier": "", "gesture": "wheel_up"}})
        dialog = _dialog(manager)

        page = create_mouse_page(dialog)
        try:
            assert dialog._mouse_gesture_rows == []
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_action_combo_lists_the_registry(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            _add_mouse_row(dialog)
            combo = dialog._mouse_gesture_rows[0]["action"]
            assert [combo.itemData(i) for i in range(combo.count())] == [
                action.id for action in actions.ACTIONS
            ]
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_gesture_and_modifier_combos_use_the_platform_vocabulary(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            _add_mouse_row(dialog)
            row = dialog._mouse_gesture_rows[0]
            assert [row["gesture"].itemData(i) for i in range(row["gesture"].count())] == list(
                platform_pointer.MOUSE_GESTURES
            )
            assert [row["modifier"].itemData(i) for i in range(row["modifier"].count())] == list(
                platform_pointer.MOUSE_MODIFIERS
            )
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_add_prefers_an_unbound_action(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            _add_mouse_row(dialog)                       # 默认第一个动作
            second = _add_mouse_row(dialog)              # 第二个应换一个动作
            assert dialog._mouse_gesture_rows[0]["action"].currentData() == actions.ACTIONS[0].id
            assert second["action"].currentData() == actions.ACTIONS[1].id
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_duplicate_action_keeps_the_last_row(self, qapp, tmp_path):
        """界面允许重复（用户中途改主意很正常），存的时候收敛成一张表。"""
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            first = _add_mouse_row(dialog, "screenshot_copy", "", "wheel_up")
            second = _add_mouse_row(dialog, "screenshot_copy", "ctrl", "wheel_down")
            assert first is not second

            assert collect_mouse_gestures(dialog) == {
                "screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_down"}
            }
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestIgnoredApps:
    def test_configured_names_are_shown(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_mouse_ignored_apps(["Finder", "chrome"])
        dialog = _dialog(manager)

        page = create_mouse_page(dialog)
        try:
            assert read_ignored_apps(dialog) == ["Finder", "chrome"]
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_typing_a_name_adds_it(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            dialog.mouse_ignored_edit.setText("  Finder  ")
            from ui.settings_ui.page_mouse import _add_ignored_app

            _add_ignored_app(dialog)

            assert read_ignored_apps(dialog) == ["Finder"]
            assert dialog.mouse_ignored_edit.text() == ""
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_blank_and_duplicate_names_are_ignored(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            from ui.settings_ui.page_mouse import _add_ignored_app

            dialog.mouse_ignored_edit.setText("   ")
            _add_ignored_app(dialog)
            dialog.mouse_ignored_edit.setText("Finder")
            _add_ignored_app(dialog)
            dialog.mouse_ignored_edit.setText("Finder")
            _add_ignored_app(dialog)

            assert read_ignored_apps(dialog) == ["Finder"]
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_removing_a_name_updates_the_list(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_mouse_ignored_apps(["Finder", "chrome"])
        dialog = _dialog(manager)

        page = create_mouse_page(dialog)
        try:
            from ui.settings_ui.page_mouse import _remove_ignored_app

            _remove_ignored_app(dialog, "Finder")

            assert read_ignored_apps(dialog) == ["chrome"]
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestResetAndSave:
    def test_reset_clears_everything(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_mouse_gesture("screenshot_copy", "ctrl", "wheel_up")
        manager.set_mouse_capture_overlay_enabled(True)
        manager.set_mouse_ignored_apps(["Finder"])
        dialog = _dialog(manager)

        page = create_mouse_page(dialog)
        try:
            reset_mouse_page(dialog)

            assert dialog._mouse_gesture_rows == []
            assert dialog.mouse_overlay_toggle.isChecked() is False
            assert read_ignored_apps(dialog) == []
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_dialog_accept_writes_the_page(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            _add_mouse_row(dialog, "screenshot_copy", "ctrl", "wheel_up")
            dialog.mouse_overlay_toggle.setChecked(True)
            dialog.mouse_ignored_edit.setText("Finder")
            from ui.settings_ui.page_mouse import _add_ignored_app

            _add_ignored_app(dialog)

            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)
            dialog.accept()

            assert manager.get_mouse_gestures() == {
                "screenshot_copy": {"modifier": "ctrl", "gesture": "wheel_up"}
            }
            assert manager.get_mouse_capture_overlay_enabled() is True
            assert manager.get_mouse_ignored_apps() == ["Finder"]
        finally:
            dialog.deleteLater()
            qapp.processEvents()


def test_every_page_row_survives_a_rebuild(qapp, tmp_path):
    """页面重建（切换语言时会重建）后配置必须原样回来。"""
    manager = _manager(tmp_path)
    manager.set_mouse_gesture("screenshot_pin", "", "middle_click")
    manager.set_mouse_ignored_apps(["Finder"])

    first = _dialog(manager)
    page = create_mouse_page(first)
    try:
        assert collect_mouse_gestures(first) == {
            "screenshot_pin": {"modifier": "", "gesture": "middle_click"}
        }
        assert read_ignored_apps(first) == ["Finder"]
    finally:
        page.deleteLater()
        qapp.processEvents()

    second = _dialog(manager)
    page = create_mouse_page(second)
    try:
        assert collect_mouse_gestures(second) == collect_mouse_gestures(first)
        assert read_ignored_apps(second) == ["Finder"]
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_ignored_rows_render_for_the_empty_case(qapp, tmp_path):
    _manager_, dialog, page = _page(qapp, tmp_path)
    try:
        _render_ignored_rows(dialog)
        assert read_ignored_apps(dialog) == []
    finally:
        page.deleteLater()
        qapp.processEvents()
