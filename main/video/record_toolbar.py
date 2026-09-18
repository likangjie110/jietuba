# -*- coding: utf-8 -*-
"""视频录制的悬浮工具栏：清晰度 / 帧率 / 开始·暂停·停止 / 时长 / 拖动 / 关闭。

与 GIF 的录制工具栏长得像是有意的（同一套图标与按钮尺寸），但这里是独立的一份：
GIF 那份挂着一整排绘制工具与二级设置面板，而视频录制没有「录制中在画面上涂画」这条
需求，共用一个会逼出「这个按钮在视频模式下没意义」的分支。
小部件助手仍复用 ``gif/_widgets.py``（图标渲染与下拉按钮，两处行为要一致）。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core import safe_event
from core.i18n import make_tr
from gif._widgets import ClickMenuButton as _ClickMenuButton, svg_icon as _svg_icon

_tr = make_tr("VideoRecordToolbar")

_BUTTON_SIZE = 32
_ICON_SIZE = 20

#: 清晰度档位：(菜单文案, 档位值)；``source`` = 跟随录制区域的物理像素
QUALITY_CHOICES = (
    ("跟随区域", "source"),
    ("1080p", "1080p"),
    ("720p", "720p"),
    ("480p", "480p"),
)


class _DragHandle(QWidget):
    """工具栏的拖动手柄：按下拖动，位移交给窗口去搬工具栏与选区。"""

    move_requested = Signal(QPoint)
    drag_ended = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip(_tr("拖动工具栏"))
        self._origin = None

    @safe_event
    def paintEvent(self, _event):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#888888"))
        # 六个点：两个竖列，一眼看出这是可以拖的地方
        for column in (0, 1):
            for row in range(3):
                painter.drawEllipse(QRectF(10 + column * 7, 9 + row * 7, 2.4, 2.4))

    @safe_event
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()

    @safe_event
    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        current = event.globalPosition().toPoint()
        self.move_requested.emit(current - self._origin)
        self._origin = current

    @safe_event
    def mouseReleaseEvent(self, event):
        if self._origin is not None:
            self._origin = None
            self.drag_ended.emit()


class VideoRecordToolbar(QWidget):
    """录制阶段的悬浮工具栏（只做视频录制这一件事）。"""

    start_requested = Signal()
    stop_requested = Signal()
    pause_toggled = Signal(bool)
    quality_changed = Signal(str)
    fps_changed = Signal(int)
    move_requested = Signal(QPoint)
    drag_ended = Signal()
    close_requested = Signal()

    def __init__(self, quality: str = "source", fps: int = 30, parent=None):
        super().__init__(parent)
        self._recording = False
        self._paused = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._build_ui(quality, fps)

    # ── 界面 ──

    def _build_ui(self, quality: str, fps: int):
        container = QWidget(self)
        container.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 2px solid #333;
                border-radius: 6px;
            }
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background: rgba(0,0,0,0.06); }
            QPushButton:pressed { background: rgba(0,0,0,0.12); }
            QPushButton:disabled { opacity: 0.4; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        from settings.tool_settings import get_tool_settings_manager

        config = get_tool_settings_manager()

        # 清晰度
        quality_index = next(
            (i for i, (_label, value) in enumerate(QUALITY_CHOICES) if value == quality), 0
        )
        self._quality_btn = _ClickMenuButton(
            options=list(QUALITY_CHOICES), default_index=quality_index
        )
        self._quality_btn.option_selected.connect(self.quality_changed.emit)
        layout.addWidget(self._quality_btn)

        # 帧率
        options = [(f"{value} fps", value) for value in config.get_video_fps_options()]
        fps_index = next((i for i, (_label, value) in enumerate(options) if value == fps), 0)
        self._fps_btn = _ClickMenuButton(options=options, default_index=fps_index)
        self._fps_btn.option_selected.connect(self._on_fps_selected)
        layout.addWidget(self._fps_btn)

        # 开始 / 停止
        self._record_btn = QPushButton()
        self._record_btn.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._record_btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._record_btn.setIcon(_svg_icon("开始录制.svg"))
        self._record_btn.setToolTip(_tr("开始录制"))
        self._record_btn.clicked.connect(self._on_record_clicked)
        layout.addWidget(self._record_btn)

        # 暂停 / 恢复
        self._pause_btn = QPushButton()
        self._pause_btn.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._pause_btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._pause_btn.setIcon(_svg_icon("暂停不可.svg"))
        self._pause_btn.setToolTip(_tr("暂停录制"))
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        layout.addWidget(self._pause_btn)

        self._time_label = QLabel("00:00")
        self._time_label.setStyleSheet(
            "color: #666; font-size: 12px; border: none; min-width: 38px;"
        )
        layout.addWidget(self._time_label)

        separator = QWidget()
        separator.setFixedSize(1, 24)
        separator.setStyleSheet("background: #ccc; border: none;")
        layout.addWidget(separator)

        # 拖动 / 关闭
        self._drag_handle = _DragHandle()
        self._drag_handle.move_requested.connect(self.move_requested.emit)
        self._drag_handle.drag_ended.connect(self.drag_ended.emit)
        layout.addWidget(self._drag_handle)

        close_btn = QPushButton()
        close_btn.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        close_btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        close_btn.setIcon(_svg_icon("关闭.svg"))
        close_btn.setToolTip(_tr("关闭"))
        close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(close_btn)

        self.adjustSize()

    # ── 状态 ──

    def current_quality(self) -> str:
        return self._quality_btn.current_value()

    def current_fps(self) -> int:
        return self._fps_btn.current_value()

    def set_elapsed(self, milliseconds: int) -> None:
        seconds = max(0, int(milliseconds // 1000))
        self._time_label.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")

    def set_limits_enabled(self, enabled: bool) -> None:
        """录制中不许改清晰度/帧率（改了也不会对这次录制生效）。"""
        self._quality_btn.set_enabled(enabled)
        self._fps_btn.set_enabled(enabled)

    def reset_state(self) -> None:
        """回到「还没开始录」的外观。"""
        self._recording = False
        self._paused = False
        self._record_btn.setIcon(_svg_icon("开始录制.svg"))
        self._record_btn.setToolTip(_tr("开始录制"))
        self._pause_btn.setEnabled(False)
        self._pause_btn.setIcon(_svg_icon("暂停不可.svg"))
        self._pause_btn.setToolTip(_tr("暂停录制"))
        self.set_limits_enabled(True)
        self.set_elapsed(0)

    # ── 交互 ──

    def _on_record_clicked(self):
        if not self._recording:
            self._recording = True
            self._paused = False
            self._record_btn.setIcon(_svg_icon("结束录制.svg"))
            self._record_btn.setToolTip(_tr("停止录制"))
            self._pause_btn.setEnabled(True)
            self._pause_btn.setIcon(_svg_icon("暂停录制.svg"))
            self.set_limits_enabled(False)
            self.start_requested.emit()
        else:
            self._recording = False
            self._paused = False
            self.reset_state()
            self.stop_requested.emit()

    def _on_pause_clicked(self):
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.setIcon(_svg_icon("重开录制.svg"))
            self._pause_btn.setToolTip(_tr("恢复录制"))
        else:
            self._pause_btn.setIcon(_svg_icon("暂停录制.svg"))
            self._pause_btn.setToolTip(_tr("暂停录制"))
        self.pause_toggled.emit(self._paused)

    def _on_fps_selected(self, fps: int):
        self.fps_changed.emit(int(fps))
