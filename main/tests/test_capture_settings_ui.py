import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, QTranslator

from settings.tool_settings import ToolSettingsManager
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_capture import create_capture_page


def _dialog_for(manager):
    """只够建页面用的假对话框。

    页面里会连一些对话框自己的回调（改保存目录、打开目录），假对象上补成空函数即可。
    """
    return SimpleNamespace(
        config_manager=manager, tr=lambda text: text, _get_input_style=lambda: "",
        _change_save_dir=lambda: None, _open_save_dir=lambda: None,
        _open_save_path=lambda: None,
    )


def _manager(tmp_path):
    qsettings = QSettings(
        str(tmp_path / "capture_settings.ini"),
        QSettings.Format.IniFormat,
    )
    return ToolSettingsManager(qsettings=qsettings)


@pytest.mark.parametrize("enabled", [True, False])
def test_capture_page_reads_double_click_toggle(qapp, tmp_path, enabled):
    manager = _manager(tmp_path)
    manager.set_double_click_copy_close_enabled(enabled)
    dialog = SimpleNamespace(
        config_manager=manager,
        tr=lambda text: text,
        _change_save_dir=lambda: None,
        _open_save_dir=lambda: None,
    )

    page = create_capture_page(dialog)

    try:
        assert dialog.double_click_copy_close_toggle.isChecked() is enabled
    finally:
        page.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("enabled", [True, False])
def test_capture_page_reads_annotation_behavior_toggles(qapp, tmp_path, enabled):
    manager = _manager(tmp_path)
    manager.set_cross_tool_selection_enabled(enabled)
    manager.set_text_always_on_top_enabled(enabled)
    dialog = SimpleNamespace(
        config_manager=manager,
        tr=lambda text: text,
        _change_save_dir=lambda: None,
        _open_save_dir=lambda: None,
    )

    page = create_capture_page(dialog)

    try:
        assert dialog.cross_tool_selection_toggle.isChecked() is enabled
        assert dialog.text_always_on_top_toggle.isChecked() is enabled
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_settings_dialog_saves_double_click_toggle(monkeypatch, qapp, tmp_path):
    manager = _manager(tmp_path)
    manager.set_log_dir(str(tmp_path))
    monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
        lambda _self, _hotkey: True,
    )
    dialog = SettingsDialog(manager)

    for attr in (
        "log_toggle",
        "autostart_toggle",
        "language_combo",
        "_ui_theme_combo",
        "_appearance_theme_color",
        "_appearance_mask_color",
        "_inapp_edits",
    ):
        if hasattr(dialog, attr):
            delattr(dialog, attr)

    dialog._settings_snapshot = dialog._snapshot_settings()
    dialog.double_click_copy_close_toggle.setChecked(False)

    assert dialog._has_unsaved_changes()
    dialog.accept()
    assert manager.get_double_click_copy_close_enabled() is False

    dialog.deleteLater()
    qapp.processEvents()


def test_settings_dialog_saves_annotation_behavior_toggles(
    monkeypatch,
    qapp,
    tmp_path,
):
    manager = _manager(tmp_path)
    manager.set_log_dir(str(tmp_path))
    monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
        lambda _self, _hotkey: True,
    )
    dialog = SettingsDialog(manager)

    for attr in (
        "log_toggle",
        "autostart_toggle",
        "language_combo",
        "_ui_theme_combo",
        "_appearance_theme_color",
        "_appearance_mask_color",
        "_inapp_edits",
    ):
        if hasattr(dialog, attr):
            delattr(dialog, attr)

    dialog._settings_snapshot = dialog._snapshot_settings()
    dialog.cross_tool_selection_toggle.setChecked(False)
    dialog.text_always_on_top_toggle.setChecked(False)

    assert dialog._has_unsaved_changes()
    dialog.accept()
    assert manager.get_cross_tool_selection_enabled() is False
    assert manager.get_text_always_on_top_enabled() is False

    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("mode", ["none", "window", "element"])
def test_capture_page_reads_ui_detection_mode(qapp, tmp_path, mode):
    """UI 检测档位要能回填，元素边距只在 element 档可编辑。"""
    manager = _manager(tmp_path)
    manager.set_ui_detection(mode)
    dialog = SimpleNamespace(
        config_manager=manager,
        tr=lambda text: text,
        _change_save_dir=lambda: None,
        _open_save_dir=lambda: None,
    )

    page = create_capture_page(dialog)

    try:
        assert dialog.ui_detection_combo.currentData() == mode
        assert dialog.ui_detection_margin_spin.isEnabled() is (mode == "element")
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_capture_page_reads_ui_detection_margin(qapp, tmp_path):
    manager = _manager(tmp_path)
    manager.set_ui_detection("element")
    manager.set_ui_detection_margin(12)
    dialog = SimpleNamespace(
        config_manager=manager,
        tr=lambda text: text,
        _change_save_dir=lambda: None,
        _open_save_dir=lambda: None,
    )

    page = create_capture_page(dialog)

    try:
        assert dialog.ui_detection_margin_spin.value() == 12
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_settings_dialog_saves_ui_detection(monkeypatch, qapp, tmp_path):
    manager = _manager(tmp_path)
    manager.set_log_dir(str(tmp_path))
    monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
        lambda _self, _hotkey: True,
    )
    dialog = SettingsDialog(manager)

    for attr in (
        "log_toggle",
        "autostart_toggle",
        "language_combo",
        "_ui_theme_combo",
        "_appearance_theme_color",
        "_appearance_mask_color",
        "_inapp_edits",
    ):
        if hasattr(dialog, attr):
            delattr(dialog, attr)

    dialog._settings_snapshot = dialog._snapshot_settings()
    dialog.ui_detection_combo.setCurrentIndex(dialog.ui_detection_combo.findData("window"))
    dialog.ui_detection_margin_spin.setValue(9)

    assert dialog._has_unsaved_changes()
    dialog.accept()

    assert manager.get_ui_detection() == "window"
    assert manager.get_ui_detection_margin() == 9

    dialog.deleteLater()
    qapp.processEvents()


def test_global_hotkey_duplicates_are_marked_and_never_persisted(
    monkeypatch,
    qapp,
    tmp_path,
):
    manager = _manager(tmp_path)
    manager.set_hotkey("ctrl+shift+a")
    manager.set_hotkey_2("ctrl+alt+a")
    monkeypatch.setattr(
        "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
        lambda _self, _hotkey: True,
    )
    warnings = []
    monkeypatch.setattr(
        "ui.settings_ui.dialog.show_warning_dialog",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    dialog = SettingsDialog(manager)

    # 模拟用户把某个动作的备用键改成与主键相同：两个输入框都应立即显示冲突。
    row = dialog._action_hotkey_rows[0]
    row["secondary"].setText(row["primary"].text())
    assert row["primary"].status_lbl.text() == "❌"
    assert row["secondary"].status_lbl.text() == "❌"

    dialog.accept()

    # 保存被拦截；旧配置不受影响，冲突值不会污染下次打开的界面。
    assert manager.get_action_hotkeys()["screenshot"] == ["ctrl+shift+a", "ctrl+alt+a"]
    assert warnings

    # 冲突解除后，两项恢复各自的系统可用性结果。
    row["secondary"].setText("ctrl+alt+b")
    assert row["primary"].status_lbl.text() == "✅"
    assert row["secondary"].status_lbl.text() == "✅"

    dialog.deleteLater()
    qapp.processEvents()


def test_double_click_toggle_reset_and_refresh(qapp, tmp_path):
    manager = _manager(tmp_path)
    toggle = SimpleNamespace(value=False)
    toggle.isChecked = lambda: toggle.value
    toggle.setChecked = lambda value: setattr(toggle, "value", value)
    dialog = SimpleNamespace(
        config_manager=manager,
        double_click_copy_close_toggle=toggle,
    )

    SettingsDialog._reset_screenshot_settings_page(dialog)
    assert toggle.value is True

    manager.set_double_click_copy_close_enabled(False)
    SettingsDialog.refresh_settings(dialog)
    assert toggle.value is False

    snapshot = SettingsDialog._snapshot_settings(dialog)
    assert snapshot["double_click_copy_close_toggle"] is False


def test_annotation_behavior_toggles_reset_refresh_and_snapshot(qapp, tmp_path):
    manager = _manager(tmp_path)

    def toggle():
        value = SimpleNamespace(value=False)
        value.isChecked = lambda: value.value
        value.setChecked = lambda checked: setattr(value, "value", checked)
        return value

    cross_toggle = toggle()
    text_toggle = toggle()
    dialog = SimpleNamespace(
        config_manager=manager,
        cross_tool_selection_toggle=cross_toggle,
        text_always_on_top_toggle=text_toggle,
    )

    SettingsDialog._reset_screenshot_settings_page(dialog)
    assert cross_toggle.value is True
    assert text_toggle.value is True

    manager.set_cross_tool_selection_enabled(False)
    manager.set_text_always_on_top_enabled(False)
    SettingsDialog.refresh_settings(dialog)
    assert cross_toggle.value is False
    assert text_toggle.value is False

    snapshot = SettingsDialog._snapshot_settings(dialog)
    assert snapshot["cross_tool_selection_toggle"] is False
    assert snapshot["text_always_on_top_toggle"] is False


def test_refresh_settings_repaints_clipboard_theme_button(monkeypatch, qapp, tmp_path):
    """refresh_settings 的导入写在函数里，只有打开设置窗口才执行，改名漏改时启动不报错。"""
    manager = _manager(tmp_path)
    manager.set_clipboard_theme("pink")
    monkeypatch.setattr("settings.get_tool_settings_manager", lambda: manager)
    styles = []
    dialog = SimpleNamespace(
        config_manager=manager,
        _clip_theme_btn=SimpleNamespace(setStyleSheet=styles.append),
        _clip_theme_name="light",
    )

    SettingsDialog.refresh_settings(dialog)

    assert dialog._clip_theme_name == "pink"
    assert styles and "#E91E63" in styles[-1]


def test_double_click_setting_translations_exist_and_load(qapp):
    translations = Path(__file__).parents[1] / "translations"
    expected_by_language = {
        "en": {
            "Capture Behavior": "Capture Behavior",
            "Double-click to Copy and Close": "Double-click to Copy and Close",
            "Double-click the selected screenshot to copy it to the clipboard and close the capture.":
                "Double-click the selected screenshot to copy it to the clipboard and close the capture.",
            "Enable Ctrl Cross-Tool Selection": "Enable Ctrl Cross-Tool Selection",
            "Hold Ctrl and click any editable annotation to adjust it without switching tools.":
                "Hold Ctrl and click any editable annotation to adjust it without switching tools.",
            "Keep Text Annotations on Top": "Keep Text Annotations on Top",
            "Keep text above other annotations, including ones drawn later.":
                "Keep text above other annotations, including ones drawn later.",
            "Automatically Wrap Text": "Automatically Wrap Text",
            "Start new text annotations at about 30 characters wide and keep them inside the selection.":
                "Start new text annotations at about 30 characters wide and keep them inside the selection.",
            "UI Detection": "UI Detection",
            "Automatically frames the window or control under the mouse cursor.":
                "Automatically frames the window or control under the mouse cursor.",
            "None": "None",
            "Window only": "Window only",
            "Elements": "Elements",
            "Element Margin": "Element Margin",
            "Expands the detected element by this many pixels.":
                "Expands the detected element by this many pixels.",
        },
        "zh": {
            "Capture Behavior": "截图行为",
            "Double-click to Copy and Close": "双击复制并关闭",
            "Double-click the selected screenshot to copy it to the clipboard and close the capture.":
                "双击已选截图时复制到剪贴板并关闭截图。",
            "Enable Ctrl Cross-Tool Selection": "启用 Ctrl 跨工具选择",
            "Hold Ctrl and click any editable annotation to adjust it without switching tools.":
                "按住 Ctrl 点击任意可编辑标注，无需切换工具即可调整。",
            "Keep Text Annotations on Top": "文字标注始终置顶",
            "Keep text above other annotations, including ones drawn later.":
                "让文字保持在其他标注上方，包括之后绘制的标注。",
            "Automatically Wrap Text": "文字自动换行",
            "Start new text annotations at about 30 characters wide and keep them inside the selection.":
                "新建文字标注默认约 30 个字符宽，并限制在选区内。",
            "UI Detection": "UI 检测",
            "Automatically frames the window or control under the mouse cursor.":
                "自动框出鼠标指针下的窗口或控件。",
            "None": "不检测",
            "Window only": "仅窗口",
            "Elements": "检测元素",
            "Element Margin": "元素检测边距",
            "Expands the detected element by this many pixels.":
                "把检测到的元素向外扩这么多像素。",
        },
        "ja": {
            "Capture Behavior": "キャプチャ動作",
            "Double-click to Copy and Close": "ダブルクリックでコピーして閉じる",
            "Double-click the selected screenshot to copy it to the clipboard and close the capture.":
                "選択したスクリーンショットをダブルクリックすると、クリップボードにコピーしてキャプチャを閉じます。",
            "Enable Ctrl Cross-Tool Selection": "Ctrlによるツール横断選択を有効にする",
            "Hold Ctrl and click any editable annotation to adjust it without switching tools.":
                "Ctrlを押しながら編集可能な注釈をクリックすると、ツールを切り替えずに調整できます。",
            "Keep Text Annotations on Top": "テキスト注釈を常に最前面に表示",
            "Keep text above other annotations, including ones drawn later.":
                "後から描画したものを含め、テキストを他の注釈より前面に保ちます。",
            "Automatically Wrap Text": "テキストを自動で折り返す",
            "Start new text annotations at about 30 characters wide and keep them inside the selection.":
                "新しいテキスト注釈は約30文字幅で開始し、選択範囲内に収めます。",
            "UI Detection": "UI 検出",
            "Automatically frames the window or control under the mouse cursor.":
                "マウスカーソル下のウィンドウまたはコントロールを自動で枠選択します。",
            "None": "検出しない",
            "Window only": "ウィンドウのみ",
            "Elements": "要素を検出",
            "Element Margin": "要素検出の余白",
            "Expands the detected element by this many pixels.":
                "検出した要素を指定ピクセル分だけ外側に広げます。",
        },
        "ko": {
            "Capture Behavior": "캡처 동작",
            "Double-click to Copy and Close": "두 번 클릭하여 복사 후 닫기",
            "Double-click the selected screenshot to copy it to the clipboard and close the capture.":
                "선택한 스크린샷을 두 번 클릭하면 클립보드에 복사하고 캡처를 닫습니다.",
            "Enable Ctrl Cross-Tool Selection": "Ctrl 도구 간 선택 사용",
            "Hold Ctrl and click any editable annotation to adjust it without switching tools.":
                "Ctrl 키를 누른 채 편집 가능한 주석을 클릭하면 도구를 바꾸지 않고 조정할 수 있습니다.",
            "Keep Text Annotations on Top": "텍스트 주석을 항상 위에 표시",
            "Keep text above other annotations, including ones drawn later.":
                "나중에 그린 항목을 포함해 텍스트를 다른 주석보다 위에 유지합니다.",
            "Automatically Wrap Text": "텍스트 자동 줄 바꿈",
            "Start new text annotations at about 30 characters wide and keep them inside the selection.":
                "새 텍스트 주석은 약 30자 너비로 시작하고 선택 영역 안에 유지됩니다.",
            "UI Detection": "UI 감지",
            "Automatically frames the window or control under the mouse cursor.":
                "마우스 커서 아래의 창 또는 컨트롤을 자동으로 선택합니다.",
            "None": "감지 안 함",
            "Window only": "창만",
            "Elements": "요소 감지",
            "Element Margin": "요소 감지 여백",
            "Expands the detected element by this many pixels.":
                "감지된 요소를 지정한 픽셀만큼 바깥으로 확장합니다.",
        },
    }

    for language, expected in expected_by_language.items():
        root = ET.parse(translations / f"app_{language}.xml").getroot()
        settings_messages = {
            message.findtext("source"): message.findtext("translation")
            for context in root.findall("context")
            if context.findtext("name") == "SettingsDialog"
            for message in context.findall("message")
        }
        assert expected.items() <= settings_messages.items()

        translator = QTranslator()
        assert translator.load(str(translations / f"app_{language}.qm"))
        for source, translated in expected.items():
            assert translator.translate("SettingsDialog", source) == translated


class TestCaptureAppearanceSection:
    """截图设置页的「截图外观」与「放大镜」两组（圆角 / 描边阴影 / 放大镜倍率）。

    这些键过去只能在截图窗口里的浮层面板改；两处读写的是同一批应用设置，所以这里也
    断言「页面改完，配置里就是那个值」。
    """

    def test_values_come_from_the_config(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_app_setting("screenshot_rounded_enabled", True)
        manager.set_app_setting("screenshot_rounded_radius", 30)
        manager.set_app_setting("screenshot_border_enabled", True)
        manager.set_app_setting("screenshot_border_mode", "border")
        manager.set_app_setting("screenshot_border_size", 5)
        manager.set_app_setting("screenshot_border_color", "#112233")
        manager.set_app_setting("screenshot_shadow_color", "#445566")
        manager.set_app_setting("screenshot_border_persist", True)
        manager.set_app_setting("magnifier_zoom", 6.0)
        dialog = _dialog_for(manager)

        page = create_capture_page(dialog)
        try:
            assert dialog.screenshot_rounded_toggle.isChecked() is True
            assert dialog.screenshot_radius_spin.value() == 30
            assert dialog.screenshot_border_toggle.isChecked() is True
            assert dialog.screenshot_border_mode_combo.currentData() == "border"
            assert dialog.screenshot_border_size_spin.value() == 5
            assert dialog._screenshot_border_color.name() == "#112233"
            assert dialog._screenshot_shadow_color.name() == "#445566"
            assert dialog.screenshot_border_persist_toggle.isChecked() is True
            assert dialog.magnifier_zoom_spin.value() == 6.0
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_corner_radius_range_matches_the_in_window_panel(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        dialog = _dialog_for(manager)

        page = create_capture_page(dialog)
        try:
            assert (dialog.screenshot_radius_spin.minimum(),
                    dialog.screenshot_radius_spin.maximum()) == (0, 100)
            assert (dialog.screenshot_border_size_spin.minimum(),
                    dialog.screenshot_border_size_spin.maximum()) == (1, 50)
            assert (dialog.magnifier_zoom_spin.minimum(),
                    dialog.magnifier_zoom_spin.maximum()) == (1.0, 10.0)
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_accept_writes_the_whole_group(self, monkeypatch, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.set_log_dir(str(tmp_path))
        monkeypatch.setattr("ui.settings_ui.dialog.log_info", lambda *_a, **_k: None)
        monkeypatch.setattr(
            "core.shortcut_manager.HotkeySystem.check_hotkey_availability",
            lambda _self, _hotkey: True,
        )
        dialog = SettingsDialog(manager)

        try:
            dialog.screenshot_rounded_toggle.setChecked(True)
            dialog.screenshot_radius_spin.setValue(24)
            dialog.screenshot_border_toggle.setChecked(True)
            dialog.screenshot_border_mode_combo.setCurrentIndex(
                dialog.screenshot_border_mode_combo.findData("border"))
            dialog.screenshot_border_size_spin.setValue(7)
            dialog._screenshot_border_color.setRgb(0x11, 0x22, 0x33)
            dialog._screenshot_shadow_color.setRgb(0x44, 0x55, 0x66)
            dialog.screenshot_border_persist_toggle.setChecked(True)
            dialog.magnifier_zoom_spin.setValue(8.0)

            monkeypatch.setattr(SettingsDialog, "close", lambda _self: None)
            dialog.accept()

            assert manager.get_app_setting("screenshot_rounded_enabled") is True
            assert manager.get_app_setting("screenshot_rounded_radius") == 24
            assert manager.get_app_setting("screenshot_border_enabled") is True
            assert manager.get_app_setting("screenshot_border_mode") == "border"
            assert manager.get_app_setting("screenshot_border_size") == 7
            assert manager.get_app_setting("screenshot_border_color") == "#112233"
            assert manager.get_app_setting("screenshot_shadow_color") == "#445566"
            assert manager.get_app_setting("screenshot_border_persist") is True
            assert manager.get_app_setting("magnifier_zoom") == 8.0
        finally:
            dialog.deleteLater()
            qapp.processEvents()
