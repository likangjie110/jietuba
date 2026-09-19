# -*- coding: utf-8 -*-
"""视觉模型结果窗口：可改的结果、可换模板重跑、公式模板带排版预览。

窗口的价值在「同一张图换一种解读」：所以判据里有两件必须成立——重跑时用的任务模板跟着
下拉走，以及窗口一直留着那张图（不然重跑就没素材了）。

请求本身通过替身线程同步完成（真线程在测试里只会让用例变慢变随机）；`convert_image`
这个外部边界也换成 stub，被测的是窗口的行为。
"""

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage

from ocr import vision_models
from ocr.vision_result_window import VisionResultWindow
from settings.tool_settings import ToolSettingsManager
from PySide6.QtCore import QSettings


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "vision.ini"), QSettings.Format.IniFormat))


def image(width=64, height=32, color="#3366CC") -> QImage:
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor(color))
    return picture


@pytest.fixture
def model():
    return vision_models.VisionModel(name="stub", base_url="http://127.0.0.1:1/v1",
                                     model_id="vision-1", api_key="k")


@pytest.fixture
def sync_thread(monkeypatch):
    """把请求线程换成同步替身，并记录每次请求的任务模板。"""
    calls = []

    class _SyncThread:
        def __init__(self, image, model, *, task, target):
            self._image = image
            self._model = model
            self._task = task
            self._target = target
            self.converted = _InlineSignal()
            self.finished = _InlineSignal()

        def start(self):
            calls.append({"task": self._task, "target": self._target})
            result = vision_models.convert_image(self._image, self._model,
                                                 target=self._target, task=self._task)
            self.converted.emit(result)
            self.finished.emit()

    monkeypatch.setattr("ocr.vision_result_window._ConvertThread", _SyncThread)
    return calls


class _InlineSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def _reply(text="识别结果", ok=True, error=""):
    def _convert(image_, model_, *, target="markdown", task="table", opener=None):
        return vision_models.VisionResult(ok, text=text, error=error)

    return _convert


class TestVisionWindow:
    def test_the_window_keeps_the_image_for_reruns(self, qapp, model):
        picture = image()
        window = VisionResultWindow(picture, model, task="table")
        try:
            assert window.image is picture
            assert window.text() == ""
            assert window.copy_button.isEnabled() is False
        finally:
            window.deleteLater()

    def test_without_a_model_the_run_button_is_disabled_and_says_why(self, qapp, config):
        window = VisionResultWindow(image(), None, task="table")
        try:
            assert window.run_button.isEnabled() is False
            assert window.run() is False
            assert "vision model" in window.status_label.text()
        finally:
            window.deleteLater()

    def test_running_fills_the_editable_result(self, qapp, model, monkeypatch, sync_thread):
        monkeypatch.setattr(vision_models, "convert_image", _reply("模型的答案"))
        window = VisionResultWindow(image(), model, task="table")
        try:
            assert window.run() is True
            assert window.text() == "模型的答案"
            assert window.text_edit.isReadOnly() is False
            assert window.copy_button.isEnabled() is True
            assert sync_thread[-1]["task"] == "table"
        finally:
            window.deleteLater()

    def test_changing_the_task_changes_what_gets_sent(self, qapp, model, monkeypatch,
                                                      sync_thread):
        """换模板再点重跑：第二次请求用的必须是新模板。"""
        monkeypatch.setattr(vision_models, "convert_image", _reply("答案"))
        window = VisionResultWindow(image(), model, task="table")
        try:
            window.run()
            index = window.task_combo.findData("code")
            window.task_combo.setCurrentIndex(index)
            window.run()

            assert [call["task"] for call in sync_thread] == ["table", "code"]
        finally:
            window.deleteLater()

    def test_changing_the_task_does_not_send_a_request_by_itself(self, qapp, model, monkeypatch,
                                                                 sync_thread):
        """换模板只切模板，不自动发请求——额度是用户自己的。"""
        monkeypatch.setattr(vision_models, "convert_image", _reply("答案"))
        window = VisionResultWindow(image(), model, task="table")
        try:
            window.task_combo.setCurrentIndex(window.task_combo.findData("general"))
            assert sync_thread == []
        finally:
            window.deleteLater()

    def test_a_failure_is_shown_and_the_run_button_comes_back(self, qapp, model, monkeypatch,
                                                              sync_thread):
        monkeypatch.setattr(vision_models, "convert_image",
                            _reply(ok=False, error="视觉模型返回 HTTP 500"))
        window = VisionResultWindow(image(), model, task="table")
        try:
            window.run()
            assert "500" in window.status_label.text()
            assert window.run_button.isEnabled() is True
            assert window.text() == ""
        finally:
            window.deleteLater()

    def test_copy_puts_the_current_text_on_the_clipboard(self, qapp, model):
        window = VisionResultWindow(image(), model, task="table", initial_text="初始结果")
        try:
            window.text_edit.setPlainText("改过之后的结果")
            assert window.copy_text() is True
            assert QGuiApplication.clipboard().text() == "改过之后的结果"
        finally:
            window.deleteLater()

    def test_empty_text_is_not_copied(self, qapp, model):
        window = VisionResultWindow(image(), model, task="table")
        try:
            assert window.copy_text() is False
        finally:
            window.deleteLater()

    def test_a_formula_result_gets_a_rendered_preview(self, qapp, model):
        window = VisionResultWindow(image(), model, task="formula",
                                    initial_text=r"\frac{a}{b}")
        try:
            assert window.preview is not None
            assert window.preview.latex() == r"\frac{a}{b}"
        finally:
            window.deleteLater()

    def test_plain_text_result_does_not_get_a_preview(self, qapp, model):
        window = VisionResultWindow(image(), model, task="text", initial_text="普通的识别结果")
        try:
            assert window.preview is None
        finally:
            window.deleteLater()

    def test_initial_text_is_shown_immediately(self, qapp, model):
        window = VisionResultWindow(image(), model, task="general", initial_text="已经有的结果")
        try:
            assert window.text() == "已经有的结果"
            assert window.copy_button.isEnabled() is True
        finally:
            window.deleteLater()

    def test_the_task_list_comes_from_the_shared_registry(self, qapp, model):
        window = VisionResultWindow(image(), model)
        try:
            listed = [window.task_combo.itemData(index)
                      for index in range(window.task_combo.count())]
            assert listed == [task.id for task in vision_models.TASKS]
            assert "solve" in listed
        finally:
            window.deleteLater()

    def test_the_target_setting_is_passed_through(self, qapp, model, monkeypatch, sync_thread):
        monkeypatch.setattr(vision_models, "convert_image", _reply("答案"))
        window = VisionResultWindow(image(), model, task="table", target="html")
        try:
            window.run()
            assert sync_thread[-1]["target"] == "html"
        finally:
            window.deleteLater()


class TestPreviewVisibility:
    def test_switching_away_from_formula_hides_the_preview(self, qapp, model):
        window = VisionResultWindow(image(), model, task="formula",
                                    initial_text=r"\frac{a}{b}")
        try:
            assert window.preview is not None
            assert window.preview.isHidden() is False
            window.task_combo.setCurrentIndex(window.task_combo.findData("general"))
            assert window.preview.isHidden() is True
            window.task_combo.setCurrentIndex(window.task_combo.findData("formula"))
            assert window.preview.isHidden() is False
        finally:
            window.deleteLater()
