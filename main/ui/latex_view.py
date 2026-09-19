# -*- coding: utf-8 -*-
"""公式显示控件：把 LaTeX 排版出来画在控件里。

控件只负责画，排版与解析在 ``ocr.latex_render``（那边是纯逻辑、能穷举测试）。主题变了
要重画：公式的颜色默认取界面正文色，浅色主题下画成白的就看不见了。
"""

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from core import safe_event
from core.ui_theme import get_ui_theme
from ui.fluent_lite import ui_tokens


class LatexView(QWidget):
    """一块居中显示公式的画布；``set_latex`` 换公式，尺寸跟着公式走。"""

    def __init__(self, latex: str = "", parent=None, *, pixel_size: int = 26,
                 padding: int = 10) -> None:
        super().__init__(parent)
        self._latex = latex or ""
        self._pixel_size = pixel_size
        self._padding = padding
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(pixel_size * 2 + padding * 2)
        # 构造时给的公式也要量高度：不量的话分数/根号会被下边缘裁掉（set_latex 只在
        # 公式真的变化时才量，首屏那条路径不经过它）
        self._update_height()
        get_ui_theme().theme_changed.connect(self._on_theme_changed)

    # ── 数据 ──

    def latex(self) -> str:
        return self._latex

    def set_latex(self, latex: str) -> None:
        latex = latex or ""
        if latex == self._latex:
            return
        self._latex = latex
        self._update_height()
        self.update()

    def _update_height(self) -> None:
        from ocr.latex_render import layout_size

        try:
            _width, height = layout_size(self._latex, pixel_size=self._pixel_size)
        except Exception:
            height = self._pixel_size * 2
        self.setMinimumHeight(int(height + self._padding * 2))

    def _on_theme_changed(self, _tokens=None) -> None:
        self.update()

    # ── 绘制 ──

    @safe_event
    def paintEvent(self, event):
        from ocr.latex_render import paint_latex

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        color = QColor(ui_tokens(self).text)
        paint_latex(painter, self._latex, QRectF(0, self._padding, self.width(),
                                                self.height() - self._padding * 2),
                    pixel_size=self._pixel_size, color=color)
        painter.end()


def latex_pixmap(latex: str, *, pixel_size: int = 26, color=None, dpr: float = 1.0):
    """把公式渲染成 QImage（复制成图片、导出时用）。"""
    from ocr.latex_render import render_latex_image

    return render_latex_image(latex, pixel_size=pixel_size, color=color,
                              device_pixel_ratio=dpr)


__all__ = ["LatexView", "latex_pixmap"]
