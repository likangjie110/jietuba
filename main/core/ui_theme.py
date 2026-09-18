# -*- coding: utf-8 -*-
"""Application-wide light/dark appearance management.

This module intentionally does not manage screenshot accent and mask colours;
those remain in :mod:`core.theme`.  UIThemeManager owns only semantic colours
used by application windows and native Qt widgets.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


class UIThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @classmethod
    def coerce(cls, value) -> "UIThemeMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.SYSTEM


@dataclass(frozen=True)
class UIThemeTokens:
    mode: UIThemeMode
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    window: str
    window_top: str
    window_bottom: str
    surface: str
    surface_strong: str
    surface_subtle: str
    surface_hover: str
    text: str
    text_muted: str
    text_disabled: str
    border: str
    border_hover: str
    window_border: str
    input_background: str
    popup_background: str
    popup_hover: str
    separator: str
    switch_off: str
    selected_text: str = "#FFFFFF"

    @property
    def is_dark(self) -> bool:
        return self.mode is UIThemeMode.DARK


_COMMON = {
    "accent": "#6F8FAB",
    "accent_hover": "#627F99",
    "accent_pressed": "#526D85",
}

LIGHT_TOKENS = UIThemeTokens(
    mode=UIThemeMode.LIGHT,
    **_COMMON,
    accent_soft="#DFE8EF",
    window="#D9E3EC",
    window_top="#DFE7EE",
    window_bottom="#CDD8E2",
    surface="rgba(255, 255, 255, 0.92)",
    surface_strong="rgba(255, 255, 255, 0.98)",
    surface_subtle="rgba(248, 251, 253, 0.85)",
    surface_hover="#FFFFFF",
    text="#17201B",
    text_muted="#68736D",
    text_disabled="#9AA39D",
    border="rgba(112, 130, 119, 0.20)",
    border_hover="rgba(76, 101, 86, 0.34)",
    window_border="#B4C1CD",
    input_background="#FFFFFF",
    popup_background="#FFFFFF",
    popup_hover="#EAF2FA",
    separator="rgba(98, 116, 105, 0.13)",
    switch_off="#C4CCC6",
)

DARK_TOKENS = UIThemeTokens(
    mode=UIThemeMode.DARK,
    **_COMMON,
    accent_soft="#31404C",
    window="#1B1E22",
    window_top="#24282D",
    window_bottom="#191C20",
    surface="rgba(43, 47, 52, 0.96)",
    surface_strong="rgba(49, 54, 60, 0.99)",
    surface_subtle="rgba(36, 40, 45, 0.92)",
    surface_hover="#373C43",
    text="#F2F4F6",
    text_muted="#AEB5BD",
    text_disabled="#737B84",
    border="rgba(255, 255, 255, 0.12)",
    border_hover="rgba(255, 255, 255, 0.28)",
    window_border="#3B424A",
    input_background="#2B2F34",
    popup_background="#292D32",
    popup_hover="#363B42",
    separator="rgba(255, 255, 255, 0.10)",
    switch_off="#626A73",
)


# ── 皮肤覆盖层 ────────────────────────────────────────────────
#: 用户可以自定义的三项；其余 token 由这三项派生，避免出现半截皮肤
#: （例如窗口换了深色、卡片还是浅色）。
SKIN_ACCENT_KEY = "skin_accent_color"
SKIN_WINDOW_KEY = "skin_window_color"
SKIN_TEXT_KEY = "skin_text_color"


def normalize_color(value) -> Optional[str]:
    """把用户给的颜色规整成 ``#rrggbb``；不是合法颜色时返回 None。

    皮肤是「可选覆盖」：字段非法（空串、乱写的字符串）时必须退回内置 token，
    不能让界面变成没有颜色的空白。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    color = QColor(value.strip())
    if not color.isValid():
        return None
    return color.name()


def _mix(base: str, other: str, ratio: float) -> str:
    """把 base 朝 other 混合 ratio（0..1），返回 ``#rrggbb``。"""
    a, b = QColor(base), QColor(other)
    return QColor(
        round(a.red() * (1 - ratio) + b.red() * ratio),
        round(a.green() * (1 - ratio) + b.green() * ratio),
        round(a.blue() * (1 - ratio) + b.blue() * ratio),
    ).name()


def _shade(color: str, factor: float) -> str:
    """按比例调亮（factor > 1）或调暗（factor < 1），返回 ``#rrggbb``。"""
    c = QColor(color)
    return QColor(
        min(255, max(0, round(c.red() * factor))),
        min(255, max(0, round(c.green() * factor))),
        min(255, max(0, round(c.blue() * factor))),
    ).name()


def _rgba(color: str, alpha: float) -> str:
    """把 ``#rrggbb`` 转成 ``rgba(r, g, b, a)``（QSS 里那些半透明 token 用的形式）。"""
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:g})"


def _is_dark(color: str) -> bool:
    return QColor(color).lightness() < 128


def _luminance(color: str) -> float:
    """WCAG 相对亮度。"""
    c = QColor(color)

    def channel(value: int) -> float:
        srgb = value / 255.0
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    return (0.2126 * channel(c.red()) + 0.7152 * channel(c.green())
            + 0.0722 * channel(c.blue()))


def contrast_ratio(foreground: str, background: str) -> float:
    """两种颜色的对比度（1:1 ～ 21:1）。

    皮肤是用户点出来的，但不能点出「文字和背景一个颜色」——内置的两套配色都是
    10:1 以上，所以这里用 WCAG AA 的 4.5:1 当护栏。
    """
    first, second = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (first + 0.05) / (second + 0.05)


def flatten_color(color: str, backdrop: str) -> str:
    """把带透明度的颜色压到背景上，返回实际看到的 ``#rrggbb``。

    token 里不少是 ``rgba(...)``（卡片、输入框底色都是半透明的），对比度要按
    「压在窗口底色上之后」的实际颜色算，否则算出来的对比度是假的。
    """
    rgba = QColor(color)
    if not rgba.isValid() and isinstance(color, str) and color.startswith("rgba("):
        parts = color[color.index("(") + 1: color.rindex(")")].split(",")
        if len(parts) == 4:
            alpha = float(parts[3])
            rgba = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
            rgba.setAlpha(round(alpha * 255) if alpha <= 1 else round(alpha))
    if not rgba.isValid():
        return color
    if rgba.alpha() == 255:
        return rgba.name()
    base = QColor(backdrop)
    alpha = rgba.alpha() / 255.0
    return QColor(
        round(rgba.red() * alpha + base.red() * (1 - alpha)),
        round(rgba.green() * alpha + base.green() * (1 - alpha)),
        round(rgba.blue() * alpha + base.blue() * (1 - alpha)),
    ).name()


#: 正文与它所在背景的最低对比度（WCAG AA 正文 4.5:1）
MIN_TEXT_CONTRAST = 4.5


def _away_from(color: str, other: str, threshold: float = MIN_TEXT_CONTRAST) -> str:
    """把 color 往黑/白方向拉，直到与 other 的对比度达标。

    先试较温和的拉法，不够就继续拉；两边拉满都不行（极端中间灰）时取对比度更高的
    那一端。用户显式选的颜色因此只会在「读不清」时才被改动，且改动方向总是更可读。
    """
    for ratio in (0.6, 0.8, 0.92):
        darker = _mix(color, "#000000", ratio)
        if contrast_ratio(other, darker) >= threshold:
            return darker
    for ratio in (0.6, 0.8, 0.92):
        lighter = _mix(color, "#FFFFFF", ratio)
        if contrast_ratio(other, lighter) >= threshold:
            return lighter
    return "#000000" if contrast_ratio(other, "#000000") >= contrast_ratio(other, "#FFFFFF") \
        else "#FFFFFF"


def _readable_background(background: str, text: str) -> str:
    """正文色是用户定的：底色太接近就拉开，保证能读。"""
    if contrast_ratio(text, background) >= MIN_TEXT_CONTRAST:
        return background
    return _away_from(background, text)


def _readable_text(tokens: UIThemeTokens, background: str) -> str:
    """没指定正文色时给背景挑一套可读的文字色。

    优先用同一明暗阵营的内置文字色（两套内置配色是成对设计过的）；连它都读不清
    （用户把底色设成了中灰）就用黑白里对比度更高的那一端兜底。
    """
    if contrast_ratio(tokens.text, background) >= MIN_TEXT_CONTRAST:
        return tokens.text
    candidate = DARK_TOKENS.text if _is_dark(background) else LIGHT_TOKENS.text
    if contrast_ratio(candidate, background) >= MIN_TEXT_CONTRAST:
        return candidate
    return max(("#000000", "#FFFFFF"), key=lambda color: contrast_ratio(color, background))


def _surface_from(window: str, toward: float, text: str) -> str:
    """从窗口底色派生卡片底色：深色皮肤往亮处提；再保证与正文的对比度达标。"""
    surface = _shade(window, 1.22 if toward >= 0.9 else 1.12) if _is_dark(window) \
        else _mix(window, "#FFFFFF", toward)
    if contrast_ratio(text, surface) >= MIN_TEXT_CONTRAST:
        return surface
    return _away_from(surface, text)


def _readable_against(background: str, text: str) -> str:
    """保证「承载文字的表面」与正文可读：已经很远就原样返回，太近就拉开。

    菜单/下拉选中态、列表项选中底色、输入框选中底色都是这类表面——它们上面直接写
    ``tokens.text``，所以必须跟正文一起派生，不能留在内置配色上。
    """
    if contrast_ratio(text, background) >= MIN_TEXT_CONTRAST:
        return background
    return _away_from(background, text)


def _accent_surfaces(accent: str, window: str, text: str, surface_popup: str) -> dict:
    """强调色系里那两个**承载文字**的表面：柔和底（选中态）与悬停底（菜单/下拉/输入框）。

    它们跟着最终底色走：只改窗口背景时如果还留着内置的浅色 ``#EAF2FA``，
    菜单选中项的文字（深色皮肤下是浅色字）就压在上面读不出来（实测 1.03:1）。
    """
    return {
        "accent_soft": _readable_against(_mix(accent, window, 0.78), text),
        "popup_hover": _readable_against(_mix(accent, surface_popup, 0.82), text),
    }


def _selected_text_for(accent: str, fallback: str) -> str:
    """选中态的正文色：它是写在强调色底上的（输入框/列表的 selection-color）。

    强调色由用户自选，浅色强调色配内置的白色字会看不见（例如 #FFE000 上的白字
    只有 1.1:1），所以这里按对比度在黑/白里挑一个更清楚的。
    """
    if contrast_ratio(fallback, accent) >= MIN_TEXT_CONTRAST:
        return fallback
    return max(("#000000", "#FFFFFF"), key=lambda color: contrast_ratio(color, accent))


def _background_family(tokens: UIThemeTokens, window: str, text: str, accent: str) -> dict:
    """窗口底色 + 正文色 + 强调色 → 全部与背景相关的 token（成对派生，保证可读）。"""
    window_border = _mix(window, text, 0.25)
    dark = _is_dark(window)
    surface_popup = _surface_from(window, 0.96, text)
    values = {
        "window": window,
        "window_top": _shade(window, 1.06),
        "window_bottom": _shade(window, 0.93),
        "window_border": window_border,
        "border": _rgba(window_border, 0.12 if dark else 0.20),
        "border_hover": _rgba(window_border, 0.28 if dark else 0.34),
        "separator": _rgba(window_border, 0.10 if dark else 0.13),
        "surface": _rgba(_surface_from(window, 0.92, text), 0.92),
        "surface_strong": _rgba(_surface_from(window, 0.98, text), 0.98),
        "surface_subtle": _rgba(_surface_from(window, 0.82, text), 0.85),
        "surface_hover": _surface_from(window, 0.99 if dark else 1.0, text),
        "input_background": _surface_from(window, 0.99, text),
        "popup_background": surface_popup,
        "switch_off": _mix(window, text, 0.22),
        "text": text,
        # 次级/禁用文字往窗口底色混的比例比内置配色更保守一点：皮肤常用更暗的底色，
        # 混得太狠会把说明文字压到 4:1 以下（内置浅色主题的次级文字本来就在 4.3:1 上下）
        "text_muted": _mix(text, window, 0.35),
        "text_disabled": _mix(text, window, 0.55),
    }
    values.update(_accent_surfaces(accent, window, text, surface_popup))
    return values


@dataclass(frozen=True)
class SkinOverrides:
    """用户对主题 token 的覆盖；字段为 None 表示该项跟随内置主题。"""

    accent: Optional[str] = None
    window: Optional[str] = None
    text: Optional[str] = None

    @classmethod
    def from_values(cls, accent=None, window=None, text=None) -> "SkinOverrides":
        """按值构造；每个值都过一遍 ``normalize_color``（非法值等于没设）。"""
        return cls(
            accent=normalize_color(accent),
            window=normalize_color(window),
            text=normalize_color(text),
        )

    @classmethod
    def from_settings(cls, config) -> "SkinOverrides":
        if config is None:
            return cls()
        return cls.from_values(
            accent=config.get_app_setting(SKIN_ACCENT_KEY, ""),
            window=config.get_app_setting(SKIN_WINDOW_KEY, ""),
            text=config.get_app_setting(SKIN_TEXT_KEY, ""),
        )

    def is_empty(self) -> bool:
        return not (self.accent or self.window or self.text)

    def as_settings(self) -> dict:
        """持久化用的键值对（没设的写空串，便于「清除皮肤」）。"""
        return {
            SKIN_ACCENT_KEY: self.accent or "",
            SKIN_WINDOW_KEY: self.window or "",
            SKIN_TEXT_KEY: self.text or "",
        }


def resolve_skin(tokens: UIThemeTokens, overrides: Optional[SkinOverrides]) -> UIThemeTokens:
    """把皮肤覆盖套到内置 token 上，返回界面实际使用的 token。

    窗口底色与正文色是**成对**派生的：只改底色时正文跟着换阵营（否则深色底上写深色字），
    只改正文时底色会被拉开到读得清；卡片/输入框/弹层这些表面既跟随底色，也保证与正文
    的对比度达标。其余派生项（边框、次级文字、悬停色）都按同一套比例算。
    """
    if overrides is None or overrides.is_empty():
        return tokens

    accent = overrides.accent or tokens.accent
    values: dict = {}

    if overrides.window or overrides.text:
        window = overrides.window or tokens.window
        if overrides.text:
            # 正文是用户定的：底色（以及下面的各种表面）往反方向拉开，保证能读
            text = overrides.text
            window = _readable_background(window, text)
        else:
            text = _readable_text(tokens, window)
        # 承载文字的表面全部跟最终底色走：accent_soft / popup_hover 也在其中
        # （选中态、菜单/下拉悬浮态），留在内置浅色上就是「浅底浅字」
        values.update(_background_family(tokens, window, text, accent))
    elif overrides.accent:
        # 只改强调色：底色/表面/文字保持内置。但强调色系那两个承载文字的表面
        # 要用新强调色重算，否则新旧强调色会打架
        surface_popup = flatten_color(tokens.popup_background, tokens.window)
        values.update(_accent_surfaces(accent, tokens.window, tokens.text, surface_popup))

    if overrides.accent:
        values.update({
            "accent": accent,
            "accent_hover": _shade(accent, 0.88),
            "accent_pressed": _shade(accent, 0.74),
            # 选中态正文写在强调色上，跟着强调色一起保证可读
            "selected_text": _selected_text_for(accent, tokens.selected_text),
        })

    return replace(tokens, **values)


class UIThemeManager(QObject):
    """Resolve, persist and apply the effective application appearance."""

    theme_changed = Signal(object)
    mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = None
        self._application: Optional[QApplication] = None
        self._mode = UIThemeMode.SYSTEM
        self._effective_mode = UIThemeMode.LIGHT
        self._skin = SkinOverrides()
        self._system_signal_connected = False

    def init(self, config_manager=None, application=None):
        self._config = config_manager
        app = application or QApplication.instance()
        self._application = app if isinstance(app, QApplication) else None
        saved = (
            config_manager.get_app_setting("ui_theme_mode", "system")
            if config_manager is not None
            else "system"
        )
        self._mode = UIThemeMode.coerce(saved)
        self._skin = SkinOverrides.from_settings(config_manager)
        self._connect_system_theme()
        self.apply(force=True)
        return self

    @property
    def mode(self) -> UIThemeMode:
        return self._mode

    @property
    def effective_mode(self) -> UIThemeMode:
        return self._effective_mode

    @property
    def is_dark(self) -> bool:
        return self._effective_mode is UIThemeMode.DARK

    @property
    def base_tokens(self) -> UIThemeTokens:
        """内置主题的原始 token（不含用户皮肤覆盖）。"""
        return DARK_TOKENS if self.is_dark else LIGHT_TOKENS

    @property
    def skin(self) -> SkinOverrides:
        return self._skin

    @property
    def tokens(self) -> UIThemeTokens:
        """界面实际使用的 token：内置主题 + 用户皮肤覆盖。"""
        return resolve_skin(self.base_tokens, self._skin)

    def set_skin(self, overrides, persist: bool = True):
        """设置皮肤覆盖层（非法值自动退回内置值），并立即刷新界面。

        持久化与界面应用分开：这里只管「写配置 + 重新应用」，token 计算在
        ``resolve_skin`` 那个纯函数里，便于直接单测。
        """
        normalized = overrides if isinstance(overrides, SkinOverrides) else SkinOverrides()
        changed = normalized != self._skin
        self._skin = normalized
        if persist and self._config is not None:
            for key, value in normalized.as_settings().items():
                self._config.set_app_setting(key, value)
        if changed:
            self.apply(force=True)

    def set_mode(self, mode, persist: bool = True):
        normalized = UIThemeMode.coerce(mode)
        changed = normalized is not self._mode
        self._mode = normalized
        if persist and self._config is not None:
            self._config.set_app_setting("ui_theme_mode", normalized.value)
        self.apply(force=changed)
        if changed:
            self.mode_changed.emit(normalized.value)

    def apply(self, force: bool = False):
        effective = self._resolve_effective_mode()
        changed = effective is not self._effective_mode
        self._effective_mode = effective
        if not (force or changed):
            return

        if self._application is not None:
            self._application.setProperty("uiTheme", effective.value)
            self._application.setPalette(self.build_palette(self.tokens))
            self._application.setStyleSheet(
                self._build_global_stylesheet(self.tokens)
            )

        self.theme_changed.emit(self.tokens)

    def _resolve_effective_mode(self) -> UIThemeMode:
        if self._mode is not UIThemeMode.SYSTEM:
            return self._mode
        hints = QGuiApplication.styleHints()
        if hints is not None and hasattr(hints, "colorScheme"):
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return UIThemeMode.DARK
            if scheme == Qt.ColorScheme.Light:
                return UIThemeMode.LIGHT

        # Some remote-desktop and older platform plugins report Unknown.
        app = self._application or QApplication.instance()
        if app is not None:
            window = app.palette().color(QPalette.ColorRole.Window)
            return (
                UIThemeMode.DARK
                if window.lightness() < 128
                else UIThemeMode.LIGHT
            )
        return UIThemeMode.LIGHT

    def _connect_system_theme(self):
        if self._system_signal_connected:
            return
        hints = QGuiApplication.styleHints()
        signal = getattr(hints, "colorSchemeChanged", None) if hints else None
        if signal is not None:
            signal.connect(self._on_system_theme_changed)
            self._system_signal_connected = True

    def _on_system_theme_changed(self, _scheme):
        if self._mode is UIThemeMode.SYSTEM:
            self.apply()

    @staticmethod
    def build_palette(t: UIThemeTokens) -> QPalette:
        palette = QPalette()
        normal = QPalette.ColorGroup.Normal
        inactive = QPalette.ColorGroup.Inactive

        roles = {
            QPalette.ColorRole.Window: t.window,
            QPalette.ColorRole.WindowText: t.text,
            QPalette.ColorRole.Base: t.input_background,
            QPalette.ColorRole.AlternateBase: t.surface_subtle,
            QPalette.ColorRole.ToolTipBase: t.popup_background,
            QPalette.ColorRole.ToolTipText: t.text,
            QPalette.ColorRole.Text: t.text,
            QPalette.ColorRole.Button: t.surface_strong,
            QPalette.ColorRole.ButtonText: t.text,
            QPalette.ColorRole.BrightText: "#FFFFFF",
            QPalette.ColorRole.Link: t.accent,
            QPalette.ColorRole.Highlight: t.accent,
            QPalette.ColorRole.HighlightedText: t.selected_text,
            QPalette.ColorRole.PlaceholderText: t.text_muted,
            QPalette.ColorRole.Mid: t.border_hover,
            QPalette.ColorRole.Dark: t.window_border,
        }
        for group in (normal, inactive):
            for role, value in roles.items():
                palette.setColor(group, role, QColor(value))

        disabled = QPalette.ColorGroup.Disabled
        for role, value in roles.items():
            palette.setColor(disabled, role, QColor(value))
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.PlaceholderText,
        ):
            palette.setColor(disabled, role, QColor(t.text_disabled))
        return palette

    @staticmethod
    def _build_global_stylesheet(t: UIThemeTokens) -> str:
        # Keep this intentionally small.  Feature windows with their own theme
        # (for example Clipboard) remain free to override it locally.
        return f"""
            QToolTip {{
                color: {t.text};
                background-color: {t.popup_background};
                border: 1px solid {t.border_hover};
                padding: 4px 7px;
            }}
            QMenu {{
                color: {t.text};
                background-color: {t.popup_background};
                border: 1px solid {t.border};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                color: {t.text};
                background-color: transparent;
                padding: 6px 28px 6px 26px;
                margin: 1px 0;
            }}
            QMenu::item:selected {{
                color: {t.text};
                background-color: {t.popup_hover};
            }}
            QMenu::item:disabled {{
                color: {t.text_disabled};
                background-color: transparent;
            }}
            QMenu::separator {{
                height: 1px;
                background: {t.separator};
                margin: 4px 7px;
            }}
        """


_manager: Optional[UIThemeManager] = None


def get_ui_theme() -> UIThemeManager:
    global _manager
    if _manager is None:
        _manager = UIThemeManager()
    return _manager
