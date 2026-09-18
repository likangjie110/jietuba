# -*- coding: utf-8 -*-
"""「标注」设置页：各工具的默认样式（颜色/粗细/透明度/马赛克/字号）。

关键约束：这一页和工具栏读写的是**同一份工具设置**（`tools/<工具>/<键>`），所以这里
既要测「读出来的是当前值」，也要测「写回去以后工具栏那边读得到」——如果哪天有人给
这一页另存一份默认值，最后一条用例就会红。
"""
from types import SimpleNamespace

from PySide6.QtCore import QSettings

from settings.tool_settings import ToolSettingsManager
from ui.settings_ui.page_annotation import (
    apply_annotation_settings, collect_annotation_settings, create_annotation_page,
    refresh_annotation_page, reset_annotation_page,
)


def _manager(tmp_path):
    settings = QSettings(str(tmp_path / "annotation.ini"), QSettings.Format.IniFormat)
    return ToolSettingsManager(qsettings=settings)


def _dialog(manager):
    return SimpleNamespace(config_manager=manager, tr=lambda text: text)


def _page(qapp, tmp_path):
    manager = _manager(tmp_path)
    dialog = _dialog(manager)
    return manager, dialog, create_annotation_page(dialog)


class TestReadsCurrentToolSettings:
    def test_defaults_of_every_tool_on_the_page_are_shown(self, qapp, tmp_path):
        manager, dialog, page = _page(qapp, tmp_path)
        try:
            values = collect_annotation_settings(dialog)
            assert set(values) == {"pen", "highlighter", "rect", "ellipse", "arrow",
                                   "mosaic", "text"}
            assert values["pen"]["color"] == "#ff0000"
            assert values["pen"]["stroke_width"] == 12
            assert values["highlighter"]["color"] == "#ffff00"
            assert values["mosaic"]["style"] == "pixelate"
            assert values["text"]["font_size"] == 14
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_values_come_from_the_live_tool_settings(self, qapp, tmp_path):
        """工具栏改过的样式，这一页打开时就该看到（同一份存储）。"""
        manager = _manager(tmp_path)
        manager.update_settings("pen", color="#00ff00", stroke_width=25)
        manager.update_settings("mosaic", style="blur", block_size=3)
        dialog = _dialog(manager)

        page = create_annotation_page(dialog)
        try:
            values = collect_annotation_settings(dialog)
            assert values["pen"]["color"] == "#00ff00"
            assert values["pen"]["stroke_width"] == 25
            assert values["mosaic"]["style"] == "blur"
            assert values["mosaic"]["block_size"] == 3
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_only_the_tools_on_the_page_are_collected(self, qapp, tmp_path):
        _manager_, dialog, page = _page(qapp, tmp_path)
        try:
            values = collect_annotation_settings(dialog)
            # 聚光灯、序号这些不在这一页露面，不该被写回去
            assert "spotlight" not in values
            assert "number" not in values
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestWriteBack:
    def test_apply_writes_every_tool(self, qapp, tmp_path):
        manager, dialog, page = _page(qapp, tmp_path)
        try:
            dialog._annotation_widths["pen"].setValue(30)
            dialog._annotation_opacities["pen"].setValue(0.5)
            dialog.annotation_mosaic_style_combo.setCurrentIndex(
                dialog.annotation_mosaic_style_combo.findData("blur"))
            dialog.annotation_text_font_spin.setValue(24)

            apply_annotation_settings(dialog)

            # 工具栏读的是同一份设置，所以这里改了它就该看到
            assert manager.get_setting("pen", "stroke_width") == 30
            assert manager.get_setting("pen", "opacity") == 0.5
            assert manager.get_setting("mosaic", "style") == "blur"
            assert manager.get_setting("text", "font_size") == 24
        finally:
            page.deleteLater()
            qapp.processEvents()

    def test_color_button_round_trip(self, qapp, tmp_path):
        manager, dialog, page = _page(qapp, tmp_path)
        try:
            _read, write = dialog._annotation_color_readers["rect"]
            write("#123456")

            apply_annotation_settings(dialog)

            assert manager.get_setting("rect", "color") == "#123456"
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestReset:
    def test_reset_restores_factory_styles(self, qapp, tmp_path):
        manager = _manager(tmp_path)
        manager.update_settings("pen", color="#123456", stroke_width=42)
        manager.update_settings("mosaic", style="blur")
        dialog = _dialog(manager)

        page = create_annotation_page(dialog)
        try:
            reset_annotation_page(dialog)

            values = collect_annotation_settings(dialog)
            assert values["pen"]["color"] == "#ff0000"
            assert values["pen"]["stroke_width"] == 12
            assert values["mosaic"]["style"] == "pixelate"
            assert manager.get_setting("pen", "stroke_width") == 12
        finally:
            page.deleteLater()
            qapp.processEvents()


class TestRefresh:
    def test_refresh_rereads_the_store(self, qapp, tmp_path):
        manager, dialog, page = _page(qapp, tmp_path)
        try:
            manager.update_settings("arrow", color="#abcdef", stroke_width=20)

            refresh_annotation_page(dialog)

            values = collect_annotation_settings(dialog)
            assert values["arrow"]["color"] == "#abcdef"
            assert values["arrow"]["stroke_width"] == 20
        finally:
            page.deleteLater()
            qapp.processEvents()
