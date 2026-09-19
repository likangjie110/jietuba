# -*- coding: utf-8 -*-
"""截图历史窗口：筛选、复制、钉图、另存、删除、清空。

窗口是真的，存储是真的，副作用也是真的（剪贴板内容、贴图窗口、落盘文件）。
只有「另存为」的文件对话框与「清空」的确认框换成替身——那是系统弹窗，不是被测单元。
"""

import os
import time

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from history.store import HistoryStore
from history.window import HistoryWindow, describe_entry
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history")


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "histwin.ini"), QSettings.Format.IniFormat))


@pytest.fixture(autouse=True)
def _isolated_shared_store(tmp_path):
    """共享存储换成临时目录：入史入口（record_screenshot）写的是它，
    跑测试绝不能碰开发机上真实的历史目录。

    teardown 不置空：那会撤销 conftest 的会话级隔离（见那边的用例级守卫）。
    """
    from history import HistoryStore, reset_store

    reset_store(HistoryStore(tmp_path / "shared"))
    yield


@pytest.fixture
def window(qapp, store, config):
    win = HistoryWindow(config_manager=config, store=store)
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def image(width=32, height=24, color="#22AA55") -> QImage:
    picture = QImage(width, height, QImage.Format.Format_RGB32)
    picture.fill(QColor(color))
    return picture


def seed(store, count=3, *, source="region", label="", step=60):
    base = time.time() - count * step
    written = []
    for index in range(count):
        written.append(store.add(image(20 + index, 10 + index), source=source, label=label,
                                 created_at=base + index * step))
    return written


class TestListingAndFiltering:
    def test_lists_entries_newest_first(self, qapp, window, store):
        written = seed(store, 3)
        window.refresh()
        assert window.list_widget.count() == 3
        first = window.list_widget.item(0).data(0x0100)      # Qt.UserRole
        assert first == written[-1].id

    def test_source_filter_narrows_the_list(self, qapp, window, store):
        seed(store, 2, source="window", label="编辑器")
        seed(store, 3, source="monitor")
        window.refresh()
        assert window.list_widget.count() == 5

        index = window.source_combo.findData("window")
        window.source_combo.setCurrentIndex(index)
        assert window.list_widget.count() == 2
        assert window.selected_entry() is None or window.selected_entry().source == "window"

        window.source_combo.setCurrentIndex(window.source_combo.findData("monitor"))
        assert window.list_widget.count() == 3

    def test_date_filter_narrows_the_list(self, qapp, window, store):
        seed(store, 2, step=86400 * 3)                      # 两条三天前
        seed(store, 2, step=60)                             # 两条刚才
        window.refresh()
        assert window.list_widget.count() == 4

        window.date_combo.setCurrentIndex(window.date_combo.findData(1))     # 最近 24 小时
        assert window.list_widget.count() == 2

    def test_the_summary_line_carries_time_source_and_size(self, store):
        entry = store.add(image(120, 90), source="window", label="编辑器")._replace() \
            if False else store.add(image(120, 90), source="window", label="编辑器")
        text = describe_entry(entry)
        assert "2026-" in text or time.strftime("%Y-") in text
        assert "120×90" in text
        assert "编辑器" in text


class TestActions:
    def _select_first(self, window):
        """选中列表里的第一条（= 最新的一条）。"""
        window.list_widget.setCurrentRow(0)
        entry = window.selected_entry()
        assert entry is not None
        return entry

    def test_copy_puts_the_image_on_the_clipboard(self, qapp, window, store):
        store.add(image(48, 32, "#FF0000"))
        window.refresh()
        self._select_first(window)

        assert window.on_copy() is True
        clipboard_image = QApplication.clipboard().image()
        assert not clipboard_image.isNull()
        assert clipboard_image.pixelColor(0, 0).red() > 200
        assert clipboard_image.size().width() == 48

    def test_pin_creates_a_real_pinned_window(self, qapp, window, store):
        from pin.pin_manager import PinManager

        entry = store.add(image(60, 40))
        window.refresh()
        self._select_first(window)
        manager = PinManager.instance()
        before = manager.count()

        try:
            assert window.on_pin() is True
            assert manager.count() == before + 1
            # 钉图窗口里拿到的就是这一条的图（尺寸与历史条目一致）
            pin = manager.get_all_pins()[-1]
            assert pin._orig_size.width() == entry.width
            assert pin._orig_size.height() == entry.height
        finally:
            manager.close_all()
            qapp.processEvents()

    def test_save_as_writes_the_file(self, qapp, window, store, tmp_path, monkeypatch):
        store.add(image(64, 48, "#0000FF"))
        window.refresh()
        self._select_first(window)
        target = tmp_path / "exported.png"

        monkeypatch.setattr("history.window.QFileDialog.getSaveFileName",
                            staticmethod(lambda *a, **k: (str(target), "PNG (*.png)")))

        assert window.on_save_as() is True
        assert target.exists() and target.stat().st_size > 0
        loaded = QImage(str(target))
        assert (loaded.width(), loaded.height()) == (64, 48)

    def test_delete_removes_one_entry_and_its_file(self, qapp, window, store):
        written = seed(store, 2)
        window.refresh()
        selected = self._select_first(window)             # 列表第一条 = 最新的一条

        assert window.on_delete() is True
        assert len(store.entries()) == 1
        assert not any(entry.id == selected.id for entry in store.entries())
        assert not os.path.exists(selected.path)
        survivor = [e for e in written if e.id != selected.id][0]
        assert os.path.exists(survivor.path)

    def test_clear_asks_first_and_wipes_everything(self, qapp, window, store, monkeypatch):
        seed(store, 3)
        window.refresh()
        monkeypatch.setattr("ui.dialogs.show_confirm_dialog",
                            lambda *args, **kwargs: True)

        assert window.on_clear() is True
        assert store.entries() == []
        assert window.list_widget.count() == 0

    def test_clear_does_nothing_when_the_user_declines(self, qapp, window, store, monkeypatch):
        seed(store, 2)
        window.refresh()
        monkeypatch.setattr("ui.dialogs.show_confirm_dialog",
                            lambda *args, **kwargs: False)

        assert window.on_clear() is False
        assert len(store.entries()) == 2

    def test_actions_are_disabled_without_a_selection(self, qapp, window, store):
        window.refresh()
        assert window.copy_btn.isEnabled() is False
        assert window.on_copy() is False and window.on_pin() is False

        seed(store, 1)
        window.refresh()
        assert window.copy_btn.isEnabled() is False      # 还没选中任何一行
        window.list_widget.setCurrentRow(0)
        assert window.copy_btn.isEnabled() is True


class TestOpenWindowEntry:
    def test_open_history_window_reuses_one_instance(self, qapp, config, tmp_path, monkeypatch):
        from history import recorder
        from history.window import open_history_window

        monkeypatch.setattr(recorder, "_store", HistoryStore(tmp_path / "hist"))
        first = open_history_window(config_manager=config)
        second = open_history_window(config_manager=config)
        try:
            assert first is second
        finally:
            first.close()
            first.deleteLater()
            qapp.processEvents()
            QApplication.instance()._history_window = None


class TestLiveRefresh:
    def test_a_new_capture_shows_up_in_an_open_window(self, qapp, window, store, config,
                                                      monkeypatch):
        """窗口开着的时候新截的图要立刻出现在列表里（真命令行会切走焦点，列表不能自己变旧）。"""
        from history import recorder

        monkeypatch.setattr(recorder, "_store", store)
        assert window.list_widget.count() == 0

        # 走真正的入史入口（订阅在这里生效）
        entry = recorder.record_screenshot(image(24, 18), None, config, screens=[])
        assert entry is not None
        assert window.list_widget.count() == 1

    def test_a_closed_window_stops_subscribing(self, qapp, config):
        """关掉的窗口不该因为订阅被留住（弱引用 + 退订）。"""
        from history import get_store, recorder

        opened = HistoryWindow(config_manager=config)          # 用共享存储
        closed = HistoryWindow(config_manager=config)
        try:
            closed.close()
            closed.deleteLater()
            qapp.processEvents()

            recorder.record_screenshot(image(10, 10), None, config, screens=[])

            assert len(get_store().entries()) == 1
            assert opened.list_widget.count() == 1
        finally:
            opened.close()
            opened.deleteLater()
            qapp.processEvents()
