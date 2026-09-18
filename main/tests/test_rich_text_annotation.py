# -*- coding: utf-8 -*-
"""富文本标注：同一条文字标注里可以混多段不同字体/字号/颜色。

判据有三条，这里逐个守：
1. 逐字符格式真的落到文档里（选区之外的那截不受影响）；
2. 格式随标注存续——再次进编辑、或从 HTML 重建，格式都还在；
3. 导出（保存/复制走的 `ExportService.export`）渲染出来的图上，两段颜色都在，
   没有退化成单一格式。

驱动的是发布实现：`TextItem` 的光标接口、`SmartEditController` 的格式槽、
`ExportService` 的真实渲染路径。
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFocusEvent, QFont, QImage, QPen, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import QGraphicsScene

from canvas.items import TextItem
from canvas.smart_edit_controller import SmartEditController
from core.export import ExportService

RED = QColor("#FF0000")
BLUE = QColor("#0000FF")


def _item(text="红色蓝色", size=24):
    item = TextItem(text, QPointF(0, 0), QFont("Arial", size), QColor("black"))
    item.set_outline(False)
    return item


def _color_format(color: QColor) -> QTextCharFormat:
    from PySide6.QtGui import QBrush

    fmt = QTextCharFormat()
    fmt.setForeground(QBrush(color))
    return fmt


def _font_format(family: str, size: int) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setFont(QFont(family, size))
    return fmt


def _select(item, start: int, end: int):
    """像用户那样框选一段文字（真实光标接口，不是伪造内部状态）。"""
    cursor = item.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    item.setTextCursor(cursor)
    return cursor


def _fragment_formats(item) -> list:
    """逐片段读出 (文字, 颜色, 字体族, 字号)，用于断言格式真的落到文档里。"""
    out = []
    block = item.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.text():
                fmt = fragment.charFormat()
                font = fmt.font()
                out.append((
                    fragment.text(),
                    fmt.foreground().color().name(),
                    font.family(),
                    round(font.pointSizeF(), 1),
                ))
            iterator += 1
        block = block.next()
    return out


class TestMergeCharFormat:
    def test_without_a_selection_nothing_is_applied(self, qapp):
        item = _item("备注")

        assert item.merge_char_format(_color_format(RED)) is False
        assert item.has_mixed_char_formats() is False

    def test_only_the_selected_part_changes(self, qapp):
        item = _item("AAABBB")

        _select(item, 0, 3)
        assert item.merge_char_format(_color_format(RED)) is True

        fragments = _fragment_formats(item)
        assert fragments[0][1] == "#ff0000"
        assert all(color != "#ff0000" for text, color, *_ in fragments if text == "BBB")

    def test_two_segments_keep_their_own_formats(self, qapp):
        item = _item("AAABBB")

        _select(item, 0, 3)
        item.merge_char_format(_color_format(RED))
        _select(item, 3, 6)
        item.merge_char_format(_font_format("Courier New", 30))

        assert item.has_mixed_char_formats() is True
        fragments = _fragment_formats(item)
        first = next(f for f in fragments if f[0] == "AAA")
        second = next(f for f in fragments if f[0] == "BBB")
        assert first[1] == "#ff0000"
        assert second[2].startswith("Courier")
        assert second[3] == 30.0
        assert first[3] == 24.0, "前半段的字号不该被后半段的修改带走"

    def test_formats_survive_leaving_and_re_entering_edit_mode(self, qapp):
        item = _item("AAABBB")
        _select(item, 0, 3)
        item.merge_char_format(_color_format(RED))
        _select(item, 3, 6)
        item.merge_char_format(_color_format(BLUE))

        # 退出编辑（点到别处）再回来
        item.clearFocus()
        item.setFocus()

        assert item.has_mixed_char_formats() is True
        colors = {f[1] for f in _fragment_formats(item)}
        assert {"#ff0000", "#0000ff"} <= colors

        # 第三次修改不会把前两段冲掉
        _select(item, 0, 3)
        item.merge_char_format(_font_format("Courier New", 30))
        colors = {f[1] for f in _fragment_formats(item)}
        assert "#0000ff" in colors, "改前半段的字体把后半段的颜色冲掉了"

    def test_html_roundtrip_keeps_the_formats(self, qapp):
        """标注存的就是文档本身：按 HTML 重建也必须还在（否则复制/贴图就退化了）。"""
        item = _item("AAABBB")
        _select(item, 0, 3)
        item.merge_char_format(_color_format(RED))
        _select(item, 3, 6)
        item.merge_char_format(_color_format(BLUE))

        rebuilt = _item("")
        rebuilt.setHtml(item.toHtml())

        colors = {f[1] for f in _fragment_formats(rebuilt)}
        assert {"#ff0000", "#0000ff"} <= colors

    def test_cursor_char_format_follows_the_cursor(self, qapp):
        item = _item("AAABBB")
        _select(item, 0, 3)
        item.merge_char_format(_color_format(RED))

        _select(item, 1, 2)
        assert item.cursor_char_format().foreground().color().name() == "#ff0000"
        _select(item, 4, 5)
        assert item.cursor_char_format().foreground().color().name() != "#ff0000"


class TestControllerAppliesToTheSelection:
    def _controller_with(self, item):
        scene = QGraphicsScene()
        scene.addItem(item)
        controller = SmartEditController(scene)
        controller.select_item(item)
        return controller

    def test_selection_survives_the_focus_loss_from_clicking_the_toolbar(self, qapp):
        """真实操作顺序：进编辑 → 框选 → 点工具栏（文字项失焦）→ 改格式。

        文字项失焦时会清掉文档选区，所以这一条专门盯住「记住那段范围」的行为。
        失焦事件直接投给真实处理器：视图外的事件派发依赖窗口激活，在这台机器上
        是出了名的不稳，而这里要验的是处理器本身的契约。
        """
        item = _item("AAABBB")
        controller = self._controller_with(item)
        item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        item.setFocus()
        _select(item, 0, 3)

        item.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))     # 等价于点了工具栏上的按钮
        assert item.textCursor().hasSelection() is False
        assert item._format_target == (0, 3)

        controller.on_text_color_changed(RED)

        first = next(f for f in _fragment_formats(item) if f[0] == "AAA")
        second = next(f for f in _fragment_formats(item) if f[0] == "BBB")
        assert first[1] == "#ff0000"
        assert second[1] != "#ff0000"

    def test_switching_items_drops_the_remembered_range(self, qapp):
        """换到别的标注再回来，不该拿旧范围去改新标注。"""
        from canvas.items import RectItem

        first = _item("AAABBB")
        scene = QGraphicsScene()
        scene.addItem(first)
        controller = SmartEditController(scene)
        controller.select_item(first)
        _select(first, 0, 3)
        first.clearFocus()                      # 记下了 0..3

        other = RectItem(QRectF(40, 40, 20, 20), QPen(QColor("red"), 2))
        scene.addItem(other)
        controller.select_item(other)
        controller.select_item(first)
        first.setTextCursor(QTextCursor(first.document()))    # 取消选择：整条改

        controller.on_text_color_changed(BLUE)

        assert first.defaultTextColor().name() == "#0000ff", "旧范围被当成了这次的目标"
        assert first.has_mixed_char_formats() is False

    def test_color_change_hits_only_the_selection(self, qapp):
        item = _item("AAABBB")
        controller = self._controller_with(item)
        _select(item, 0, 3)

        controller.on_text_color_changed(RED)

        first = next(f for f in _fragment_formats(item) if f[0] == "AAA")
        second = next(f for f in _fragment_formats(item) if f[0] == "BBB")
        assert first[1] == "#ff0000"
        assert second[1] != "#ff0000"

    def test_font_change_hits_only_the_selection(self, qapp):
        item = _item("AAABBB")
        controller = self._controller_with(item)
        _select(item, 3, 6)

        controller.on_text_font_changed(QFont("Courier New", 30))

        first = next(f for f in _fragment_formats(item) if f[0] == "AAA")
        second = next(f for f in _fragment_formats(item) if f[0] == "BBB")
        assert second[2].startswith("Courier") and second[3] == 30.0
        assert first[3] == 24.0

    def test_without_a_selection_the_whole_item_changes(self, qapp):
        """老行为要留着：选中整条标注改样式时，整条一起变。"""
        item = _item("AAABBB")
        controller = self._controller_with(item)
        item.setTextCursor(QTextCursor(item.document()))     # 清掉选区

        controller.on_text_color_changed(BLUE)
        controller.on_text_font_changed(QFont("Courier New", 28))

        assert item.defaultTextColor().name() == "#0000ff"
        assert item.font().family().startswith("Courier")
        assert item.has_mixed_char_formats() is False


class TestExportRendersRichText:
    """保存/复制走的是 ExportService.export → scene.render，必须把逐段格式画出来。"""

    def _scene_with(self, item):
        from canvas.scene import CanvasScene

        image = QImage(240, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("white"))
        scene = CanvasScene(image, QRectF(0, 0, 240, 80), enable_mosaic=False)
        scene.addItem(item)
        return scene

    def test_two_segments_render_in_their_own_colors(self, qapp):
        item = _item("AAABBB", size=28)
        _select(item, 0, 3)
        item.merge_char_format(_color_format(RED))
        _select(item, 3, 6)
        item.merge_char_format(_color_format(BLUE))

        exported = ExportService(self._scene_with(item)).export(QRectF(0, 0, 240, 80))
        assert not exported.isNull()

        pixels = [
            exported.pixelColor(x, y)
            for x in range(exported.width())
            for y in range(exported.height())
        ]
        has_red = any(p.red() > 150 and p.green() < 110 and p.blue() < 110 for p in pixels)
        has_blue = any(p.blue() > 150 and p.red() < 110 and p.green() < 110 for p in pixels)
        assert has_red, "导出的图里没有前半段的红色"
        assert has_blue, "导出的图里没有后半段的蓝色"

    def test_single_format_item_still_renders(self, qapp):
        """没有混排时照旧（回归保护）。"""
        item = _item("AAAAAA", size=28)

        exported = ExportService(self._scene_with(item)).export(QRectF(0, 0, 240, 80))

        pixels = [exported.pixelColor(x, y) for x in range(exported.width())
                  for y in range(exported.height())]
        assert any(p.red() < 60 and p.green() < 60 and p.blue() < 60 for p in pixels)
        assert not any(p.red() > 150 and p.green() < 110 and p.blue() < 110 for p in pixels)

    def test_whole_item_color_change_renders(self, qapp):
        """无选区时控制器改的是整条标注（setDefaultTextColor），导出也要跟着变。"""
        item = _item("AAAAAA", size=28)
        scene = self._scene_with(item)

        item.setDefaultTextColor(BLUE)

        exported = ExportService(scene).export(QRectF(0, 0, 240, 80))
        pixels = [exported.pixelColor(x, y) for x in range(exported.width())
                  for y in range(exported.height())]
        assert any(p.blue() > 150 and p.red() < 110 and p.green() < 110 for p in pixels)

    def test_whole_item_bold_change_renders(self, qapp):
        """整条改字号/字重同样要落到导出的图里（用墨量对比，不依赖具体字形）。"""
        plain = _item("AAAAAA", size=28)
        plain_scene = self._scene_with(plain)
        ink_plain = _dark_pixels(ExportService(plain_scene).export(QRectF(0, 0, 240, 80)))

        bold = _item("AAAAAA", size=28)
        bold_scene = self._scene_with(bold)
        font = QFont("Arial", 28)
        font.setBold(True)
        bold.setFont(font)
        ink_bold = _dark_pixels(ExportService(bold_scene).export(QRectF(0, 0, 240, 80)))

        assert ink_bold > ink_plain, (ink_bold, ink_plain)


def _dark_pixels(image) -> int:
    return sum(
        1
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).lightness() < 90
    )
