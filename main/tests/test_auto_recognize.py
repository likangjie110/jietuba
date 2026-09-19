# -*- coding: utf-8 -*-
"""自动分流：`ocr.auto_route.choose_route` 的纯函数判据 + 动作层的接线。

判据的重点是**顺序**：本地能拿到结果就不联网（表格 → 文字 → 公式），只有本地确实取不到
才用视觉模型（低置信度重读 / 图像描述），两样都没有时如实报「没识别到」。
"""

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

from core import actions
from ocr.auto_route import (
    ROUTE_FORMULA,
    ROUTE_NONE,
    ROUTE_TABLE,
    ROUTE_TEXT,
    ROUTE_VISION_DESCRIBE,
    ROUTE_VISION_TEXT,
    choose_route,
)

ROW_H = 20


def _block(text, left, right, top, score=0.95):
    return {
        "text": text,
        "score": score,
        "box": [[left, top], [right, top], [right, top + ROW_H], [left, top + ROW_H]],
    }


def _table_result():
    """3 行 × 3 列，列间距远大于字高 → 有表格特征。"""
    data = []
    for row_index, row in enumerate((("姓名", "年龄", "城市"),
                                     ("张三", "30", "北京"),
                                     ("李四", "25", "上海"))):
        top = row_index * (ROW_H + 10)
        for col_index, text in enumerate(row):
            left = 10 + col_index * 200
            data.append(_block(text, left, left + 60, top))
    return {"code": 100, "data": data}


def _text_result(score=0.95):
    """两行普通文字：行距 10px，每行只有一个块 → 不是表格。"""
    return {"code": 100, "data": [
        _block("hello world", 10, 120, 0, score),
        _block("second line", 10, 130, ROW_H + 10, score),
    ]}


class TestChooseRoute:
    def test_table_features_win(self):
        assert choose_route(_table_result(), has_vision=True) == ROUTE_TABLE

    def test_confident_text_stays_local(self):
        assert choose_route(_text_result(0.95), has_vision=True) == ROUTE_TEXT

    def test_low_confidence_text_goes_to_the_vision_model_when_configured(self):
        assert choose_route(_text_result(0.30), has_vision=True) == ROUTE_VISION_TEXT

    def test_low_confidence_text_without_a_model_is_still_copied(self):
        assert choose_route(_text_result(0.30), has_vision=False) == ROUTE_TEXT

    def test_no_text_never_calls_the_formula_engine(self):
        """没有文字 ≠ 有公式：真机验证过公式引擎对普通窗口截图也会吐 LaTeX（拿到过 \\cdot）。"""
        empty = {"code": 100, "data": []}

        assert choose_route(empty, has_formula=True, has_vision=True) == ROUTE_VISION_DESCRIBE
        assert choose_route(empty, has_formula=True, has_vision=False) == ROUTE_NONE
        assert choose_route(empty, has_formula=False, has_vision=True) == ROUTE_VISION_DESCRIBE
        assert choose_route(empty, has_formula=False, has_vision=False) == ROUTE_NONE

    def test_low_confidence_text_falls_back_to_the_formula_engine_without_a_model(self):
        """公式图在 OCR 眼里就是「认出一堆符号但都不踏实」，这让它落在这一条上。"""
        assert choose_route(_text_result(0.20), has_formula=True,
                            has_vision=False) == ROUTE_FORMULA
        # 有视觉模型时仍优先模型：它的语义比公式引擎强
        assert choose_route(_text_result(0.20), has_formula=True,
                            has_vision=True) == ROUTE_VISION_TEXT

    def test_broken_result_is_treated_as_no_text(self):
        assert choose_route(None, has_vision=True) == ROUTE_VISION_DESCRIBE
        assert choose_route({"code": 200, "data": []}, has_formula=True) == ROUTE_NONE
        assert choose_route("不是字典", has_vision=False) == ROUTE_NONE

    def test_threshold_is_read_leniently(self):
        result = _text_result(0.70)

        assert choose_route(result, threshold=0.9, has_vision=True) == ROUTE_VISION_TEXT
        assert choose_route(result, threshold=0.5, has_vision=True) == ROUTE_TEXT
        # 坏阈值按 0.6 处理，不是抛异常
        assert choose_route(result, threshold="坏值", has_vision=True) == ROUTE_TEXT

    def test_blocks_without_coordinates_do_not_make_a_table(self):
        """没有坐标的块既不成表也不算文字——与产品里 `format_ocr_result_text` 的行为一致。

        排版那一步本来就靠坐标分行分列，没有坐标的块它一律丢掉；分流跟着同一套判据走，
        不在这里另造一条「有文字但排不了版」的通路。
        """
        result = {"code": 100, "data": [{"text": "no box", "score": 0.9}]}

        assert choose_route(result, has_vision=False) == ROUTE_NONE
        assert choose_route(result, has_vision=True) == ROUTE_VISION_DESCRIBE


class _FakeApp:
    config_manager = None


def _image(width=80, height=40):
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#3366CC"))
    return picture


class _InlineThread:
    """同步版 OCR 线程替身：start() 里直接把结果发出去。"""

    def __init__(self, result):
        self._result = result
        self.recognized = _InlineSignal()
        self.finished = _InlineSignal()

    def start(self):
        self.recognized.emit(self._result)
        self.finished.emit()


class _InlineSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def _patch_capture(monkeypatch, result):
    monkeypatch.setattr(actions, "capture_at_cursor",
                        lambda app: (_image(), QRectF(0, 0, 80, 40)))
    monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)
    monkeypatch.setattr(actions, "_record_history", lambda image, rect, app: None)
    monkeypatch.setattr(actions, "_OcrRawThread", lambda image: _InlineThread(result))


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    return get_tool_settings_manager()


class TestAutoRecognizeAction:
    def test_a_table_result_opens_the_table_editor(self, monkeypatch, qapp, config):
        opened = []
        monkeypatch.setattr("ocr.table_editor.open_table_editor",
                            lambda document: opened.append(document))
        monkeypatch.setattr(actions, "_formula_ready", lambda: False)
        _patch_capture(monkeypatch, _table_result())

        assert actions.run_action("auto_recognize", _FakeApp()) is True
        assert opened and opened[0].rows == 3

    def test_plain_text_is_copied_locally(self, monkeypatch, qapp, config):
        monkeypatch.setattr(actions, "_formula_ready", lambda: False)
        monkeypatch.setattr("ocr.result_dialog.maybe_show_ocr_result", lambda *a, **k: None)
        _patch_capture(monkeypatch, _text_result())

        assert actions.run_action("auto_recognize", _FakeApp()) is True
        assert "hello world" in qapp.clipboard().text()

    def test_low_confidence_text_calls_the_vision_model(self, monkeypatch, qapp, config):
        calls = []
        monkeypatch.setattr(actions, "_formula_ready", lambda: False)
        monkeypatch.setattr(actions, "_ocr_text_options",
                            lambda: {"layout": "auto", "punctuation": "none",
                                     "threshold": 0.6, "vision_ready": True})
        monkeypatch.setattr(actions, "recognize_image_with_vision",
                            lambda image, config, *, task=None, show_window=False:
                            calls.append(task) or True)
        _patch_capture(monkeypatch, _text_result(0.20))

        assert actions.run_action("auto_recognize", _FakeApp()) is True
        assert calls == ["text"]

    def test_low_confidence_text_goes_to_the_formula_engine_without_a_model(
            self, monkeypatch, qapp, config):
        calls = []
        monkeypatch.setattr(actions, "_formula_ready", lambda: True)
        monkeypatch.setattr(actions, "_recognize_formula", lambda image: calls.append(image) or True)
        monkeypatch.setattr(actions, "_ocr_text_options",
                            lambda: {"layout": "auto", "punctuation": "none",
                                     "threshold": 0.6, "vision_ready": False})
        _patch_capture(monkeypatch, _text_result(0.20))

        assert actions.run_action("auto_recognize", _FakeApp()) is True
        assert calls, "低置信度文字、没有视觉模型但有公式引擎时应当交给公式引擎"

    def test_nothing_recognized_reports_instead_of_failing_silently(self, monkeypatch, qapp, config):
        warnings = []
        monkeypatch.setattr(actions, "_formula_ready", lambda: False)
        monkeypatch.setattr("ui.dialogs.show_warning_dialog",
                            lambda parent, title, message: warnings.append((title, message)))
        _patch_capture(monkeypatch, {"code": 100, "data": []})

        assert actions.run_action("auto_recognize", _FakeApp()) is True
        assert warnings, "什么都没识别到时要给用户提示"
        # 测试环境按英文源串断言（没有加载译者时 tr() 返回源串本身）
        assert "vision model" in warnings[0][1]

    def test_action_is_registered_as_a_silent_capture(self):
        action = actions.ACTIONS_BY_ID["auto_recognize"]
        assert action.silent_capture is True
        assert "auto_recognize" in actions.GESTURE_ACTION_IDS
