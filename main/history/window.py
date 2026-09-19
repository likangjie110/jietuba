# -*- coding: utf-8 -*-
"""截图历史窗口：按来源/日期筛选，对任一条再复制、钉图、另存。

界面走的是应用既有的两样东西：``ui.fluent_lite`` 的控件与 ``core.ui_theme`` 的配色 token，
所以换主题和皮肤时这个窗口跟着变。列表用 ``QListWidget``（图标 + 一行摘要）：条目数在
千级，虚拟化不是瓶颈，而「一屏能扫完、点一下就选中」比什么都重要。
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget,
)

from core.i18n import make_tr
from core.logger import T, log_debug, log_exception
from ui.fluent_lite import ComboBox, PushButton

from . import recorder
from .store import SOURCES, HistoryEntry

_tr = make_tr("HistoryWindow")

#: 缩略图尺寸（列表图标）
THUMBNAIL_SIZE = QSize(120, 72)


def _source_label(source: str) -> str:
    """来源的界面文案（与 tr 的上下文一致）。"""
    return {
        "window": _tr("Window"),
        "element": _tr("Window element"),
        "monitor": _tr("Monitor"),
        "region": _tr("Selection"),
    }.get(source, source)


def describe_entry(entry: HistoryEntry) -> str:
    """列表里一行摘要：时间 · 来源 · 标签 · 尺寸 · 体积。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.created_at))
    parts = [stamp, _source_label(entry.source)]
    if entry.label:
        parts.append(entry.label)
    parts.append(f"{entry.width}×{entry.height}")
    if entry.size_bytes:
        parts.append(f"{entry.size_bytes / 1024:.0f} KB")
    return "  ·  ".join(parts)


class HistoryWindow(QWidget):
    """截图历史浏览窗口（单例由调用方持有，见 ``open_history_window``）。"""

    def __init__(self, config_manager=None, store=None, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._store = store or recorder.get_store()
        self.setWindowTitle(_tr("Screenshot History"))
        self.setMinimumSize(760, 520)
        self._build_ui()
        # 窗口开着的时候新截的图要立刻出现在列表里（订阅用弱引用，关掉就自动退出）
        recorder.subscribe(self._on_entry_recorded)
        self.destroyed.connect(lambda *_args: recorder.unsubscribe(self._on_entry_recorded))
        self.refresh()

    def _on_entry_recorded(self, _entry) -> None:
        """新条目到达：刷新列表（订阅回调，可能来自任意保存路径）。"""
        self.refresh()

    # ── 界面 ──

    def _build_ui(self):
        from core.resource_manager import ResourceManager

        icon = ResourceManager.get_app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # 筛选条
        filters = QHBoxLayout()
        filters.setSpacing(8)

        self.source_combo = ComboBox(self)
        self.source_combo.setFixedWidth(160)
        self.source_combo.addItem(_tr("All sources"), userData="")
        for source in SOURCES:
            self.source_combo.addItem(_source_label(source), userData=source)
        self.source_combo.currentIndexChanged.connect(lambda _index: self.refresh())
        filters.addWidget(QLabel(_tr("Source"), self))
        filters.addWidget(self.source_combo)

        self.date_combo = ComboBox(self)
        self.date_combo.setFixedWidth(160)
        for value, label in ((0, _tr("Any time")), (1, _tr("Last 24 hours")),
                             (7, _tr("Last 7 days")), (30, _tr("Last 30 days"))):
            self.date_combo.addItem(label, userData=value)
        self.date_combo.currentIndexChanged.connect(lambda _index: self.refresh())
        filters.addWidget(QLabel(_tr("Date"), self))
        filters.addWidget(self.date_combo)

        refresh_btn = PushButton(_tr("Refresh"), self)
        refresh_btn.clicked.connect(self.refresh)
        filters.addWidget(refresh_btn)
        filters.addStretch(1)

        self.count_label = QLabel("", self)
        filters.addWidget(self.count_label)
        outer.addLayout(filters)

        # 列表
        self.list_widget = QListWidget(self)
        self.list_widget.setIconSize(THUMBNAIL_SIZE)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.currentItemChanged.connect(lambda *_args: self._sync_buttons())
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.on_pin())
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.list_widget, 1)

        # 操作按钮
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_btn = PushButton(_tr("Copy"), self)
        self.copy_btn.clicked.connect(self.on_copy)
        self.pin_btn = PushButton(_tr("Pin to Screen"), self)
        self.pin_btn.clicked.connect(self.on_pin)
        self.view_btn = PushButton(_tr("View"), self)
        self.view_btn.clicked.connect(self.on_view)
        self.save_btn = PushButton(_tr("Save as"), self)
        self.save_btn.clicked.connect(self.on_save_as)
        self.delete_btn = PushButton(_tr("Delete"), self)
        self.delete_btn.clicked.connect(self.on_delete)
        self.clear_btn = PushButton(_tr("Clear History"), self)
        self.clear_btn.clicked.connect(self.on_clear)
        for button in (self.view_btn, self.copy_btn, self.pin_btn, self.save_btn,
                       self.delete_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.clear_btn)
        outer.addLayout(actions)

        self.empty_label = QLabel(_tr("No screenshots yet."), self)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.empty_label)
        self._sync_buttons()

    # ── 数据 ──

    def entries(self) -> list:
        """当前筛选条件下的条目。"""
        days = int(self.date_combo.currentData() or 0)
        start = time.time() - days * 86400 if days else 0.0
        return self._store.filtered(source=self.source_combo.currentData() or "", start=start)

    def refresh(self) -> None:
        """重读筛选条件并重建列表（保留当前选中项）。"""
        selected = self.selected_entry()
        self.list_widget.clear()
        entries = self.entries()
        for entry in entries:
            item = QListWidgetItem(self._thumbnail(entry), describe_entry(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            if entry.label:
                item.setToolTip(entry.label)
            self.list_widget.addItem(item)
        if selected is not None:
            self._select_by_id(selected.id)
        self.count_label.setText(_tr("{count} entries").format(count=len(entries)))
        self.empty_label.setVisible(not entries)
        self.list_widget.setVisible(bool(entries))
        self._sync_buttons()

    def _thumbnail(self, entry: HistoryEntry) -> QIcon:
        image = self._store.image(entry.id)
        if image is None:
            return QIcon()
        return QIcon(QPixmap.fromImage(image).scaled(
            THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def _select_by_id(self, entry_id: str) -> None:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == entry_id:
                self.list_widget.setCurrentItem(item)
                return

    def selected_entry(self) -> HistoryEntry | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return self._store.get(item.data(Qt.ItemDataRole.UserRole))

    def _sync_buttons(self) -> None:
        has_selection = self.selected_entry() is not None
        for button in (self.view_btn, self.copy_btn, self.pin_btn, self.save_btn,
                       self.delete_btn):
            button.setEnabled(has_selection)
        self.clear_btn.setEnabled(bool(self._store.entries()))

    # ── 操作 ──

    def on_view(self) -> bool:
        """在独立查看器里打开这一条（大图/需要放大看细节时用）。"""
        entry = self.selected_entry()
        if entry is None:
            return False
        image = self._store.image(entry.id)
        if image is None:
            return False
        from ui.image_viewer import open_image_viewer

        viewer = open_image_viewer(image=image, config_manager=self._config)
        if viewer is None:
            return False
        log_debug(T("历史截图已打开查看器: {entry_id}", entry_id=entry.id), "History")
        return True

    def on_copy(self) -> bool:
        """复制到剪贴板（走平台层的写图：Windows 上是 DIBV5 + PNG 格式）。"""
        entry = self.selected_entry()
        if entry is None:
            return False
        image = self._store.image(entry.id)
        if image is None:
            log_exception(RuntimeError("image missing"), T("复制历史截图"))
            return False
        from core.platform import clipboard as platform_clipboard

        ok = bool(platform_clipboard.copy_image(image))
        log_debug(T("历史截图已复制: {entry_id}", entry_id=entry.id), "History")
        return ok

    def on_pin(self) -> bool:
        """把这一条钉回屏幕（钉在鼠标位置，与截图里的钉图行为一致）。"""
        entry = self.selected_entry()
        if entry is None:
            return False
        image = self._store.image(entry.id)
        if image is None:
            return False
        from PySide6.QtGui import QCursor

        from pin.pin_manager import PinManager

        pin = PinManager.instance().create_pin(
            image=image, position=QCursor.pos(), config_manager=self._config)
        if pin is None:
            return False
        log_debug(T("历史截图已钉图: {entry_id}", entry_id=entry.id), "History")
        return True

    def on_save_as(self) -> bool:
        """另存为（默认文件名带时间与来源，扩展名跟随所选过滤器）。"""
        entry = self.selected_entry()
        if entry is None:
            return False
        image = self._store.image(entry.id)
        if image is None:
            return False

        from core.image_formats import save_dialog_filter
        from core.save import SaveService

        suffix = os.path.splitext(entry.path)[1] or ".png"
        default_name = time.strftime("screenshot_%Y%m%d_%H%M%S", time.localtime(entry.created_at))
        # 过滤器与其它保存入口共用一份派生清单（见 core/image_formats.py）
        path, selected_filter = QFileDialog.getSaveFileName(
            self, _tr("Save Screenshot"), f"{default_name}{suffix}",
            save_dialog_filter(),
        )
        if not path:
            return False
        image_format = _format_from_path(path, selected_filter)
        if not os.path.splitext(path)[1]:
            path = f"{path}.{image_format.lower()}"
        saved = SaveService(config_manager=self._config).save_qimage_to_path(
            image, path, image_format=image_format)
        if saved:
            log_debug(T("历史截图已另存: {path}", path=path), "History")
        return bool(saved)

    def on_delete(self) -> bool:
        entry = self.selected_entry()
        if entry is None:
            return False
        removed = self._store.remove(entry.id)
        self.refresh()
        return removed

    def on_clear(self) -> bool:
        """清空历史（先问一次：这一步不可撤销）。"""
        from ui.dialogs import show_confirm_dialog

        if not self._store.entries():
            return False
        if not show_confirm_dialog(self, _tr("Clear History"),
                                   _tr("Delete every saved screenshot? This cannot be undone.")):
            return False
        self._store.clear()
        self.refresh()
        return True


def _format_from_path(path: str, selected_filter: str) -> str:
    """按扩展名定格式，扩展名缺失时退回过滤器里写的那个（与截图保存同一套判断）。"""
    extension = os.path.splitext(path)[1].lstrip(".").upper()
    if extension:
        return "JPG" if extension == "JPEG" else extension
    match = selected_filter.split("(")[-1].split(")")[0].strip().lstrip("*.")
    return (match or "png").upper()


def open_history_window(config_manager=None, parent=None) -> HistoryWindow:
    """打开（或复用）历史窗口：挂在 QApplication 上，避免被 GC 掉。"""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    window = getattr(application, "_history_window", None)
    if window is None:
        window = HistoryWindow(config_manager=config_manager, parent=parent)
        if application is not None:
            application._history_window = window
            window.destroyed.connect(
                lambda: setattr(application, "_history_window", None))
    window.refresh()
    window.show()
    window.raise_()
    window.activateWindow()
    return window
