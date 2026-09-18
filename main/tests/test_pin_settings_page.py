# -*- coding: utf-8 -*-
"""「贴图设置」页：读取、保存、恢复默认。

这些值以前写死在 pin/pin_window.py 里（滚轮步长 1.05、透明度步长 0.05、阴影恒开），
现在由这一页读写；范围常量两边共用，所以这里也断言「界面能选到的范围就是贴图窗口
认的范围」。
"""
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from pin import pin_actions
from pin.pin_text_pin import TEXT_FONT_SIZE_RANGE, TEXT_MAX_WIDTH_RANGE
from settings.tool_settings import (
    PIN_HISTORY_RANGE, PIN_OPACITY_RANGE, PIN_OPACITY_STEP_RANGE, PIN_ZOOM_STEP_RANGE, ToolSettingsManager,
)
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_pin import (
    collect_pin_settings, create_pin_page, refresh_pin_page, reset_pin_page,
)


def _manager(tmp_path):
    settings = QSettings(str(tmp_path / "pin.ini"), QSettings.Format.IniFormat)
    return ToolSettingsManager(qsettings=settings)


def _dialog(manager):
    return SimpleNamespace(config_manager=manager, tr=lambda text: text)


def _page(qapp, tmp_path):
    manager = _manager(tmp_path)
    dialog = _dialog(manager)
    return manager, dialog, create_pin_page(dialog)


class TestPinPageDefaults:
    def test_values_come_from_the_config(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            assert dialog.pin_zoom_step_spin.value() == 1.05
            assert dialog.pin_opacity_step_spin.value() == 0.05
            assert dialog.pin_default_opacity_spin.value() == 1.0
            assert dialog.pin_shadow_toggle.isChecked() is True
            assert dialog.pin_auto_toolbar_toggle.isChecked() is False
            assert dialog.pin_new_position_combo.currentData() == "selection"
            assert dialog.pin_order_combo.currentData() == "top"
            assert dialog.pin_close_confirm_toggle.isChecked() is False
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_configured_values_are_shown(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_pin_zoom_step(1.2)
        manager.set_pin_opacity_step(0.15)
        manager.set_pin_default_opacity(0.6)
        manager.set_pin_shadow_enabled(False)
        manager.set_pin_auto_toolbar(True)
        manager.set_pin_new_position("center")
        manager.set_pin_order("bottom")
        manager.set_pin_close_confirm(True)
        dialog = _dialog(manager)

        page = create_pin_page(dialog)
        try:
            assert dialog.pin_zoom_step_spin.value() == 1.2
            assert dialog.pin_opacity_step_spin.value() == 0.15
            assert dialog.pin_default_opacity_spin.value() == 0.6
            assert dialog.pin_shadow_toggle.isChecked() is False
            assert dialog.pin_auto_toolbar_toggle.isChecked() is True
            assert dialog.pin_new_position_combo.currentData() == "center"
            assert dialog.pin_order_combo.currentData() == "bottom"
            assert dialog.pin_close_confirm_toggle.isChecked() is True
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_spin_ranges_match_the_shared_constants(self, qapp, tmp_path):
        """界面能选到的范围必须就是贴图窗口认的范围，否则选了会被静默夹掉。"""
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            assert (dialog.pin_zoom_step_spin.minimum(), dialog.pin_zoom_step_spin.maximum()) == PIN_ZOOM_STEP_RANGE
            assert (dialog.pin_opacity_step_spin.minimum(), dialog.pin_opacity_step_spin.maximum()) == PIN_OPACITY_STEP_RANGE
            assert (dialog.pin_default_opacity_spin.minimum(), dialog.pin_default_opacity_spin.maximum()) == PIN_OPACITY_RANGE
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_position_combo_offers_every_supported_mode(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            combo = dialog.pin_new_position_combo
            assert {combo.itemData(i) for i in range(combo.count())} == {
                "selection", "cursor", "center",
            }
            order = dialog.pin_order_combo
            assert {order.itemData(i) for i in range(order.count())} == {"top", "bottom"}
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestPinPageCollectAndReset:
    def test_collect_reads_the_widgets(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            dialog.pin_zoom_step_spin.setValue(1.15)
            dialog.pin_close_confirm_toggle.setChecked(True)
            dialog.pin_order_combo.setCurrentIndex(
                dialog.pin_order_combo.findData("bottom")
            )

            values = collect_pin_settings(dialog)

            assert values["zoom_step"] == 1.15
            assert values["close_confirm"] is True
            assert values["order"] == "bottom"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_collect_skips_a_page_that_was_never_built(self):
        assert collect_pin_settings(SimpleNamespace()) == {}

    def test_reset_restores_every_default(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_pin_zoom_step(1.2)
        manager.set_pin_shadow_enabled(False)
        manager.set_pin_new_position("cursor")
        dialog = _dialog(manager)

        page = create_pin_page(dialog)
        try:
            reset_pin_page(dialog)

            assert dialog.pin_zoom_step_spin.value() == 1.05
            assert dialog.pin_shadow_toggle.isChecked() is True
            assert dialog.pin_new_position_combo.currentData() == "selection"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_refresh_rereads_the_config(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        dialog = _dialog(manager)
        page = create_pin_page(dialog)
        try:
            manager.set_pin_zoom_step(1.25)
            manager.set_pin_default_opacity(0.5)
            refresh_pin_page(dialog)

            assert dialog.pin_zoom_step_spin.value() == 1.25
            assert dialog.pin_default_opacity_spin.value() == 0.5
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestDialogPersistence:
    def test_accept_writes_every_pin_setting(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            dialog.pin_zoom_step_spin.setValue(1.1)
            dialog.pin_opacity_step_spin.setValue(0.2)
            dialog.pin_default_opacity_spin.setValue(0.75)
            dialog.pin_shadow_toggle.setChecked(False)
            dialog.pin_auto_toolbar_toggle.setChecked(True)
            dialog.pin_new_position_combo.setCurrentIndex(
                dialog.pin_new_position_combo.findData("center")
            )
            dialog.pin_order_combo.setCurrentIndex(
                dialog.pin_order_combo.findData("bottom")
            )
            dialog.pin_close_confirm_toggle.setChecked(True)

            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)
            dialog.accept()

            assert manager.get_pin_zoom_step() == 1.1
            assert manager.get_pin_opacity_step() == 0.2
            assert manager.get_pin_default_opacity() == 0.75
            assert manager.get_pin_shadow_enabled() is False
            assert manager.get_pin_auto_toolbar() is True
            assert manager.get_pin_new_position() == "center"
            assert manager.get_pin_order() == "bottom"
            assert manager.get_pin_close_confirm() is True
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_the_pin_page_is_its_own_nav_entry(self, monkeypatch, qapp, tmp_path):
        """贴图设置从「其他」页搬出来了：它该有独立的导航项与标题。"""
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        dialog = SettingsDialog(manager)

        try:
            routes = [item[0] for item in dialog._nav_items]
            assert routes[:4] == ["shortcuts", "mouse", "capture", "pin"]
            dialog._on_nav_changed(3, "pin")
            assert dialog.content_title.text() == "Pin Settings"
        finally:
            dialog.deleteLater()
            qapp.processEvents()


class TestPinGestureTable:
    """贴图窗口的鼠标手势绑定（块③）：固定七个手势，每个一个动作下拉。"""

    def test_defaults_are_shown(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            combos = dialog.pin_gesture_combos
            assert combos["wheel_up"].currentData() == "zoom_in"
            assert combos["wheel_down"].currentData() == "zoom_out"
            assert combos["ctrl_wheel_up"].currentData() == "opacity_up"
            assert combos["right_click"].currentData() == "context_menu"
            assert combos["middle_click"].currentData() == "none"
            assert combos["double_click"].currentData() == "none"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_every_gesture_and_action_is_offered(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            assert set(dialog.pin_gesture_combos) == set(pin_actions.GESTURES)
            combo = dialog.pin_gesture_combos["wheel_up"]
            values = [combo.itemData(i) for i in range(combo.count())]
            assert values[0] == pin_actions.ACTION_NONE
            assert set(values[1:]) == set(pin_actions.PIN_ACTIONS_BY_ID)
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_configured_bindings_are_shown(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_pin_mouse_actions({"wheel_up": "copy_and_close"})
        dialog = _dialog(manager)

        page = create_pin_page(dialog)
        try:
            assert dialog.pin_gesture_combos["wheel_up"].currentData() == "copy_and_close"
            # 没写进配置的手势仍显示默认值
            assert dialog.pin_gesture_combos["wheel_down"].currentData() == "zoom_out"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_collect_and_reset(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            combo = dialog.pin_gesture_combos["double_click"]
            combo.setCurrentIndex(combo.findData("copy_and_close"))

            values = collect_pin_settings(dialog)
            assert values["mouse_actions"]["double_click"] == "copy_and_close"

            reset_pin_page(dialog)
            assert dialog.pin_gesture_combos["double_click"].currentData() == "none"
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_accept_writes_the_table(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            combo = dialog.pin_gesture_combos["right_click"]
            combo.setCurrentIndex(combo.findData("copy_and_close"))
            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)

            dialog.accept()

            assert manager.get_pin_mouse_actions()["right_click"] == "copy_and_close"
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_broken_config_falls_back_to_defaults(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.qsettings.setValue("pin/mouse_actions", "{不是 json")
        dialog = _dialog(manager)

        page = create_pin_page(dialog)
        try:
            assert dialog.pin_gesture_combos["wheel_up"].currentData() == "zoom_in"
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestPinRestoreSettings:
    """「启动恢复未关闭的贴图」与「贴图历史数量」。"""

    def test_defaults_are_shown(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            assert dialog.pin_restore_toggle.isChecked() is False
            assert dialog.pin_history_limit_spin.value() == 10
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_spin_range_matches_the_shared_constant(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            spin = dialog.pin_history_limit_spin
            assert (spin.minimum(), spin.maximum()) == PIN_HISTORY_RANGE
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_collect_and_reset(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            dialog.pin_restore_toggle.setChecked(True)
            dialog.pin_history_limit_spin.setValue(25)

            values = collect_pin_settings(dialog)
            assert values["restore_on_startup"] is True
            assert values["history_limit"] == 25

            reset_pin_page(dialog)
            assert dialog.pin_restore_toggle.isChecked() is False
            assert dialog.pin_history_limit_spin.value() == 10
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_accept_writes_both_keys(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            dialog.pin_restore_toggle.setChecked(True)
            dialog.pin_history_limit_spin.setValue(7)
            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)

            dialog.accept()

            assert manager.get_pin_restore_on_startup() is True
            assert manager.get_pin_history_limit() == 7
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_history_limit_is_clamped_by_the_accessor(self, tmp_path):
        manager = _manager(tmp_path)

        manager.set_pin_history_limit(999)
        assert manager.get_pin_history_limit() == PIN_HISTORY_RANGE[1]
        manager.set_pin_history_limit(0)
        assert manager.get_pin_history_limit() == PIN_HISTORY_RANGE[0]


class TestTextPinSettings:
    """「文字贴图」的字号与宽度（把剪贴板文字做成贴图用）。"""

    def test_defaults_and_ranges_come_from_the_shared_constants(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            font = dialog.pin_text_font_size_spin
            width = dialog.pin_text_max_width_spin
            assert font.value() == 14
            assert width.value() == 480
            assert (font.minimum(), font.maximum()) == TEXT_FONT_SIZE_RANGE
            assert (width.minimum(), width.maximum()) == TEXT_MAX_WIDTH_RANGE
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_collect_and_accept_write_both_keys(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            dialog.pin_text_font_size_spin.setValue(22)
            dialog.pin_text_max_width_spin.setValue(300)
            assert collect_pin_settings(dialog)["text_font_size"] == 22

            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)
            dialog.accept()

            assert manager.get_pin_text_font_size() == 22
            assert manager.get_pin_text_max_width() == 300
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_reset_goes_back_to_defaults(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_pin_text_font_size(30)
        dialog = _dialog(manager)

        page = create_pin_page(dialog)
        try:
            reset_pin_page(dialog)

            assert dialog.pin_text_font_size_spin.value() == 14
        finally:
            page.deleteLater()
            qapp.processEvents()
