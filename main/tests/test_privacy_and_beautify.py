# -*- coding: utf-8 -*-
"""隐私自动遮挡与美化导出。

两条都走真实动作入口（``core.actions`` 里那两条静默动作），OCR 用替身喂已知结果，
断言的是**产物像素**与**落盘文件**：被检测到的号码区间确实被盖住、区域外一个像素没动；
美化产物尺寸与声明一致、多尺寸文件真的写出来。
"""

import pytest
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from core import actions
from core.beautify import EXPORT_WIDTHS, add_margins, apply_layout, export_sizes
from core.privacy import find_sensitive_spans, mask_regions, mosaic_rectangles, span_rect

LINE = "我的手机号 13812345678 请勿外传"
BOX = [[10, 10], [190, 10], [190, 30], [10, 30]]


@pytest.fixture(scope="module", autouse=True)
def qapp():
    """本文件里画文字要 QGuiApplication —— 不建就直接 abort，所以整个文件自动带上。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def text_image(text: str = LINE, width=220, height=60) -> QImage:
    """一张真实画了文字的图——马赛克要有东西可糊。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QColor("black"))
    painter.setFont(QFont("Helvetica", 14))
    painter.drawText(10, 32, text)
    painter.end()
    return image


def ocr_result(text: str = LINE, box=BOX) -> dict:
    return {"code": 100, "data": [{"box": box, "text": text, "score": 0.95}]}


class TestSpanDetection:
    def test_phone_email_id_and_card_are_found(self):
        text = "电话 13812345678 邮箱 a.b@example.com 身份证 11010519900307123X 卡 6222021234567890123"

        kinds = {span.kind for span in find_sensitive_spans(text)}

        assert kinds == {"phone", "email", "id_card", "bank_card"}

    def test_plain_text_has_no_spans(self):
        assert find_sensitive_spans("今天天气不错，适合出去走走") == []

    def test_a_number_is_reported_once(self):
        """11 位手机号同时也是「连续数字」的子串，不能报两次（否则框会叠两层）。"""
        spans = find_sensitive_spans("13812345678")
        assert len(spans) == 1 and spans[0].kind == "phone"

    def test_short_numbers_are_not_sensitive(self):
        assert find_sensitive_spans("订单号 12345 共 6 件") == []


class TestRegionMapping:
    def test_span_rect_covers_only_the_sensitive_part(self):
        start = LINE.index("13812345678")
        end = start + len("13812345678")

        rect = span_rect(BOX, start, end, len(LINE))

        left, top, right, bottom = rect
        assert 10 <= left < right <= 190
        assert (top, bottom) == (10, 30)
        # 只覆盖整行的一部分，而不是整行（整行打码会连普通文字一起糊掉）
        assert (right - left) < (190 - 10)

    def test_broken_box_gives_no_rect(self):
        assert span_rect(None, 0, 3, 10) is None
        assert span_rect([], 0, 3, 10) is None

    def test_mask_regions_reads_the_ocr_result(self):
        regions = mask_regions(ocr_result())

        assert len(regions) == 1
        assert regions[0]["kind"] == "phone"
        assert regions[0]["text"] == "13812345678"

    def test_result_without_sensitive_text_gives_no_regions(self):
        assert mask_regions(ocr_result("这是一段普通文字")) == []
        assert mask_regions(None) == []


class TestMosaicPixels:
    def test_only_the_sensitive_area_changes(self):
        image = text_image()
        regions = mask_regions(ocr_result())

        output = mosaic_rectangles(image, regions)

        left, top, right, bottom = regions[0]["rect"]
        changed = sum(1 for x in range(left, right) for y in range(top, bottom)
                      if output.pixelColor(x, y) != image.pixelColor(x, y))
        assert changed > 100, "号码区间没有被糊到"
        # 区域外一个像素都不该动
        for point in ((5, 5), (200, 55), (2, 30)):
            assert output.pixelColor(*point) == image.pixelColor(*point)

    def test_no_regions_returns_a_copy(self):
        image = text_image()
        output = mosaic_rectangles(image, [])
        assert (output.width(), output.height()) == (image.width(), image.height()) and output is not image


class TestMaskAction:
    """真实入口：静默截图 → OCR → 遮挡 → 剪贴板。"""

    def _run(self, monkeypatch, result, clip):
        from PySide6.QtCore import QRectF

        monkeypatch.setattr(actions, "capture_at_cursor",
                            lambda app: (text_image(), QRectF(0, 0, 220, 60)))
        monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)
        monkeypatch.setattr("ocr.is_ocr_available", lambda: True)
        monkeypatch.setattr("ocr.recognize_text", lambda image, **kwargs: result)
        monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: clip))
        return actions.run_action("mask_sensitive_info", _FakeApp())

    def test_masked_image_lands_in_the_clipboard(self, qapp, monkeypatch):
        clip = _FakeClipboard()
        assert self._run(monkeypatch, ocr_result(), clip) is True

        assert _wait_until(lambda: not clip.image.isNull() if clip.image else False, qapp)
        left, top, right, bottom = mask_regions(ocr_result())[0]["rect"]
        original = text_image()
        changed = sum(1 for x in range(left, right) for y in range(top, bottom)
                      if clip.image.pixelColor(x, y) != original.pixelColor(x, y))
        assert changed > 100, "剪贴板里拿到的不是遮挡后的图"

    def test_nothing_sensitive_copies_nothing(self, qapp, monkeypatch):
        clip = _FakeClipboard()
        assert self._run(monkeypatch, ocr_result("普通文字"), clip) is True

        assert _wait_until(lambda: actions._privacy_refs == [], qapp)
        assert clip.image is None


class TestBeautifyPixels:
    def test_margins_are_added_around_the_image(self, qapp):
        image = QImage(100, 60, QImage.Format.Format_ARGB32)
        image.fill(QColor("#3366CC"))

        output = add_margins(image, 20, "#FFFFFF")

        assert (output.width(), output.height()) == (140, 100)
        assert output.pixelColor(5, 5) == QColor("white")
        assert output.pixelColor(25, 25) == QColor("#3366CC")

    def test_layout_adds_room_for_the_shadow(self, qapp):
        image = QImage(100, 60, QImage.Format.Format_ARGB32)
        image.fill(QColor("#3366CC"))

        output = apply_layout(image, margin=20, radius=10, shadow=16,
                              background="#FFFFFF")

        assert (output.width(), output.height()) == (100 + 40 + 16, 60 + 40 + 16)
        assert output.pixelColor(2, 2) == QColor("white")

    def test_zero_margin_and_shadow_keep_the_size(self, qapp):
        image = QImage(40, 30, QImage.Format.Format_ARGB32)
        image.fill(QColor("#3366CC"))

        output = apply_layout(image, margin=0, radius=0, shadow=0)

        assert (output.width(), output.height()) == (image.width(), image.height())
        assert output.pixelColor(20, 15) == QColor("#3366CC")

    def test_background_colour_is_honoured(self, qapp):
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("black"))

        output = add_margins(image, 10, "#123456")

        assert output.pixelColor(2, 2) == QColor("#123456")


class TestSizeExport:
    def test_sizes_are_scaled_keeping_the_ratio(self, qapp):
        image = QImage(1000, 500, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))

        pairs = export_sizes(image, (640, 1280))

        assert [width for width, _image in pairs] == [640]
        assert (pairs[0][1].width(), pairs[0][1].height()) == (640, 320)

    def test_small_images_are_not_upscaled(self, qapp):
        image = QImage(100, 50, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))

        assert export_sizes(image, EXPORT_WIDTHS) == []

    def test_upscaling_can_be_requested_explicitly(self, qapp):
        image = QImage(100, 50, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))

        pairs = export_sizes(image, (640,), allow_upscale=True)

        assert pairs and (pairs[0][1].width(), pairs[0][1].height()) == (640, 320)


class TestBeautifyAction:
    """真实入口：静默截图 → 美化 → 剪贴板 + 多尺寸落盘。"""

    def test_files_are_written_and_the_clipboard_gets_the_result(
            self, qapp, monkeypatch, tmp_path):
        from PySide6.QtCore import QRectF

        from settings import get_tool_settings_manager

        config = get_tool_settings_manager()
        config.set_beautify_margin(10)
        config.set_beautify_radius(0)
        config.set_beautify_shadow(0)
        config.set_beautify_export_widths([640, 1280])

        clip = _FakeClipboard()
        monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: clip))
        monkeypatch.setattr(actions, "capture_at_cursor",
                            lambda app: (text_image(width=1000, height=400),
                                         QRectF(0, 0, 1000, 400)))
        monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)

        written = []

        class _Service:
            def __init__(self, config_manager=None):
                pass

            def save_qimage_async(self, image, *, prefix="", suffix="", **kwargs):
                path = tmp_path / f"{prefix}{suffix}.png"
                image.save(str(path), "PNG")
                written.append((suffix, image.size()))
                return str(path)

        monkeypatch.setattr("core.save.SaveService", _Service)

        assert actions.run_action("beautify_export", _FakeApp()) is True

        assert clip.image is not None
        assert (clip.image.width(), clip.image.height()) == (1020, 420)
        # 原图 1000px 宽：640 会缩、1280 不放大所以只出一个（不放大是刻意的）
        assert [suffix for suffix, _size in written] == ["_640w"]
        saved = QImage(str(tmp_path / "美化_640w.png"))
        assert not saved.isNull() and saved.width() == 640


# ── 测试替身 ──────────────────────────────────────────

class _FakeApp:
    """静默动作只需要一个带 config_manager 的应用替身。"""

    @property
    def config_manager(self):
        from settings import get_tool_settings_manager

        return get_tool_settings_manager()


class _FakeClipboard:
    def __init__(self):
        self.image = None
        self.text = ""

    def setImage(self, image):
        self.image = image

    def setText(self, text):
        self.text = text


def _wait_until(predicate, qapp, timeout_ms=4000) -> bool:
    """等后台 OCR 线程把结果送回主线程。"""
    import time

    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False
