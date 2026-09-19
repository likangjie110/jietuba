# -*- coding: utf-8 -*-
"""保存路径管理器：一份路径列表，每条自带格式与质量，改完能就地预览。

Snow Shot 的保存设置给的是「多个保存路径 + 每条的质量 + 预览」：一次截图同时落到几个
目录（比如「原图存档」和「发给同事的精简版」），预览则回答「这一档质量到底多大」。

预览打的是**真实编码路径**（``SaveService.save_qimage_to_path`` 到临时文件），不是另写
一份估算：估算与实际编码器的行为很容易分叉，而这里要回答的正是「实际写出来多大」。
"""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.i18n import make_tr
from core.image_formats import format_names, preferred_format
from core.logger import T, log_debug, log_warning
from core.save import SaveService
from ui.fluent_lite import ComboBox, PushButton, SpinBox

from core.size_format import human_size

_tr = make_tr("SavePathsDialog")

#: 预览用的样例图尺寸（与常见截图接近，编码耗时可忽略）
PREVIEW_SIZE = (640, 400)


class SavePathsDialog(QWidget):
    """管理「多保存路径」：增删条目、选目录、选格式与质量，并预览编码结果。"""

    def __init__(self, config_manager=None, parent=None, image: QImage | None = None):
        super().__init__(parent)
        self._config = config_manager
        self._sample = image
        self._paths = list(config_manager.get_save_paths()) if config_manager else []
        self.setWindowTitle(_tr("Save Paths"))
        self.setMinimumSize(720, 420)
        self._build_ui()
        self.reload()

    # ── 界面 ──

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([_tr("Folder"), _tr("Format"), _tr("Quality")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._sync_preview)
        outer.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for label, slot in ((_tr("Add"), self.on_add),
                            (_tr("Change Folder"), self.on_change_folder),
                            (_tr("Remove"), self.on_remove),
                            (_tr("Preview"), self.on_preview)):
            button = PushButton(label, self)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.close_button = PushButton(_tr("Save"), self)
        self.close_button.clicked.connect(self.on_save)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)

        self.preview_label = QLabel(_tr("Preview: pick a row and press Preview."), self)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.preview_label.setWordWrap(True)
        outer.addWidget(self.preview_label)

        self.preview_image = QLabel(self)
        self.preview_image.setMinimumHeight(120)
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.preview_image, 1)

    # ── 数据 ──

    def reload(self) -> None:
        """按当前列表重建表格（编辑与撤销都从这里进）。"""
        self.table.setRowCount(len(self._paths))
        for row, entry in enumerate(self._paths):
            self.table.setItem(row, 0, QTableWidgetItem(str(entry.get("path", ""))))

            format_combo = ComboBox(self)
            for name in format_names():
                format_combo.addItem(name, userData=name)
            index = format_combo.findData(
                preferred_format(entry.get("format", "PNG")))
            format_combo.setCurrentIndex(max(0, index))
            format_combo.currentIndexChanged.connect(
                lambda _idx, r=row: self._on_format_changed(r))
            self.table.setCellWidget(row, 1, format_combo)

            quality_spin = SpinBox(self)
            quality_spin.setRange(1, 100)
            quality_spin.setValue(int(entry.get("quality", 85) or 85))
            quality_spin.setEnabled(_is_lossy(entry.get("format", "PNG")))
            quality_spin.valueChanged.connect(
                lambda value, r=row: self._on_quality_changed(r, value))
            self.table.setCellWidget(row, 2, quality_spin)

        self.preview_label.setText(_tr("{count} save paths").format(count=len(self._paths)))
        self._sync_preview()

    def paths(self) -> list:
        """当前的路径列表（未保存的编辑也在里面）。"""
        return [dict(entry) for entry in self._paths]

    def _format_at(self, row: int) -> str:
        widget = self.table.cellWidget(row, 1)
        return str(widget.currentData() or "PNG") if widget is not None else "PNG"

    def _quality_at(self, row: int) -> int:
        widget = self.table.cellWidget(row, 2)
        return int(widget.value()) if widget is not None else 85

    def _on_format_changed(self, row: int) -> None:
        if not 0 <= row < len(self._paths):
            return
        fmt = self._format_at(row)
        self._paths[row]["format"] = fmt
        widget = self.table.cellWidget(row, 2)
        if widget is not None:
            widget.setEnabled(_is_lossy(fmt))

    def _on_quality_changed(self, row: int, value: int) -> None:
        if 0 <= row < len(self._paths):
            self._paths[row]["quality"] = int(value)

    def current_row(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else 0

    # ── 操作 ──

    def on_add(self) -> bool:
        folder = QFileDialog.getExistingDirectory(self, _tr("Choose Folder"), "")
        if not folder:
            return False
        self._paths.append({"path": folder, "format": "PNG", "quality": 85})
        self.reload()
        self.table.selectRow(len(self._paths) - 1)
        return True

    def on_change_folder(self) -> bool:
        row = self.current_row()
        if not 0 <= row < len(self._paths):
            return False
        folder = QFileDialog.getExistingDirectory(
            self, _tr("Choose Folder"), str(self._paths[row].get("path", "")))
        if not folder:
            return False
        self._paths[row]["path"] = folder
        self.reload()
        self.table.selectRow(row)
        return True

    def on_remove(self) -> bool:
        row = self.current_row()
        if not 0 <= row < len(self._paths):
            return False
        self._paths.pop(row)
        self.reload()
        return True

    def on_preview(self) -> bool:
        """按选中那条的格式与质量真编码一次，显示体积与画面。"""
        row = self.current_row()
        if not 0 <= row < len(self._paths):
            return False
        entry = self._paths[row]
        image = self._sample
        if image is None or image.isNull():
            image = _sample_image(*PREVIEW_SIZE)
        fmt = preferred_format(entry.get("format", "PNG"))
        quality = self._quality_at(row)

        directory = tempfile.mkdtemp(prefix="jietuba-preview-")
        target = os.path.join(directory, f"preview.{fmt.lower()}")
        service = SaveService(config_manager=self._config)
        if not service.save_qimage_to_path(image, target, image_format=fmt, quality=quality):
            log_warning(T("预览编码失败: {path}", path=target), "SavePaths")
            self.preview_label.setText(_tr("Preview failed."))
            return False

        size = os.path.getsize(target)
        log_debug(T("保存预览: {format} q={quality} → {size} 字节", format=fmt,
                    quality=quality, size=size), "SavePaths")
        text = _tr("Preview: {format} · quality {quality} · {size}").format(
            format=fmt, quality=quality if _is_lossy(fmt) else "—", size=human_size(size))
        self.preview_label.setText(text + f"   ({image.width()}×{image.height()})")
        if fmt != "PDF":
            pixmap = QPixmap(target)
            if not pixmap.isNull():
                self.preview_image.setPixmap(pixmap.scaled(
                    480, 200, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        return True

    def on_save(self) -> bool:
        """写回配置（保存按钮）。"""
        if self._config is None:
            return False
        self._config.set_save_paths(self._paths)
        log_debug(T("保存路径已更新: {count} 条", count=len(self._paths)), "SavePaths")
        self.close()
        return True

    def _sync_preview(self) -> None:
        row = self.current_row()
        if 0 <= row < len(self._paths):
            entry = self._paths[row]
            self.preview_label.setText(_tr("Preview: {path}").format(
                path=entry.get("path", "")))


def _is_lossy(image_format: str) -> bool:
    """这个格式吃不吃质量参数（PNG/BMP 不吃）。"""
    return str(image_format or "").upper() in ("JPG", "JPEG", "WEBP", "JXL", "AVIF")


def _sample_image(width: int, height: int) -> QImage:
    """没有真实截图时用的样例图：带渐变与文字的彩图，能看出质量差异。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            image.setPixelColor(x, y, QColor((x * 255) // max(1, width),
                                             (y * 255) // max(1, height),
                                             ((x + y) * 255) // max(1, width + height)))
    return image


def open_save_paths_dialog(config_manager=None, parent=None) -> SavePathsDialog:
    """打开保存路径管理器（模态由调用方决定）。"""
    dialog = SavePathsDialog(config_manager=config_manager, parent=parent)
    dialog.show()
    dialog.raise_()
    return dialog
