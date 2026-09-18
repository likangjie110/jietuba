# -*- coding: utf-8 -*-
"""
设置窗口 — 共享 UI 组件库
"""
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from core import safe_event
from core.ui_theme import get_ui_theme

from ui.fluent_lite import (
    SwitchButton, SimpleCardWidget, SwitchSettingCard as _SwitchSettingCard, SettingCardGroup as _SettingCardGroupBase,
)


def theme_color(light: str, dark: str) -> str:
    """Return one of two values for the effective application theme."""
    return dark if get_ui_theme().is_dark else light


# 下面这几个「某处底色」都从当前 token 取，不写死。
#
# 为什么必须跟 token 走：token 现在是「内置主题 + 用户皮肤」的合成结果（见
# core/ui_theme.py 的 resolve_skin）。这些地方原先按 light/dark 二选一写死颜色，
# 用户在外观页把窗口底色改成深色后，内容区仍是原来的浅色，而同窗口的文字走
# tokens.text —— 结果就是浅底上写浅字，肉眼读不出来。

def theme_surface_color() -> str:
    """设置窗口主内容面（右区）的底色 —— 就是窗口底色本身。"""
    return get_ui_theme().tokens.window


def theme_sidebar_color() -> str:
    """左侧导航区的底色：窗口底色上再压一层细微表面色。"""
    return get_ui_theme().tokens.surface_subtle


def theme_border_color() -> str:
    return get_ui_theme().tokens.border


def theme_input_background() -> str:
    return get_ui_theme().tokens.input_background


def theme_popup_background() -> str:
    return get_ui_theme().tokens.popup_background


def theme_popup_hover_background() -> str:
    return get_ui_theme().tokens.popup_hover


def theme_text_style(font_size: int = 13, bold: bool = False, extra: str = "") -> str:
    weight = " font-weight: 600;" if bold else ""
    suffix = f" {extra.strip()}" if extra.strip() else ""
    color = get_ui_theme().tokens.text
    return f"font-size: {font_size}px; color: {color}; background: transparent;{weight}{suffix}"


def theme_caption_style(font_size: int = 12, extra: str = "") -> str:
    suffix = f" {extra.strip()}" if extra.strip() else ""
    color = get_ui_theme().tokens.text_muted
    return f"font-size: {font_size}px; color: {color}; background: transparent;{suffix}"


def apply_theme_text_style(
    widget: QWidget,
    font_size: int = 13,
    bold: bool = False,
    extra: str = "",
    caption: bool = False,
):
    """Apply and register a semantic text style for runtime refresh."""
    widget._ui_theme_text_spec = (font_size, bold, extra, caption)
    style = (
        theme_caption_style(font_size, extra)
        if caption
        else theme_text_style(font_size, bold, extra)
    )
    widget.setStyleSheet(style)


def refresh_theme_widget_styles(root: QWidget):
    """Refresh semantic text styles registered below a top-level widget."""
    for widget in (root, *root.findChildren(QWidget)):
        spec = getattr(widget, "_ui_theme_text_spec", None)
        if spec is not None:
            apply_theme_text_style(widget, *spec)


def theme_menu_style() -> str:
    return f"""
        QMenu {{
            background-color: {theme_popup_background()};
            color: {get_ui_theme().tokens.text};
            border: 1px solid {theme_border_color()};
            border-radius: 6px;
            padding: 4px 0;
        }}
        QMenu::item {{
            padding: 6px 20px;
            font-size: 13px;
            color: {get_ui_theme().tokens.text};
            background: transparent;
        }}
        QMenu::item:selected {{
            background-color: {theme_popup_hover_background()};
        }}
    """


class SettingCardGroup(_SettingCardGroupBase):
    """调整 SettingCardGroup 的最小高度计算。"""

    def addSettingCard(self, card: QWidget):
        super().addSettingCard(card)

    @safe_event
    def showEvent(self, e):
        super().showEvent(e)
        # 显示后 card.height() 才准确，重新算 minimumHeight
        h = self.cardLayout.heightForWidth(self.width()) + 46
        self.setMinimumHeight(h)

# ── 「一行一个条目」的列表块 ──────────────────────────
# 全局鼠标页（动作绑定）与快捷键/动作页（动作热键）共用同一套行高，两页都要自己算
# 容器高度（WhiteCard 的高度由内容决定，不跟着布局走）。
ROW_H = 46
ROW_SPACING = 8
CARD_PADDING = 24
EMPTY_HINT_H = 28


def rows_block_height(count: int) -> int:
    """条目块的高度；空表时只留一行「暂无条目」提示的位置。"""
    if count <= 0:
        return EMPTY_HINT_H
    return count * ROW_H + (count - 1) * ROW_SPACING


def make_row(label, ctrl_widget: QWidget) -> QHBoxLayout:
    """创建统一的「标签 — 控件」行。"""
    row = QHBoxLayout()
    row.setSpacing(10)
    if isinstance(label, str):
        lbl = QLabel(label)
        apply_theme_text_style(lbl, 13)
    else:
        lbl = label
    row.addWidget(lbl, 1)
    row.addWidget(ctrl_widget)
    return row


def make_card_title(text: str) -> QLabel:
    """创建卡片标题"""
    lbl = QLabel(text)
    apply_theme_text_style(lbl, 14, bold=True)
    return lbl


def adjust_button_width(button, min_width: int = 0, horizontal_padding: int = 28):
    """按当前文字和图标内容调整按钮宽度。"""
    button.ensurePolished()
    content_width = button.sizeHint().width() + horizontal_padding
    button.setMinimumWidth(max(min_width, content_width))


class ToggleSwitch(SwitchButton):
    """Fluent SwitchButton 兼容层。"""
    toggled = Signal(bool)

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent=parent)
        self.setOnText('')
        self.setOffText('')
        self.checkedChanged.connect(self.toggled)


class SettingCard(QFrame):
    """白底圆角卡片容器 — 旧版兼容"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet("""
            #Card {
                background-color: #FFFFFF;
                border-radius: 8px;
                border: 1px solid #E5E5E5;
            }
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)


class HLine(QFrame):
    """分割线"""
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setStyleSheet("background-color: #F0F0F0; border: none; max-height: 1px;")


# ── Fluent 辅助 ──────────────────────────────────────

class FluentCard(SimpleCardWidget):
    """基于 SimpleCardWidget 的自由布局 Fluent 卡片。
    用于需要复杂自定义内容的场景（路径+按钮、多行输入等）。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.vBoxLayout.setSpacing(8)


class TransparentCard(QFrame):
    """透明卡片 — 用于 SettingCardGroup 内需要多行/复杂布局的场景。

    不绘制背景和边框（避免灰色方块），但仍是独立 QFrame，
    ExpandLayout 可以正确定位它。使用后需调用 setFixedHeight() 确保
    ExpandLayout 能拿到正确高度。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("background: transparent; border: none;")


class WhiteCard(QFrame):
    """自定义可变高度卡片。

    使用轻量绘制逻辑避免样式表导致的局部背景异常，
    同时不受旧组件固定高度限制。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def minimumSizeHint(self):
        return QSize(0, max(self.minimumHeight(), self.layout().minimumSize().height() if self.layout() else 0))

    @safe_event
    def paintEvent(self, e):
        t = get_ui_theme().tokens
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        if t.is_dark:
            painter.setBrush(QColor(43, 47, 52, 245))
            painter.setPen(QColor(255, 255, 255, 31))
        else:
            painter.setBrush(QColor(255, 255, 255, 46))
            painter.setPen(QColor(255, 255, 255, 76))
        painter.drawRoundedRect(rect, 11, 11)


def make_switch_card(dialog, icon, title, content, checked, attr_name, parent=None):
    """创建 SwitchSettingCard 并将其绑定到 dialog 属性。

    SwitchSettingCard 自带 isChecked()/setChecked()，
    所以直接赋给 dialog.attr_name 即可兼容 accept/reset/refresh。
    """
    card = _SwitchSettingCard(icon, title, content, parent=parent)
    card.setChecked(checked)
    setattr(dialog, attr_name, card)
    return card
 
