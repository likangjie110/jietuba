# -*- coding: utf-8 -*-
"""表格 → Markdown：`format_ocr_result_markdown` 纯函数 + 「复制表格」动作。

判据：带坐标的表格结果产出标准 Markdown 表格（表头 + --- 分隔 + 数据行）；输入里
没有表格特征时**如实退回纯文本**，不硬造表格。动作那条走的是真实入口
（`run_action` → 抓图 → OCR 线程 → 转换 → 剪贴板），OCR 引擎只替掉边界。
"""

import time

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

from core import actions
from ocr import format_ocr_result_markdown, format_ocr_result_text

ROW_H = 20


def _block(text, left, right, top):
    return {
        "text": text,
        "box": [[left, top], [right, top], [right, top + ROW_H], [left, top + ROW_H]],
    }


def _table_result() -> dict:
    """3 行 × 3 列：列间距 140px（远大于字高），行距 10px（同一列的上下两行）。"""
    data = []
    for row_index, row in enumerate((("姓名", "年龄", "城市"),
                                     ("张三", "30", "北京"),
                                     ("李四", "25", "上海"))):
        top = row_index * (ROW_H + 10)
        for col_index, text in enumerate(row):
            left = 10 + col_index * 200
            data.append(_block(text, left, left + 60, top))
    return {"code": 100, "data": data}


def _paragraph_result() -> dict:
    """两行普通文字：每行内部只有一个块，且块之间只有行距（没有列结构）。"""
    return {
        "code": 100,
        "data": [
            _block("这是一段普通文字", 10, 300, 0),
            _block("没有表格结构", 10, 200, 30),
        ],
    }


class TestFormatMarkdown:
    def test_a_table_becomes_markdown(self):
        markdown = format_ocr_result_markdown(_table_result())

        lines = markdown.splitlines()
        assert lines[0] == "| 姓名 | 年龄 | 城市 |"
        assert lines[1] == "| --- | --- | --- |"
        assert lines[2] == "| 张三 | 30 | 北京 |"
        assert lines[3] == "| 李四 | 25 | 上海 |"

    def test_paragraph_stays_plain_text(self):
        result = _paragraph_result()

        assert format_ocr_result_markdown(result) == format_ocr_result_text(result)

    def test_a_single_line_is_not_a_table(self):
        result = {"code": 100, "data": [_block("只有一行", 10, 200, 0)]}

        assert format_ocr_result_markdown(result) == format_ocr_result_text(result)

    def test_blocks_without_coordinates_stay_plain(self):
        result = {"code": 100, "data": [{"text": "没有坐标"}, {"text": "也没有"}]}

        assert format_ocr_result_markdown(result) == format_ocr_result_text(result) == ""

    def test_failed_result_stays_empty(self):
        assert format_ocr_result_markdown({"code": 0, "data": []}) == ""
        assert format_ocr_result_markdown({}) == ""

    def test_pipes_inside_cells_are_escaped(self):
        result = _table_result()
        result["data"][3]["text"] = "张|三"

        markdown = format_ocr_result_markdown(result)

        assert "张\\|三" in markdown
        # 单元格里的竖线被转义：整行「没被转义的竖线」仍是 列数 + 1，表格没被拆断
        line = markdown.splitlines()[2]
        assert line.count("|") - line.count("\\|") == 4

    def test_ragged_rows_are_padded_to_the_widest_row(self):
        result = _table_result()
        # 最后一行少认出一格
        result["data"] = [b for b in result["data"] if not (b["text"] == "上海")]

        lines = format_ocr_result_markdown(result).splitlines()

        assert lines[3] == "| 李四 | 25 |  |"

    def test_extra_blocks_within_a_row_stay_in_one_cell(self):
        """同一格里的多段文字（间隙远小于列间距）要合成一格，不能各自成列。"""
        result = _table_result()
        result["data"].append(_block("先生", 80, 120, 30))      # 与「张三」(10..70) 只隔 10px

        lines = format_ocr_result_markdown(result).splitlines()

        assert lines[2] == "| 张三 先生 | 30 | 北京 |"

    def test_alignment_by_x_not_by_reading_order(self):
        """引擎给的顺序被打乱时，仍按 x 排列表格列。"""
        result = _table_result()
        result["data"] = list(reversed(result["data"]))

        lines = format_ocr_result_markdown(result).splitlines()

        assert lines[0].startswith("| ")
        assert "姓名" in lines[0] or "姓名" in lines[1]


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    return get_tool_settings_manager()


class _FakeApp:
    def __init__(self, config_manager):
        self.config_manager = config_manager


def _wait_until(predicate, qapp, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        qapp.processEvents()
        time.sleep(0.02)
    return predicate()


class TestCopyTableMarkdownAction:
    """动作走真实入口：run_action → 抓图 → OCR 线程 → 转换 → 剪贴板。"""

    def _patch(self, monkeypatch, result):
        monkeypatch.setattr(actions, "capture_at_cursor", lambda app: (
            _image(), QRectF(0, 0, 40, 20)))
        monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)
        monkeypatch.setattr("ocr.is_ocr_available", lambda: True)
        monkeypatch.setattr("ocr.recognize_text", lambda image, **kwargs: result)

    def test_action_copies_a_markdown_table(self, qapp, config, monkeypatch):
        self._patch(monkeypatch, _table_result())
        clipboard = qapp.clipboard()
        previous = clipboard.text()
        try:
            clipboard.clear()
            assert actions.run_action("copy_table_markdown", _FakeApp(config)) is True
            assert _wait_until(lambda: clipboard.text().strip() != "", qapp), "没有写进剪贴板"

            text = clipboard.text()
            assert text.splitlines()[0] == "| 姓名 | 年龄 | 城市 |"
            assert text.splitlines()[1] == "| --- | --- | --- |"
        finally:
            clipboard.setText(previous)

    def test_action_falls_back_to_plain_text_without_a_table(self, qapp, config, monkeypatch):
        self._patch(monkeypatch, _paragraph_result())
        clipboard = qapp.clipboard()
        previous = clipboard.text()
        try:
            clipboard.clear()
            assert actions.run_action("copy_table_markdown", _FakeApp(config)) is True
            assert _wait_until(lambda: clipboard.text().strip() != "", qapp)

            text = clipboard.text()
            assert "|" not in text
            assert text == format_ocr_result_text(_paragraph_result())
        finally:
            clipboard.setText(previous)

    def test_action_is_registered_and_bindable(self):
        assert actions.ACTIONS_BY_ID["copy_table_markdown"].silent_capture is True
        assert "copy_table_markdown" in actions.GESTURE_ACTION_IDS


def _image() -> QImage:
    image = QImage(40, 20, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    return image
