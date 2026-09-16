# -*- coding: utf-8 -*-
"""
compare_ocr_engines.py 的指标口径

这个脚本的结论会被用来决定「值不值得接一个新 OCR 引擎」，公式错了决策就错了，
所以把 CER 和编辑距离的定义钉在这里。
"""
import pytest

from scripts.compare_ocr_engines import character_error_rate, levenshtein, normalize


class TestLevenshtein:

    def test_identical_and_empty(self):
        assert levenshtein("", "") == 0
        assert levenshtein("会议时间", "会议时间") == 0
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3

    def test_single_substitution(self):
        assert levenshtein("会议时间", "会议时问") == 1

    def test_textbook_case(self):
        # kitten → sitting：2 处替换 + 1 处插入
        assert levenshtein("kitten", "sitting") == 3


class TestCharacterErrorRate:

    def test_whitespace_differences_are_not_errors(self):
        """OCR 的换行、空格位置和标准答案很难一致，不该算成识别错误。"""
        assert normalize("会议时间 改为\n14:00") == "会议时间改为14:00"
        assert character_error_rate("会议时间改为 14:00", "会议时间改为\n14:00") == 0.0

    def test_counts_one_wrong_character(self):
        # 8 个字里错 1 个
        assert character_error_rate("会议时间改为两点", "会议时间改为丙点") == pytest.approx(1 / 8)

    def test_empty_expected_is_bounded(self):
        """标准答案为空白（样本没写好）时不能除零。"""
        assert character_error_rate("", "") == 0.0
        assert character_error_rate("", "x") == 1.0

    def test_extra_output_counts_as_error(self):
        assert character_error_rate("会议", "会议时间") == pytest.approx(2 / 2)
