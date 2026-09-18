# -*- coding: utf-8 -*-
"""文字贴图（pin/pin_text_pin.py）与它的动作入口。

贴图管线是围绕 QImage 的，所以「文字贴图」= 把文字渲染成一张图再去贴；这里测渲染
本身（尺寸策略、空输入、主题色）以及动作从剪贴板取文字的那条路。
"""
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage

from pin import pin_text_pin


@pytest.fixture(autouse=True)
def _needs_qapp(qapp):
    """渲染文字需要 QApplication：没有它 QPainter/QFontMetrics 会直接 abort 掉进程。"""
    return qapp


class TestRenderTextImage:
    def test_empty_text_produces_an_empty_image(self):
        assert pin_text_pin.render_text_image("", font_size=16, max_width=320).isNull()
        assert pin_text_pin.render_text_image("   \n ", font_size=16, max_width=320).isNull()

    def test_short_text_gets_a_tight_box(self):
        """一行放得下就用文字本身的宽度：短句贴出来不该顶着一条空边框。"""
        image = pin_text_pin.render_text_image("短", font_size=16, max_width=400)

        assert not image.isNull()
        assert image.width() < 400

    def test_long_text_wraps_at_the_configured_width(self):
        image = pin_text_pin.render_text_image("这是一段很长的文本，" * 10,
                                               font_size=16, max_width=200)

        assert image.width() == 200      # max_width 指贴图整体宽度上限（含内边距）
        assert image.height() > 2 * pin_text_pin.PADDING

    def test_font_size_is_clamped_to_the_shared_range(self):
        small = pin_text_pin.render_text_image("x", font_size=1, max_width=400)
        big = pin_text_pin.render_text_image("x", font_size=999, max_width=400)

        assert small.height() < big.height()

    def test_width_is_clamped_to_the_shared_range(self):
        text = "很长的文本" * 20
        narrow = pin_text_pin.render_text_image(text, font_size=16, max_width=1)
        wide = pin_text_pin.render_text_image(text, font_size=16, max_width=99999)

        # 下限：给再小的宽度也至少这么宽；上限：一行放得下就用文字宽度，不会超过上限
        assert narrow.width() == pin_text_pin.TEXT_MAX_WIDTH_RANGE[0]
        assert wide.width() <= pin_text_pin.TEXT_MAX_WIDTH_RANGE[1]

    def test_image_is_translucent_so_the_pin_blends(self):
        image = pin_text_pin.render_text_image("x", font_size=16, max_width=400)

        assert image.hasAlphaChannel()

    def test_pixels_differ_from_the_background(self):
        """渲染出来的图里必须有字（不能是一张纯色底）。"""
        image = pin_text_pin.render_text_image("AAAA", font_size=24, max_width=400)
        colors = {image.pixelColor(x, y).name() for x in range(0, image.width(), 3)
                  for y in range(0, image.height(), 3)}

        assert len(colors) > 1

    def test_newlines_are_respected(self):
        one = pin_text_pin.render_text_image("a", font_size=16, max_width=400)
        three = pin_text_pin.render_text_image("a\nb\nc", font_size=16, max_width=400)

        assert three.height() > one.height()


class TestFont:
    def test_font_family_comes_from_the_platform_layer(self):
        font = pin_text_pin.build_font(16)

        assert font.family()
        assert font.pixelSize() == 16

    def test_pixel_size_is_clamped(self):
        assert pin_text_pin.build_font(1).pixelSize() == pin_text_pin.TEXT_FONT_SIZE_RANGE[0]
        assert pin_text_pin.build_font(999).pixelSize() == pin_text_pin.TEXT_FONT_SIZE_RANGE[1]


class _FakeManager:
    def __init__(self):
        self.created = []

    def create_pin(self, *, image, position, config_manager):
        self.created.append((image, position, config_manager))
        return "pin"


@pytest.fixture
def clipboard(monkeypatch):
    """替换「读剪贴板文本」这一步，不碰 Qt 类本体。

    打补丁到 QApplication 上会把 conftest 的拆除也一并拦下来（它要调
    QApplication.instance()），第一版就是这么把整套拆除搞崩的。
    """
    holder = SimpleNamespace(text="")
    monkeypatch.setattr("core.actions.clipboard_text", lambda: holder.text)
    return lambda text: setattr(holder, "text", text)


class TestPinClipboardTextAction:
    def _app(self, tmp_path):
        from settings.tool_settings import ToolSettingsManager
        from PySide6.QtCore import QSettings

        manager = ToolSettingsManager(
            qsettings=QSettings(str(tmp_path / "text.ini"), QSettings.Format.IniFormat)
        )
        return SimpleNamespace(config_manager=manager)

    def test_clipboard_text_becomes_a_pin(self, monkeypatch, qapp, tmp_path, clipboard):
        from core import actions
        from pin.pin_manager import PinManager

        clipboard("要钉住的文字")
        manager = _FakeManager()
        monkeypatch.setattr(PinManager, "instance", classmethod(lambda cls: manager))

        assert actions.run_action("pin_clipboard_text", self._app(tmp_path)) is True

        image, position, config = manager.created[0]
        assert isinstance(image, QImage) and not image.isNull()
        assert config is not None
        assert position is not None

    def test_empty_clipboard_does_nothing(self, monkeypatch, qapp, tmp_path, clipboard,
                                          caplog):
        from core import actions
        from pin.pin_manager import PinManager

        clipboard("   ")
        manager = _FakeManager()
        monkeypatch.setattr(PinManager, "instance", classmethod(lambda cls: manager))

        assert actions.run_action("pin_clipboard_text", self._app(tmp_path)) is False
        assert manager.created == []

    def test_configured_font_size_is_used(self, monkeypatch, qapp, tmp_path, clipboard):
        from core import actions
        from pin.pin_manager import PinManager

        clipboard("字号")
        app = self._app(tmp_path)
        app.config_manager.set_pin_text_font_size(30)
        manager = _FakeManager()
        monkeypatch.setattr(PinManager, "instance", classmethod(lambda cls: manager))

        actions.run_action("pin_clipboard_text", app)

        image = manager.created[0][0]
        tiny = pin_text_pin.render_text_image("字号", font_size=8, max_width=480)
        assert image.height() > tiny.height()

    def test_the_action_is_in_the_registry_for_hotkeys_and_the_tray(self):
        from core import actions

        action = actions.ACTIONS_BY_ID["pin_clipboard_text"]
        assert action.tray is True             # 托盘里默认给一个入口（可在动作页关掉）
        assert action.id in actions.APP_ENTRY_ACTIONS
