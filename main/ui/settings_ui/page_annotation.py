# -*- coding: utf-8 -*-
"""标注设置页 — Fluent Design

把各标注工具的**默认样式**（颜色、粗细、马赛克风格、文字字号）摆出来。

与工具栏的关系：两边读写的是同一份工具设置（``tools/<工具>/<键>``），所以这里改完，
下次用该工具画出来的就是新样式，工具栏那边也会跟着变（``ToolSettingsManager`` 发
``settings_changed``）。这里**不**另存一份默认值——那会变成两个真相。

「恢复默认」是把这一页涉及的键复位成 ``DEFAULT_SETTINGS`` 里的值（按工具走
``reset_tool``，它会连没在这一页露面的键一起复位，正好符合「恢复该工具默认」的语义）。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QScrollArea, QVBoxLayout, QWidget

from ui.fluent_lite import (
    CaptionLabel, ComboBox, FluentIcon, PushButton, SettingCard as FSettingCard, SpinBox,
)
from .components import SettingCardGroup
from .page_appearance import _make_color_btn, _update_color_btn

#: 这一页露面的工具：(工具 id, 界面标题, 是否需要透明度)
_TOOLS = (
    ("pen", "Pen", True),
    ("highlighter", "Highlighter", True),
    ("rect", "Rectangle", False),
    ("ellipse", "Ellipse", False),
    ("arrow", "Arrow", False),
)

_WIDTH_RANGE = (1, 60)
_OPACITY_RANGE = (0.1, 1.0)
_STROKE_KEYS = {
    "pen": "stroke_width",
    "highlighter": "stroke_width",
    "rect": "stroke_width",
    "ellipse": "stroke_width",
    "arrow": "stroke_width",
}

#: 马赛克风格（与 MosaicTool 支持的两种一致）
_MOSAIC_STYLES = (("pixelate", "Pixelate"), ("blur", "Blur"))
#: 马赛克笔刷档位（与 MosaicTool.BLOCK_SIZE_LEVELS 一致：粗中细）
_MOSAIC_BLOCK_SIZES = (10, 6, 3)
_TEXT_FONT_SIZE_RANGE = (8, 72)


def _color_row(dialog, group, tool_id, title, key):
    """一行颜色设置：色块按钮 + 取色对话框。"""
    card = FSettingCard(FluentIcon.PALETTE, title, dialog.tr("Default color"), parent=group)
    btn = _make_color_btn(card)
    color = QColor(dialog.config_manager.get_setting(tool_id, key))
    _update_color_btn(btn, color)

    def _pick():
        picked = QColorDialog(color, None)
        picked.setWindowTitle(dialog.tr("Pick a color"))
        picked.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        if picked.exec():
            chosen = picked.currentColor()
            color.setRgb(chosen.red(), chosen.green(), chosen.blue())
            _update_color_btn(btn, color)

    btn.clicked.connect(_pick)
    card.hBoxLayout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
    card.hBoxLayout.addSpacing(16)
    group.addSettingCard(card)

    def read():
        return color.name()

    def write(value):
        color.setRgb(QColor(value).rgb())
        _update_color_btn(btn, color)

    return read, write


def _number_row(dialog, group, tool_id, title, key, limits, step, *, decimals=None):
    """一行数字设置（粗细/透明度）。"""
    card = FSettingCard(FluentIcon.FONT_SIZE, title, dialog.tr("Default value"), parent=group)
    spin = SpinBox(card) if decimals is None else None
    if decimals is None:
        spin.setRange(limits[0], limits[1])
        spin.setSingleStep(step)
        spin.setValue(int(dialog.config_manager.get_setting(tool_id, key)))
    else:
        from ui.fluent_lite import DoubleSpinBox

        spin = DoubleSpinBox(card)
        spin.setRange(limits[0], limits[1])
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(float(dialog.config_manager.get_setting(tool_id, key)))
    spin.setFixedWidth(120)
    card.hBoxLayout.addWidget(spin, 0, Qt.AlignmentFlag.AlignRight)
    card.hBoxLayout.addSpacing(16)
    group.addSettingCard(card)
    return spin


def create_annotation_page(dialog) -> QWidget:
    """创建「标注」设置页。"""
    config = dialog.config_manager

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # 每个工具一组：颜色 + 粗细（荧光笔/画笔再加透明度）
    dialog._annotation_color_readers = {}
    dialog._annotation_widths = {}
    dialog._annotation_opacities = {}

    for tool_id, title, with_opacity in _TOOLS:
        group = SettingCardGroup(dialog.tr(title), view)
        read_color, write_color = _color_row(
            dialog, group, tool_id, dialog.tr("Color"), "color")
        dialog._annotation_color_readers[tool_id] = (read_color, write_color)
        dialog._annotation_widths[tool_id] = _number_row(
            dialog, group, tool_id, dialog.tr("Thickness"), _STROKE_KEYS[tool_id],
            _WIDTH_RANGE, 1)
        if with_opacity:
            dialog._annotation_opacities[tool_id] = _number_row(
                dialog, group, tool_id, dialog.tr("Opacity"), "opacity",
                _OPACITY_RANGE, 0.05, decimals=2)
        layout.addWidget(group)

    # 马赛克：风格 + 笔刷档位
    grp_mosaic = SettingCardGroup(dialog.tr("Mosaic"), view)
    style_card = FSettingCard(FluentIcon.FILTER, dialog.tr("Mosaic Style"),
                              dialog.tr("Pixelate for hard blocks, blur for soft ones."),
                              parent=grp_mosaic)
    dialog.annotation_mosaic_style_combo = ComboBox(style_card)
    dialog.annotation_mosaic_style_combo.setFixedWidth(140)
    for value, label in _MOSAIC_STYLES:
        dialog.annotation_mosaic_style_combo.addItem(dialog.tr(label), userData=value)
    current = str(config.get_setting("mosaic", "style", "pixelate") or "pixelate")
    index = dialog.annotation_mosaic_style_combo.findData(current)
    if index >= 0:
        dialog.annotation_mosaic_style_combo.setCurrentIndex(index)
    style_card.hBoxLayout.addWidget(dialog.annotation_mosaic_style_combo, 0,
                                    Qt.AlignmentFlag.AlignRight)
    style_card.hBoxLayout.addSpacing(16)
    grp_mosaic.addSettingCard(style_card)

    brush_card = FSettingCard(FluentIcon.BRUSH, dialog.tr("Mosaic Brush"),
                              dialog.tr("How coarse the mosaic brush is."),
                              parent=grp_mosaic)
    dialog.annotation_mosaic_brush_combo = ComboBox(brush_card)
    dialog.annotation_mosaic_brush_combo.setFixedWidth(140)
    for size in _MOSAIC_BLOCK_SIZES:
        # 档位按粗细写在数据里：block_size 越大格子越粗
        dialog.annotation_mosaic_brush_combo.addItem(
            dialog.tr("Coarse") if size >= 10 else
            dialog.tr("Medium") if size >= 6 else dialog.tr("Fine"),
            userData=size,
        )
    current_size = int(config.get_setting("mosaic", "block_size", _MOSAIC_BLOCK_SIZES[0]) or 10)
    index = dialog.annotation_mosaic_brush_combo.findData(current_size)
    if index >= 0:
        dialog.annotation_mosaic_brush_combo.setCurrentIndex(index)
    brush_card.hBoxLayout.addWidget(dialog.annotation_mosaic_brush_combo, 0,
                                    Qt.AlignmentFlag.AlignRight)
    brush_card.hBoxLayout.addSpacing(16)
    grp_mosaic.addSettingCard(brush_card)
    layout.addWidget(grp_mosaic)

    # 文字：字号
    grp_text = SettingCardGroup(dialog.tr("Text"), view)
    dialog.annotation_text_font_spin = _number_row(
        dialog, grp_text, "text", dialog.tr("Font Size"), "font_size",
        _TEXT_FONT_SIZE_RANGE, 1)
    layout.addWidget(grp_text)

    hint = CaptionLabel(
        dialog.tr("💡 These are the defaults each tool starts with; the toolbar keeps "
                  "remembering whatever you last used."),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    layout.addWidget(hint)

    buttons = FSettingCard(FluentIcon.SYNC, dialog.tr("Restore Defaults"),
                           dialog.tr("Reset every tool on this page to its factory style."),
                           parent=view)
    btn = PushButton(dialog.tr("Restore"), buttons)
    btn.setFixedHeight(32)
    btn.clicked.connect(lambda: reset_annotation_page(dialog))
    buttons.hBoxLayout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
    buttons.hBoxLayout.addSpacing(16)
    layout.addWidget(buttons)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll


def collect_annotation_settings(dialog) -> dict:
    """读出页面上的样式：``{工具 id: {键: 值}}``。"""
    values: dict = {}
    for tool_id, (read_color, _write) in getattr(dialog, "_annotation_color_readers", {}).items():
        values.setdefault(tool_id, {})["color"] = read_color()
    for tool_id, spin in getattr(dialog, "_annotation_widths", {}).items():
        values.setdefault(tool_id, {})[_STROKE_KEYS[tool_id]] = spin.value()
    for tool_id, spin in getattr(dialog, "_annotation_opacities", {}).items():
        values.setdefault(tool_id, {})["opacity"] = spin.value()
    if hasattr(dialog, "annotation_mosaic_style_combo"):
        values.setdefault("mosaic", {})["style"] = (
            dialog.annotation_mosaic_style_combo.currentData())
    if hasattr(dialog, "annotation_mosaic_brush_combo"):
        values.setdefault("mosaic", {})["block_size"] = (
            dialog.annotation_mosaic_brush_combo.currentData())
    if hasattr(dialog, "annotation_text_font_spin"):
        values.setdefault("text", {})["font_size"] = dialog.annotation_text_font_spin.value()
    return values


def refresh_annotation_page(dialog) -> None:
    """按配置刷新页面（语言切换/外部改动后）。"""
    config = dialog.config_manager
    for tool_id, (_read, write_color) in getattr(
            dialog, "_annotation_color_readers", {}).items():
        write_color(config.get_setting(tool_id, "color"))
    for tool_id, spin in getattr(dialog, "_annotation_widths", {}).items():
        spin.setValue(int(config.get_setting(tool_id, _STROKE_KEYS[tool_id])))
    for tool_id, spin in getattr(dialog, "_annotation_opacities", {}).items():
        spin.setValue(float(config.get_setting(tool_id, "opacity")))
    if hasattr(dialog, "annotation_mosaic_style_combo"):
        index = dialog.annotation_mosaic_style_combo.findData(
            str(config.get_setting("mosaic", "style", "pixelate")))
        if index >= 0:
            dialog.annotation_mosaic_style_combo.setCurrentIndex(index)
    if hasattr(dialog, "annotation_mosaic_brush_combo"):
        index = dialog.annotation_mosaic_brush_combo.findData(
            int(config.get_setting("mosaic", "block_size", 10) or 10))
        if index >= 0:
            dialog.annotation_mosaic_brush_combo.setCurrentIndex(index)
    if hasattr(dialog, "annotation_text_font_spin"):
        dialog.annotation_text_font_spin.setValue(
            int(config.get_setting("text", "font_size", 14)))


def reset_annotation_page(dialog) -> None:
    """把这一页露面的工具复位成出厂样式。

    按工具整体复位（``reset_tool``）：这一页只露出颜色/粗细/透明度，而「恢复默认样式」
    对用户意味着「这个工具回到默认」，把没露面的键一起复位才对得上这句话。
    """
    config = dialog.config_manager
    for tool_id, _title, _with_opacity in _TOOLS:
        config.reset_tool(tool_id)
    config.reset_tool("mosaic")
    config.reset_tool("text")
    refresh_annotation_page(dialog)


def apply_annotation_settings(dialog) -> None:
    """把页面上的样式写回工具设置（保存时调用）。"""
    config = dialog.config_manager
    for tool_id, keys in collect_annotation_settings(dialog).items():
        config.update_settings(tool_id, **keys)
