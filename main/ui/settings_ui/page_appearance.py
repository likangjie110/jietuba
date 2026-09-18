# -*- coding: utf-8 -*-
"""外观设置页 — Fluent Design"""
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QColorDialog, QWidgetAction,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap

from ui.fluent_lite import (
    SettingCard as FSettingCard, FluentIcon,
    ComboBox, CaptionLabel, PushButton,
)
from .components import (
    SettingCardGroup, theme_menu_style, theme_color,
)

# 统一控件宽度
_CTRL_W = 80
_CTRL_H = 26


def _swatch_qss(color: QColor, customized: bool = False) -> str:
    """色块按钮的样式：填色 + 一圈描边；自定义过的加粗描边以便一眼看出。"""
    r, g, b = color.red(), color.green(), color.blue()
    border = theme_color("#C9CDD4", "#4A4F57")
    hover_border = theme_color("#8A9099", "#B8BEC8")
    if customized:
        border = theme_color("#333333", "#F3F3F3")
    width = 2 if customized else 1
    return f"""
        QPushButton {{
            background-color: rgb({r}, {g}, {b});
            border: {width}px solid {border};
            border-radius: 3px;
        }}
        QPushButton:hover {{
            border: {width}px solid {hover_border};
        }}
    """


class ColorSwatchButton(PushButton):
    """色块按钮：颜色存在自己身上，样式由 ``_style_sheet`` 产出。

    fluent_lite 的 ``PushButton`` 在主题变化时会重刷自己的默认样式（``_apply_theme``
    里调 ``_style_sheet()``），所以直接 ``setStyleSheet`` 上色的按钮**任何一次主题/皮肤
    变化都会被冲成普通按钮**（实测：保存皮肤后三个色块变成空的描边框）。把颜色记下来、
    覆写 ``_style_sheet``，刷新时自然还是色块。
    """

    # 类级默认值：基类 __init__ 里就会调一次 _apply_theme() → _style_sheet()，
    # 那时实例属性还没赋值（会 AttributeError）
    _swatch_color: QColor | None = None
    _swatch_customized = False
    _swatch_provider = None

    def __init__(self, parent=None, size_w=_CTRL_W, size_h=_CTRL_H):
        super().__init__(parent)
        self.setFixedSize(size_w, size_h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_swatch(self, color: QColor | None = None, *, customized: bool = False,
                   style_provider=None):
        """记录这块色块该长什么样，并立即重刷。"""
        self._swatch_color = None if color is None else QColor(color)
        self._swatch_customized = bool(customized)
        self._swatch_provider = style_provider
        self._apply_theme()

    def _style_sheet(self) -> str:
        if self._swatch_provider is not None:
            return self._swatch_provider()
        if self._swatch_color is not None:
            return _swatch_qss(self._swatch_color, self._swatch_customized)
        return super()._style_sheet()


def _update_color_btn(btn: QPushButton, color: QColor, customized: bool = False):
    """更新色块按钮的背景颜色（色块按钮走自己的样式通道，见 ColorSwatchButton）。"""
    set_swatch = getattr(btn, "set_swatch", None)
    if callable(set_swatch):
        set_swatch(color, customized=customized)
        return
    btn.setStyleSheet(_swatch_qss(color, customized))


def _make_color_btn(dialog, size_w=_CTRL_W, size_h=_CTRL_H):
    """创建统一尺寸的色块按钮"""
    return ColorSwatchButton(dialog, size_w, size_h)


# ================================================================
# 剪贴板主题色块 — 色板取自 clipboard.ui.theme.themes.PRESET_THEME_SWATCHES
# ================================================================
def _clip_theme_qss(name: str) -> str:
    """剪贴板主题的双色方块样式（色板取自 clipboard 那套预设）。"""
    from clipboard.ui.theme.themes import PRESET_THEME_SWATCHES
    swatch = PRESET_THEME_SWATCHES.get(name)
    if swatch is None:
        return ""
    accent, bg = swatch
    return f"""
        QPushButton {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {bg}, stop:0.5 {bg},
                stop:0.5 {accent}, stop:1 {accent});
            border: 2px solid {accent};
            border-radius: 3px;
        }}
        QPushButton:hover {{ border: 2px solid {theme_color('#333333', '#F3F3F3')}; }}
    """


def _apply_clip_theme_btn_style(btn: QPushButton, name: str):
    """把主题按钮画成该主题的双色方块。建页与 refresh_settings 共用，不再各抄一份。"""
    if not _clip_theme_qss(name):
        return
    set_swatch = getattr(btn, "set_swatch", None)
    if callable(set_swatch):
        # 走色块自己那条通道：主题变化时 PushButton 会把样式重刷成默认按钮
        set_swatch(style_provider=lambda: _clip_theme_qss(name))
        return
    btn.setStyleSheet(_clip_theme_qss(name))


def create_appearance_page(dialog) -> QWidget:
    """创建外观设置页面 — Fluent Design"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ── 应用界面 ──────────────────────────────────────
    grp_app = SettingCardGroup(dialog.tr("Application"), view)
    _build_application_section(dialog, grp_app)
    layout.addWidget(grp_app)

    # ── 截图外观 ──────────────────────────────────────
    grp_ss = SettingCardGroup(dialog.tr("Screenshot"), view)
    _build_screenshot_section(dialog, grp_ss)
    layout.addWidget(grp_ss)

    # ── 剪贴板外观 ────────────────────────────────────
    grp_clip = SettingCardGroup(dialog.tr("Clipboard"), view)
    _build_clipboard_section(dialog, grp_clip)
    layout.addWidget(grp_clip)

    # 提示
    hint = CaptionLabel(
        dialog.tr("💡 Hint: Color changes take effect on the next screenshot."),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    layout.addWidget(hint)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll


# ================================================================
# 应用界面
# ================================================================

#: 皮肤可覆盖的三项：配置字段名 → (界面文案, SkinOverrides 字段名)
_SKIN_FIELDS = (
    ("skin_window_color", "Window Background", "window"),
    ("skin_text_color", "Text Color", "text"),
    ("skin_accent_color", "Accent Color", "accent"),
)


def _skin_effective_colors(overrides) -> dict:
    """色块显示用的颜色：用户设过就显示覆盖值，没设就显示内置主题当前值。"""
    from core.ui_theme import get_ui_theme
    tokens = get_ui_theme().tokens
    return {
        "window": overrides.window or tokens.window,
        "text": overrides.text or tokens.text,
        "accent": overrides.accent or tokens.accent,
    }


def _refresh_skin_buttons(dialog):
    """按当前（未保存的）皮肤值刷新三个色块与其提示。"""
    overrides = dialog._skin_overrides
    colors = _skin_effective_colors(overrides)
    for field, btn in dialog._skin_buttons.items():
        customized = getattr(overrides, field) is not None
        # 覆盖过的那一项加粗描边，一眼看出哪几项自定义了
        _update_color_btn(btn, QColor(colors[field]), customized)
        btn.setToolTip(
            dialog.tr("Customized") if customized else dialog.tr("Following the theme")
        )


def _pick_skin_color(dialog, field: str):
    from core.ui_theme import SkinOverrides

    current = dialog._skin_overrides
    preset = _skin_effective_colors(current)[field]
    picker = QColorDialog(QColor(preset), None)
    picker.setWindowTitle(dialog.tr("Select Color"))
    picker.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    if not picker.exec():
        return

    values = {"accent": current.accent, "window": current.window, "text": current.text}
    values[field] = picker.selectedColor().name()
    dialog._skin_overrides = SkinOverrides.from_values(**values)
    _refresh_skin_buttons(dialog)


def _build_skin_card(dialog, grp: SettingCardGroup):
    """皮肤：在内置浅色/深色之上再覆盖窗口底色、文字色与强调色。"""
    from core.ui_theme import SkinOverrides, get_ui_theme

    dialog._skin_overrides = get_ui_theme().skin

    card = FSettingCard(
        FluentIcon.PALETTE,
        dialog.tr("Skin Colors"),
        dialog.tr("Override the window background, text and accent colors."),
        parent=grp,
    )

    box = QWidget(card)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    dialog._skin_buttons = {}
    for _key, label, field in _SKIN_FIELDS:
        btn = _make_color_btn(dialog, 48, _CTRL_H)
        btn.setToolTip(dialog.tr(label))
        btn.clicked.connect(lambda _=False, f=field: _pick_skin_color(dialog, f))
        row.addWidget(btn)
        dialog._skin_buttons[field] = btn

    reset_btn = PushButton(dialog.tr("Follow Theme"), card)
    reset_btn.setFixedHeight(_CTRL_H)
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog._skin_reset_btn = reset_btn

    def _follow_theme():
        dialog._skin_overrides = SkinOverrides()
        _refresh_skin_buttons(dialog)

    reset_btn.clicked.connect(_follow_theme)
    row.addWidget(reset_btn)

    card.hBoxLayout.addWidget(box, 0, Qt.AlignmentFlag.AlignRight)
    grp.addSettingCard(card)
    _refresh_skin_buttons(dialog)


def _refresh_logo_preview(dialog):
    """刷新 logo 预览与说明文字（显示的是**未保存的**选择）。"""
    from core.resource_manager import ResourceManager

    path = dialog._custom_logo_path
    pixmap = QPixmap(path) if path else QPixmap()
    if pixmap.isNull():
        # 没设或读不出图 → 预览按内置品牌图标显示，和保存后的实际结果一致
        pixmap = ResourceManager.get_app_icon().pixmap(28, 28)
        if path:
            dialog._logo_hint.setText(dialog.tr("Cannot read this image, using the built-in icon"))
        else:
            dialog._logo_hint.setText(dialog.tr("Built-in icon"))
    else:
        dialog._logo_hint.setText(os.path.basename(path))
    dialog._logo_preview.setPixmap(
        pixmap.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    )


def _set_custom_logo(dialog, path: str):
    dialog._custom_logo_path = path or ""
    _refresh_logo_preview(dialog)


def _choose_custom_logo(dialog):
    from PySide6.QtWidgets import QFileDialog

    path, _ = QFileDialog.getOpenFileName(
        dialog,
        dialog.tr("Choose Logo Image"),
        "",
        dialog.tr("Images (*.png *.jpg *.jpeg *.bmp *.gif *.svg *.ico)"),
    )
    if path:
        _set_custom_logo(dialog, path)


def _build_logo_card(dialog, grp: SettingCardGroup):
    """自定义 logo：托盘与窗口图标都用这张图（读不出就回退内置图标）。"""
    from settings import get_tool_settings_manager

    config = get_tool_settings_manager()
    dialog._custom_logo_path = config.get_app_setting("custom_logo_path", "") or ""

    card = FSettingCard(
        FluentIcon.APPLICATION,
        dialog.tr("Custom Logo"),
        dialog.tr("Use your own image for the tray and window icons."),
        parent=grp,
    )

    box = QWidget(card)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    preview = QLabel(card)
    preview.setFixedSize(28, 28)
    preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dialog._logo_preview = preview
    row.addWidget(preview)

    hint = CaptionLabel("", card)
    hint.setStyleSheet("padding: 0 2px;")
    dialog._logo_hint = hint
    row.addWidget(hint)

    choose_btn = PushButton(dialog.tr("Choose Image"), card)
    choose_btn.setFixedHeight(_CTRL_H)
    choose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    choose_btn.clicked.connect(lambda: _choose_custom_logo(dialog))
    row.addWidget(choose_btn)

    clear_btn = PushButton(dialog.tr("Clear"), card)
    clear_btn.setFixedHeight(_CTRL_H)
    clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    clear_btn.clicked.connect(lambda: _set_custom_logo(dialog, ""))
    row.addWidget(clear_btn)

    card.hBoxLayout.addWidget(box, 0, Qt.AlignmentFlag.AlignRight)
    grp.addSettingCard(card)
    _refresh_logo_preview(dialog)


def _build_application_section(dialog, grp: SettingCardGroup):
    """Application appearance: follow OS, force light or force dark."""
    from core.ui_theme import get_ui_theme

    card = FSettingCard(
        FluentIcon.APPLICATION,
        dialog.tr("Interface Theme"),
        dialog.tr("Choose a display mode."),
        parent=grp,
    )
    dialog._ui_theme_combo = ComboBox(card)
    dialog._ui_theme_combo.setFixedWidth(150)
    dialog._ui_theme_combo.addItem(dialog.tr("System"), userData="system")
    dialog._ui_theme_combo.addItem(dialog.tr("Light"), userData="light")
    dialog._ui_theme_combo.addItem(dialog.tr("Dark"), userData="dark")
    index = dialog._ui_theme_combo.findData(get_ui_theme().mode.value)
    dialog._ui_theme_combo.setCurrentIndex(max(0, index))
    card.hBoxLayout.addWidget(
        dialog._ui_theme_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    card.hBoxLayout.addSpacing(16)
    grp.addSettingCard(card)

    _build_skin_card(dialog, grp)
    _build_logo_card(dialog, grp)


# ================================================================
# 截图外观
# ================================================================

def _build_screenshot_section(dialog, grp: SettingCardGroup):
    """截图外观：主题色 + 遮罩色"""
    from core.theme import get_theme
    theme = get_theme()

    # 主题色
    theme_card = FSettingCard(
        FluentIcon.PALETTE,
        dialog.tr("Theme Color"),
        parent=grp,
    )
    dialog._theme_color_btn = _make_color_btn(dialog)
    dialog._appearance_theme_color = QColor(theme.theme_color)
    _update_color_btn(dialog._theme_color_btn, dialog._appearance_theme_color)

    def _pick_theme_color():
        _dlg = QColorDialog(dialog._appearance_theme_color, None)
        _dlg.setWindowTitle(dialog.tr("Select Theme Color"))
        _dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if _dlg.exec():
            color = _dlg.selectedColor()
            dialog._appearance_theme_color = color
            _update_color_btn(dialog._theme_color_btn, color)

    dialog._theme_color_btn.clicked.connect(_pick_theme_color)
    theme_card.hBoxLayout.addWidget(
        dialog._theme_color_btn, 0, Qt.AlignmentFlag.AlignRight
    )
    theme_card.hBoxLayout.addSpacing(16)
    grp.addSettingCard(theme_card)

    # 遮罩色
    mask_card = FSettingCard(
        FluentIcon.BRUSH,
        dialog.tr("Mask Color"),
        parent=grp,
    )
    dialog._mask_color_btn = _make_color_btn(dialog)
    mc = theme.mask_color
    dialog._appearance_mask_color = QColor(mc.red(), mc.green(), mc.blue())
    _update_color_btn(dialog._mask_color_btn, dialog._appearance_mask_color)

    def _pick_mask_color():
        _dlg = QColorDialog(dialog._appearance_mask_color, None)
        _dlg.setWindowTitle(dialog.tr("Select Mask Color"))
        _dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if _dlg.exec():
            color = _dlg.selectedColor()
            dialog._appearance_mask_color = color
            _update_color_btn(dialog._mask_color_btn, color)

    dialog._mask_color_btn.clicked.connect(_pick_mask_color)
    mask_card.hBoxLayout.addWidget(
        dialog._mask_color_btn, 0, Qt.AlignmentFlag.AlignRight
    )
    mask_card.hBoxLayout.addSpacing(16)
    grp.addSettingCard(mask_card)


# ================================================================
# 剪贴板外观
# ================================================================

def _build_clipboard_section(dialog, grp: SettingCardGroup):
    """剪贴板外观：主题 + 字体大小 + 透明度"""
    from clipboard.ui.theme.themes import PRESET_THEME_SWATCHES, get_theme_manager
    from settings import get_tool_settings_manager
    config = get_tool_settings_manager()
    theme_mgr = get_theme_manager()

    # ── 剪贴板主题（颜色块按钮） ──────────────────────
    current_theme_name = config.get_clipboard_theme()

    theme_card = FSettingCard(
        FluentIcon.BRUSH,
        dialog.tr("Theme"),
        parent=grp,
    )
    dialog._clip_theme_btn = _make_color_btn(dialog)
    dialog._clip_theme_name = current_theme_name

    _apply_clip_theme_btn_style(dialog._clip_theme_btn, current_theme_name)

    def _show_theme_popup():
        from PySide6.QtWidgets import QMenu
        menu = QMenu(dialog)
        menu.setStyleSheet(theme_menu_style() + " QMenu { padding: 4px; }")
        theme_buttons = []

        def _update_btns(selected: str):
            for n, btn, accent, bg in theme_buttons:
                is_sel = n == selected
                btn.setText("✓" if is_sel else "")
                text_color = "#FFFFFF" if n == "dark" else theme_color("#333333", "#F3F3F3")
                bw = 2 if is_sel else 1
                bc = accent if is_sel else theme_color("#CCCCCC", "#4A4F57")
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                            stop:0 {bg}, stop:0.5 {bg},
                            stop:0.5 {accent}, stop:1 {accent});
                        border: {bw}px solid {bc};
                        border-radius: 3px;
                        color: {text_color};
                        font-size: 12px; font-weight: bold;
                        text-align: left; padding-left: 6px;
                    }}
                    QPushButton:hover {{ border: 2px solid {accent}; }}
                """)

        def _on_click(name: str):
            dialog._clip_theme_name = name
            _apply_clip_theme_btn_style(dialog._clip_theme_btn, name)
            theme_mgr.set_theme(name)
            _update_btns(name)
            menu.close()

        for tname, (accent, bg) in PRESET_THEME_SWATCHES.items():
            wa = QWidgetAction(menu)
            btn = PushButton(menu)
            btn.setFixedSize(_CTRL_W, _CTRL_H)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c, n=tname: _on_click(n))
            wa.setDefaultWidget(btn)
            menu.addAction(wa)
            theme_buttons.append((tname, btn, accent, bg))

        _update_btns(dialog._clip_theme_name)
        pos = dialog._clip_theme_btn.mapToGlobal(
            dialog._clip_theme_btn.rect().bottomLeft()
        )
        menu.popup(pos)

    dialog._clip_theme_btn.clicked.connect(_show_theme_popup)
    theme_card.hBoxLayout.addWidget(
        dialog._clip_theme_btn, 0, Qt.AlignmentFlag.AlignRight
    )
    theme_card.hBoxLayout.addSpacing(16)
    grp.addSettingCard(theme_card)

    # ── 字体大小 ─────────────────────────────────────
    font_card = FSettingCard(
        FluentIcon.FONT_SIZE,
        dialog.tr("Font Size"),
        parent=grp,
    )
    dialog._clip_font_combo = ComboBox(font_card)
    dialog._clip_font_combo.setFixedWidth(_CTRL_W)

    font_options = config.get_clipboard_font_size_options()
    current_font = config.get_clipboard_font_size()
    for size in font_options:
        dialog._clip_font_combo.addItem(f"{size}px", userData=size)
    idx = dialog._clip_font_combo.findData(current_font)
    if idx >= 0:
        dialog._clip_font_combo.setCurrentIndex(idx)

    def _on_font_changed(index):
        size = dialog._clip_font_combo.itemData(index)
        if size is not None:
            config.set_clipboard_font_size(size)
            theme_mgr.notify_font_size_changed(size)

    dialog._clip_font_combo.currentIndexChanged.connect(_on_font_changed)
    font_card.hBoxLayout.addWidget(
        dialog._clip_font_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    font_card.hBoxLayout.addSpacing(16)
    grp.addSettingCard(font_card)

    # ── 透明度 ───────────────────────────────────────
    opacity_card = FSettingCard(
        FluentIcon.TRANSPARENT,
        dialog.tr("Opacity"),
        parent=grp,
    )
    dialog._clip_opacity_combo = ComboBox(opacity_card)
    dialog._clip_opacity_combo.setFixedWidth(_CTRL_W)

    opacity_options = config.get_clipboard_window_opacity_options()
    current_opacity = config.get_clipboard_window_opacity()
    for percent in opacity_options:
        label = dialog.tr("Opaque") if percent == 0 else f"{percent}%"
        dialog._clip_opacity_combo.addItem(label, userData=percent)
    idx = dialog._clip_opacity_combo.findData(current_opacity)
    if idx >= 0:
        dialog._clip_opacity_combo.setCurrentIndex(idx)

    def _on_opacity_changed(index):
        percent = dialog._clip_opacity_combo.itemData(index)
        if percent is not None:
            config.set_clipboard_window_opacity(percent)
            theme_mgr.notify_opacity_changed(percent)

    dialog._clip_opacity_combo.currentIndexChanged.connect(_on_opacity_changed)
    opacity_card.hBoxLayout.addWidget(
        dialog._clip_opacity_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    opacity_card.hBoxLayout.addSpacing(16)
    grp.addSettingCard(opacity_card)
 
