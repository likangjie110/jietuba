# -*- coding: utf-8 -*-
"""多保存路径与图像编码质量：配置往返、真编码写盘、界面预览。

质量那两条按**真实编码结果**断言（同一张图 q30 必须明显小于 q95），不比对写死的字节数：
不同 Qt 版本的编码器会变，但「低质量更小」这个关系不会。
"""

import os

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage

from core.save import SaveService
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "savepaths.ini"), QSettings.Format.IniFormat))


def noisy_image(width=400, height=300) -> QImage:
    """带细节的图：纯色图上质量差异量不出来。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            image.setPixelColor(x, y, QColor((x * 7) % 256, (y * 5) % 256, (x * y) % 256))
    return image


# ── 配置 ──────────────────────────────────────────────

class TestSavePathConfig:
    def test_without_a_configured_list_it_falls_back_to_the_single_path(self, config, tmp_path):
        config.set_screenshot_save_path(str(tmp_path / "shots"))
        config.set_screenshot_format("JPG")
        paths = config.get_save_paths()
        assert len(paths) == 1
        assert paths[0]["path"] == str(tmp_path / "shots")
        assert paths[0]["format"] == "JPG"

    def test_the_list_round_trips(self, config, tmp_path):
        config.set_save_paths([
            {"path": str(tmp_path / "a"), "format": "jpg", "quality": 30},
            {"path": str(tmp_path / "b"), "format": "PNG", "quality": 90},
        ])
        paths = config.get_save_paths()
        assert [entry["path"] for entry in paths] == [str(tmp_path / "a"), str(tmp_path / "b")]
        assert paths[0]["format"] == "JPG"
        assert paths[0]["quality"] == 30

    def test_empty_entries_and_bad_values_are_dropped(self, config, tmp_path):
        config.set_save_paths([
            {"path": "  "},
            42,                                     # 既不是目录串也不是条目
            {"path": str(tmp_path / "ok"), "quality": 999, "format": "nonsense"},
        ])
        paths = config.get_save_paths()
        assert len(paths) == 1
        assert paths[0]["quality"] == 100          # 钳到上限
        assert paths[0]["format"] in ("PNG", "JPG", "BMP", "WEBP", "PDF")

    def test_a_plain_string_is_taken_as_a_folder_path(self, config, tmp_path):
        """配置里直接写 ``["/a", "/b"]`` 也要能用（JSON 手写时的自然写法）。"""
        config.set_save_paths([str(tmp_path / "a"), str(tmp_path / "b")])
        assert [entry["path"] for entry in config.get_save_paths()] == [
            str(tmp_path / "a"), str(tmp_path / "b")]

    def test_a_corrupt_stored_value_falls_back_to_the_single_path(self, config, tmp_path):
        config.set_screenshot_save_path(str(tmp_path / "shots"))
        config.set_app_setting("screenshot_save_paths", "{not json")
        paths = config.get_save_paths()
        assert len(paths) == 1 and paths[0]["path"] == str(tmp_path / "shots")


# ── 真编码 ────────────────────────────────────────────

class TestQuality:
    def test_a_lower_quality_writes_a_smaller_jpeg(self, qapp, tmp_path):
        image = noisy_image()
        service = SaveService()
        low = tmp_path / "low.jpg"
        high = tmp_path / "high.jpg"

        assert service.save_qimage_to_path(image, str(low), image_format="JPG", quality=25)
        assert service.save_qimage_to_path(image, str(high), image_format="JPG", quality=95)

        assert os.path.getsize(low) < os.path.getsize(high)

    def test_the_configured_quality_is_used(self, qapp, config, tmp_path):
        config.set_screenshot_quality(20)
        low = tmp_path / "configured-low.jpg"
        assert SaveService(config_manager=config).save_qimage_to_path(
            noisy_image(), str(low), image_format="JPG")

        config.set_screenshot_quality(95)
        high = tmp_path / "configured-high.jpg"
        assert SaveService(config_manager=config).save_qimage_to_path(
            noisy_image(), str(high), image_format="JPG")

        assert os.path.getsize(low) < os.path.getsize(high)

    def test_lossless_formats_ignore_the_quality(self, qapp, tmp_path):
        image = noisy_image(80, 60)
        service = SaveService()
        first = tmp_path / "a.png"
        second = tmp_path / "b.png"
        service.save_qimage_to_path(image, str(first), image_format="PNG", quality=10)
        service.save_qimage_to_path(image, str(second), image_format="PNG", quality=90)
        assert first.read_bytes() == second.read_bytes()


class TestMultiPathSave:
    def test_every_configured_folder_gets_a_file(self, qapp, config, tmp_path):
        config.set_save_paths([
            {"path": str(tmp_path / "first"), "format": "PNG", "quality": 90},
            {"path": str(tmp_path / "second"), "format": "JPG", "quality": 40},
        ])
        written = SaveService(config_manager=config).save_to_configured_paths(
            noisy_image(), prefix="shot")

        assert len(written) == 2
        assert all(os.path.exists(path) for path in written)
        assert written[0].endswith(".png") and written[1].endswith(".jpg")
        assert written[0].startswith(str(tmp_path / "first"))

    def test_paths_come_from_the_config_when_not_passed(self, qapp, config, tmp_path):
        config.set_save_paths([{"path": str(tmp_path / "only"), "format": "PNG",
                                "quality": 85}])
        written = SaveService(config_manager=config).save_to_configured_paths(noisy_image())
        assert len(written) == 1
        assert os.path.dirname(written[0]) == str(tmp_path / "only")

    def test_a_missing_folder_is_reported_without_losing_the_others(self, qapp, config,
                                                                   tmp_path, monkeypatch):
        config.set_save_paths([
            {"path": str(tmp_path / "bad"), "format": "PNG", "quality": 85},
            {"path": str(tmp_path / "good"), "format": "PNG", "quality": 85},
        ])
        service = SaveService(config_manager=config)
        original = service.save_qimage_to_path

        def flaky(image, target, **kwargs):
            if "/bad/" in target:
                return False
            return original(image, target, **kwargs)

        monkeypatch.setattr(service, "save_qimage_to_path", flaky)
        written = service.save_to_configured_paths(noisy_image())
        assert len(written) == 1 and "/good/" in written[0]

    def test_the_settings_page_button_opens_the_manager(self, qapp, config, monkeypatch):
        """设置页那个「管理…」按钮要真的打开保存路径管理器。"""
        from ui.settings_ui.dialog import SettingsDialog
        from ui import save_paths_dialog

        opened = []
        monkeypatch.setattr(save_paths_dialog, "open_save_paths_dialog",
                            lambda *a, **k: opened.append(True) or _StubWindow())

        dialog = SettingsDialog(config_manager=config)
        try:
            assert dialog.save_paths_label.text()          # 卡片与摘要都在
            from ui.settings_ui.page_capture import _open_save_paths

            _open_save_paths(dialog)
            assert opened == [True]
        finally:
            dialog.close()
            dialog.deleteLater()
            qapp.processEvents()


class _StubWindow:
    """只提供 ``destroyed.connect`` 的替身窗口（页面只连这个信号）。"""

    class _Signal:
        def connect(self, *_args):
            pass

    destroyed = _Signal()


# ── 界面预览 ──────────────────────────────────────────

class TestSavePathsDialog:
    @pytest.fixture
    def dialog(self, qapp, config, tmp_path):
        from ui.save_paths_dialog import SavePathsDialog

        config.set_save_paths([
            {"path": str(tmp_path / "a"), "format": "JPG", "quality": 30},
            {"path": str(tmp_path / "b"), "format": "PNG", "quality": 90},
        ])
        window = SavePathsDialog(config_manager=config, image=noisy_image(320, 200))
        yield window
        window.close()
        window.deleteLater()
        qapp.processEvents()

    def test_it_lists_the_configured_paths(self, dialog):
        assert dialog.table.rowCount() == 2
        assert dialog.paths()[0]["format"] == "JPG"
        assert dialog.table.cellWidget(0, 2).isEnabled() is True     # JPG 吃质量
        assert dialog.table.cellWidget(1, 2).isEnabled() is False     # PNG 不吃

    def test_the_preview_reports_a_real_encoded_size(self, dialog):
        assert dialog.on_preview() is True
        text = dialog.preview_label.text()
        assert "JPG" in text and "KB" in text
        assert dialog.preview_image.pixmap() is not None
        assert dialog.preview_image.pixmap().isNull() is False

    def test_the_preview_reflects_the_quality(self, dialog):
        dialog.table.selectRow(0)
        dialog.table.cellWidget(0, 2).setValue(90)
        assert dialog.on_preview() is True
        high = dialog.preview_label.text()
        dialog.table.cellWidget(0, 2).setValue(10)
        assert dialog.on_preview() is True
        low = dialog.preview_label.text()
        assert _size_kb(low) < _size_kb(high)

    def test_saving_writes_the_list_back_to_the_config(self, dialog, config):
        dialog.table.selectRow(1)
        assert dialog.on_remove() is True
        assert dialog.on_save() is True
        assert len(config.get_save_paths()) == 1
        assert config.get_save_paths()[0]["format"] == "JPG"

    def test_format_change_updates_the_quality_switch(self, dialog):
        dialog.table.selectRow(1)
        combo = dialog.table.cellWidget(1, 1)
        combo.setCurrentIndex(combo.findData("JPG"))
        assert dialog.table.cellWidget(1, 2).isEnabled() is True
        assert dialog.paths()[1]["format"] == "JPG"


def _size_kb(text: str) -> float:
    """从预览文案里抠出体积（KB）用于比较；解析不出时直接报错（免得比较变成空转）。"""
    import re

    match = re.search(r"([0-9.]+)\s*(KB|MB|B)\b", text)
    assert match, f"预览文案里没有体积: {text!r}"
    value = float(match.group(1))
    return {"B": value / 1024, "KB": value, "MB": value * 1024}[match.group(2)]
