# -*- coding: utf-8 -*-
"""全局鼠标动作截图后的遮罩提示。

整屏压暗、把抓到的区域留亮，闪一下就走。**只能抓完再显示**：把窗口排除在截图之外的
能力只有 Windows 有（``core/platform/window_ops.set_exclude_from_capture``，macOS
登记为 NONE），先显示再抓会把遮罩本身拍进截图。
"""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from core import safe_event
from ui.fluent_lite.theme import ACCENT

#: 遮罩停留时长：够看见、又不至于挡着下一步操作
MASK_DURATION_MS = 180


class CaptureMaskOverlay(QWidget):
    """全屏遮罩，中心（``rect``）留亮。"""

    def __init__(self, rect: QRectF, duration_ms: int = MASK_DURATION_MS):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # 覆盖整个虚拟桌面（多显示器时不能只盖住主屏），再把矩形转成窗口内坐标
        primary = QGuiApplication.primaryScreen()
        geometry = primary.virtualGeometry() if primary else self.geometry()
        self.setGeometry(geometry)
        self._hole = QRectF(rect).translated(-geometry.x(), -geometry.y())

        QTimer.singleShot(max(0, int(duration_ms)), self.close)

    @safe_event
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        full = QPainterPath()
        full.addRect(QRectF(self.rect()))
        hole_path = QPainterPath()
        hole_path.addRect(self._hole.intersected(QRectF(self.rect())))
        painter.fillPath(full.subtracted(hole_path), QColor(0, 0, 0, 80))

        if not self._hole.isEmpty():
            painter.setPen(QColor(ACCENT))
            painter.drawRoundedRect(self._hole, 2, 2)


def flash_capture_mask(rect: QRectF) -> CaptureMaskOverlay | None:
    """按给定屏幕矩形闪一下遮罩；矩形无效时不显示。

    引用挂在 QApplication 上，否则局部变量一出作用域窗口就被回收、根本看不见。
    """
    from PySide6.QtWidgets import QApplication

    if rect is None or QRectF(rect).isEmpty():
        return None

    overlay = CaptureMaskOverlay(QRectF(rect), MASK_DURATION_MS)
    app = QApplication.instance()
    if app is not None:
        app._capture_mask_overlay = overlay
        overlay.destroyed.connect(lambda: setattr(app, "_capture_mask_overlay", None))
    overlay.show()
    return overlay
