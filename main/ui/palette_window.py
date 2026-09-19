# -*- coding: utf-8 -*-
"""配色提取结果窗口：每个主色一块色块，点一下复制那个色值，也可以一次全复制。

色块的颜色由数据决定（用户截的图里有什么颜色就画什么颜色），所以色值文字用
``core.theme.contrast_ink`` 现算黑或白——固定用主题文字色的话，截到浅黄底时会看不清。
窗口本身的卡片、按钮、滚动条照旧跟应用主题走。
"""

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget,
)

from core import safe_event
from core.i18n import make_tr
from core.logger import T, log_info
from core.palette import PaletteColor, palette_text
from core.theme import contrast_ink
from core.ui_theme import get_ui_theme
from ui.dialogs import track_modeless_dialog
from ui.fluent_lite import (
    CaptionLabel, FluentTitleBar, FrostedFramelessDialog, PushButton, ui_tokens,
)

_tr = make_tr("PaletteWindow")

#: 一行放几个色块。3 个刚好让色块大到能看清、窗口又不宽
_COLUMNS = 3

_SWATCH_W = 150
_SWATCH_H = 84


class _Swatch(QWidget):
    """一块色块：上面是颜色块，下面压着色值与占比，点一下把色值复制走。"""

    clicked = Signal(str)

    def __init__(self, color: PaletteColor, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(_SWATCH_W, _SWATCH_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_tr("Copy %1").replace("%1", color.hex))

    @safe_event
    def paintEvent(self, event):
        tokens = ui_tokens(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(self.color.hex)
        painter.setBrush(fill)
        painter.setPen(QPen(QColor(tokens.window_border), 1))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(contrast_ink(fill))
        font = painter.font()
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(10, 0, -10, -20), Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignBottom, self.color.hex)

        font.setBold(False)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(rect.adjusted(10, 20, -10, 0), Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignTop, f"{self.color.ratio * 100:.1f}%")

    @safe_event
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.color.hex)


class PaletteWindow(FrostedFramelessDialog):
    """配色结果窗口。``colors`` 为空时列一个「没提取到颜色」的说明，窗口照样能开。"""

    WIDTH = 520

    def __init__(self, colors=None, parent=None):
        super().__init__(parent)
        self.colors = list(colors or [])
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        title_bar.iconLabel.hide()
        # 隐藏图标后标题会紧贴窗口左边，补回原生标题栏的留白
        title_bar.hBoxLayout.setContentsMargins(12, 0, 0, 0)
        self.setWindowTitle(_tr("Palette"))

        self.status_label = CaptionLabel(
            _tr("%1 colors").replace("%1", str(len(self.colors))) if self.colors
            else _tr("No color could be extracted from this image."), self)

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.swatches = []
        for index, color in enumerate(self.colors):
            swatch = _Swatch(color, self)
            swatch.clicked.connect(self.copy_color)
            self.grid.addWidget(swatch, index // _COLUMNS, index % _COLUMNS)
            self.swatches.append(swatch)

        self.copy_all_button = PushButton(_tr("Copy All"), self)
        self.copy_all_button.setEnabled(bool(self.colors))
        self.copy_all_button.clicked.connect(self.copy_all)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.copy_all_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, title_bar.height() + 4, 12, 12)
        root.setSpacing(10)
        root.addLayout(self.grid)
        root.addLayout(footer)

        self._apply_theme()
        get_ui_theme().theme_changed.connect(self._apply_theme)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._place()

    # ── 行为 ──

    def copy_color(self, hex_value: str) -> bool:
        """把一个色值放进剪贴板；返回值是「是否真的复制了」。"""
        if not hex_value:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(hex_value)
        self._flash_status(_tr("Copied %1").replace("%1", hex_value))
        log_info(T("配色已复制: {hex_value}", hex_value=hex_value), "Palette")
        return True

    def copy_all(self) -> bool:
        text = palette_text(self.colors)
        if not text:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        self._flash_status(_tr("Copied %1 colors").replace("%1", str(len(self.colors))))
        log_info(T("已复制 {count} 个配色色值", count=len(self.colors)), "Palette")
        return True

    def _flash_status(self, message: str) -> None:
        """状态行显示一句反馈，1.5 秒后回到常驻文案（按钮的「已复制」也走这套）。"""
        default = (_tr("%1 colors").replace("%1", str(len(self.colors))) if self.colors
                   else _tr("No color could be extracted from this image."))
        self.status_label.setText(message)
        QTimer.singleShot(1500, lambda: self.status_label.setText(default))

    # ── 外观 ──

    def _apply_theme(self, _tokens=None):
        tokens = ui_tokens(self)
        self.setStyleSheet(
            f"FrostedFramelessDialog {{ background: {tokens.window}; }}"
            f" QLabel {{ color: {tokens.text}; }}"
        )

    def _place(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        rows = max(1, (len(self.colors) + _COLUMNS - 1) // _COLUMNS)
        self.resize(
            min(self.WIDTH, available.width()),
            min(80 + rows * (_SWATCH_H + 8) + 56, available.height()),
        )
        # 色块少的时候让宽度跟着列数收，免得右边空一大块
        if self.colors:
            from PySide6.QtWidgets import QLayout

            self.grid.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.move(available.center() - self.rect().center())


def show_palette_result(colors, parent=None):
    """弹出配色结果窗口。取不到颜色时也照常开窗——让用户知道「这张图没提取到」比什么都不弹清楚。"""
    window = PaletteWindow(colors, parent)
    track_modeless_dialog(window)
    window.show()
    window.raise_()
    window.activateWindow()
    return window
