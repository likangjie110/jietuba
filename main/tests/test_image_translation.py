# -*- coding: utf-8 -*-
"""截图翻译落图：原文替换 / 双语对照，以及逐行取译文。

判据是**像素**与**尺寸**，不是「函数被调过」：

- 替换模式：框内出现译文（不再等于原像素），框周围底色被保留，输出尺寸不变；
- 双语模式：原图区域一行不动，下方按译文行数长出相称的高度；
- 逐行翻译：请求逐行发出、失败行如实计数、超过上限不硬发。
"""

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from translation.image_translation import (
    MAX_LINE_REQUESTS,
    TranslatedLine,
    bounding_rect,
    bilingual_panel_height,
    lines_from_ocr_result,
    render_translated_image,
    translate_lines,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def white_image(width=320, height=200) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    return image


def box(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def region_pixels(image, rect):
    return [image.pixelColor(x, y)
            for y in range(int(rect.top()), int(rect.bottom()))
            for x in range(int(rect.left()), int(rect.right()))]


def dark_pixels(image, rect) -> int:
    return sum(1 for color in region_pixels(image, rect) if color.lightness() < 100)


class TestBoundingRect:
    def test_quad_points(self):
        rect = bounding_rect(box(10, 20, 30, 8))
        assert (rect.left(), rect.top(), rect.width(), rect.height()) == (10, 20, 30, 8)

    def test_flat_list_is_treated_as_left_top_right_bottom(self):
        rect = bounding_rect([10, 20, 40, 28])
        assert (rect.width(), rect.height()) == (30, 8)

    def test_broken_values_give_an_empty_rect(self):
        assert bounding_rect(None).isEmpty()
        assert bounding_rect("nonsense").isEmpty()
        assert bounding_rect([]).isEmpty()


class TestReplaceMode:
    def test_translation_is_drawn_inside_the_box(self, qapp):
        image = white_image()
        rect = bounding_rect(box(40, 40, 200, 24))
        line = TranslatedLine(box=box(40, 40, 200, 24), text="Hello world",
                              translation="你好世界")

        output = render_translated_image(image, [line], "replace")

        assert output.size() == image.size()
        assert dark_pixels(output, rect) > 0, "框里没画出译文"
        assert dark_pixels(image, rect) == 0, "原图不该被就地改动"

    def test_background_outside_the_box_is_untouched(self, qapp):
        image = white_image()
        line = TranslatedLine(box=box(40, 40, 120, 20), text="a", translation="甲")

        output = render_translated_image(image, [line], "replace")

        outside = output.pixelColor(300, 180)
        assert outside == QColor("white")

    def test_lines_without_translation_are_left_alone(self, qapp):
        image = white_image()
        rect = bounding_rect(box(20, 20, 100, 20))
        line = TranslatedLine(box=box(20, 20, 100, 20), text="keep", translation="  ")

        output = render_translated_image(image, [line], "replace")

        assert dark_pixels(output, rect) == 0

    def test_dark_background_gets_light_text(self, qapp):
        image = QImage(200, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor("black"))
        line = TranslatedLine(box=box(10, 20, 160, 24), text="x", translation="黑底白字")

        output = render_translated_image(image, [line], "replace")

        rect = bounding_rect(box(10, 20, 160, 24))
        assert any(color.lightness() > 150 for color in region_pixels(output, rect))


class TestBilingualMode:
    def test_panel_is_added_below_the_image(self, qapp):
        image = white_image()
        lines = [TranslatedLine(box=box(10, 10, 100, 16), text="one", translation="第一行"),
                 TranslatedLine(box=box(10, 40, 100, 16), text="two", translation="第二行")]

        output = render_translated_image(image, lines, "bilingual")

        assert output.width() == image.width()
        assert output.height() > image.height()
        # 原图区域逐像素保持不变
        for x, y in ((5, 5), (100, 90), (300, 190)):
            assert output.pixelColor(x, y) == image.pixelColor(x, y)

    def test_panel_contains_the_translation_and_grows_with_lines(self, qapp):
        image = white_image()
        one = [TranslatedLine(box=box(10, 10, 100, 16), text="a", translation="第一行")]
        two = one + [TranslatedLine(box=box(10, 40, 100, 16), text="b", translation="第二行")]

        short = render_translated_image(image, one, "bilingual")
        tall = render_translated_image(image, two, "bilingual")

        assert tall.height() > short.height(), "多一行译文，双语区就要更高"
        panel = tall.copy(0, image.height(), tall.width(), tall.height() - image.height())
        dark = [(x, y) for y in range(panel.height()) for x in range(panel.width())
                if panel.pixelColor(x, y).lightness() < 100]
        assert dark, "双语区没有画出任何文字"

    def test_height_matches_the_declared_panel_height(self, qapp):
        image = white_image()
        lines = [TranslatedLine(box=box(10, 10, 100, 16), text="a", translation="一行译文")]

        output = render_translated_image(image, lines, "bilingual")

        assert output.height() == image.height() + bilingual_panel_height(lines, image.width())

    def test_long_translation_wraps_into_more_rows(self, qapp):
        short = [TranslatedLine(box=box(5, 5, 100, 12), text="a", translation="短句")]
        long = [TranslatedLine(box=box(5, 5, 100, 12), text="a", translation="很长很长" * 20)]

        assert (bilingual_panel_height(long, 240)
                > bilingual_panel_height(short, 240))


class TestTextMode:
    def test_text_mode_returns_the_image_unchanged(self, qapp):
        image = white_image()
        line = TranslatedLine(box=box(10, 10, 100, 20), text="a", translation="甲")

        output = render_translated_image(image, [line], "text")

        assert output.size() == image.size()
        assert output.pixelColor(50, 20) == image.pixelColor(50, 20)

    def test_empty_image_returns_empty(self, qapp):
        assert render_translated_image(QImage(), [], "replace").isNull()

    def test_no_usable_lines_returns_a_copy(self, qapp):
        image = white_image()
        line = TranslatedLine(box=[], text="a", translation="甲")
        output = render_translated_image(image, [line], "replace")
        assert output.size() == image.size()


class TestLinesFromOcrResult:
    def test_reads_boxes_and_text(self):
        result = {"code": 100, "data": [
            {"box": box(0, 0, 10, 10), "text": "甲", "score": 0.9},
            {"box": box(0, 20, 10, 10), "text": "  ", "score": 0.9},
            {"box": box(0, 40, 10, 10), "text": "乙", "score": 0.8},
        ]}

        lines = lines_from_ocr_result(result)

        assert [line.text for line in lines] == ["甲", "乙"]
        assert all(line.translation == "" for line in lines)

    def test_broken_result_gives_no_lines(self):
        assert lines_from_ocr_result(None) == []
        assert lines_from_ocr_result({"code": 200}) == []
        assert lines_from_ocr_result({"data": ["不是字典"]}) == []


class _StubService:
    """假翻译服务：逐行回 ``行号-译文``，并可指定哪些行失败。"""

    def __init__(self, fail_lines=()):
        self.seen = []
        self.fail_lines = set(fail_lines)

    def translate(self, request):
        self.seen.append(request.text)
        from translation.models import TranslationResult

        if request.text in self.fail_lines:
            return TranslationResult(success=False, error_message="boom")
        return TranslationResult(success=True, translated_text=f"译[{request.text}]")


class TestTranslateLines:
    def test_each_line_is_translated_separately(self):
        lines = [TranslatedLine(box=box(0, 0, 10, 10), text="one"),
                 TranslatedLine(box=box(0, 20, 10, 10), text="two")]
        service = _StubService()

        filled, failures = translate_lines(service, lines, "zh-Hans")

        assert failures == 0
        assert [line.translation for line in filled] == ["译[one]", "译[two]"]
        assert service.seen == ["one", "two"]

    def test_failed_lines_are_counted_not_faked(self):
        lines = [TranslatedLine(box=box(0, 0, 10, 10), text="one"),
                 TranslatedLine(box=box(0, 20, 10, 10), text="bad")]
        service = _StubService(fail_lines={"bad"})

        filled, failures = translate_lines(service, lines, "zh-Hans")

        assert failures == 1
        assert filled[0].translation and filled[1].translation == ""

    def test_too_many_lines_makes_no_requests(self):
        lines = [TranslatedLine(box=box(0, 0, 10, 10), text=f"l{i}")
                 for i in range(MAX_LINE_REQUESTS + 1)]
        service = _StubService()

        filled, failures = translate_lines(service, lines, "zh-Hans")

        assert service.seen == [], "超过上限就不该再逐行发请求"
        assert failures == 0
        assert all(line.translation == "" for line in filled)

    def test_missing_service_is_not_an_exception(self):
        lines = [TranslatedLine(box=box(0, 0, 10, 10), text="a")]
        filled, failures = translate_lines(None, lines, "zh-Hans")
        assert filled[0].translation == "" and failures == 0


class TestRealActionPath:
    """真实入口：``actions.translate_image_in_place`` → OCR → 翻译 → 落图 → 文件 + 剪贴板。

    这条是把上面那些渲染函数接进应用的那一段，所以它必须真的跑一遍：产物是**文件**，
    断言文件存在、能被 QImage 读回、尺寸符合所选模式。
    """

    def _run(self, monkeypatch, tmp_path, mode, *, ocr_result, open_viewer=True):
        from PySide6.QtCore import QEventLoop, QTimer
        from core import actions
        from translation.models import TranslationResult

        class _Service:
            def translate(self, request):
                return TranslationResult(success=True, translated_text=f"译[{request.text}]")

        config = _stub_config(mode)
        monkeypatch.setattr("ocr.is_ocr_available", lambda: True)
        monkeypatch.setattr("ocr.recognize_text", lambda image, **kwargs: ocr_result)
        monkeypatch.setattr("translation.service.create_default_translation_service",
                            lambda *a, **k: _Service())
        monkeypatch.setattr(actions, "_rendered_dir", lambda: tmp_path)
        opened = []
        if open_viewer:
            monkeypatch.setattr("ui.image_viewer.open_image_viewer",
                                lambda **kwargs: opened.append(kwargs.get("path")))

        assert actions.translate_image_in_place(white_image(240, 120), config) is True

        loop = QEventLoop()
        QTimer.singleShot(4000, loop.quit)
        deadline = QTimer()
        deadline.timeout.connect(lambda: (
            loop.quit() if not actions._image_translation_refs else None))
        deadline.start(20)
        while actions._image_translation_refs:
            loop.processEvents()
            if not actions._image_translation_refs:
                break
        deadline.stop()
        return sorted(tmp_path.glob("*.png")), opened

    def test_replace_mode_writes_a_png_of_the_original_size(self, qapp, monkeypatch, tmp_path):
        ocr = {"code": 100, "data": [
            {"box": box(20, 20, 160, 22), "text": "Hello", "score": 0.9}]}

        files, opened = self._run(monkeypatch, tmp_path, "replace", ocr_result=ocr)

        assert files, "没有落盘产物"
        saved = QImage(str(files[-1]))
        assert not saved.isNull(), "产物不是能读回的图片"
        assert saved.size() == white_image(240, 120).size()
        rect = bounding_rect(box(20, 20, 160, 22))
        assert dark_pixels(saved, rect) > 0, "替换模式产物里应有译文"
        assert opened == [str(files[-1])], "产物应当能在查看器里打开（可另存）"

    def test_bilingual_mode_writes_a_taller_png(self, qapp, monkeypatch, tmp_path):
        ocr = {"code": 100, "data": [
            {"box": box(20, 20, 160, 22), "text": "Hello", "score": 0.9},
            {"box": box(20, 60, 160, 22), "text": "World", "score": 0.9}]}

        files, _opened = self._run(monkeypatch, tmp_path, "bilingual", ocr_result=ocr)

        saved = QImage(str(files[-1]))
        assert saved.height() > 120, "双语模式应当长出译文区"

    def test_no_text_recognized_writes_nothing(self, qapp, monkeypatch, tmp_path):
        files, opened = self._run(monkeypatch, tmp_path, "replace",
                                  ocr_result={"code": 100, "data": []})
        assert files == [] and opened == []


def _stub_config(mode):
    """落图只需要配置管理器提供模式与翻译参数。"""
    class _Config:
        def get_translation_image_mode(self):
            return mode

        def get_translation_request_params(self):
            return {"target_lang": "zh-Hans", "split_sentences": "nonewlines",
                    "preserve_formatting": True}

    return _Config()
