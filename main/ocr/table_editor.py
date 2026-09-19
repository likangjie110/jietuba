# -*- coding: utf-8 -*-
"""可编辑表格窗口：识别结果 → 表格 → 改 → 复制 Markdown/HTML。

界面是「左侧表格编辑器 + 右侧预览」：左边改，右边的 Markdown/HTML 立刻跟着变——预览
不是另算一遍，而是从同一个 ``TableDocument`` 导出（见 ocr/table_document.py），所以
两边不可能对不上。

编辑动作只做「用户真的要的那几件」：合并（多选取并）、拆分、复制、粘贴、清空、加行加列。
每一步都经 ``QUndoStack``，撤销即回退到操作前的文档快照。
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.i18n import make_tr
from core.logger import T, log_debug, log_exception
from ui.fluent_lite import PushButton, TextEdit

from .table_document import TableDocument

_tr = make_tr("TableEditor")


class _DocumentCommand(QUndoCommand):
    """一步编辑：保存操作前后的文档快照（表格很小，整份拷贝比逐格 diff 简单可靠）。"""

    def __init__(self, editor, before: TableDocument, after: TableDocument, text: str):
        super().__init__(text)
        self._editor = editor
        self._before = before
        self._after = after

    def undo(self):
        self._editor.apply_document(self._before)

    def redo(self):
        self._editor.apply_document(self._after)


class TableEditor(QWidget):
    """表格编辑器窗口（识别结果确认后打开）。"""

    def __init__(self, document: TableDocument | None = None, title: str = "", parent=None):
        super().__init__(parent)
        self.document = document or TableDocument()
        self.undo_stack = QUndoStack(self)
        self.setWindowTitle(title or _tr("Table Editor"))
        self.setMinimumSize(860, 560)
        self._build_ui()
        self.reload()

    # ── 界面 ──

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        buttons = (
            (_tr("Merge cells"), self.on_merge),
            (_tr("Split cell"), self.on_split),
            (_tr("Copy"), self.on_copy),
            (_tr("Paste"), self.on_paste),
            (_tr("Clear cells"), self.on_clear),
            (_tr("Undo"), self.undo_stack.undo),
            (_tr("Redo"), self.undo_stack.redo),
        )
        self.buttons = []
        for label, slot in buttons:
            button = PushButton(label, self)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
            self.buttons.append(button)
        toolbar.addStretch(1)
        self.copy_markdown_button = PushButton(_tr("Copy Markdown"), self)
        self.copy_markdown_button.clicked.connect(lambda: self.copy_format("markdown"))
        self.copy_html_button = PushButton(_tr("Copy HTML"), self)
        self.copy_html_button.clicked.connect(lambda: self.copy_format("html"))
        toolbar.addWidget(self.copy_markdown_button)
        toolbar.addWidget(self.copy_html_button)
        outer.addLayout(toolbar)

        splitter = QHBoxLayout()
        self.table = QTableWidget(self)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self.table, 3)

        self.preview_tabs = QTabWidget(self)
        # 用共享控件而不是裸 QTextEdit：右键菜单/主题跟着 fluent_lite 走
        # （main/tests/test_fluent_lite.py 的护栏也要求这样）
        self.markdown_view = TextEdit(self)
        self.markdown_view.setReadOnly(True)
        self.html_view = TextEdit(self)
        self.html_view.setReadOnly(True)
        self.preview_tabs.addTab(self.markdown_view, _tr("Markdown"))
        self.preview_tabs.addTab(self.html_view, _tr("HTML"))
        splitter.addWidget(self.preview_tabs, 2)
        outer.addLayout(splitter, 1)

        self.hint = TextEdit(self)
        self.hint.setReadOnly(True)
        self.hint.setMaximumHeight(48)
        self.hint.setPlainText(_tr("Tip: select a block of cells and use Merge cells; "
                                   "split restores them."))
        outer.addWidget(self.hint)

    # ── 与文档同步 ──

    def reload(self) -> None:
        """按文档重建表格与预览（加载与撤销都走这里）。"""
        document = self.document
        self.table.blockSignals(True)
        self.table.clear()
        self.table.setRowCount(document.rows)
        self.table.setColumnCount(document.cols)
        self.table.setHorizontalHeaderLabels(
            [str(index + 1) for index in range(document.cols)])
        self.table.setVerticalHeaderLabels([str(index + 1) for index in range(document.rows)])
        for (row, col), text in document.cells.items():
            if document.in_bounds(row, col):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.blockSignals(False)
        self._refresh_preview()

    def apply_document(self, document: TableDocument) -> None:
        """把整份文档换上去（撤销/重做的落地）。"""
        self.document = document
        self.reload()

    def _refresh_preview(self) -> None:
        self.markdown_view.setPlainText(self.document.to_markdown())
        self.html_view.setPlainText(self.document.to_html())

    def _push(self, before: TableDocument, after: TableDocument, text: str) -> None:
        self.document = after
        self.undo_stack.push(_DocumentCommand(self, before, after, text))
        self.reload()

    def _on_item_changed(self, item) -> None:
        if item is None:
            return
        before = self.document.clone()
        after = self.document.clone()
        after.set(item.row(), item.column(), item.text())
        self.document = after
        self.undo_stack.push(_DocumentCommand(self, before, after, _tr("Edit cell")))
        self._refresh_preview()

    # ── 选区 ──

    def selected_rect(self) -> tuple:
        """当前选区 ``(row1, col1, row2, col2)``；没有选中时用第 0 格。"""
        ranges = self.table.selectedRanges()
        if not ranges:
            return 0, 0, 0, 0
        area = ranges[0]
        return (area.topRow(), area.leftColumn(), area.bottomRow(), area.rightColumn())

    # ── 操作 ──

    def on_merge(self) -> bool:
        row1, col1, row2, col2 = self.selected_rect()
        before = self.document.clone()
        after = self.document.clone()
        if not after.merge_cells(row1, col1, row2, col2):
            return False
        self._push(before, after, _tr("Merge cells"))
        # 合并后把光标落在锚点上：紧接着「拆分」要能拆到刚合并的那一格
        self.table.setCurrentCell(row1, col1)
        return True

    def on_split(self) -> bool:
        row, col = self.table.currentRow(), self.table.currentColumn()
        # 当前格不是合并格时，退回用选区的左上角：用户刚刚框选合并的时候，
        # currentCell 常常还停在别处，那一步「拆分」不该什么都不做
        if not self.document.is_merged(max(0, row), max(0, col)):
            row1, col1, _row2, _col2 = self.selected_rect()
            row, col = row1, col1
        before = self.document.clone()
        after = self.document.clone()
        if not after.split_cell(max(0, row), max(0, col)):
            return False
        self._push(before, after, _tr("Split cell"))
        return True

    def on_copy(self) -> bool:
        row1, col1, row2, col2 = self.selected_rect()
        _grid, text = self.document.copy_region(row1, col1, row2, col2)
        if not text:
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        log_debug(T("已复制表格选区: {rows} 行", rows=row2 - row1 + 1), "TableEditor")
        return True

    def on_paste(self) -> bool:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        grid = _parse_tsv(clipboard.text())
        if not grid:
            return False
        row, col = self.table.currentRow(), self.table.currentColumn()
        before = self.document.clone()
        after = self.document.clone()
        if not after.paste_cells(grid, max(0, row), max(0, col)):
            self._warn(_tr("Pasted cells do not fit inside the table."))
            return False
        self._push(before, after, _tr("Paste cells"))
        return True

    def on_clear(self) -> bool:
        row1, col1, row2, col2 = self.selected_rect()
        before = self.document.clone()
        after = self.document.clone()
        if after.clear_region(row1, col1, row2, col2) == 0:
            return False
        self._push(before, after, _tr("Clear cells"))
        return True

    def copy_format(self, target: str) -> bool:
        """复制整表的 Markdown / HTML。"""
        text = self.document.to_markdown() if target == "markdown" else self.document.to_html()
        clipboard = QGuiApplication.clipboard()
        if not text or clipboard is None:
            return False
        clipboard.setText(text)
        log_debug(T("已复制表格导出: {target}", target=target), "TableEditor")
        return True

    def _warn(self, message: str) -> None:
        """越界等拒绝原因直接写在提示条上（不弹模态框打断编辑）。"""
        self.hint.setPlainText(message)

    # ── 入口 ──

    def from_ocr_result(self, result) -> bool:
        """用一次识别结果重建表格（真机上由动作调用）。"""
        try:
            document = TableDocument.from_ocr_result(result)
        except Exception as e:
            log_exception(e, T("从识别结果建表"))
            return False
        if document.rows == 0 or document.cols == 0:
            return False
        self.undo_stack.clear()
        self.document = document
        self.reload()
        return True


def _parse_tsv(text: str) -> list:
    """把剪贴板文本按 TSV 拆成二维（制表符优先，其次逗号？不：只认制表符与换行）。"""
    if not text or not text.strip():
        return []
    rows = [line.split("\t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    return rows


def open_table_editor(document: TableDocument | None = None, *,
                      config_manager=None) -> TableEditor:
    """打开（或复用）表格编辑器窗口。"""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    window = getattr(application, "_table_editor", None)
    if window is None:
        window = TableEditor(document)
        if application is not None:
            application._table_editor = window
            window.destroyed.connect(lambda: setattr(application, "_table_editor", None))
    elif document is not None:
        window.document = document
        window.reload()
    window.show()
    window.raise_()
    window.activateWindow()
    return window
