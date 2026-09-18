# -*- coding: utf-8 -*-
"""视频录制窗口 — 协调选区边框、悬浮工具栏与录制器。

状态机很小：``IDLE``（可调整选区）→ 点开始 → 录制中（选区边框转红、穿透，鼠标可以
继续操作被录的应用）→ 点停止 → 等编码器收尾 → 写盘并提示。

选区边框复用 GIF 录制那一份（``gif/overlay.py`` 的 ``CaptureOverlay``）：它的四边拖拽、
穿透切换、蓝/红配色正是这里需要的，另写一份只会多一处行为差。
"""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtWidgets import QApplication

from core.i18n import make_tr
from core.logger import T, log_debug, log_exception, log_info, log_warning
from gif.overlay import CaptureOverlay, OverlayMode

from .record_toolbar import VideoRecordToolbar
from .video_recorder import VideoOptions, VideoRecorder, VideoState

#: 界面文案（对话框）走 i18n；日志走 core.logger 的 T()
_tr = make_tr("VideoRecordWindow")

#: 容器 → 文件扩展名（保存对话框与文件名用）
_EXTENSIONS = {"mp4": "mp4", "mkv": "mkv", "mov": "mov", "avi": "avi"}


class VideoRecordWindow(QObject):
    """视频录制的协调者：选区 + 工具栏 + 录制器 + 保存提示。"""

    closed = Signal()

    def __init__(self, capture_rect: QRect, parent=None, *, show_result_dialog: bool = True):
        super().__init__(parent)
        from settings.tool_settings import get_tool_settings_manager

        self._config = get_tool_settings_manager()
        self._rect = QRect(capture_rect)
        self._show_result_dialog = show_result_dialog
        self._closing = False

        self._recorder = VideoRecorder(self)
        self._overlay = CaptureOverlay(self._rect)
        self._toolbar = VideoRecordToolbar(
            quality=self._config.get_video_quality(),
            fps=self._config.get_video_fps(),
        )

        self._overlay.rect_changed.connect(self._on_rect_changed)
        self._toolbar.start_requested.connect(self._on_start)
        self._toolbar.stop_requested.connect(self._on_stop)
        self._toolbar.pause_toggled.connect(self._on_pause_toggled)
        self._toolbar.quality_changed.connect(self._on_quality_changed)
        self._toolbar.fps_changed.connect(self._on_fps_changed)
        self._toolbar.move_requested.connect(self._on_move_requested)
        self._toolbar.drag_ended.connect(self._on_drag_ended)
        self._toolbar.close_requested.connect(self.close_all)

        self._recorder.duration_changed.connect(self._toolbar.set_elapsed)
        self._recorder.failed.connect(self._on_failed)
        self._recorder.finished.connect(self._on_finished)

        self._toolbar.reset_state()
        self._overlay.show()
        self._toolbar.show()
        self._keep_windows_visible()
        self._sync_geometry(self._rect)
        log_info(T("视频录制窗口已启动, 区域={rect}", rect=self._rect), "Video")

    def _keep_windows_visible(self) -> None:
        """让遮罩与工具栏在应用失去前台时也不隐藏。

        macOS 上 Qt 的 ``Qt.Tool`` 窗口是 NSPanel，默认 ``hidesOnDeactivate=True``：
        用户一去看别的程序（录屏时几乎必然发生），遮罩与工具栏就一起「消失」——
        屏幕上什么都没有，而录制其实还在跑。平台层这个能力在 Windows/Linux 上是空操作。
        """
        from core.platform import window_ops

        for widget in (self._overlay, self._toolbar):
            try:
                window_ops.keep_visible_when_inactive(widget)
            except Exception as e:
                log_warning(T("设置录制窗口失焦保持可见失败: {e}", e=e), "Video")

    # ── 只读访问（真机验证与测试用） ──

    @property
    def recorder(self) -> VideoRecorder:
        return self._recorder

    @property
    def overlay(self) -> CaptureOverlay:
        return self._overlay

    @property
    def toolbar(self) -> VideoRecordToolbar:
        return self._toolbar

    def capture_rect(self) -> QRect:
        return QRect(self._rect)

    # ── 位置同步 ──

    def _sync_geometry(self, rect: QRect, reposition: bool = True):
        self._rect = QRect(rect)
        self._overlay.update_rect(rect)
        # 录制器要用同一份区域：选区被拖动/缩放之后它得跟着变
        self._recorder.set_rect(rect)
        if reposition:
            self._reposition_toolbar()

    def _reposition_toolbar(self):
        """工具栏智能定位：优先选区下方 → 上方 → 左侧 → 右侧（与 GIF 录制同一套算法）。"""
        rect = self._rect
        toolbar = self._toolbar
        width, height, gap = toolbar.width(), toolbar.height(), 4

        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        screen_rect = screen.geometry()

        x_center = rect.left() + (rect.width() - width) // 2
        x_center = max(screen_rect.left(), min(x_center, screen_rect.right() - width))

        y_below = rect.bottom() + gap
        if y_below + height <= screen_rect.bottom():
            x, y = x_center, y_below
        elif rect.top() - height - gap >= screen_rect.top():
            x, y = x_center, rect.top() - height - gap
        else:
            y_mid = rect.top() + (rect.height() - height) // 2
            y_mid = max(screen_rect.top(), min(y_mid, screen_rect.bottom() - height))
            if rect.left() - width - gap >= screen_rect.left():
                x, y = rect.left() - width - gap, y_mid
            else:
                x = min(rect.right() + gap, screen_rect.right() - width)
                x, y = max(screen_rect.left(), x), y_mid
        toolbar.move(x, y)

    def _on_rect_changed(self, rect: QRect):
        self._sync_geometry(rect)

    def _on_move_requested(self, delta: QPoint):
        """拖动整个选区 + 工具栏；松手后再按屏幕边界重新定位。"""
        self._sync_geometry(self._rect.translated(delta), reposition=False)
        self._toolbar.move(self._toolbar.pos() + delta)

    def _on_drag_ended(self):
        self._reposition_toolbar()

    # ── 录制 ──

    def _on_start(self):
        options = self._build_options()
        if not self._recorder.start(options):
            # 起不来时把工具栏退回「未录制」，否则用户会以为正在录
            self._toolbar.reset_state()
            return
        self._overlay.set_recording(True)
        # 录制中让选区能被点穿：用户还要操作被录的那个程序
        self._overlay.set_mode(OverlayMode.PASSTHROUGH)

    def _on_stop(self):
        self._recorder.stop()

    def _on_pause_toggled(self, paused: bool):
        if paused:
            self._recorder.pause()
        else:
            self._recorder.resume()

    def _on_failed(self, message: str):
        self._toolbar.reset_state()
        self._overlay.set_recording(False)
        self._overlay.set_mode(OverlayMode.RESIZE)
        if self._show_result_dialog:
            from ui.dialogs import show_warning_dialog

            show_warning_dialog(None, _tr("视频录制"), message)

    def _on_finished(self, path: str):
        self._toolbar.reset_state()
        self._overlay.set_recording(False)
        if self._show_result_dialog:
            self._show_result(path)
        self._teardown()

    def _on_quality_changed(self, quality: str):
        self._config.set_video_quality(quality)
        log_debug(T("视频清晰度切换: {quality}", quality=quality), "Video")

    def _on_fps_changed(self, fps: int):
        self._config.set_video_fps(fps)
        log_debug(T("视频帧率切换: {fps}", fps=fps), "Video")

    # ── 参数与落盘 ──

    def _build_options(self) -> VideoOptions:
        """按当前设置与工具栏选择组装一次录制的参数。"""
        container = self._config.get_video_container()
        return VideoOptions(
            container=container,
            codec=self._config.get_video_codec(),
            fps=self._toolbar.current_fps(),
            quality=self._toolbar.current_quality(),
            bitrate_mbps=self._config.get_video_bitrate_mbps(),
            audio=self._config.get_video_audio() == "microphone",
            audio_device=self._config.get_video_audio_device(),
            max_duration_s=self._config.get_video_max_duration_s(),
            output_path=self._next_output_path(container),
        )

    def _save_directory(self) -> str:
        """视频保存目录；没配就用截图目录，都没有时退回用户主目录。"""
        from core.platform import paths

        for getter in (self._config.get_video_save_path, self._config.get_screenshot_save_path):
            try:
                folder = str(getter() or "").strip()
            except Exception as e:
                log_exception(e, T("读取视频保存目录"))
                folder = ""
            if folder:
                return folder
        return paths.default_screenshot_dir() or os.path.expanduser("~")

    def _next_output_path(self, container: str) -> str:
        folder = self._save_directory()
        extension = _EXTENSIONS.get(container, "mp4")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            log_exception(e, T("创建视频保存目录"))
        return os.path.join(folder, f"jietuba_video_{stamp}.{extension}")

    def _show_result(self, path: str):
        """录完给一条明确的落点提示，并把「打开文件夹 / 复制路径」放在同一处。"""
        from PySide6.QtWidgets import QDialogButtonBox

        from core.platform import shell
        from ui.dialogs import show_custom_confirm_dialog

        folder = os.path.dirname(path)
        choice = show_custom_confirm_dialog(
            None,
            _tr("视频录制"),
            _tr("视频已保存到:\n{path}").format(path=path),
            [
                {"id": "open", "text": _tr("打开文件夹"),
                 "role": QDialogButtonBox.ButtonRole.ActionRole},
                {"id": "copy", "text": _tr("复制路径"),
                 "role": QDialogButtonBox.ButtonRole.ActionRole},
                {"id": "ok", "text": _tr("确定"),
                 "role": QDialogButtonBox.ButtonRole.AcceptRole, "default": True},
            ],
        )
        if choice == "open":
            shell.open_path(folder)
        elif choice == "copy":
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(path)

    # ── 生命周期 ──

    def close_all(self):
        """关闭录制窗口；正在录时先停，收尾完成后再收窗口。"""
        if self._recorder.state in (VideoState.RECORDING, VideoState.PAUSED):
            self._closing = True
            self._recorder.stop()
            return
        self._teardown()

    def _teardown(self):
        for widget in (self._overlay, self._toolbar):
            try:
                widget.close()
                widget.deleteLater()
            except Exception as e:
                log_warning(T("关闭录制窗口部件失败: {e}", e=e), "Video")
        log_debug(T("视频录制窗口已关闭"), "Video")
        self.closed.emit()


def start_video_record_window(capture_rect: QRect, parent=None) -> VideoRecordWindow:
    """创建并挂到 QApplication 上（截图窗口关掉之后，得有人持有它）。"""
    window = VideoRecordWindow(capture_rect, parent)
    app = QApplication.instance()
    if app is not None:
        previous = getattr(app, "_video_window", None)
        if previous is not None and previous is not window:
            try:
                previous.close_all()
            except Exception as e:
                log_exception(e, T("关闭旧的视频录制窗口"))
        app._video_window = window
        window.closed.connect(lambda: setattr(app, "_video_window", None))
    return window
