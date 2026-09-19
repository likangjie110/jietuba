# -*- coding: utf-8 -*-
"""第二批标注工具的设置面板（直线 / 水印 / 滤镜 / 智能擦除）。

一个面板装四组控件，按当前工具显示对应那组——比给每个工具各写一个面板省得多，而这点
控件量（每组 2~5 个）也没有到「必须拆开」的程度。改动直接写进工具设置（``tools/<id>/<键>``），
工具在按下的那一刻读一次，所以这里不需要额外的「应用」按钮。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.i18n import make_tr
from core.logger import T, log_debug, log_exception
from ui.fluent_lite import ComboBox, LineEdit, PushButton, SpinBox

_tr = make_tr("AnnotationSettingsPanel")

#: 面板支持的四个工具（顺序即界面顺序）
PANEL_TOOLS = ("line", "watermark", "filter", "smart_erase")

LINE_STYLES = (("solid", "Solid"), ("dashed", "Dashed"), ("dashed_dense", "Dense dashes"))
FILTER_KINDS = (("grayscale", "Grayscale"), ("invert", "Invert"),
                ("blur", "Gaussian blur"), ("emboss", "Emboss"))


class StyleTemplateControls(QWidget):
    """样式模板行：存当前工具的一套参数、套用到新元素、删除。

    模板内容就是工具设置里的那些键（见 ``canvas/annotation_templates.py``），所以套用之后
    再画出来的元素自然带上模板参数——不需要为模板另立一份参数模型，也不需要动元素本身。
    """

    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tool_id = "line"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.name_input = LineEdit(self)
        self.name_input.setPlaceholderText(_tr("Template name"))
        self.name_input.setFixedWidth(110)
        layout.addWidget(self.name_input)

        self.save_button = PushButton(_tr("Save template"), self)
        self.save_button.clicked.connect(self.save_current)
        layout.addWidget(self.save_button)

        self.template_combo = ComboBox(self)
        self.template_combo.setFixedWidth(120)
        layout.addWidget(self.template_combo)

        self.apply_button = PushButton(_tr("Apply"), self)
        self.apply_button.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_button)

        self.delete_button = PushButton(_tr("Delete"), self)
        self.delete_button.clicked.connect(self.delete_selected)
        layout.addWidget(self.delete_button)

        self.refresh()

    # ── 状态 ──

    def set_tool(self, tool_id: str) -> None:
        self._tool_id = tool_id

    @staticmethod
    def _manager():
        from settings import get_tool_settings_manager

        return get_tool_settings_manager()

    def refresh(self) -> None:
        """重填模板下拉（保留当前选中项）。"""
        from canvas.annotation_templates import load_templates

        current = self.template_combo.currentText()
        self.template_combo.clear()
        names = sorted(load_templates(self._manager()))
        for name in names:
            self.template_combo.addItem(name, name)
        if current:
            index = self.template_combo.findText(current)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
        self.apply_button.setEnabled(bool(names))
        self.delete_button.setEnabled(bool(names))
        self.name_input.setPlaceholderText(_tr("Template name"))

    # ── 操作 ──

    def save_current(self) -> bool:
        """把当前工具的设置存成一个模板。"""
        from canvas.annotation_templates import capture_from_manager, save_template

        name = self.name_input.text()
        values = capture_from_manager(self._manager(), self._tool_id)
        if not values:
            return False
        templates = save_template(self._manager(), name, self._tool_id, values)
        if not templates:
            return False
        self.name_input.clear()
        self.refresh()
        index = self.template_combo.findText(name.strip())
        if index >= 0:
            self.template_combo.setCurrentIndex(index)
        self.settings_changed.emit()
        return True

    def apply_selected(self) -> bool:
        """把选中的模板写回工具设置：之后画的元素就带这套参数。"""
        from canvas.annotation_templates import apply_to_manager, load_templates

        name = self.template_combo.currentData()
        templates = load_templates(self._manager())
        values = (templates.get(name) or {}).get(self._tool_id) or {}
        if not values:
            return False
        return apply_to_manager(self._manager(), self._tool_id, values) > 0

    def delete_selected(self) -> bool:
        from canvas.annotation_templates import delete_template

        name = self.template_combo.currentData()
        if not name:
            return False
        delete_template(self._manager(), name)
        self.refresh()
        return True


class AnnotationSettingsPanel(QWidget):
    """直线/水印/滤镜/智能擦除共用的设置面板。"""

    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tool_id = "line"
        self._loading = False
        self._build_ui()

    # ── 界面 ──

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        # 直线：线型
        self._line_row = self._make_row(_tr("Line style"))
        self.line_style_combo = ComboBox(self)
        for value, label in LINE_STYLES:
            self.line_style_combo.addItem(_tr(label), userData=value)
        self.line_style_combo.currentIndexChanged.connect(self._on_line_style_changed)
        self._line_row.layout().addWidget(self.line_style_combo)
        outer.addWidget(self._line_row)

        # 水印：文字 / 字号 / 角度 / 间距 / 不透明度
        self._watermark_text_row = self._make_row(_tr("Watermark text"))
        self.watermark_text_input = LineEdit(self)
        self.watermark_text_input.setFixedWidth(140)
        self.watermark_text_input.editingFinished.connect(self._on_watermark_text_changed)
        self._watermark_text_row.layout().addWidget(self.watermark_text_input)
        outer.addWidget(self._watermark_text_row)

        self._watermark_size_row, self.watermark_size_spin = self._make_spin_row(
            _tr("Font size"), 8, 96, self._on_watermark_size_changed)
        outer.addWidget(self._watermark_size_row)

        self._watermark_angle_row, self.watermark_angle_spin = self._make_spin_row(
            _tr("Angle"), -90, 90, self._on_watermark_angle_changed)
        outer.addWidget(self._watermark_angle_row)

        self._watermark_gap_row, self.watermark_gap_spin = self._make_spin_row(
            _tr("Spacing"), 10, 600, self._on_watermark_gap_changed)
        outer.addWidget(self._watermark_gap_row)

        self._watermark_opacity_row, self.watermark_opacity_spin = self._make_spin_row(
            _tr("Opacity %"), 5, 100, self._on_watermark_opacity_changed)
        outer.addWidget(self._watermark_opacity_row)

        # 滤镜：种类 / 半径 / 强度
        self._filter_kind_row = self._make_row(_tr("Filter"))
        self.filter_kind_combo = ComboBox(self)
        for value, label in FILTER_KINDS:
            self.filter_kind_combo.addItem(_tr(label), userData=value)
        self.filter_kind_combo.currentIndexChanged.connect(self._on_filter_kind_changed)
        self._filter_kind_row.layout().addWidget(self.filter_kind_combo)
        outer.addWidget(self._filter_kind_row)

        self._filter_radius_row, self.filter_radius_spin = self._make_spin_row(
            _tr("Blur radius"), 1, 60, self._on_filter_radius_changed)
        outer.addWidget(self._filter_radius_row)

        self._filter_strength_row, self.filter_strength_spin = self._make_spin_row(
            _tr("Emboss strength"), 1, 40, self._on_filter_strength_changed)
        outer.addWidget(self._filter_strength_row)

        # 智能擦除：笔刷宽度
        self._erase_width_row, self.erase_width_spin = self._make_spin_row(
            _tr("Brush size"), 4, 200, self._on_erase_width_changed)
        outer.addWidget(self._erase_width_row)

        # 样式模板：四个工具共用一行（内容按当前工具取/存）
        self._template_row = self._make_row(_tr("Style template"))
        self.template_controls = StyleTemplateControls(self._template_row)
        self._template_row.layout().addWidget(self.template_controls)
        outer.addWidget(self._template_row)

        self.adjustSize()

    def _make_row(self, title: str) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel(_tr(title), row)
        label.setMinimumWidth(96)
        layout.addWidget(label)
        return row

    def _make_spin_row(self, title: str, low: int, high: int, handler):
        row = self._make_row(title)
        spin = SpinBox(row)
        spin.setRange(low, high)
        spin.setFixedWidth(90)
        spin.valueChanged.connect(handler)
        row.layout().addWidget(spin)
        return row, spin

    # ── 工具切换与回填 ──

    def set_tool(self, tool_id: str) -> None:
        """切到某个工具：只显示它那几行，并从设置里回填。"""
        self._tool_id = tool_id if tool_id in PANEL_TOOLS else "line"
        self._loading = True
        try:
            self._apply_visibility()
            self.sync_from_settings()
            if hasattr(self, "template_controls"):
                self.template_controls.set_tool(self._tool_id)
                self.template_controls.refresh()
        finally:
            self._loading = False

    def _apply_visibility(self) -> None:
        rows = {
            "line": (self._line_row,),
            "watermark": (self._watermark_text_row, self._watermark_size_row,
                          self._watermark_angle_row, self._watermark_gap_row,
                          self._watermark_opacity_row),
            "filter": (self._filter_kind_row, self._filter_radius_row,
                       self._filter_strength_row),
            "smart_erase": (self._erase_width_row,),
        }
        visible = set(rows.get(self._tool_id, ()))
        for row in (self._line_row, self._watermark_text_row, self._watermark_size_row,
                    self._watermark_angle_row, self._watermark_gap_row,
                    self._watermark_opacity_row, self._filter_kind_row,
                    self._filter_radius_row, self._filter_strength_row,
                    self._erase_width_row):
            row.setVisible(row in visible)
        self.adjustSize()

    def sync_from_settings(self) -> None:
        """按当前工具的持久化设置回填控件。"""
        from types import SimpleNamespace

        from tools.annotation import (ERASE_DEFAULTS, FILTER_DEFAULTS,
                                      WATERMARK_DEFAULTS, read_tool_settings)

        settings = read_tool_settings(SimpleNamespace(settings_manager=self._manager()),
                                      self._tool_id)
        self._loading = True
        try:
            if self._tool_id == "line":
                style = settings.get("line_style", "solid")
                index = self.line_style_combo.findData(style)
                self.line_style_combo.setCurrentIndex(max(0, index))
            elif self._tool_id == "watermark":
                values = {**WATERMARK_DEFAULTS, **settings}
                self.watermark_text_input.setText(str(values["text"]))
                self.watermark_size_spin.setValue(int(values["font_size"]))
                self.watermark_angle_spin.setValue(int(values["angle"]))
                self.watermark_gap_spin.setValue(int(values["gap"]))
                self.watermark_opacity_spin.setValue(int(round(float(values["opacity"]) * 100)))
            elif self._tool_id == "filter":
                values = {**FILTER_DEFAULTS, **settings}
                index = self.filter_kind_combo.findData(str(values["kind"]))
                self.filter_kind_combo.setCurrentIndex(max(0, index))
                self.filter_radius_spin.setValue(int(values["radius"]))
                self.filter_strength_spin.setValue(int(values["strength"]))
            elif self._tool_id == "smart_erase":
                values = {**ERASE_DEFAULTS, **settings}
                self.erase_width_spin.setValue(int(values["brush_width"]))
        except Exception as e:
            log_exception(e, T("回填标注设置面板"))
        finally:
            self._loading = False

    # ── 写设置 ──

    @staticmethod
    def _manager():
        from settings import get_tool_settings_manager

        return get_tool_settings_manager()

    def _store(self, **values) -> None:
        if self._loading:
            return
        manager = self._manager()
        if manager is None:
            return
        try:
            manager.update_settings(self._tool_id, **values)
            log_debug(T("标注参数已更新: {tool_id} {values}", tool_id=self._tool_id,
                        values=values), "AnnotationSettingsPanel")
            self.settings_changed.emit()
        except Exception as e:
            log_exception(e, T("保存标注参数"))

    def _on_line_style_changed(self, _index: int):
        self._store(line_style=self.line_style_combo.currentData())

    def _on_watermark_text_changed(self):
        self._store(text=self.watermark_text_input.text().strip() or "jietuba")

    def _on_watermark_size_changed(self, value: int):
        self._store(font_size=int(value))

    def _on_watermark_angle_changed(self, value: int):
        self._store(angle=float(value))

    def _on_watermark_gap_changed(self, value: int):
        self._store(gap=int(value))

    def _on_watermark_opacity_changed(self, value: int):
        self._store(opacity=max(0.05, float(value) / 100.0))

    def _on_filter_kind_changed(self, _index: int):
        self._store(kind=self.filter_kind_combo.currentData())

    def _on_filter_radius_changed(self, value: int):
        self._store(radius=int(value))

    def _on_filter_strength_changed(self, value: int):
        self._store(strength=float(value))

    def _on_erase_width_changed(self, value: int):
        self._store(brush_width=int(value))


__all__ = ["AnnotationSettingsPanel", "PANEL_TOOLS", "StyleTemplateControls"]
