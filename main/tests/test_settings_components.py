# -*- coding: utf-8 -*-
"""
设置页共享主题工具测试（ui/settings_ui/components.py）

components.py 被六个设置页共用，其中一组 theme_* 函数负责按当前明暗主题
产出颜色和样式串。它们是纯函数——唯一的外部依赖是 get_ui_theme()——
但此前没有任何覆盖：主题切换后颜色取错、样式串少一个分号导致整段 CSS 失效，
这类问题只能靠肉眼看界面才能发现。

隔离方式：这些函数在模块顶部 import get_ui_theme，因此打桩目标是
ui.settings_ui.components.get_ui_theme 本身，不必启动 QApplication，
也不必真的切换应用主题。
"""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QTextEdit

from ui.settings_ui import components


@pytest.fixture
def theme(monkeypatch):
    """返回一个可现场改写 is_dark / tokens 的假主题"""
    fake = SimpleNamespace(
        is_dark=False,
        tokens=SimpleNamespace(text="#111111", text_muted="#888888"),
    )
    monkeypatch.setattr(components, "get_ui_theme", lambda: fake)
    return fake


# components 里「某处底色」→ 它必须取自的 token 名。
#
# 这些地方原先按 light/dark 二选一写死颜色，用户在外观页改了窗口底色之后，
# 设置窗口内容区仍是原来的浅色、文字却已经跟 token 变了 —— 浅底浅字，读不出来。
# 所以这里钉的是「来源是 token」，而不是某一组具体色值。
DERIVED_COLOR_TOKENS = {
    "theme_surface_color": "window",
    "theme_sidebar_color": "surface_subtle",
    "theme_border_color": "border",
    "theme_input_background": "input_background",
    "theme_popup_background": "popup_background",
    "theme_popup_hover_background": "popup_hover",
}

#: 改之前写死的值（回归时用来确认真的换成 token 了）
RETIRED_HARDCODED = {
    "theme_surface_color": "rgba(239, 244, 250, 0.91)",
    "theme_sidebar_color": "rgba(246, 249, 252, 0.54)",
    "theme_border_color": "rgba(255, 255, 255, 0.76)",
    "theme_input_background": "rgba(255, 255, 255, 0.78)",
    "theme_popup_background": "#FFFFFF",
    "theme_popup_hover_background": "#EAF2FA",
}


class TestThemeColor:

    def test_light_theme_takes_the_first_value(self, theme):
        theme.is_dark = False
        assert components.theme_color("light-value", "dark-value") == "light-value"

    def test_dark_theme_takes_the_second_value(self, theme):
        theme.is_dark = True
        assert components.theme_color("light-value", "dark-value") == "dark-value"

    def test_selection_follows_the_theme_on_every_call(self, theme):
        """主题是运行期可切换的，函数不能缓存首次读到的值"""
        theme.is_dark = False
        assert components.theme_color("a", "b") == "a"
        theme.is_dark = True
        assert components.theme_color("a", "b") == "b"
        theme.is_dark = False
        assert components.theme_color("a", "b") == "a"


class TestDerivedColors:

    def test_each_helper_comes_from_its_token(self, theme):
        for name, token in DERIVED_COLOR_TOKENS.items():
            theme.tokens = SimpleNamespace(**{token: f"<{token}>"})
            assert getattr(components, name)() == f"<{token}>", name

    def test_light_and_dark_tokens_differ_per_surface(self, theme):
        from core.ui_theme import DARK_TOKENS, LIGHT_TOKENS

        for token in DERIVED_COLOR_TOKENS.values():
            assert getattr(LIGHT_TOKENS, token) != getattr(DARK_TOKENS, token), token

    def test_a_skin_override_reaches_every_surface(self, monkeypatch):
        """真跑一遍 resolve_skin：换了窗口/文字/强调色，这些函数全都要跟着变。"""
        from core.ui_theme import LIGHT_TOKENS, SkinOverrides, resolve_skin

        skinned = resolve_skin(
            LIGHT_TOKENS,
            SkinOverrides.from_values(window="#203040", text="#F0F0F0", accent="#FF8800"),
        )
        monkeypatch.setattr(
            components, "get_ui_theme",
            lambda: SimpleNamespace(tokens=skinned, is_dark=False))

        for name, token in DERIVED_COLOR_TOKENS.items():
            value = getattr(components, name)()
            assert value == getattr(skinned, token), name
            assert value != RETIRED_HARDCODED[name], f"{name} 还是写死的老颜色"


class TestMenuTextIsReadable:
    """菜单/右键菜单的「选中态底色 + 正文」是写在一起的，皮肤下也要读得清。

    popup_hover 曾经只在强调色被覆盖时才派生，于是「只改窗口背景」会让菜单选中项变成
    内置浅色底 + 新派生的浅色字（实测 1.03:1）。这两条盯住发布的那两个样式函数。
    """

    @staticmethod
    def _apply_dark_window_skin(monkeypatch):
        from core.ui_theme import LIGHT_TOKENS, SkinOverrides, resolve_skin

        skinned = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(window="#203040"))
        assert skinned.text != LIGHT_TOKENS.text, "用例前提：这一覆盖应当换掉正文色"
        return skinned

    def test_settings_menu_style_pairs_text_with_a_readable_highlight(self, monkeypatch, qapp):
        from core.ui_theme import contrast_ratio

        skinned = self._apply_dark_window_skin(monkeypatch)
        monkeypatch.setattr(
            components, "get_ui_theme",
            lambda: SimpleNamespace(tokens=skinned, is_dark=False))

        style = components.theme_menu_style()

        assert f"background-color: {skinned.popup_hover};" in style
        assert f"color: {skinned.text};" in style
        assert contrast_ratio(skinned.text, skinned.popup_hover) >= 4.5

    def test_text_context_menu_pairs_text_with_a_readable_highlight(self, monkeypatch, qapp):
        from core.ui_theme import SkinOverrides, contrast_ratio, get_ui_theme
        from ui.fluent_lite import text_context_menu

        skinned = self._apply_dark_window_skin(monkeypatch)
        # 这个模块通过 fluent_lite.theme.ui_tokens 取色
        monkeypatch.setattr(text_context_menu, "ui_tokens", lambda _widget=None: skinned)

        editor = QTextEdit()          # 这个入口要一个真的编辑器控件
        menu = text_context_menu.create_text_context_menu(editor)
        try:
            style = menu.styleSheet()
            assert f"background-color: {skinned.popup_hover};" in style
            assert contrast_ratio(skinned.text, skinned.popup_hover) >= 4.5
        finally:
            editor.deleteLater()
            menu.deleteLater()
            get_ui_theme().set_skin(SkinOverrides(), persist=False)


class TestThemeTextStyle:

    def test_default_style_uses_the_theme_text_colour(self, theme):
        assert components.theme_text_style() == (
            "font-size: 13px; color: #111111; background: transparent;")

    def test_font_size_is_honoured(self, theme):
        for size in (9, 13, 20):
            assert f"font-size: {size}px;" in components.theme_text_style(font_size=size)

    def test_bold_appends_a_font_weight(self, theme):
        assert components.theme_text_style(bold=True) == (
            "font-size: 13px; color: #111111; background: transparent; font-weight: 600;")

    def test_extra_css_is_appended_after_a_single_space(self, theme):
        assert components.theme_text_style(extra="margin-left: 4px;") == (
            "font-size: 13px; color: #111111; background: transparent; margin-left: 4px;")

    def test_whitespace_only_extra_adds_nothing(self, theme):
        for extra in ("", "   ", "\t\n"):
            assert components.theme_text_style(extra=extra) == components.theme_text_style()

    def test_extra_is_trimmed_so_no_double_space_appears(self, theme):
        style = components.theme_text_style(extra="   padding: 2px;   ")
        assert "transparent; padding: 2px;" in style
        assert "  " not in style

    def test_bold_and_extra_combine_in_a_stable_order(self, theme):
        style = components.theme_text_style(bold=True, extra="padding: 2px;")
        assert style.index("font-weight: 600;") < style.index("padding: 2px;")

    def test_dark_theme_switches_the_text_colour(self, theme):
        theme.tokens = SimpleNamespace(text="#EEEEEE", text_muted="#AAAAAA")
        assert "color: #EEEEEE;" in components.theme_text_style()


class TestThemeCaptionStyle:

    def test_caption_uses_the_muted_colour_and_a_smaller_default_size(self, theme):
        assert components.theme_caption_style() == (
            "font-size: 12px; color: #888888; background: transparent;")

    def test_caption_never_goes_bold(self, theme):
        """说明文字没有 bold 参数，样式串里也不该出现字重"""
        assert "font-weight" not in components.theme_caption_style()
        assert "font-weight" not in components.theme_caption_style(font_size=20)

    def test_extra_css_is_appended(self, theme):
        assert components.theme_caption_style(extra="margin: 0;") == (
            "font-size: 12px; color: #888888; background: transparent; margin: 0;")

    def test_whitespace_only_extra_adds_nothing(self, theme):
        for extra in ("", "  ", "\n"):
            assert components.theme_caption_style(extra=extra) == (
                components.theme_caption_style())

    def test_caption_and_body_text_use_different_colours(self, theme):
        assert components.theme_text_style() != components.theme_caption_style(font_size=13)
