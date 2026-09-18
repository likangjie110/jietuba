# -*- coding: utf-8 -*-
"""品牌应用图标（svg/品牌.svg + ResourceManager.get_app_icon()）。

图标只有 SVG 一份源：它既是运行时的窗口/托盘图标来源，也是打包 .icns/.ico 的来源
（`main/scripts/make_app_icons.py`）。所以这里守住两件事：SVG 本身能渲染，以及
资源缺失时不会把窗口搞崩。
"""
from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from core.resource_manager import ResourceManager

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAND_SVG = REPO_ROOT / "svg" / "品牌.svg"


class TestBrandSvg:
    def test_the_file_is_there_and_parses(self, qapp):
        assert BRAND_SVG.exists(), "品牌图标 SVG 丢了"
        assert QSvgRenderer(str(BRAND_SVG)).isValid()

    def test_it_renders_at_every_size_we_ship(self, qapp):
        """小尺寸最容易糊/丢形（图标要在 16px 也认得出）。"""
        renderer = QSvgRenderer(str(BRAND_SVG))

        for size in (16, 32, 64, 128, 256, 512):
            image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()

            assert not image.isNull()
            # 至少要有非透明像素，否则等于一张空图
            opaque = sum(
                1 for x in range(0, size, max(1, size // 16))
                for y in range(0, size, max(1, size // 16))
                if image.pixelColor(x, y).alpha() > 0
            )
            assert opaque > 0, f"{size}px 渲染出来是空的"

    def test_the_mark_is_multicolour_not_a_flat_blob(self, qapp):
        """品牌感的底线：徽章底色和白色图案要都在。"""
        renderer = QSvgRenderer(str(BRAND_SVG))
        image = QImage(QSize(256, 256), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()

        pixels = [image.pixelColor(x, y) for x in range(0, 256, 8) for y in range(0, 256, 8)]
        # 白色图案叠在青底上（带了 0.97 透明度），所以按亮度判而不是抠 #ffffff
        assert any(p.lightness() > 200 for p in pixels), "图里没有浅色图案"
        assert any(p.green() > 120 and p.blue() > 120 and p.red() < 160 for p in pixels), \
            "图里没有青色徽章底色"


class TestGetAppIcon:
    def test_returns_the_brand_icon(self, qapp, monkeypatch):
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})

        icon = ResourceManager.get_app_icon()

        assert not icon.isNull()

    def test_falls_back_when_the_brand_file_is_missing(self, qapp, monkeypatch):
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        monkeypatch.setattr(ResourceManager, "get_icon_path",
                            staticmethod(lambda name: "/nowhere/" + name))
        monkeypatch.setattr(ResourceManager, "custom_logo_path", staticmethod(lambda: ""))

        icon = ResourceManager.get_app_icon()

        # 回退链走完仍为空 QIcon：不能抛异常（图标坏了不该让窗口建不出来）
        assert icon.isNull()

    def test_result_is_cached(self, qapp, monkeypatch):
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        calls = []
        original = ResourceManager.get_icon_path
        monkeypatch.setattr(ResourceManager, "get_icon_path",
                            staticmethod(lambda name: calls.append(name) or original(name)))

        ResourceManager.get_app_icon()
        first = len(calls)
        ResourceManager.get_app_icon()

        assert len(calls) == first          # 第二次没有再去找文件


# ── 自定义 logo ────────────────────────────────────────────────

def _write_png(path: Path, color: str = "#FF00FF", size: int = 64) -> Path:
    """落一张纯色 PNG 当用户的「自定义 logo」。"""
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return path


def _has_color(icon, predicate, size: int = 64) -> bool:
    image = icon.pixmap(size, size).toImage()
    return any(
        predicate(image.pixelColor(x, y))
        for x in range(0, image.width(), 4)
        for y in range(0, image.height(), 4)
    )


def _is_magenta(c: QColor) -> bool:
    return c.alpha() > 200 and c.red() > 200 and c.blue() > 200 and c.green() < 80


def _is_brand_cyan(c: QColor) -> bool:
    return c.alpha() > 0 and c.green() > 120 and c.blue() > 120 and c.red() < 160


@pytest.fixture
def logo_setting():
    """把 app/custom_logo_path 指向给定文件；用例结束自动清空（别污染别的用例）。"""
    from settings import get_tool_settings_manager

    manager = get_tool_settings_manager()

    def _set(path: str):
        manager.set_app_setting(ResourceManager.CUSTOM_LOGO_SETTING_KEY, path)

    yield _set
    manager.set_app_setting(ResourceManager.CUSTOM_LOGO_SETTING_KEY, "")


class TestCustomLogo:
    def test_path_is_empty_by_default(self, qapp, logo_setting):
        assert ResourceManager.custom_logo_path() == ""
        assert ResourceManager.custom_logo_icon() is None

    def test_path_comes_from_the_setting(self, qapp, tmp_path, logo_setting):
        png = _write_png(tmp_path / "logo.png")
        logo_setting(str(png))

        assert ResourceManager.custom_logo_path() == str(png)

    def test_missing_file_is_treated_as_unset(self, qapp, logo_setting):
        logo_setting(str(Path("/nowhere/logo.png")))

        assert ResourceManager.custom_logo_path() == ""
        assert ResourceManager.get_app_icon().isNull() is False

    def test_valid_image_replaces_the_brand_icon(self, qapp, tmp_path, logo_setting, monkeypatch):
        png = _write_png(tmp_path / "logo.png", "#FF00FF")
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        logo_setting(str(png))

        icon = ResourceManager.get_app_icon()

        assert _has_color(icon, _is_magenta), "窗口图标没有用上自定义 logo"
        assert not _has_color(icon, _is_brand_cyan), "还用着内置品牌图标"

    def test_unreadable_file_falls_back_to_the_brand_icon(self, qapp, tmp_path, logo_setting,
                                                         monkeypatch):
        fake = tmp_path / "logo.png"
        fake.write_text("这不是图片", encoding="utf-8")
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        logo_setting(str(fake))

        icon = ResourceManager.get_app_icon()

        assert not icon.isNull()
        assert _has_color(icon, _is_brand_cyan), "读不出图时应当回退到内置品牌图标"

    def test_removing_the_logo_goes_back_to_the_brand_icon(self, qapp, tmp_path, logo_setting,
                                                           monkeypatch):
        png = _write_png(tmp_path / "logo.png", "#FF00FF")
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        logo_setting(str(png))
        assert _has_color(ResourceManager.get_app_icon(), _is_magenta)

        logo_setting("")
        assert ResourceManager.refresh_app_icons() is False
        assert _has_color(ResourceManager.get_app_icon(), _is_brand_cyan)

    def test_refresh_app_icons_updates_the_application_icon(self, qapp, tmp_path, logo_setting,
                                                            monkeypatch):
        png = _write_png(tmp_path / "logo.png", "#FF00FF")
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        logo_setting(str(png))

        assert ResourceManager.refresh_app_icons() is True

        assert _has_color(qapp.windowIcon(), _is_magenta), "应用图标没跟着自定义 logo 变"

    def test_refresh_skips_windows_that_raise(self, qapp, tmp_path, logo_setting, monkeypatch):
        """某个窗口在重设图标时抛异常不能带塌整个刷新（其余窗口照旧换）。"""
        class _Hostile:
            def isWindow(self):
                return True

            def setWindowIcon(self, _icon):
                raise RuntimeError("窗口已经没了")

        png = _write_png(tmp_path / "logo.png", "#FF00FF")
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        logo_setting(str(png))
        monkeypatch.setattr(qapp, "topLevelWidgets", lambda: [_Hostile()])

        assert ResourceManager.refresh_app_icons() is True


class TestStartupApplicationIcon:
    """启动时也要把 Dock/任务栏那一枚换成自定义 logo（不然用户以为没生效）。"""

    def test_applies_the_custom_logo(self, qapp, tmp_path, logo_setting, monkeypatch):
        import main_app
        from core.platform import window_ops

        calls = []
        monkeypatch.setattr(window_ops, "set_application_icon",
                            lambda path: calls.append(path) or True)
        png = _write_png(tmp_path / "logo.png", "#FF00FF")
        logo_setting(str(png))

        assert main_app.apply_configured_app_icon() is True
        assert calls == [str(png)]

    def test_does_nothing_without_a_logo(self, qapp, monkeypatch):
        import main_app
        from core.platform import window_ops

        calls = []
        monkeypatch.setattr(window_ops, "set_application_icon",
                            lambda path: calls.append(path) or True)

        assert main_app.apply_configured_app_icon() is False
        assert calls == []


class TestTrayIcon:
    """托盘那一枚也应当换成自定义 logo（它由 main_app.create_app_icon 组装）。"""

    def test_tray_icon_uses_the_custom_logo(self, qapp, tmp_path, logo_setting, monkeypatch):
        import main_app

        png = _write_png(tmp_path / "logo.png", "#FF00FF")
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})
        logo_setting(str(png))

        icon = main_app.create_app_icon()

        assert not icon.isNull()
        assert _has_color(icon, _is_magenta), "托盘图标没有用上自定义 logo"

    def test_tray_icon_falls_back_to_the_builtin_one(self, qapp, monkeypatch):
        import main_app

        monkeypatch.setattr(ResourceManager, "_icon_cache", {})

        icon = main_app.create_app_icon()

        assert not icon.isNull()
