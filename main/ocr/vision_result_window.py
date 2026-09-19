# -*- coding: utf-8 -*-
"""视觉模型结果窗口：一张图、一个任务模板下拉、一个「重跑」，结果可改可复制。

为什么要留着那张图：视觉模型给的结果不一定是最合适的解读——同一个选区可能「先当表格读、
再让它解释代码」。以前换一种解读只能重新截一次图（甚至得先去设置里改模板），这里把图和
结果放在同一个窗口里，换个模板点一下就好。

请求在后台线程跑（一次调用可能几十秒），窗口先出来显示「识别中…」；结果回来填进文本框，
用户改完再复制（模型偶尔多写一句解释，就地删掉比复制出去再改顺手）。公式模板的结果额外
显示一块排版预览（渲染器见 ``ocr.latex_render``）。
"""

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout

from core import safe_event
from core.i18n import make_tr
from core.logger import T, log_debug, log_exception, log_info, log_warning
from core.ui_theme import get_ui_theme
from ui.dialogs import track_modeless_dialog
from ui.fluent_lite import (
    FONT_FAMILY, CaptionLabel, ComboBox, FluentTitleBar, FrostedFramelessDialog,
    PrimaryPushButton, PushButton, TextEdit, scrollbar_qss, ui_tokens,
)

_tr = make_tr("VisionResultWindow")

#: 请求线程的引用：线程跑完自己回收，但对象在 Python 里也要有人拿着
_threads = []


class _ConvertThread(QThread):
    """在后台调视觉模型；只发结果，不碰界面对象。"""

    converted = Signal(object)

    def __init__(self, image, model, *, task: str, target: str):
        super().__init__()
        self._image = image
        self._model = model
        self._task = task
        self._target = target

    def run(self):
        try:
            from ocr.vision_models import convert_image

            result = convert_image(self._image, self._model, target=self._target,
                                   task=self._task)
        except Exception as e:                                   # pragma: no cover - 兜底
            log_exception(e, T("调用视觉模型"))
            from ocr.vision_models import VisionResult

            result = VisionResult(False, error=T("调用视觉模型失败: {e}", e=e).render())
        self.converted.emit(result)
        self._image = None


def show_vision_result(image, model, *, task=None, target="markdown", initial_text="",
                       parent=None):
    """弹出视觉模型结果窗口；``model`` 为空时窗口照样开（提示去设置里配模型）。"""
    window = VisionResultWindow(image, model, task=task, target=target,
                               initial_text=initial_text, parent=parent)
    track_modeless_dialog(window)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


class VisionResultWindow(FrostedFramelessDialog):
    WIDTH = 560
    HEIGHT = 480

    def __init__(self, image, model, *, task=None, target="markdown", initial_text="",
                 parent=None):
        super().__init__(parent)
        from ocr.vision_models import DEFAULT_TASK, TASKS

        self.image = image
        self.model = model
        self.target = target
        self._thread = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        title_bar.iconLabel.hide()
        title_bar.hBoxLayout.setContentsMargins(12, 0, 0, 0)
        self.setWindowTitle(_tr("Read with AI"))

        self.task_combo = ComboBox(self)
        for item in TASKS:
            self.task_combo.addItem(self._task_label(item), userData=item.id)
        self._select_task(task or DEFAULT_TASK)
        self.task_combo.currentIndexChanged.connect(self._on_task_changed)

        self.run_button = PushButton(_tr("Run"), self)
        self.run_button.clicked.connect(self.run)
        self.copy_button = PrimaryPushButton(_tr("Copy"), self)
        self.copy_button.clicked.connect(self.copy_text)

        self.status_label = CaptionLabel("", self)
        self.text_edit = TextEdit(self)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setPlainText(initial_text or "")
        self.text_edit.setReadOnly(True)      # 出结果再放开，识别中不给改

        self.preview = None

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.task_combo)
        bar.addWidget(self.run_button)
        bar.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.copy_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, title_bar.height() + 4, 12, 12)
        root.setSpacing(10)
        root.addLayout(bar)
        root.addWidget(self.text_edit, 1)
        root.addLayout(footer)

        self._apply_theme()
        get_ui_theme().theme_changed.connect(self._apply_theme)
        self.resize(self.WIDTH, self.HEIGHT)

        # 公式模板且已有结果时，直接把预览装上（要等布局建好才能插控件）
        if (initial_text or "").strip() and self._task_id() == "formula":
            self._install_preview(initial_text)

        self._update_ready_state(initial_text)
        # 打开时还没有结果就自动跑一次：用户点了「AI 解读」就是想看结果
        if not (initial_text or "").strip():
            QTimer.singleShot(0, self.run)

    # ── 数据 ──

    def _task_label(self, item) -> str:
        return self.tr(item.label)

    def _task_id(self) -> str:
        return str(self.task_combo.currentData() or "")

    def _select_task(self, task_id: str) -> None:
        index = self.task_combo.findData(task_id)
        if index >= 0:
            self.task_combo.setCurrentIndex(index)

    def text(self) -> str:
        return self.text_edit.toPlainText()

    def _update_ready_state(self, text: str) -> None:
        ready = bool(self.model is not None and getattr(self.model, "usable", False))
        self.run_button.setEnabled(ready)
        self.copy_button.setEnabled(bool((text or "").strip()))
        if not ready:
            self.status_label.setText(
                _tr("No vision model is configured. Add one in the OCR settings."))
        elif not (text or "").strip():
            self.status_label.setText(_tr("Reading..."))

    # ── 行为 ──

    def _on_task_changed(self, _index: int) -> None:
        """换模板不自动重跑：用户可能只是想换个模板再点一次「重跑」，自动发请求会花额度。

        公式预览跟着模板显示/隐藏——切到别的模板时留着一块旧公式只会让人以为结果没更新。
        """
        is_formula = self._task_id() == "formula"
        if is_formula and self.text().strip():
            self._install_preview(self.text())
        if self.preview is not None:
            self.preview.setVisible(is_formula)

    def run(self) -> bool:
        """按当前模板跑一次；已有请求在跑时忽略（避免叠着发几个请求）。"""
        if self._thread is not None:
            return False
        if self.model is None or not getattr(self.model, "usable", False):
            log_warning(T("没有配置可用的视觉模型，无法转换图片"), "VisionResultWindow")
            self._update_ready_state("")
            return False

        task = self._task_id()
        self.text_edit.setReadOnly(True)
        self.status_label.setText(_tr("Reading..."))
        self.run_button.setEnabled(False)
        log_debug(T("视觉模型窗口请求: {task}", task=task), "VisionResultWindow")
        thread = _ConvertThread(self.image, self.model, task=task, target=self.target)
        thread.converted.connect(self._on_converted)
        thread.finished.connect(lambda: self._forget_thread(thread))
        _threads.append(thread)
        self._thread = thread
        thread.start()
        return True

    def _forget_thread(self, thread) -> None:
        if self._thread is thread:
            self._thread = None
        if thread in _threads:
            _threads.remove(thread)

    def _on_converted(self, result) -> None:
        ok = bool(getattr(result, "ok", False))
        text = getattr(result, "text", "") or ""
        if not ok:
            message = getattr(result, "error", "") or _tr("The vision model failed.")
            log_warning(T("视觉模型失败: {error}", error=message), "VisionResultWindow")
            self.status_label.setText(message)
            self.text_edit.setReadOnly(False)
            self.run_button.setEnabled(True)
            return

        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(False)
        self.copy_button.setEnabled(True)
        self.run_button.setEnabled(True)
        self.status_label.setText(_tr("%1 characters").replace("%1", str(len(text))))
        log_info(T("视觉模型返回结果: {count} 字符", count=len(text)), "VisionResultWindow")
        if self._task_id() == "formula":
            self._install_preview(text)

    def copy_text(self) -> bool:
        text = self.text().strip()
        if not text:
            return False
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        self.status_label.setText(_tr("Copied"))
        # 常驻文案在定时器里现算，不闭包 self——窗口可能在这 1.5 秒内就被关掉了
        default = _tr("%1 characters").replace("%1", str(len(text)))
        QTimer.singleShot(1500, lambda: self.status_label.setText(default))
        return True

    # ── 公式预览 ──

    def _install_preview(self, latex: str) -> None:
        """公式模板：在文本框上方加一块排版预览（只加一次，之后只更新内容）。"""
        from ocr.latex_render import looks_like_latex
        from ui.latex_view import LatexView

        if not looks_like_latex(latex):
            return
        layout = self.layout()
        if self.preview is None:
            self.preview = LatexView(latex, self)
            # 插在任务下拉下面、文本框上面：先看排版，再看/改文字
            layout.insertWidget(1, self.preview)
        else:
            self.preview.set_latex(latex)
            self.preview.setVisible(True)

    # ── 外观 ──

    def _apply_theme(self, _tokens=None):
        tokens = ui_tokens(self)
        self.text_edit.setStyleSheet(
            f"QTextEdit {{ background: {tokens.input_background}; color: {tokens.text}; "
            f"border: 1px solid {tokens.border}; border-radius: 6px; padding: 8px 10px; "
            f"font: 13px {FONT_FAMILY}; selection-background-color: {tokens.accent}; "
            f"selection-color: {tokens.selected_text}; }}"
            + scrollbar_qss(self)
        )
        self.setStyleSheet(f"FrostedFramelessDialog {{ background: {tokens.window}; }}")

    @safe_event
    def closeEvent(self, event):
        # 线程跑完自己回收，这里只把引用放掉：关窗口不该打断已经发出的请求
        self._thread = None
        super().closeEvent(event)
