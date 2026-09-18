# -*- coding: utf-8 -*-
"""外观页的皮肤与自定义 logo 控件（`ui/settings_ui/page_appearance.py` + dialog 接线）。

判据是「用户选了就真的生效」：色块要跟着变、未保存的覆盖要能读出来、保存后配置与
主题管理器都要更新、自定义 logo 要真的换掉应用图标。这里驱动的是真实页面与真实的
对话框方法（`create_appearance_page` 建真控件，`_refresh_skin_buttons` /
`_save_appearance_settings` 用真实实现），不是复制一份逻辑来断言。
"""

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget

from core.resource_manager import ResourceManager
from core.ui_theme import SkinOverrides, get_ui_theme
from ui.settings_ui import page_appearance
from ui.settings_ui.dialog import SettingsDialog
from ui.settings_ui.page_appearance import _update_color_btn


class _FakeDialog(page_appearance.QWidget):
    """真实外观页需要的最小宿主：一个能当父窗口的 QWidget + tr() + 配置管理器。

    被驱动的对话框方法直接取真实实现挂在类上（它们只用 tr / config_manager / 自己的
    控件），这样测的是发布的那份代码，而不是往测试里抄一遍逻辑。
    """

    _reset_appearance_page = SettingsDialog._reset_appearance_page
    _snapshot_settings = SettingsDialog._snapshot_settings
    _save_appearance_settings = SettingsDialog._save_appearance_settings
    _apply_taskbar_icon = SettingsDialog._apply_taskbar_icon

    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager

    def tr(self, text, *_args, **_kwargs):
        return text


class _StubColorDialog:
    """替掉 QColorDialog：固定返回一个颜色，用来驱动「选色」这条真实路径。"""

    chosen = QColor("#FF00FF")

    def __init__(self, *_args, **_kwargs):
        self.title = ""

    def setWindowTitle(self, title):        # noqa: N802
        self.title = title

    def setWindowFlag(self, *_args):        # noqa: N802
        pass

    def exec(self):
        return 1

    def selectedColor(self):                # noqa: N802
        return type(self).chosen


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    return get_tool_settings_manager()


@pytest.fixture
def dialog(qapp, config):
    from settings import get_tool_settings_manager

    host = _FakeDialog(get_tool_settings_manager())
    yield host
    host.close()
    host.deleteLater()
    # 页面构造时读过真实主题；用例改过皮肤的话恢复回去，别影响别的用例
    get_ui_theme().set_skin(SkinOverrides(), persist=False)


@pytest.fixture(autouse=True)
def _restore_dock_icon(qapp):
    """`_apply_taskbar_icon` 会真的去改 Dock 图标（macOS），用例结束还原。"""
    import sys

    if sys.platform != "darwin":
        yield
        return

    from AppKit import NSApplication

    app = NSApplication.sharedApplication()
    original = app.applicationIconImage()
    yield
    app.setApplicationIconImage_(original)


class TestSkinReachesTheSettingsWindow:
    """皮肤必须落到设置窗口自己的表面上，且窗口里的文字要读得清。

    这条针对的是一类真实缺陷：设置窗口的底色原先按 light/dark 写死，而文字走
    tokens.text —— 用户在外观页把窗口底色改成深色后，浅底上写浅字，肉眼读不出来。
    所以这里既断言「表面色来自 token」，也**渲染真实对话框取样**算一次对比度。
    """

    #: 改之前写死的两个表面色（回归时用来确认真的换成 token 了）
    RETIRED = ("rgba(239, 244, 250, 0.91)", "rgba(246, 249, 252, 0.54)")

    @pytest.fixture
    def skinned_dialog(self, qapp, config):
        from core.ui_theme import SkinOverrides, get_ui_theme

        get_ui_theme().set_skin(SkinOverrides.from_values(
            window="#203040", text="#F0F0F0", accent="#FF8800"))
        dialog = SettingsDialog(config)
        dialog.resize(900, 760)
        dialog.show()
        qapp.processEvents()
        dialog._on_nav_changed(6, "appearance")
        qapp.processEvents()
        yield dialog
        dialog._skip_unsaved_close_prompt = True
        dialog.close()
        dialog.deleteLater()
        get_ui_theme().set_skin(SkinOverrides(), persist=False)

    def test_the_window_surfaces_come_from_tokens(self, skinned_dialog):
        from core.ui_theme import get_ui_theme

        tokens = get_ui_theme().tokens
        sheet = skinned_dialog.styleSheet()

        assert tokens.window in sheet, "主内容面没有用皮肤后的窗口底色"
        assert tokens.surface_subtle in sheet, "左侧导航区没有跟皮肤走"
        for retired in self.RETIRED:
            assert retired not in sheet, f"还留着写死的表面色 {retired}"

    def test_the_input_style_follows_the_skin(self, skinned_dialog):
        from core.ui_theme import get_ui_theme

        tokens = get_ui_theme().tokens
        style = skinned_dialog._get_input_style()

        assert f"color: {tokens.text};" in style
        assert tokens.input_background in style

    def test_rendered_title_text_is_readable_on_its_background(self, skinned_dialog):
        """渲染真实对话框，量标题文字的像素与它实际所在的底色，算对比度。"""
        from core.ui_theme import contrast_ratio, get_ui_theme

        image = _render(skinned_dialog)
        title = skinned_dialog.content_title
        region = _widget_region(image, skinned_dialog, title)

        background = _dominant_color(region)
        text_color = _text_color_of(title) or get_ui_theme().tokens.text
        ratio = contrast_ratio(text_color, background)
        assert ratio >= 4.5, (text_color, background, round(ratio, 2))

    def test_rendered_body_text_is_readable_on_the_cards(self, skinned_dialog):
        """卡片里的正文与说明文字同样量一次（只取当前页、可见的那些控件）。"""
        from core.ui_theme import contrast_ratio, get_ui_theme

        page = skinned_dialog.content_stack.currentWidget()
        labels = [child for child in page.findChildren(QLabel)
                  if child.text().strip() and child.isVisible() and child.width() > 30
                  and child.height() > 10]
        assert labels, "当前页里没有可见的文字控件"

        image = _render(skinned_dialog)
        checked = 0
        for label in labels:
            region = _widget_region(image, skinned_dialog, label)
            if not region:
                continue
            ratio = contrast_ratio(
                _text_color_of(label) or get_ui_theme().tokens.text,
                _dominant_color(region))
            assert ratio >= 4.5, (label.text()[:40], round(ratio, 2))
            checked += 1
        assert checked >= 3, f"只量到 {checked} 块文字"

    def test_color_swatches_survive_a_theme_change(self, skinned_dialog, qapp):
        """色块在主题/皮肤变化后仍是色块。

        fluent_lite 的 PushButton 收到 theme_changed 会重刷自己的默认样式，直接
        setStyleSheet 上色的按钮因此会被冲成普通按钮（实测保存皮肤后三个色块变成空框）。
        """
        from core.ui_theme import SkinOverrides, get_ui_theme

        accent_swatch = skinned_dialog._skin_buttons["accent"]
        swatch_qss = accent_swatch.styleSheet()
        assert "rgb(255, 136, 0)" in swatch_qss

        # 换一组皮肤（会触发 theme_changed）后，色块还得是原来的颜色
        get_ui_theme().set_skin(SkinOverrides.from_values(
            window="#101418", text="#F0F0F0", accent="#FF8800"))
        qapp.processEvents()

        after = accent_swatch.styleSheet()
        assert "rgb(255, 136, 0)" in after, f"色块被主题刷新冲掉了: {after[:120]}"
        image = _render(skinned_dialog)
        origin = accent_swatch.mapTo(skinned_dialog, accent_swatch.rect().topLeft())
        pixel = image.pixelColor(origin.x() + accent_swatch.width() // 2,
                                 origin.y() + accent_swatch.height() // 2).name()
        assert pixel == "#ff8800", pixel

    def test_theme_colour_and_mask_swatches_are_colours_not_plain_buttons(self, skinned_dialog,
                                                                          qapp):
        """同一类缺陷的另外两个按钮（主题色/遮罩色）也走色块通道。"""
        from core.theme import get_theme
        from core.ui_theme import SkinOverrides, get_ui_theme

        theme = get_theme()
        _update_color_btn(skinned_dialog._theme_color_btn, theme.theme_color)
        _update_color_btn(skinned_dialog._mask_color_btn, theme.mask_color)

        theme_color_rgb = f"rgb({theme.theme_color.red()}, {theme.theme_color.green()}, " \
                          f"{theme.theme_color.blue()})"
        assert theme_color_rgb in skinned_dialog._theme_color_btn.styleSheet()
        assert "min-height" not in skinned_dialog._theme_color_btn.styleSheet()

        get_ui_theme().set_skin(SkinOverrides.from_values(window="#101418"))
        qapp.processEvents()

        assert theme_color_rgb in skinned_dialog._theme_color_btn.styleSheet(), \
            "主题色按钮被主题刷新冲成了普通按钮"

    def test_the_input_style_pairs_text_with_a_readable_hover_surface(self, skinned_dialog):
        """下拉悬浮/选中态是「底色 + 正文」写在一起的，量一下这对组合的对比度。

        回归：popup_hover 原先不跟皮肤派生，深色皮肤下这里会生成
        `background-color: #EAF2FA; color: #F2F4F6;`（1.03:1，选中项文字看不见）。
        """
        import re

        from core.ui_theme import contrast_ratio, get_ui_theme

        style = skinned_dialog._get_input_style()
        rule = re.search(
            r"QComboBox QAbstractItemView::item:hover\s*\{([^}]*)\}", style)
        assert rule is not None, "没找到下拉悬浮态那条规则"
        body = rule.group(1)
        background = re.search(r"background-color:\s*([^;]+);", body)
        # 注意用后顾断言避开 background-color 里的 "color:"
        color = re.search(r"(?<!background-)color:\s*([^;]+);", body)
        assert background and color, body

        ratio = contrast_ratio(color.group(1).strip(), background.group(1).strip())
        assert ratio >= 4.5, (color.group(1), background.group(1), round(ratio, 2))
        # 而且用的确实是皮肤后的 token，不是内置那一份
        assert background.group(1).strip() == get_ui_theme().tokens.popup_hover

    def test_the_window_background_pixel_matches_the_skin(self, skinned_dialog):
        """主内容面确实刷上了皮肤色（按像素而不是只按样式串）。"""
        from core.ui_theme import get_ui_theme

        tokens = get_ui_theme().tokens
        image = _render(skinned_dialog)
        area = skinned_dialog.findChild(QWidget, "SettingsRightArea")
        assert area is not None
        origin = area.mapTo(skinned_dialog, area.rect().topLeft())
        # 取主内容面左缘的空白带（避开圆角、标题栏与卡片），这里应当就是窗口底色
        pixels = {
            image.pixelColor(origin.x() + x, origin.y() + y).name()
            for x in range(2, 9)
            for y in range(area.height() // 4, area.height() * 3 // 4)
        }
        assert tokens.window in pixels, (tokens.window, pixels)


def _render(widget):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return image


def _widget_region(image, root, widget) -> list:
    """把某个子控件的矩形映射到整窗渲染图里，取其中的像素列表（去掉边缘一圈）。"""
    top_left = widget.mapTo(root, widget.rect().topLeft())
    colors = []
    for x in range(top_left.x() + 3, top_left.x() + widget.width() - 3):
        for y in range(top_left.y() + 3, top_left.y() + widget.height() - 3):
            if 0 <= x < image.width() and 0 <= y < image.height():
                colors.append(image.pixelColor(x, y))
    return colors


def _dominant_color(colors) -> str:
    """区域里出现次数最多的颜色 —— 文字只占少数像素，多数派就是它所在的底色。"""
    assert colors, "取样区域是空的"
    counts = {}
    for color in colors:
        name = color.name()
        counts[name] = counts.get(name, 0) + 1
    return max(counts, key=lambda name: counts[name])


def _text_color_of(widget) -> str:
    """从控件自己的样式表里取文字色（没写就返回空串）。"""
    import re

    match = re.search(r"color:\s*(#[0-9a-fA-F]{6})", widget.styleSheet() or "")
    return match.group(1) if match else ""


def _rgb(color: str) -> str:
    """色块按钮的样式表里写的是 rgb(r, g, b)，比对要用同一种写法。"""
    c = QColor(color)
    return f"rgb({c.red()}, {c.green()}, {c.blue()})"


def _png(tmp_path, color="#FF00FF"):
    from PySide6.QtGui import QImage

    path = tmp_path / "logo.png"
    image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    assert image.save(str(path), "PNG")
    return path


def _build_page(dialog):
    """建出真实外观页并抓住它。

    create_appearance_page 返回的 QScrollArea 没有父窗口，丢掉引用会被 GC 连同里面的
    子控件一起删掉（后续断言会报「C++ object already deleted」），所以这里挂在宿主上。
    """
    dialog._page = page_appearance.create_appearance_page(dialog)
    return dialog._page


class TestSkinControls:
    def test_page_builds_three_color_buttons(self, dialog):
        _build_page(dialog)

        assert set(dialog._skin_buttons) == {"window", "text", "accent"}
        assert dialog._skin_overrides == SkinOverrides()
        # 初始色块显示内置主题的当前色（不是空白）
        tokens = get_ui_theme().tokens
        for field, expected in (("window", tokens.window), ("text", tokens.text),
                                ("accent", tokens.accent)):
            assert _rgb(expected) in dialog._skin_buttons[field].styleSheet(), field

    def test_picking_a_color_stores_the_override_and_repaints(self, dialog, monkeypatch):
        _build_page(dialog)
        monkeypatch.setattr(page_appearance, "QColorDialog", _StubColorDialog)

        page_appearance._pick_skin_color(dialog, "accent")

        assert dialog._skin_overrides.accent == "#ff00ff"
        stylesheet = dialog._skin_buttons["accent"].styleSheet().lower()
        assert "rgb(255, 0, 255)" in stylesheet, "色块没有跟着重新上色"
        assert "2px solid" in stylesheet, "自定义过的那一项没有标出来"
        # 其它两项仍是「跟随主题」
        assert dialog._skin_overrides.window is None

    def test_follow_theme_clears_every_override(self, dialog, monkeypatch):
        _build_page(dialog)
        monkeypatch.setattr(page_appearance, "QColorDialog", _StubColorDialog)
        page_appearance._pick_skin_color(dialog, "window")
        assert dialog._skin_overrides.window == "#ff00ff"

        dialog._skin_reset_btn.click()

        assert dialog._skin_overrides == SkinOverrides()
        tokens = get_ui_theme().tokens
        assert _rgb(tokens.window) in dialog._skin_buttons["window"].styleSheet()


class TestLogoControls:
    def test_choosing_an_image_shows_its_name(self, dialog, tmp_path):
        _build_page(dialog)
        assert dialog._custom_logo_path == ""
        assert dialog._logo_hint.text() == "Built-in icon"

        png = _png(tmp_path)
        page_appearance._set_custom_logo(dialog, str(png))

        assert dialog._custom_logo_path == str(png)
        assert dialog._logo_hint.text() == "logo.png"
        assert not dialog._logo_preview.pixmap().isNull()

    def test_clearing_goes_back_to_the_builtin_icon(self, dialog, tmp_path):
        _build_page(dialog)
        page_appearance._set_custom_logo(dialog, str(_png(tmp_path)))

        page_appearance._set_custom_logo(dialog, "")

        assert dialog._custom_logo_path == ""
        assert dialog._logo_hint.text() == "Built-in icon"
        assert not dialog._logo_preview.pixmap().isNull()

    def test_unreadable_image_says_so(self, dialog, tmp_path):
        _build_page(dialog)
        bad = tmp_path / "logo.png"
        bad.write_text("这不是图片", encoding="utf-8")

        page_appearance._set_custom_logo(dialog, str(bad))

        assert dialog._logo_hint.text() == "Cannot read this image, using the built-in icon"
        assert not dialog._logo_preview.pixmap().isNull()


class TestDialogWiring:
    def test_reset_page_clears_skin_and_logo(self, dialog, tmp_path, monkeypatch):
        _build_page(dialog)
        monkeypatch.setattr(page_appearance, "QColorDialog", _StubColorDialog)
        page_appearance._pick_skin_color(dialog, "accent")
        page_appearance._set_custom_logo(dialog, str(_png(tmp_path)))

        SettingsDialog._reset_appearance_page(dialog)

        assert dialog._skin_overrides == SkinOverrides()
        assert dialog._custom_logo_path == ""
        assert dialog._logo_hint.text() == "Built-in icon"

    def test_snapshot_tracks_skin_and_logo(self, dialog, tmp_path):
        _build_page(dialog)
        before = SettingsDialog._snapshot_settings(dialog)

        page_appearance._set_custom_logo(dialog, str(_png(tmp_path)))
        dialog._skin_overrides = SkinOverrides.from_values(accent="#123456")
        after = SettingsDialog._snapshot_settings(dialog)

        assert before != after, "改了皮肤/logo 却没被当成未保存变更"
        assert after["skin"] == ("#123456", None, None)
        assert after["custom_logo_path"].endswith("logo.png")

    def test_save_persists_applies_and_refreshes_icons(self, dialog, tmp_path, monkeypatch,
                                                      config):
        import main_app

        _build_page(dialog)
        png = _png(tmp_path)
        dialog._custom_logo_path = str(png)
        dialog._skin_overrides = SkinOverrides.from_values(accent="#FF8800", window="#101418")

        refreshed = []
        monkeypatch.setattr(
            main_app, "main_app_instance",
            lambda: type("App", (), {"refresh_app_icon": lambda self: refreshed.append(True)})(),
        )
        monkeypatch.setattr(ResourceManager, "_icon_cache", {})

        SettingsDialog._save_appearance_settings(dialog)

        # 1) 落配置（重启后仍生效靠的就是这一步）
        assert config.get_app_setting("custom_logo_path") == str(png)
        assert config.get_app_setting("skin_accent_color") == "#ff8800"
        assert config.get_app_setting("skin_window_color") == "#101418"
        # 2) 立即生效：当前主题管理器已经用上新皮肤
        assert get_ui_theme().tokens.accent == "#ff8800"
        assert get_ui_theme().tokens.window == "#101418"
        # 3) 图标立即换（窗口/应用图标 + 托盘各一次）
        assert refreshed == [True], "托盘图标没有刷新"
        assert ResourceManager.custom_logo_path() == str(png)

    def test_save_without_a_logo_clears_the_setting(self, dialog, monkeypatch, config):
        import main_app

        _build_page(dialog)
        config.set_app_setting("custom_logo_path", "/somewhere/old.png")
        dialog._custom_logo_path = ""

        monkeypatch.setattr(
            main_app, "main_app_instance",
            lambda: type("App", (), {"refresh_app_icon": lambda self: None})(),
        )

        SettingsDialog._save_appearance_settings(dialog)

        assert config.get_app_setting("custom_logo_path") == ""
