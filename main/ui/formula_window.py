# -*- coding: utf-8 -*-
"""公式结果窗口：上面是排版好的公式，下面是可改的 LaTeX 源码。

为什么不是只弹一个文本框：识别结果是一串 ``\\frac``/``\\sqrt``，直接看源码没法确认识别得
对不对；把公式画出来一眼就知道，改源码时预览还跟着变（识别偶尔错一个字母，就地改完再复制
比复制出去改顺手）。

渲染器是自己写的（见 ``ocr.latex_render``），不引 QtWebEngine / matplotlib，理由写在那边。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QVBoxLayout,
)

from core import safe_event
from core.i18n import make_tr
from core.logger import T, log_exception, log_info
from core.ui_theme import get_ui_theme
from ui.dialogs import track_modeless_dialog
from ui.fluent_lite import (
    FONT_FAMILY, CaptionLabel, FluentTitleBar, FrostedFramelessDialog, PrimaryPushButton,
    PushButton, TextEdit, scrollbar_qss, ui_tokens,
)

from .latex_view import LatexView, latex_pixmap

_tr = make_tr("FormulaResult")

#: 源码改完等这么久再重排，避免每敲一个字符就排一次
_PREVIEW_DELAY_MS = 250


def show_formula_result(latex: str, parent=None):
    """弹出公式结果窗口（识别到 LaTeX 时走这里）。"""
    window = FormulaWindow(latex, parent)
    track_modeless_dialog(window)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


class FormulaWindow(FrostedFramelessDialog):
    """公式结果窗口。窗口销毁时不需要清理后台任务——这里没有线程。"""

    WIDTH = 560
    HEIGHT = 420

    def __init__(self, latex: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        title_bar.iconLabel.hide()
        title_bar.hBoxLayout.setContentsMargins(12, 0, 0, 0)
        self.setWindowTitle(_tr("Formula Result"))

        self.preview = LatexView(latex, self)

        self.status_label = CaptionLabel(_tr("Edit the LaTeX below if the preview is wrong."),
                                        self)
        self.source_edit = TextEdit(self)
        self.source_edit.setAcceptRichText(False)
        self.source_edit.setPlainText(latex)

        self.copy_button = PrimaryPushButton(_tr("Copy LaTeX"), self)
        self.copy_button.clicked.connect(self.copy_latex)
        self.copy_image_button = PushButton(_tr("Copy as Image"), self)
        self.copy_image_button.clicked.connect(self.copy_image)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.copy_image_button)
        footer.addWidget(self.copy_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, title_bar.height() + 4, 12, 12)
        root.setSpacing(10)
        root.addWidget(self.preview)
        root.addWidget(self.source_edit, 1)
        root.addLayout(footer)

        # 改源码 → 预览跟着变；用单次定时器合并连续的输入
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self.source_edit.textChanged.connect(self._schedule_preview)

        self._apply_theme()
        get_ui_theme().theme_changed.connect(self._apply_theme)
        self.resize(self.WIDTH, self.HEIGHT)

    # ── 行为 ──

    def latex(self) -> str:
        return self.source_edit.toPlainText()

    def _schedule_preview(self) -> None:
        self._preview_timer.start(_PREVIEW_DELAY_MS)

    def _refresh_preview(self) -> bool:
        """把预览换成当前源码；源码坏掉时保留上一次的排版（渲染器本身不会抛，这里是兜底）。"""
        try:
            self.preview.set_latex(self.latex())
            return True
        except Exception as e:
            log_exception(e, T("重排公式预览"))
            return False

    def copy_latex(self) -> bool:
        text = self.latex().strip()
        if not text:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        self._flash_status(_tr("Copied"))
        log_info(T("公式已复制到剪贴板（{count} 字）", count=len(text)), "FormulaResult")
        return True

    def copy_image(self) -> bool:
        """把公式渲染成图片放进剪贴板（粘到文档里用）。"""
        text = self.latex().strip()
        if not text:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        try:
            image = latex_pixmap(text, pixel_size=48, dpr=self.devicePixelRatioF())
        except Exception as e:
            log_exception(e, T("渲染公式图片"))
            return False
        if image is None or image.isNull():
            return False
        clipboard.setImage(image)
        self._flash_status(_tr("Copied"))
        log_info(T("公式图片已复制到剪贴板"), "FormulaResult")
        return True

    def _flash_status(self, message: str) -> None:
        self.status_label.setText(message)
        QTimer.singleShot(
            1500, lambda: self.status_label.setText(
                _tr("Edit the LaTeX below if the preview is wrong.")))

    # ── 外观 ──

    def _apply_theme(self, _tokens=None):
        tokens = ui_tokens(self)
        self.source_edit.setStyleSheet(
            f"QTextEdit {{ background: {tokens.input_background}; color: {tokens.text}; "
            f"border: 1px solid {tokens.border}; border-radius: 6px; padding: 8px 10px; "
            f"font: 13px {FONT_FAMILY}; selection-background-color: {tokens.accent}; "
            f"selection-color: {tokens.selected_text}; }}"
            + scrollbar_qss(self)
        )
        self.setStyleSheet(
            f"FrostedFramelessDialog {{ background: {tokens.window}; }}"
        )

    @safe_event
    def closeEvent(self, event):
        self._preview_timer.stop()
        super().closeEvent(event)
