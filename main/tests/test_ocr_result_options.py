# -*- coding: utf-8 -*-
"""「文字识别」组新增选项：文本布局 / 标点处理 / 识别语言 / 结果对话框时机。

判据是「设置真的改变了输出」：
- 布局与标点作用于发布函数 `format_ocr_result_text`（自动分行 / 逐块一行 / 并成一行；
  去行尾标点 / 全角转半角）；
- 语言设置经 `resolve_ocr_language` 换成引擎认的语言名；
- 时机设置决定 `maybe_show_ocr_result` 弹不弹，并且**复制动作那条真实路径**会读它
  （`run_action("screenshot_copy_text", app)` → OCR 线程 → 剪贴板）。
"""

import time

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

from core import actions
from ocr import apply_ocr_punctuation, format_ocr_result_text, resolve_ocr_language
from ocr import result_dialog

#: 两行三块：第一行两块（姓名 / 30），第二行一块（张三。）
RESULT = {
    "code": 100,
    "data": [
        {"box": [[10, 0], [60, 0], [60, 20], [10, 20]], "text": "姓名"},
        {"box": [[200, 0], [260, 0], [260, 20], [200, 20]], "text": "30"},
        {"box": [[10, 30], [80, 30], [80, 50], [10, 50]], "text": "张三。"},
    ],
}


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    return get_tool_settings_manager()


@pytest.fixture(autouse=True)
def _no_modal_dialogs(qapp, monkeypatch):
    """把识别结果对话框换成记录器：它是 exec() 的模态窗，真弹出来会把用例挂死。"""
    shown: list = []
    monkeypatch.setattr("ui.dialogs.show_text_dialog",
                        lambda parent, title, content: shown.append((title, content)))
    return shown


@pytest.fixture(autouse=True)
def _clean_triggers(config):
    """每个用例前后都把「结果对话框时机」清干净，免得上一个用例的选择漏到下一个。"""
    config.set_ocr_dialog_triggers([])
    yield
    config.set_ocr_dialog_triggers([])


class TestTextLayout:
    def test_auto_keeps_the_reading_order_rows(self):
        assert format_ocr_result_text(RESULT) == "姓名 30\n张三。"

    def test_lines_puts_every_block_on_its_own_line(self):
        assert format_ocr_result_text(RESULT, layout="lines") == "姓名\n30\n张三。"

    def test_single_joins_everything_into_one_line(self):
        assert format_ocr_result_text(RESULT, layout="single") == "姓名 30 张三。"

    def test_unknown_layout_falls_back_to_auto(self):
        assert format_ocr_result_text(RESULT, layout="nonsense") == format_ocr_result_text(RESULT)

    def test_reading_order_survives_the_scrambled_engine_order(self):
        scrambled = {"code": 100, "data": list(reversed(RESULT["data"]))}

        assert format_ocr_result_text(scrambled) == "姓名 30\n张三。"


class TestPunctuation:
    def test_none_leaves_the_text_alone(self):
        assert format_ocr_result_text(RESULT, punctuation="none") == "姓名 30\n张三。"

    def test_strip_trailing_removes_the_line_tail_punctuation(self):
        result = {"code": 100, "data": [
            {"box": [[10, 0], [80, 0], [80, 20], [10, 20]], "text": "第一行，"},
            {"box": [[10, 30], [80, 30], [80, 50], [10, 50]], "text": "第二行。"},
        ]}

        assert format_ocr_result_text(result, punctuation="strip_trailing") == "第一行\n第二行"

    def test_to_halfwidth_converts_fullwidth_characters(self):
        assert apply_ocr_punctuation("ＡＢ１２，", "to_halfwidth") == "AB12,"

    def test_unknown_punctuation_mode_changes_nothing(self):
        assert apply_ocr_punctuation("第一行，", "whatever") == "第一行，"


class TestOcrLanguage:
    @pytest.mark.parametrize(("configured", "expected"), [
        ("zh", "简体中文"), ("en", "English"), ("ja", "日本語"), ("ko", "한국어"),
    ])
    def test_explicit_language_wins(self, configured, expected):
        assert resolve_ocr_language(configured, "en") == expected

    @pytest.mark.parametrize(("app_language", "expected"), [
        ("zh", "简体中文"), ("en", "English"), ("ko", "한국어"),
    ])
    def test_follow_app_uses_the_interface_language(self, app_language, expected):
        assert resolve_ocr_language("follow_app", app_language) == expected

    def test_unknown_pair_falls_back_to_japanese(self):
        assert resolve_ocr_language("", "") == "日本語"
        assert resolve_ocr_language("klingon", "klingon") == "日本語"


class TestDialogTriggers:
    def test_default_is_no_dialog(self, config):
        """默认不弹：老行为是「只复制」，不能因为加了选项就变成到处弹窗。"""
        assert config.get_ocr_dialog_triggers() == []
        assert result_dialog.should_show("copy_all") is False

    def test_triggers_round_trip_and_filter_unknown_values(self, config):
        config.set_ocr_dialog_triggers(["copy_all", "nonsense", "capture"])

        assert config.get_ocr_dialog_triggers() == ["copy_all", "capture"]
        assert result_dialog.should_show("copy_all") is True
        assert result_dialog.should_show("copy_selection") is False

    def test_broken_config_reads_as_no_dialog(self, config):
        config.qsettings.setValue("app/ocr_dialog_triggers", "{not json")

        assert config.get_ocr_dialog_triggers() == []
        assert result_dialog.should_show("capture") is False

    def test_maybe_show_calls_the_dialog_only_when_enabled(self, config, _no_modal_dialogs):
        assert result_dialog.maybe_show_ocr_result("文字", "copy_all") is False
        config.set_ocr_dialog_triggers(["copy_all"])

        assert result_dialog.maybe_show_ocr_result("文字", "copy_all") is True
        assert [content for _title, content in _no_modal_dialogs] == ["文字"]

    def test_empty_text_never_opens_a_dialog(self, config, _no_modal_dialogs):
        config.set_ocr_dialog_triggers(["copy_all"])

        assert result_dialog.maybe_show_ocr_result("", "copy_all") is False
        assert _no_modal_dialogs == []


class _FakeApp:
    config_manager = None


def _wait_until(predicate, qapp, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        qapp.processEvents()
        time.sleep(0.02)
    return predicate()


class TestCopyActionUsesTheOptions:
    """真实入口：run_action → 抓图 → OCR 线程 → 剪贴板，选项从配置里读。"""

    def _patch(self, monkeypatch):
        monkeypatch.setattr(actions, "capture_at_cursor",
                            lambda app: (_image(), QRectF(0, 0, 40, 20)))
        monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)
        monkeypatch.setattr("ocr.is_ocr_available", lambda: True)
        monkeypatch.setattr("ocr.recognize_text", lambda image, **kwargs: RESULT)

    def test_layout_setting_reaches_the_clipboard(self, qapp, config, monkeypatch,
                                                  _no_modal_dialogs):
        self._patch(monkeypatch)
        config.set_ocr_text_layout("single")
        clipboard = qapp.clipboard()
        previous = clipboard.text()
        try:
            clipboard.clear()
            assert actions.run_action("screenshot_copy_text", _FakeApp()) is True
            assert _wait_until(lambda: clipboard.text().strip() != "", qapp)
            assert clipboard.text() == "姓名 30 张三。"
        finally:
            clipboard.setText(previous)

    def test_punctuation_setting_reaches_the_clipboard(self, qapp, config, monkeypatch,
                                                       _no_modal_dialogs):
        self._patch(monkeypatch)
        config.set_ocr_text_layout("lines")
        config.set_ocr_punctuation("strip_trailing")
        clipboard = qapp.clipboard()
        previous = clipboard.text()
        try:
            clipboard.clear()
            assert actions.run_action("screenshot_copy_text", _FakeApp()) is True
            assert _wait_until(lambda: clipboard.text().strip() != "", qapp)
            assert clipboard.text() == "姓名\n30\n张三"
        finally:
            clipboard.setText(previous)

    def test_dialog_trigger_is_honoured_on_the_real_path(self, qapp, config, monkeypatch,
                                                         _no_modal_dialogs):
        self._patch(monkeypatch)
        config.set_ocr_dialog_triggers(["copy_all"])
        clipboard = qapp.clipboard()
        previous = clipboard.text()
        try:
            assert actions.run_action("screenshot_copy_text", _FakeApp()) is True
            assert _wait_until(lambda: bool(_no_modal_dialogs), qapp), "勾了时机却没弹结果窗"
        finally:
            clipboard.setText(previous)


def _image() -> QImage:
    image = QImage(40, 20, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    return image
