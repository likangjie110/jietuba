# -*- coding: utf-8 -*-
"""表格文档模型：把 OCR 的文本框拼成一张**可编辑**的表格。

为什么不是一堆字符串：识别结果要么导出 Markdown、要么在界面上改，两者都要求同一份
「行列 + 合并区」的结构。所以这里存的是稀疏网格 ``cells`` 加一份 ``spans``（合并区），
Markdown/HTML 与编辑器都从它派生——不会出现「界面上改了、导出的还是原样」。

坐标契约：
- ``cells`` 是 ``{ (row, col): 文本 }`` 稀疏字典；没键 = 空格子。
- ``spans`` 是 ``{(row, col): (row_span, col_span)}``，只登记**锚点**（左上角）的格子；
  被合并覆盖的格子不在 ``cells`` 里。
- 合并/拆分会同时维护这两个结构，任一操作后 ``to_markdown()``/``to_html()`` 立刻反映。
"""

from __future__ import annotations

from html import escape

from core.logger import T, log_debug, log_warning

#: 单元格文本里的换行在 Markdown 里折成空格（否则会破坏表格）
_CELL_SEPARATOR = " "

#: 行列上限：防御性约束，坏数据不该让界面去建一张几万行的表
MAX_ROWS = 200
MAX_COLS = 60

#: 粘贴时最多接受的单元格数（越界粘贴要被拒绝，见 paste_cells）
MAX_PASTE_CELLS = 4000


class TableDocument:
    """可编辑的表格：稀疏格子 + 合并区。"""

    def __init__(self, rows: int = 0, cols: int = 0):
        self.rows = max(0, min(int(rows), MAX_ROWS))
        self.cols = max(0, min(int(cols), MAX_COLS))
        self.cells: dict = {}
        self.spans: dict = {}

    # ── 构造 ──

    @classmethod
    def from_grid(cls, grid) -> "TableDocument":
        """从二维文本（如几何启发式切出来的行列）建表。"""
        rows = len(grid or [])
        cols = max((len(row) for row in grid or []), default=0)
        document = cls(rows, cols)
        for r, row in enumerate(grid or []):
            for c, text in enumerate(row):
                text = str(text or "").strip()
                if text:
                    document.cells[(r, c)] = text
        return document

    @classmethod
    def from_ocr_result(cls, result) -> "TableDocument":
        """从 OCR 结果（``{code, text_lines:[{text, points}...]}``）建表。

        几何切分复用 ``ocr_manager.format_ocr_result_markdown`` 的那套启发式，
        这里只把它切出来的行列接进模型，保证「复制表格为 Markdown」与编辑器看到的是
        同一张表。
        """
        from ocr.ocr_manager import table_grid_from_ocr_result

        return cls.from_grid(table_grid_from_ocr_result(result))

    # ── 读写 ──

    def get(self, row: int, col: int) -> str:
        return self.cells.get((int(row), int(col)), "")

    def set(self, row: int, col: int, text: str) -> bool:
        """写一个格子；越界返回 False。"""
        row, col = int(row), int(col)
        if not self._in_bounds(row, col):
            return False
        text = str(text or "").strip()
        if text:
            self.cells[(row, col)] = text
        else:
            self.cells.pop((row, col), None)
        return True

    def in_bounds(self, row: int, col: int) -> bool:
        return self._in_bounds(int(row), int(col))

    def _in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.rows and 0 <= col < self.cols

    def region(self, row: int, col: int) -> tuple:
        """``(row, col)`` 所在的合并区（``(top, left, rowspan, colspan)``）；没合并时是 1x1。"""
        key = (int(row), int(col))
        if key in self.spans:
            rowspan, colspan = self.spans[key]
            return key[0], key[1], rowspan, colspan
        for anchor, (rowspan, colspan) in self.spans.items():
            top, left = anchor
            if top <= key[0] < top + rowspan and left <= key[1] < left + colspan:
                return top, left, rowspan, colspan
        return key[0], key[1], 1, 1

    # ── 合并 / 拆分 ──

    def merge_cells(self, row1: int, col1: int, row2: int, col2: int) -> bool:
        """合并矩形区域；只有左上角格子的文本保留（其余文本丢了要能看出来，所以记日志）。"""
        top, bottom = sorted((int(row1), int(row2)))
        left, right = sorted((int(col1), int(col2)))
        if not self._in_bounds(top, left) or not self._in_bounds(bottom, right):
            return False
        if (top, left) == (bottom, right):
            return False
        if (top, left) in self.spans:
            log_debug(T("该区域左上角已经是合并区，先拆分再合并"), "TableDocument")

        # 先拆掉区域内已有的合并，避免嵌套合并且产生对不上的 spans
        for anchor in [anchor for anchor in self.spans if self._covers(anchor, top, left, bottom, right)]:
            self._split(anchor)

        kept = self.cells.get((top, left), "")
        dropped = 0
        for r in range(top, bottom + 1):
            for c in range(left, right + 1):
                if (r, c) == (top, left):
                    continue
                if self.cells.pop((r, c), None) is not None:
                    dropped += 1
        self.spans[(top, left)] = (bottom - top + 1, right - left + 1)
        if kept:
            self.cells[(top, left)] = kept
        if dropped:
            log_warning(T("合并单元格时丢弃了 {count} 个格子的文本", count=dropped), "TableDocument")
        log_debug(T("合并单元格: ({row},{col}) {rows}x{cols}", row=top, col=left,
                    rows=bottom - top + 1, cols=right - left + 1), "TableDocument")
        return True

    def _covers(self, anchor, top, left, bottom, right) -> bool:
        rowspan, colspan = self.spans[anchor]
        return (top <= anchor[0] <= bottom and left <= anchor[1] <= right) or (
            anchor[0] <= top < anchor[0] + rowspan and anchor[1] <= left < anchor[1] + colspan)

    def split_cell(self, row: int, col: int) -> bool:
        """拆分 ``(row, col)`` 所在的合并区（把文本留在左上角）。"""
        top, left, rowspan, colspan = self.region(row, col)
        if (rowspan, colspan) == (1, 1) and (top, left) not in self.spans:
            return False
        return self._split((top, left))

    def _split(self, anchor) -> bool:
        if anchor not in self.spans:
            return False
        self.spans.pop(anchor, None)
        log_debug(T("拆分单元格: ({row},{col})", row=anchor[0], col=anchor[1]), "TableDocument")
        return True

    def is_merged(self, row: int, col: int) -> bool:
        top, left, rowspan, colspan = self.region(row, col)
        return (rowspan, colspan) != (1, 1)

    # ── 复制 / 粘贴 ──

    def copy_region(self, row1: int, col1: int, row2: int, col2: int) -> list:
        """复制一块矩形，返回 ``(grid, text)``：grid 是二维文本（空串表示空），text 是 TSV。"""
        top, bottom = sorted((int(row1), int(row2)))
        left, right = sorted((int(col1), int(col2)))
        if not self._in_bounds(top, left) or not self._in_bounds(bottom, right):
            return [], ""
        grid = []
        for r in range(top, bottom + 1):
            row_values = []
            for c in range(left, right + 1):
                anchor = self.region(r, c)
                if (r, c) != (anchor[0], anchor[1]):
                    row_values.append("")          # 合并区里非锚点的格子是空的
                else:
                    row_values.append(self.get(r, c))
            grid.append(row_values)
        text = "\n".join("\t".join(values) for values in grid)
        return grid, text

    def paste_cells(self, grid, row: int, col: int) -> bool:
        """把一块二维文本贴到 ``(row, col)``；越界或格式不对一律拒绝（返回 False）。

        拒绝而不是截断：截断会静默丢内容，用户以为贴上了其实少了一半。
        """
        if not grid:
            return False
        row, col = int(row), int(col)
        height = len(grid)
        width = max(len(values) for values in grid)
        if height * width > MAX_PASTE_CELLS:
            log_warning(T("粘贴内容过大，已拒绝: {count} 格", count=height * width), "TableDocument")
            return False
        if not self._in_bounds(row, col) or not self._in_bounds(row + height - 1, col + width - 1):
            log_warning(T("粘贴越界，已拒绝: 目标 ({row},{col}) 需要 {h}x{w}",
                          row=row, col=col, h=height, w=width), "TableDocument")
            return False
        if any(self.region(row + r, col + c)[2:4] != (1, 1)
               for r in range(height) for c in range(width)):
            log_warning(T("粘贴区域与合并单元格重叠，已拒绝"), "TableDocument")
            return False

        for r, values in enumerate(grid):
            for c, text in enumerate(values):
                self.set(row + r, col + c, text)
        log_debug(T("粘贴单元格: ({row},{col}) {h}x{w}", row=row, col=col,
                    h=height, w=width), "TableDocument")
        return True

    def clear_region(self, row1: int, col1: int, row2: int, col2: int) -> int:
        """清空一块矩形里的文本（合并区只清锚点）。返回清掉的格子数。"""
        top, bottom = sorted((int(row1), int(row2)))
        left, right = sorted((int(col1), int(col2)))
        cleared = 0
        for r in range(top, bottom + 1):
            for c in range(left, right + 1):
                if self.cells.pop((r, c), None) is not None:
                    cleared += 1
        return cleared

    # ── 导出 ──

    def _plain(self, row: int, col: int) -> str:
        return str(self.get(row, col)).replace("\n", _CELL_SEPARATOR).replace("|", "\\|").strip()

    def to_grid(self) -> list:
        """当前内容导出成二维文本（合并区的非锚点格子为空串）。"""
        grid = [["" for _ in range(self.cols)] for _ in range(self.rows)]
        for (r, c), text in self.cells.items():
            if 0 <= r < self.rows and 0 <= c < self.cols:
                grid[r][c] = text
        return grid

    def to_markdown(self) -> str:
        """导出 Markdown；合并区的锚点写文本，被覆盖的格子留空（GitHub 能渲染）。"""
        if self.rows == 0 or self.cols == 0:
            return ""
        lines = []
        header = "| " + " | ".join(self._plain(0, c) for c in range(self.cols)) + " |"
        lines.append(header)
        lines.append("| " + " | ".join("---" for _ in range(self.cols)) + " |")
        for r in range(1, self.rows):
            lines.append("| " + " | ".join(self._plain(r, c) for c in range(self.cols)) + " |")
        return "\n".join(lines)

    def to_html(self) -> str:
        """导出 HTML 表格；合并区用 rowspan/colspan 表达，被覆盖的格子不输出。"""
        if self.rows == 0 or self.cols == 0:
            return ""
        lines = ["<table>"]
        for r in range(self.rows):
            lines.append("  <tr>")
            c = 0
            while c < self.cols:
                key = (r, c)
                if key in self.spans:
                    rowspan, colspan = self.spans[key]
                    attrs = "".join(
                        f' {name}="{value}"' for name, value in
                        (("rowspan", rowspan if rowspan > 1 else 0),
                         ("colspan", colspan if colspan > 1 else 0)) if value)
                    text = escape(str(self.cells.get(key, "")))
                    tag = "th" if r == 0 else "td"
                    lines.append(f"    <{tag}{attrs}>{text}</{tag}>")
                    c += colspan
                    continue
                covered = any(
                    anchor[0] <= r < anchor[0] + span[0] and anchor[1] <= c < anchor[1] + span[1]
                    for anchor, span in self.spans.items())
                if covered:
                    c += 1
                    continue
                text = escape(str(self.cells.get(key, "")))
                tag = "th" if r == 0 else "td"
                lines.append(f"    <{tag}>{text}</{tag}>")
                c += 1
            lines.append("  </tr>")
        lines.append("</table>")
        return "\n".join(lines)

    def clone(self) -> "TableDocument":
        copy = TableDocument(self.rows, self.cols)
        copy.cells = dict(self.cells)
        copy.spans = dict(self.spans)
        return copy
