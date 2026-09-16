# -*- coding: utf-8 -*-
"""离线翻译模型的管理卡片：列表、安装/删除、进度、许可提示。

模型清单来自 translation.local_models.MODELS，卡片只读它——清单里没有模型时显示
一句说明，而不是空着让人以为是坏了（模型要先转好、上传、登记，见该模块注释）。
"""
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QProgressBar

from ui.fluent_lite import CaptionLabel, FluentIcon, PushButton, SettingCard
from .components import SettingCardGroup, adjust_button_width, apply_theme_text_style

from translation import local_engine, local_models


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.1f} GB"
    return f"{num_bytes / 1024 ** 2:.0f} MB"


class _DownloadWorker(QThread):
    """把阻塞的下载放到工作线程；进度回调从下载线程直接发信号。"""

    progressed = Signal(int, int)        # (已下载, 总字节)
    done = Signal(bool, str)             # (成功, 错误信息)

    def __init__(self, model_id: str, parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            local_models.install(
                self._model_id,
                progress=lambda done, total: self.progressed.emit(done, total),
                cancel=self._cancel,
            )
        except local_models.DownloadCancelled:
            self.done.emit(False, "")            # 取消是用户意图，不弹错误文案
        except Exception as exc:
            self.done.emit(False, str(exc))
        else:
            self.done.emit(True, "")


class LocalModelsGroup(SettingCardGroup):
    """一个模型一行：名称、语言、大小、许可、状态，加上安装/取消/删除。"""

    def __init__(self, dialog, parent=None):
        super().__init__(dialog.tr("Offline Models"), parent)
        self._dialog = dialog
        self._workers = {}
        self._rows = {}

        local_engine.log_runtime_state()   # 排查"离线翻译没生效"时的第一手线索

        if not local_engine.runtime_available():
            self._add_note(dialog.tr(
                "Offline translation runtime is missing from this build: {reason}"
            ).format(reason=local_engine.runtime_error()))
        if not local_models.MODELS:
            self._add_note(dialog.tr("No offline model is available for download yet."))
            return

        for spec in local_models.MODELS.values():
            self._rows[spec.model_id] = self._build_row(spec)

    # ── 构建 ─────────────────────────────────────────

    def _add_note(self, text: str) -> None:
        note = SettingCard(FluentIcon.INFO, text, parent=self)
        apply_theme_text_style(note.contentLabel, 12, caption=True)
        self.addSettingCard(note)

    def _build_row(self, spec) -> dict:
        dialog = self._dialog
        detail = " · ".join(filter(None, (
            ", ".join(spec.languages),
            _human_size(spec.size_bytes),
            spec.license_note,
        )))
        card = SettingCard(FluentIcon.DOWNLOAD, spec.display_name, detail, parent=self)

        # 状态单独一个标签，不动 card.contentLabel——那里放着语言/大小/许可说明
        status = CaptionLabel("", card)
        apply_theme_text_style(status, 12, caption=True)

        progress = QProgressBar(card)
        progress.setFixedWidth(150)
        progress.setRange(0, 100)
        progress.setVisible(False)

        button = PushButton(dialog.tr("Install"), card)
        adjust_button_width(button)

        card.hBoxLayout.addWidget(status, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addWidget(progress, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(16)
        self.addSettingCard(card)

        row = {"spec": spec, "button": button, "progress": progress,
               "status": status, "state": "idle"}
        button.clicked.connect(
            lambda _=False, model_id=spec.model_id: self._on_button(model_id))
        self._refresh_row(row)
        return row

    # ── 状态刷新 ─────────────────────────────────────

    def _refresh_row(self, row, *, downloaded: int = 0, total: int = 0) -> None:
        dialog = self._dialog
        spec = row["spec"]

        if row["state"] == "downloading":
            row["button"].setText(dialog.tr("Cancel"))
            row["progress"].setVisible(True)
            if total:
                percent = int(downloaded * 100 / total)
                row["progress"].setValue(percent)
                row["status"].setText(
                    f"{percent}%  {_human_size(downloaded)} / {_human_size(total)}")
            else:
                row["status"].setText(_human_size(downloaded))
        else:
            row["progress"].setVisible(False)
            row["progress"].setValue(0)
            if local_models.is_installed(spec.model_id):
                loaded = spec.model_id in local_engine.loaded_models()
                row["button"].setText(dialog.tr("Delete"))
                row["status"].setText(
                    dialog.tr("Installed (in memory)") if loaded else dialog.tr("Installed"))
            else:
                partial = local_models.partial_size(spec.model_id)
                row["button"].setText(dialog.tr("Install"))
                row["status"].setText(
                    dialog.tr("Incomplete ({size} downloaded)").format(size=_human_size(partial))
                    if partial else dialog.tr("Not installed"))
        adjust_button_width(row["button"])

    def refresh(self) -> None:
        for row in self._rows.values():
            self._refresh_row(row)

    # ── 交互 ─────────────────────────────────────────

    def _on_button(self, model_id: str) -> None:
        row = self._rows[model_id]
        if row["state"] == "downloading":
            worker = self._workers.get(model_id)
            if worker is not None:
                worker.cancel()
            return
        if local_models.is_installed(model_id):
            self._delete(model_id)
        else:
            self._install(model_id)

    def _install(self, model_id: str) -> None:
        row = self._rows[model_id]
        row["state"] = "downloading"
        self._refresh_row(row)

        worker = _DownloadWorker(model_id, self)
        worker.progressed.connect(
            lambda done, total, r=row: self._refresh_row(r, downloaded=done, total=total))
        worker.done.connect(
            lambda ok, message, m=model_id: self._on_download_done(m, ok, message))
        self._workers[model_id] = worker
        worker.start()

    def _on_download_done(self, model_id: str, ok: bool, message: str) -> None:
        row = self._rows[model_id]
        worker = self._workers.pop(model_id, None)
        if worker is not None:
            worker.wait(1000)
        row["state"] = "idle"
        self._refresh_row(row)
        if not ok and message:
            row["status"].setText(message)

    def _delete(self, model_id: str) -> None:
        # 必须先释放引擎：Windows 上 CTranslate2 把权重 mmap 住，文件还开着删不掉
        local_engine.release_engines(model_id)
        local_models.uninstall(model_id)
        self._refresh_row(self._rows[model_id])
