# -*- coding: utf-8 -*-
"""低置信度路由：本地 OCR 识别得没把握时，如实提示可以改用视觉模型。

两件事分开测：

- 纯函数（``ocr/quality.py``）：加权平均、阈值、提示文案；
- **真路径**（``core/actions.py`` 的 OCR 文字线程）：喂低置信度结果时提示真的发出来，
  喂高置信度结果时不提示，并且这条路上**一次网络请求都没有**——视觉模型要花用户自己的
  额度，提示只能是提示，不能顺手替他上传截图。
"""

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from core import actions
from ocr.quality import DEFAULT_THRESHOLD, average_confidence, low_confidence_hint


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _result(*rows) -> dict:
    """按引擎约定造结果：``[[box, text, score], ...]`` → dict 形态。"""
    return {
        "code": 100,
        "msg": "成功",
        "data": [
            {"box": [[0, 0], [10, 0], [10, 5], [0, 5]], "text": text, "score": score}
            for text, score in rows
        ],
        "elapse": 0.1,
    }


class TestAverageConfidence:
    def test_empty_result_counts_as_confident(self):
        """没有数据时不能反过来报「低置信度」——那会在识别为空时弹出无意义的提示。"""
        assert average_confidence(_result()) == 1.0
        assert average_confidence(None) == 1.0

    def test_text_length_weights_the_average(self):
        # 一行 20 字 1.0 + 一行 1 字 0.0：按行平均是 0.5，按字加权是 1.0*20/21 ≈ 0.95
        result = _result(("这是一段很长的文字共二十个字符左右", 1.0), ("错", 0.0))
        assert average_confidence(result) > 0.9

    def test_plain_average_with_equal_lengths(self):
        assert average_confidence(_result(("aa", 0.4), ("bb", 0.6))) == pytest.approx(0.5)

    def test_rows_without_score_are_ignored(self):
        result = _result(("aa", 0.2))
        result["data"].append({"box": [], "text": "没有分数"})
        assert average_confidence(result) == pytest.approx(0.2)

    def test_broken_scores_do_not_raise(self):
        result = {"data": [{"text": "x", "score": "nonsense"}, {"text": "y", "score": None}]}
        assert average_confidence(result) == 1.0


class TestHint:
    def test_low_confidence_is_reported_without_a_model(self):
        hint = low_confidence_hint(_result(("模糊", 0.2)))
        assert hint and "0" in hint
        assert "视觉模型" in hint

    def test_low_confidence_suggests_the_configured_model(self):
        hint = low_confidence_hint(_result(("模糊", 0.2)), vision_ready=True)
        assert "已配置的视觉模型" in hint

    def test_confident_result_has_no_hint(self):
        assert low_confidence_hint(_result(("清清楚楚", 0.95))) == ""

    def test_threshold_is_respected(self):
        result = _result(("中等", 0.7))
        assert low_confidence_hint(result) == ""
        assert low_confidence_hint(result, threshold=0.8) != ""

    def test_broken_threshold_falls_back_to_default(self):
        assert low_confidence_hint(_result(("中等", 0.7)), threshold="x") == ""
        assert DEFAULT_THRESHOLD == 0.6


class TestRealCopyPath:
    """真实入口：配置 → OCR 文字线程 → (文本, 提示)。"""

    def _run(self, monkeypatch, result, *, threshold=None, vision_ready=False):
        from settings import get_tool_settings_manager

        config = get_tool_settings_manager()
        if threshold is not None:
            config.set_ocr_low_confidence_threshold(threshold)
        monkeypatch.setattr("ocr.is_ocr_available", lambda: True)
        monkeypatch.setattr("ocr.recognize_text", lambda image, **kwargs: result)
        monkeypatch.setattr(actions, "_ocr_text_options",
                            lambda: {"layout": "auto", "punctuation": "none",
                                     "threshold": 0.6, "vision_ready": vision_ready})

        image = QImage(40, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        thread = actions._OcrTextThread(image)
        captured = []
        thread.recognized.connect(lambda text, hint: captured.append((text, hint)))
        thread.run()
        return captured[0]

    def test_low_confidence_result_carries_a_hint(self, qapp, monkeypatch):
        text, hint = self._run(monkeypatch, _result(("模糊的字", 0.2)))
        assert text
        assert hint and "视觉模型" in hint

    def test_confident_result_has_no_hint(self, qapp, monkeypatch):
        text, hint = self._run(monkeypatch, _result(("清清楚楚", 0.98)))
        assert text
        assert hint == ""

    def test_the_hint_path_makes_no_network_call(self, qapp, monkeypatch):
        """提示绝不允许顺手把截图传给视觉模型。"""
        calls = []
        monkeypatch.setattr("ocr.vision_models.convert_image",
                            lambda *a, **k: calls.append(a))

        _text, hint = self._run(monkeypatch, _result(("模糊的字", 0.1)), vision_ready=True)

        assert hint
        assert calls == []

    def test_ocr_failure_emits_empty_text_and_no_hint(self, qapp, monkeypatch):
        monkeypatch.setattr("ocr.is_ocr_available", lambda: True)
        monkeypatch.setattr("ocr.recognize_text",
                            lambda image, **kwargs: {"code": 200, "data": []})
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        thread = actions._OcrTextThread(image)
        captured = []
        thread.recognized.connect(lambda text, hint: captured.append((text, hint)))
        thread.run()
        assert captured == [("", "")]


class TestResultDialogHint:
    def test_hint_is_shown_in_the_window_but_not_copied(self, monkeypatch):
        """提示只加在结果窗里；剪贴板里的内容必须还是识别结果本身。"""
        from ocr import result_dialog

        monkeypatch.setattr(result_dialog, "should_show", lambda trigger: True)
        shown = []
        monkeypatch.setattr("ui.dialogs.show_text_dialog",
                            lambda parent, title, text: shown.append(text))

        assert result_dialog.maybe_show_ocr_result("识别结果", "copy_all", hint="置信度偏低") is True

        assert shown and "识别结果" in shown[0] and "置信度偏低" in shown[0]

    def test_no_hint_keeps_the_window_unchanged(self, monkeypatch):
        from ocr import result_dialog

        monkeypatch.setattr(result_dialog, "should_show", lambda trigger: True)
        shown = []
        monkeypatch.setattr("ui.dialogs.show_text_dialog",
                            lambda parent, title, text: shown.append(text))

        result_dialog.maybe_show_ocr_result("识别结果", "copy_all")

        assert shown == ["识别结果"]
