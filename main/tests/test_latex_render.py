# -*- coding: utf-8 -*-
"""LaTeX 渲染：解析、排版尺寸、真实绘制的像素判据。

判据分三层，逐层更接近「用户看到了什么」：

- 解析层：结构对不对（分数里有分子分母、``x^2`` 的上标挂到了 x 上、未知命令不吞内容）；
- 排版层：盒子尺寸合理（分数比单字高、上下标把盒子撑高、矩阵按行列排开）；
- 绘制层：真的画出来了（有非透明像素、分数中间有一条横线、根号有一条上横线）。
"""

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from ocr.latex_render import (
    _FractionBox,
    _Glyph,
    _GridBox,
    _ScriptBox,
    _SqrtBox,
    build_layout,
    layout_size,
    looks_like_latex,
    make_font,
    parse_latex,
    render_latex_image,
)

INK = QColor("#000000")


@pytest.fixture(scope="module", autouse=True)
def _qt_app(qapp):
    """排版要量字体，必须已经有 QGuiApplication；没有它 Qt 会直接 abort。"""
    yield qapp


def _text_of(node) -> str:
    """节点的文字：单原子直接取 text，``{...}`` 一组则取组内第一个。"""
    if node.text:
        return node.text
    return "".join(child.text for child in node.children)


def _ink_pixels(image: QImage) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                count += 1
    return count


def _row_ink(image: QImage, y: int) -> int:
    return sum(1 for x in range(image.width()) if image.pixelColor(x, y).alpha() > 0)


def _box_of(latex, pixel_size=24):
    return build_layout(latex, pixel_size=pixel_size, color=INK)


class TestParsing:
    def test_a_fraction_has_a_numerator_and_a_denominator(self):
        node = parse_latex(r"\frac{a}{b}")

        assert node.kind == "group"
        fraction = node.children[0]
        assert fraction.kind == "frac"
        assert fraction.rows[0][0].children[0].text == "a"
        assert fraction.rows[1][0].children[0].text == "b"

    def test_scripts_attach_to_the_previous_atom(self):
        node = parse_latex("x^2")

        script = node.children[0]
        assert script.kind == "script"
        assert script.base.text == "x"
        assert _text_of(script.sup) == "2"

    def test_subscript_and_superscript_land_in_the_same_box(self):
        script = parse_latex("x_i^2").children[0]

        assert script.kind == "script"
        assert _text_of(script.sup) == "2"
        assert _text_of(script.sub) == "i"

    def test_greek_and_operators_become_symbols(self):
        node = parse_latex(r"\alpha \times \beta")

        texts = [child.text for child in node.children]
        assert "α" in texts and "×" in texts and "β" in texts

    def test_unknown_command_keeps_its_name(self):
        """认不出的命令去掉反斜杠按字面画，不能把内容吞掉。"""
        node = parse_latex(r"\notacommand")

        assert node.children[0].text == "notacommand"

    def test_text_command_is_upright(self):
        styled = parse_latex(r"\text{hello}").children[0]

        assert styled.kind == "styled"
        assert styled.bold is False and styled.italic is False

    def test_matrix_environment_becomes_a_grid(self):
        node = parse_latex(r"\begin{matrix}a & b \\ c & d\end{matrix}")

        # 顶层是个 group，矩阵自身又包了一层（好让 pmatrix 一类能在两侧加括号）
        grid = node.children[0].children[0]
        assert grid.kind == "grid"
        assert len(grid.rows) == 2 and len(grid.rows[0]) == 2

    def test_pmatrix_wraps_the_grid_in_parentheses(self):
        node = parse_latex(r"\begin{pmatrix}a\end{pmatrix}")

        parts = node.children[0].children
        assert [child.kind for child in parts] == ["delim"]
        assert parts[0].text == "(" and parts[0].base.text == ")"
        assert parts[0].children[0].kind == "grid"

    def test_unbalanced_braces_do_not_raise(self):
        for latex in ("{a", "a}", r"\frac{a}", r"\sqrt[", r"\begin{matrix}a"):
            parse_latex(latex)

    def test_empty_input_is_an_empty_group(self):
        node = parse_latex("")
        assert node.kind == "group" and node.children == []


class TestLayout:
    def test_a_fraction_is_taller_than_a_single_glyph(self):
        assert _box_of(r"\frac{a}{b}").height > _box_of("a").height

    def test_a_fraction_box_reports_its_parts(self):
        box = _box_of(r"\frac{1}{2}")
        assert isinstance(box.children[0], _FractionBox)

    def test_scripts_make_the_box_taller_and_wider(self):
        plain = _box_of("x")

        sup = _box_of("x^2")
        assert sup.height > plain.height
        assert sup.width > plain.width

    def test_sqrt_adds_the_radical_width(self):
        content = _box_of("x")
        root = _box_of(r"\sqrt{x}")

        assert root.width > content.width
        assert root.height >= content.height

    def test_grid_box_is_as_wide_as_its_columns(self):
        box = _box_of(r"\begin{matrix}a & b \\ c & d\end{matrix}")

        assert box.width > _box_of("a").width

    def test_layout_size_matches_the_box(self):
        width, height = layout_size(r"\frac{a}{b}", pixel_size=24)
        box = _box_of(r"\frac{a}{b}")

        assert (width, height) == pytest.approx((box.width, box.height))

    def test_larger_font_gives_a_larger_box(self):
        small = build_layout(r"\int_0^1 x^2 dx", pixel_size=16, color=INK)
        large = build_layout(r"\int_0^1 x^2 dx", pixel_size=32, color=INK)

        assert large.width > small.width and large.height > small.height

    def test_boxes_are_positive_sized_for_realistic_input(self):
        for latex in (r"\frac{a}{b}", r"\sqrt[3]{x+1}", "x_i^2",
                      r"\begin{cases}a & b \\ c & d\end{cases}", r"\alpha\beta"):
            box = _box_of(latex)
            assert box.width > 0 and box.height > 0

    def test_builders_are_used_for_each_kind(self):
        assert isinstance(_box_of("a").children[0], _Glyph)
        assert isinstance(_box_of("x^2").children[0], _ScriptBox)
        assert isinstance(_box_of(r"\sqrt{x}").children[0], _SqrtBox)
        grid = _box_of(r"\begin{matrix}a\\b\end{matrix}").children[0]
        assert isinstance(grid.children[0], _GridBox)


class TestPainting:
    def test_rendering_produces_ink(self):
        image = render_latex_image(r"\frac{a}{b}", pixel_size=28, color=INK)

        assert image.width() > 0 and image.height() > 0
        assert _ink_pixels(image) > 20

    def test_empty_formula_renders_nothing(self):
        image = render_latex_image("", pixel_size=28, color=INK)

        assert _ink_pixels(image) == 0

    def test_the_fraction_bar_is_drawn(self):
        """分数线：中间那几行里应当有一行几乎横贯整个公式。"""
        image = render_latex_image(r"\frac{W}{M}", pixel_size=40, color=INK)

        widest = max(_row_ink(image, y) for y in range(image.height()))
        assert widest > image.width() * 0.5

    def test_transparent_background_keeps_alpha_only_where_ink_is(self):
        image = render_latex_image("x", pixel_size=28, color=INK)

        assert image.pixelColor(0, 0).alpha() == 0
        assert _ink_pixels(image) > 0

    def test_paint_into_a_painter_sized_rect_does_not_raise(self):
        image = QImage(200, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("white"))
        painter = QPainter(image)
        from ocr.latex_render import paint_latex

        paint_latex(painter, r"\int_0^\infty e^{-x}dx", QRectF(0, 0, 200, 80),
                    pixel_size=22, color=INK)
        painter.end()

        assert _ink_pixels(image) > 0

    def test_device_pixel_ratio_scales_the_image(self):
        base = render_latex_image("x", pixel_size=20, color=INK)
        retina = render_latex_image("x", pixel_size=20, color=INK, device_pixel_ratio=2.0)

        assert retina.width() == pytest.approx(base.width() * 2, abs=2)
        assert retina.devicePixelRatio() == pytest.approx(2.0)

    def test_math_font_helper_returns_the_requested_size(self):
        assert make_font(17).pixelSize() == 17


class TestLooksLikeLatex:
    def test_commands_and_balanced_braces_count_as_latex(self):
        assert looks_like_latex(r"\frac{a}{b}") is True
        assert looks_like_latex("E = mc^2") is False

    def test_plain_prose_is_not_latex(self):
        assert looks_like_latex("这是普通的一段话") is False
        assert looks_like_latex("") is False

    def test_unbalanced_braces_are_not_latex(self):
        assert looks_like_latex("{a") is False


class TestLatexView:
    """控件层：高度要按公式量出来，否则分数/根号会被下边缘裁掉（真机踩到过）。"""

    def test_the_widget_is_tall_enough_for_the_formula(self, qapp):
        from ui.latex_view import LatexView

        view = LatexView(r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}", pixel_size=26)
        try:
            _width, height = layout_size(r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
                                         pixel_size=26)
            assert view.minimumHeight() >= height
        finally:
            view.deleteLater()

    def test_switching_the_formula_updates_the_height(self, qapp):
        from ui.latex_view import LatexView

        view = LatexView("a", pixel_size=26)
        try:
            small = view.minimumHeight()
            view.set_latex(r"\frac{a}{\frac{b}{c}}")
            assert view.minimumHeight() > small
            assert view.latex() == r"\frac{a}{\frac{b}{c}}"
        finally:
            view.deleteLater()

    def test_the_same_formula_is_not_re_measured(self, qapp):
        from ui.latex_view import LatexView

        view = LatexView("a", pixel_size=26)
        try:
            view.set_latex("a")
            assert view.latex() == "a"
        finally:
            view.deleteLater()
