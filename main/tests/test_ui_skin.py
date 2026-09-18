# -*- coding: utf-8 -*-
"""皮肤覆盖层（`core/ui_theme.py` 的 SkinOverrides + resolve_skin）。

皮肤是「内置 token + 用户覆盖」的派生层：用户只设他明确改的那几项，其余 token（悬停色、
卡片底色、边框、次级文字）都从覆盖值按同一套比例派生出来。这里守住三件事：
覆盖真的生效、派生项跟着走、非法值退回内置值。
"""

import pytest
from PySide6.QtGui import QColor, QPalette

from core.ui_theme import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    MIN_TEXT_CONTRAST,
    SKIN_ACCENT_KEY,
    SKIN_TEXT_KEY,
    SKIN_WINDOW_KEY,
    SkinOverrides,
    UIThemeManager,
    contrast_ratio,
    flatten_color,
    normalize_color,
    resolve_skin,
)


class _Config:
    def __init__(self, **values):
        self.values = dict(values)

    def get_app_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_app_setting(self, key, value):
        self.values[key] = value


@pytest.fixture(autouse=True)
def _restore_application_theme(qapp):
    old_palette = qapp.palette()
    old_stylesheet = qapp.styleSheet()
    yield
    qapp.setPalette(old_palette)
    qapp.setStyleSheet(old_stylesheet)


class TestNormalizeColor:
    def test_accepts_hex_and_normalizes_it(self):
        assert normalize_color("#40E0D0") == "#40e0d0"
        assert normalize_color("  40e0d0 ") is None       # 不带 # 的不算颜色
        assert normalize_color("#abc") == "#aabbcc"       # Qt 允许缩写形式

    @pytest.mark.parametrize("value", ["", "   ", None, 123, "not-a-color", "#12345"])
    def test_rejects_junk(self, value):
        assert normalize_color(value) is None


class TestSkinOverrides:
    def test_invalid_values_become_unset(self):
        overrides = SkinOverrides.from_values(accent="nope", window="", text=None)
        assert overrides.accent is None
        assert overrides.window is None
        assert overrides.text is None
        assert overrides.is_empty()

    def test_reads_from_settings_and_writes_back(self):
        config = _Config(**{
            SKIN_ACCENT_KEY: "#FF8800",
            SKIN_WINDOW_KEY: "junk",          # 非法 → 当作没设
            SKIN_TEXT_KEY: "",
        })
        overrides = SkinOverrides.from_settings(config)
        assert overrides.accent == "#ff8800"
        assert overrides.window is None
        assert not overrides.is_empty()

        assert overrides.as_settings() == {
            SKIN_ACCENT_KEY: "#ff8800",
            SKIN_WINDOW_KEY: "",
            SKIN_TEXT_KEY: "",
        }

    def test_settings_without_a_manager_is_empty(self):
        assert SkinOverrides.from_settings(None).is_empty()


class TestResolveSkin:
    def test_empty_overrides_return_the_builtin_tokens_untouched(self):
        assert resolve_skin(LIGHT_TOKENS, SkinOverrides()) is LIGHT_TOKENS
        assert resolve_skin(LIGHT_TOKENS, None) is LIGHT_TOKENS

    def test_accent_override_derives_hover_pressed_and_soft(self):
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(accent="#FF8800"))

        assert result.accent == "#ff8800"
        assert result.accent != result.accent_hover
        assert result.accent_hover != result.accent_pressed
        # 悬停/按下都应当比原色更深（内置主题就是这个方向）
        assert QColor(result.accent_hover).lightness() < QColor(result.accent).lightness()
        assert QColor(result.accent_pressed).lightness() < QColor(result.accent_hover).lightness()
        # 只改强调色不该动别的 token
        assert result.window == LIGHT_TOKENS.window
        assert result.text == LIGHT_TOKENS.text

    def test_window_override_derives_the_whole_surface_family(self):
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(window="#203040"))

        assert result.window == "#203040"
        assert result.window_top != result.window
        assert result.window_bottom != result.window
        # 深色窗口下卡片要比窗口亮，否则卡片会陷进背景里
        assert QColor(result.input_background).lightness() > QColor(result.window).lightness()
        assert result.border.startswith("rgba(")
        assert result.separator.startswith("rgba(")
        # 只改底色时正文要跟着换阵营：深色底上继续写内置的深色字是读不出来的
        assert result.text == DARK_TOKENS.text

    def test_light_window_keeps_cards_light(self):
        result = resolve_skin(DARK_TOKENS, SkinOverrides.from_values(window="#F5F7FA"))

        assert result.window == "#f5f7fa"
        assert QColor(result.input_background).lightness() >= QColor(result.window).lightness()

    def test_text_override_derives_muted_and_disabled(self):
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(text="#101820"))

        assert result.text == "#101820"
        # 次级文字应当是「文字色往窗口底色混」的中间值，而不是原样或全底
        muted = QColor(result.text_muted)
        assert QColor(result.text).lightness() < muted.lightness() < QColor(result.window).lightness()
        assert QColor(result.text_disabled).lightness() > muted.lightness()

    def test_all_three_at_once(self):
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(
            accent="#CC3366", window="#1A1A1A", text="#F0F0F0"))

        assert result.accent == "#cc3366"
        assert result.window == "#1a1a1a"
        assert result.text == "#f0f0f0"
        # 强调色的柔和底色要跟着窗口走（深窗口上不能留浅色底）
        assert QColor(result.accent_soft).lightness() < 128

    def test_unknown_fields_of_the_base_tokens_survive(self):
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(accent="#123456"))
        assert result.mode is LIGHT_TOKENS.mode
        assert result.selected_text == LIGHT_TOKENS.selected_text


class TestSkinReadability:
    """皮肤是用户点出来的，但不能点出「文字和背景一个颜色」。

    内置的两套配色正文对比度都在 10:1 以上；这里用 WCAG AA 的 4.5:1 当护栏，
    并且对比度按「半透明表面压在窗口底色上之后的实际颜色」算（flatten_color），
    否则算出来的是假的。
    """

    #: 承载正文的全部底色类 token。
    #: window_top/window_bottom 是标题栏那层渐变（标题文字写在上面）；
    #: popup_hover 是菜单/下拉选中态与输入框悬浮态的底色（QMenu::item:selected、
    #: QComboBox QAbstractItemView::item:hover、ui/fluent_lite/text_context_menu.py）；
    #: accent_soft 是列表选中态与输入框选中底色（selection-background-color 配
    #: selection-color: text），两者都直接写在正文下面，必须一起算对比度。
    BACKGROUND_TOKENS = ("window", "window_top", "window_bottom", "surface", "surface_strong",
                         "surface_subtle", "surface_hover", "input_background",
                         "popup_background", "popup_hover", "accent_soft")

    @staticmethod
    def _contrast_against(token, value, window) -> float:
        background = flatten_color(value, window) if value.startswith("rgba") else value
        return contrast_ratio(token, background)

    def _assert_readable(self, tokens):
        for name in self.BACKGROUND_TOKENS:
            value = getattr(tokens, name)
            ratio = self._contrast_against(tokens.text, value, tokens.window)
            assert ratio >= MIN_TEXT_CONTRAST, (name, tokens.text, value, round(ratio, 2))

    def test_builtin_palettes_are_readable(self):
        """先钉住基线：内置配色本来就是可读的。"""
        self._assert_readable(LIGHT_TOKENS)
        self._assert_readable(DARK_TOKENS)

    def test_dark_window_alone_keeps_body_text_readable(self):
        """只改窗口背景（不碰文字）：这正是「单独用一次就把界面写坏」的场景。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(window="#203040"))

        assert result.text != LIGHT_TOKENS.text, "深色底上仍是深色字"
        self._assert_readable(result)

    def test_dark_window_alone_also_moves_the_menu_hover_surface(self):
        """只改窗口背景时，强调色系的承载文字表面也要跟着走。

        回归：popup_hover 原先只在「强调色被覆盖」时才重算，于是深色皮肤下菜单/下拉的
        选中态仍是内置浅色 #EAF2FA，而文字已经变成浅色 #F2F4F6 —— 1.03:1，看不见。
        """
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(window="#203040"))

        assert result.popup_hover != LIGHT_TOKENS.popup_hover
        assert result.accent_soft != LIGHT_TOKENS.accent_soft
        for name in ("popup_hover", "accent_soft"):
            background = getattr(result, name)
            ratio = contrast_ratio(result.text, background)
            assert ratio >= MIN_TEXT_CONTRAST, (name, background, round(ratio, 2))

    def test_light_window_on_the_dark_base_also_moves_them(self):
        """反方向同理：深色基色 + 浅色窗口覆盖。"""
        result = resolve_skin(DARK_TOKENS, SkinOverrides.from_values(window="#F5F7FA"))

        assert result.popup_hover != DARK_TOKENS.popup_hover
        assert contrast_ratio(result.text, result.popup_hover) >= MIN_TEXT_CONTRAST
        self._assert_readable(result)

    def test_accent_only_still_recomputes_the_accent_surfaces(self):
        """只改强调色时，这两个表面要跟着新强调色走（旧行为不能丢）。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(accent="#FF8800"))

        assert result.popup_hover != LIGHT_TOKENS.popup_hover
        # 底色/文字不受影响
        assert result.window == LIGHT_TOKENS.window
        assert result.text == LIGHT_TOKENS.text
        assert contrast_ratio(result.text, result.popup_hover) >= MIN_TEXT_CONTRAST

    @pytest.mark.parametrize("accent", ["#FFE000", "#FFFFFF", "#00FF66", "#203040",
                                        "#6F8FAB"])
    def test_selected_text_is_readable_on_the_accent(self, accent):
        """选中态正文是写在强调色底上的（selection-color）——强调色由用户选。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(accent=accent))

        assert contrast_ratio(result.selected_text, accent) >= MIN_TEXT_CONTRAST, accent

    def test_a_dark_accent_keeps_white_selected_text(self):
        """够深的强调色不该被改掉内置的白色选中文字。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(accent="#203040"))

        assert result.selected_text == LIGHT_TOKENS.selected_text == "#FFFFFF"

    def test_light_window_alone_keeps_body_text_readable(self):
        result = resolve_skin(DARK_TOKENS, SkinOverrides.from_values(window="#F5F7FA"))

        self._assert_readable(result)

    def test_text_alone_pulls_the_backgrounds_away(self):
        """只改文字色：底色要往反方向拉开，不能变成白字压白底。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(text="#F0F0F0"))

        assert contrast_ratio(result.text, result.window) >= MIN_TEXT_CONTRAST
        self._assert_readable(result)

    def test_a_readable_pair_is_left_untouched(self):
        """用户给的组合本来就清楚时不要自作主张改人家的颜色。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(
            window="#203040", text="#F0F0F0"))

        assert result.window == "#203040"
        assert result.text == "#f0f0f0"
        self._assert_readable(result)

    @pytest.mark.parametrize("window", [None, "#203040", "#FFFFFF", "#7F7F7F", "#101418"])
    @pytest.mark.parametrize("text", [None, "#000000", "#F0F0F0", "#7F7F7F"])
    def test_every_combination_stays_readable(self, window, text):
        """穷举一批底色 × 文字色：不论怎么组合，正文都要读得清。"""
        result = resolve_skin(LIGHT_TOKENS, SkinOverrides.from_values(window=window, text=text))

        self._assert_readable(result)
        # 显式设过的颜色要保留（不做静默替换），只有「没设」的那项才允许被派生
        if text:
            assert result.text == text.lower()
        if window:
            window_after = result.window
            assert contrast_ratio(text or result.text, window_after) >= MIN_TEXT_CONTRAST


class TestThemeManagerSkin:
    def test_skin_from_settings_applies_to_tokens(self, qapp):
        config = _Config(**{SKIN_ACCENT_KEY: "#FF8800"})
        manager = UIThemeManager()
        manager.init(config, qapp)

        assert manager.skin.accent == "#ff8800"
        assert manager.tokens.accent == "#ff8800"
        # 内置 token 不受影响
        assert manager.base_tokens is LIGHT_TOKENS

    def test_set_skin_persists_and_notifies(self, qapp):
        config = _Config()
        manager = UIThemeManager()
        manager.init(config, qapp)
        seen = []
        manager.theme_changed.connect(seen.append)

        manager.set_skin(SkinOverrides.from_values(window="#202830", text="#EEEEEE"))

        assert config.values[SKIN_WINDOW_KEY] == "#202830"
        assert config.values[SKIN_TEXT_KEY] == "#eeeeee"
        assert config.values[SKIN_ACCENT_KEY] == ""
        assert seen, "皮肤变化没有发出 theme_changed，已开窗口不会刷新"
        assert seen[-1].window == "#202830"

    def test_clearing_the_skin_goes_back_to_the_builtin_tokens(self, qapp):
        config = _Config(**{SKIN_ACCENT_KEY: "#FF8800"})
        manager = UIThemeManager()
        manager.init(config, qapp)
        assert manager.tokens.accent == "#ff8800"

        manager.set_skin(SkinOverrides())

        assert manager.tokens.accent == LIGHT_TOKENS.accent
        assert config.values[SKIN_ACCENT_KEY] == ""

    def test_skin_survives_a_new_manager_reading_the_same_config(self, qapp):
        """重启后仍生效：皮肤只存在配置里，新建管理器要能读回来。"""
        config = _Config()
        UIThemeManager().init(config, qapp).set_skin(
            SkinOverrides.from_values(accent="#00AAFF", window="#101418"))

        restarted = UIThemeManager()
        restarted.init(config, qapp)

        assert restarted.tokens.accent == "#00aaff"
        assert restarted.tokens.window == "#101418"
        # 界面真的跟着变了（调色板用的是最终 token）
        assert qapp.palette().color(QPalette.ColorRole.Window) == QColor("#101418")
