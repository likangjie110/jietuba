# -*- coding: utf-8 -*-
"""配色提取：`core.palette` 的纯函数 + 提取动作。

判据：一张由已知颜色拼出来的图，主色就是那些颜色，占比也对得上；空图 / 坏输入如实
返回空表而不是抛异常（提取失败不能把截图链路带塌）。
"""

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

from core.palette import (
    DEFAULT_COLORS,
    MAX_COLORS,
    PaletteColor,
    extract_palette,
    palette_text,
)


def _image(width=200, height=200, color="black") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return image


def _halves(top_color: str, bottom_color: str) -> QImage:
    """上下两半各一种颜色的图：主色必然就是这两个，且各占 50%。"""
    image = _image(200, 200, top_color)
    painter = None
    from PySide6.QtGui import QPainter

    painter = QPainter(image)
    painter.fillRect(0, 100, 200, 100, QColor(bottom_color))
    painter.end()
    return image


class TestExtractPalette:
    def test_single_color_image_yields_that_color(self):
        colors = extract_palette(_image(color="#3366CC"))

        assert colors == [PaletteColor("#3366CC", 1.0)]

    def test_two_halves_are_reported_with_their_ratios(self):
        colors = extract_palette(_halves("#FF0000", "#0000FF"))

        assert [color.hex for color in colors] == ["#FF0000", "#0000FF"]
        assert all(abs(color.ratio - 0.5) < 0.02 for color in colors)

    def test_colors_are_sorted_by_ratio(self):
        image = _image(200, 200, "#FF0000")
        from PySide6.QtGui import QPainter

        painter = QPainter(image)
        painter.fillRect(0, 150, 200, 50, QColor("#00FF00"))  # 1/4 面积
        painter.end()

        colors = extract_palette(image)

        assert colors[0].hex == "#FF0000"
        assert colors[0].ratio > colors[1].ratio

    def test_count_is_respected_and_clamped(self):
        image = _image(200, 200, "#FF0000")
        from PySide6.QtGui import QPainter

        painter = QPainter(image)
        for index in range(8):
            painter.fillRect(index * 20, 0, 20, 20, QColor(index * 30, 0, 0))
        painter.end()

        assert len(extract_palette(image, count=2)) <= 2
        # 超过上限时夹到 MAX_COLORS，而不是把量化结果全倒出来
        assert len(extract_palette(image, count=999)) <= MAX_COLORS

    def test_bad_count_falls_back_to_default(self):
        image = _image(color="#123456")

        assert extract_palette(image, count="不是数字") == [PaletteColor("#123456", 1.0)]
        assert DEFAULT_COLORS == 6

    def test_transparent_image_does_not_produce_black(self):
        """整张透明的图合成到白底后应当是白色，不能因为 alpha 变成黑色。"""
        image = QImage(50, 50, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))

        colors = extract_palette(image)

        assert colors and colors[0].hex == "#FFFFFF"

    def test_empty_image_returns_empty_list(self):
        assert extract_palette(QImage()) == []
        assert extract_palette(None) == []

    def test_tiny_image_is_handled(self):
        assert len(extract_palette(_image(1, 1, "#00FF00"))) == 1


class TestPaletteText:
    def test_plain_text_is_one_hex_per_line(self):
        colors = [PaletteColor("#112233", 0.5), PaletteColor("#445566", 0.2)]

        assert palette_text(colors) == "#112233\n#445566"

    def test_ratio_variant_includes_percentages(self):
        text = palette_text([PaletteColor("#112233", 0.5)], with_ratio=True)

        assert text.startswith("#112233")
        assert "50.0%" in text

    def test_empty_input_is_empty_string(self):
        assert palette_text([]) == ""
        assert palette_text(None) == ""


class _FakeApp:
    config_manager = None


def _patch_capture(monkeypatch, image=None):
    from core import actions

    monkeypatch.setattr(actions, "capture_at_cursor",
                        lambda app: (image or _halves("#FF0000", "#0000FF"),
                                     QRectF(0, 0, 200, 200)))
    monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)
    monkeypatch.setattr(actions, "_record_history", lambda image, rect, app: None)


class TestExtractColorsAction:
    def test_action_copies_the_palette_and_opens_the_window(self, monkeypatch, qapp):
        from core import actions

        _patch_capture(monkeypatch)
        opened = []
        monkeypatch.setattr("ui.palette_window.show_palette_result",
                            lambda colors, parent=None: opened.append(colors))

        assert actions.run_action("extract_colors", _FakeApp()) is True

        assert opened and [color.hex for color in opened[0]] == ["#FF0000", "#0000FF"]
        assert qapp.clipboard().text() == "#FF0000\n#0000FF"

    def test_action_reports_when_nothing_can_be_extracted(self, monkeypatch, qapp):
        from core import actions

        _patch_capture(monkeypatch, _image(10, 10, "#FFFFFF"))
        monkeypatch.setattr("ui.palette_window.show_palette_result",
                            lambda colors, parent=None: None)
        warnings = []
        monkeypatch.setattr("ui.dialogs.show_warning_dialog",
                            lambda parent, title, message: warnings.append(message))
        monkeypatch.setattr("core.palette.extract_palette", lambda image, count=6: [])

        assert actions.run_action("extract_colors", _FakeApp()) is False
        assert warnings, "提取不出颜色时没有给用户任何提示"

    def test_action_is_registered_as_a_silent_capture(self):
        from core import actions

        action = actions.ACTIONS_BY_ID["extract_colors"]
        assert action.silent_capture is True
        assert "extract_colors" in actions.GESTURE_ACTION_IDS


class TestPaletteWindow:
    def _colors(self):
        return [PaletteColor("#112233", 0.6), PaletteColor("#AABBCC", 0.3)]

    def test_window_builds_one_swatch_per_color(self, qapp):
        from ui.palette_window import PaletteWindow

        window = PaletteWindow(self._colors())
        try:
            assert len(window.swatches) == 2
            assert [swatch.color.hex for swatch in window.swatches] == ["#112233", "#AABBCC"]
            assert window.copy_all_button.isEnabled() is True
        finally:
            window.deleteLater()

    def test_clicking_a_swatch_copies_that_color(self, qapp):
        from ui.palette_window import PaletteWindow

        window = PaletteWindow(self._colors())
        try:
            window.swatches[1].clicked.emit("#AABBCC")
            assert qapp.clipboard().text() == "#AABBCC"
        finally:
            window.deleteLater()

    def test_copy_all_puts_every_hex_on_its_own_line(self, qapp):
        from ui.palette_window import PaletteWindow

        window = PaletteWindow(self._colors())
        try:
            assert window.copy_all() is True
            assert qapp.clipboard().text() == "#112233\n#AABBCC"
        finally:
            window.deleteLater()

    def test_empty_result_window_still_opens_and_says_so(self, qapp):
        from ui.palette_window import PaletteWindow

        window = PaletteWindow([])
        try:
            assert window.swatches == []
            assert window.copy_all_button.isEnabled() is False
            assert window.copy_all() is False
        finally:
            window.deleteLater()
