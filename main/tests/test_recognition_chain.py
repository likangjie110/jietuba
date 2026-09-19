# -*- coding: utf-8 -*-
"""识别链：可编辑表格、模型档位、OpenAI 兼容视觉模型。

三块都驱动发布实现：表格用真 `TableDocument` + 真 `TableEditor`（真 QTableWidget 与
真剪贴板），档位用真文件系统探测与真引擎构造（桩只有 `ppocr_rust.Engine` 这一个外部
边界），视觉模型打桩的是一个**本地 HTTP 服务**（外部边界），被测的请求构造、解析、
错误分支全是真的。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QRectF, QSettings
from PySide6.QtGui import QColor, QGuiApplication, QImage

from ocr import model_tiers, vision_models
from ocr.table_document import TableDocument
from ocr.table_editor import TableEditor
from settings.tool_settings import ToolSettingsManager


@pytest.fixture
def config(tmp_path):
    return ToolSettingsManager(
        qsettings=QSettings(str(tmp_path / "ocr.ini"), QSettings.Format.IniFormat))


def grid_document() -> TableDocument:
    return TableDocument.from_grid([
        ["Name", "Age", "City"],
        ["Ann", "31", "Oslo"],
        ["Bob", "24", "Kyoto"],
    ])


def ocr_result(boxes) -> dict:
    """按 OCR 结果的真实形状造数据：每个框是 ``(text, x, y, w, h)``。

    形状取自 ``ocr_manager._ocr_boxes`` 的读取方式：``{"code": 100, "data": [{"text", "box"}]}``，
    ``box`` 是四点坐标——用别的形状造数据等于在测一个不存在的输入。
    """
    return {
        "code": 100,
        "data": [
            {"text": text, "box": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}
            for text, x, y, w, h in boxes
        ],
    }


# ── 表格文档 ──────────────────────────────────────────

class TestTableDocument:
    def test_markdown_and_html_round_trip_the_grid(self):
        document = grid_document()
        markdown = document.to_markdown()
        assert markdown.splitlines()[0] == "| Name | Age | City |"
        assert "| Ann | 31 | Oslo |" in markdown

        html = document.to_html()
        assert html.count("<tr>") == 3
        assert "<th>Name</th>" in html
        assert "<td>Kyoto</td>" in html

    def test_merge_keeps_the_anchor_text_and_spans_the_region(self):
        document = grid_document()
        assert document.merge_cells(1, 0, 2, 0) is True

        assert document.spans == {(1, 0): (2, 1)}
        assert document.get(1, 0) == "Ann"
        assert document.region(2, 0) == (1, 0, 2, 1)
        assert document.is_merged(2, 0) is True
        assert "| Ann | 31 | Oslo |" in document.to_markdown()
        assert "|  | 24 | Kyoto |" in document.to_markdown()
        assert 'rowspan="2"' in document.to_html()

    def test_merge_drops_the_shadowed_cells_text(self):
        document = grid_document()
        document.merge_cells(1, 0, 1, 1)
        assert document.get(1, 1) == ""
        assert document.cells.get((1, 1)) is None

    def test_split_restores_the_merged_region(self):
        document = grid_document()
        document.merge_cells(1, 0, 2, 1)
        assert document.split_cell(2, 1) is True

        assert document.spans == {}
        assert document.is_merged(1, 0) is False
        assert document.get(1, 0) == "Ann"
        assert document.get(1, 1) == ""

    def test_split_on_a_plain_cell_is_a_noop(self):
        assert grid_document().split_cell(0, 0) is False

    def test_out_of_range_merge_is_refused(self):
        document = grid_document()
        assert document.merge_cells(0, 0, 9, 9) is False
        assert document.merge_cells(0, 0, 0, 0) is False
        assert document.spans == {}

    def test_copy_region_returns_grid_and_tsv(self):
        document = grid_document()
        grid, text = document.copy_region(1, 0, 2, 1)
        assert grid == [["Ann", "31"], ["Bob", "24"]]
        assert text == "Ann\t31\nBob\t24"

    def test_paste_inside_the_table_writes_the_cells(self):
        document = grid_document()
        assert document.paste_cells([["X", "Y"], ["Z", "W"]], 1, 1) is True
        assert document.get(1, 1) == "X"
        assert document.get(2, 2) == "W"

    def test_paste_that_does_not_fit_is_refused(self):
        document = grid_document()
        before = document.clone()
        assert document.paste_cells([["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]], 2, 2) is False
        assert document.cells == before.cells

    def test_paste_over_a_merged_region_is_refused(self):
        document = grid_document()
        document.merge_cells(1, 1, 2, 2)
        assert document.paste_cells([["x"]], 1, 1) is False
        assert document.paste_cells([["x"]], 2, 2) is False

    def test_clear_region_empties_the_cells(self):
        document = grid_document()
        assert document.clear_region(1, 0, 1, 2) == 3
        assert all(document.get(1, col) == "" for col in range(3))

    def test_building_from_an_ocr_result_uses_the_geometry(self):
        result = ocr_result([
            ("Name", 0, 0, 30, 10), ("Age", 60, 0, 20, 10),
            ("Ann", 0, 30, 30, 10), ("31", 60, 30, 20, 10),
            ("Bob", 0, 60, 30, 10), ("24", 60, 60, 20, 10),
        ])
        document = TableDocument.from_ocr_result(result)
        assert document.rows == 3 and document.cols == 2
        assert document.get(0, 0) == "Name"
        assert document.get(2, 1) == "24"

    def test_a_non_table_result_yields_an_empty_document(self):
        result = ocr_result([("just one line", 0, 0, 100, 12)])
        assert TableDocument.from_ocr_result(result).rows == 0

    def test_pipes_are_escaped_in_markdown(self):
        document = TableDocument.from_grid([["a|b"]])
        assert "a\\|b" in document.to_markdown()


# ── 表格编辑器 ────────────────────────────────────────

@pytest.fixture
def editor(qapp):
    window = TableEditor(grid_document())
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


class TestTableEditor:
    def test_it_shows_the_document_and_the_preview(self, editor):
        assert editor.table.rowCount() == 3
        assert editor.table.columnCount() == 3
        assert "| Name | Age | City |" in editor.markdown_view.toPlainText()
        assert "<table>" in editor.html_view.toPlainText()

    def test_merge_and_split_through_the_ui_are_undoable(self, editor):
        editor.table.setRangeSelected(
            __import__("PySide6.QtWidgets", fromlist=["QTableWidgetSelectionRange"])
            .QTableWidgetSelectionRange(1, 0, 2, 0), True)
        assert editor.on_merge() is True
        assert editor.document.spans == {(1, 0): (2, 1)}
        assert "rowspan" not in editor.markdown_view.toPlainText()

        assert editor.on_split() is True
        assert editor.document.spans == {}

        editor.undo_stack.undo()
        assert editor.document.spans == {(1, 0): (2, 1)}
        assert editor.table.rowCount() == 3

    def test_editing_a_cell_updates_document_and_preview(self, editor):
        from PySide6.QtWidgets import QTableWidgetItem

        editor.table.setItem(1, 0, QTableWidgetItem("Anna"))

        assert editor.document.get(1, 0) == "Anna"
        assert "Anna" in editor.markdown_view.toPlainText()

        editor.undo_stack.undo()
        assert editor.document.get(1, 0) == "Ann"

    def test_copy_and_paste_use_the_real_clipboard(self, editor):
        from PySide6.QtWidgets import QTableWidgetSelectionRange

        editor.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 1), True)
        assert editor.on_copy() is True
        assert QGuiApplication.clipboard().text() == "Name\tAge"

        editor.table.setCurrentCell(1, 0)
        QGuiApplication.clipboard().setText("X\tY")
        assert editor.on_paste() is True
        assert editor.document.get(1, 0) == "X"
        assert editor.document.get(1, 1) == "Y"

        editor.undo_stack.undo()
        assert editor.document.get(1, 0) == "Ann"

    def test_a_paste_that_does_not_fit_is_reported(self, editor):
        editor.table.setCurrentCell(2, 2)
        QGuiApplication.clipboard().setText("a\tb\tc\nd\te\tf")
        assert editor.on_paste() is False
        assert editor.document.get(2, 2) == "Kyoto"
        assert "fit" in editor.hint.toPlainText() or "装" in editor.hint.toPlainText()

    def test_copy_markdown_and_html(self, editor):
        assert editor.copy_format("markdown") is True
        assert QGuiApplication.clipboard().text().startswith("| Name | Age | City |")
        assert editor.copy_format("html") is True
        assert QGuiApplication.clipboard().text().startswith("<table>")

    def test_loading_an_ocr_result_replaces_the_table(self, editor):
        result = ocr_result([
            ("A", 0, 0, 20, 10), ("B", 60, 0, 20, 10),
            ("1", 0, 30, 20, 10), ("2", 60, 30, 20, 10),
        ])
        assert editor.from_ocr_result(result) is True
        assert (editor.table.rowCount(), editor.table.columnCount()) == (2, 2)
        assert editor.document.get(0, 1) == "B"


# ── 模型档位 ──────────────────────────────────────────

class TestModelTiers:
    def test_only_installed_tiers_are_listed(self, tmp_path, monkeypatch):
        models = tmp_path / "models"
        models.mkdir()
        # 只放两套模型：v6_small 与 medium
        for name in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx",
                     "PP-OCRv6_det_medium.onnx", "PP-OCRv6_rec_medium.onnx"):
            (models / name).write_bytes(b"fake")
        monkeypatch.setattr(model_tiers, "model_dirs", lambda: [str(models)])

        ids = [tier_id for tier_id, _label in model_tiers.available_tiers()]
        assert ids == ["v6_small", "v6_medium"]
        assert model_tiers.is_tier_available("v5_small") is False

    def test_resolve_gives_the_engine_and_both_paths(self, tmp_path, monkeypatch):
        models = tmp_path / "models"
        models.mkdir()
        (models / "PP-OCRv6_det_small.onnx").write_bytes(b"x")
        (models / "PP-OCRv6_rec_small.onnx").write_bytes(b"x")
        monkeypatch.setattr(model_tiers, "model_dirs", lambda: [str(models)])

        engine, det, rec = model_tiers.resolve("v6_small")
        assert engine == "ppocr_rust"
        assert det.endswith("PP-OCRv6_det_small.onnx") and rec.endswith("PP-OCRv6_rec_small.onnx")

    def test_selection_falls_back_to_an_available_tier(self, config, tmp_path, monkeypatch):
        models = tmp_path / "models"
        models.mkdir()
        (models / "PP-OCRv6_det_small.onnx").write_bytes(b"x")
        (models / "PP-OCRv6_rec_small.onnx").write_bytes(b"x")
        monkeypatch.setattr(model_tiers, "model_dirs", lambda: [str(models)])

        config.set_ocr_model_tier("v6_medium")        # 这台机器上没有
        assert config.get_ocr_model_tier() == "v6_small"

        config.set_ocr_model_tier("v6_small")
        assert config.get_ocr_model_tier() == "v6_small"

    def test_the_settings_accessor_is_whitelisted_by_availability(self, config):
        assert config.get_ocr_model_tier() in [tier_id for tier_id, _ in
                                              model_tiers.available_tiers()]

    def test_the_engine_receives_the_tier_model_paths(self, monkeypatch, tmp_path):
        """档位变化要真的传到引擎：断言构造 Engine 时用的两个模型路径跟着变。"""
        from ocr.engines import PpOcrRustEngine

        models = tmp_path / "models"
        models.mkdir()
        for name in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx",
                     "PP-OCRv6_det_medium.onnx", "PP-OCRv6_rec_medium.onnx"):
            (models / name).write_bytes(b"x")

        built = []

        class _FakeEngine:
            def __init__(self, det, rec):
                built.append((det, rec))

            def close(self):
                built.append(("closed", "closed"))

        import types

        fake_module = types.SimpleNamespace(Engine=_FakeEngine)
        monkeypatch.setitem(__import__("sys").modules, "ppocr_rust", fake_module)
        monkeypatch.setattr(model_tiers, "model_dirs", lambda: [str(models)])

        engine = PpOcrRustEngine()
        monkeypatch.setattr(engine, "is_available", lambda: True)
        monkeypatch.setattr(engine, "_probe_once", lambda: (True, "det_default", "rec_default"))

        assert engine.initialize(tier="v6_small") is True
        assert built[-1][0].endswith("PP-OCRv6_det_small.onnx")

        assert engine.initialize(tier="v6_medium") is True
        assert built[-1][0].endswith("PP-OCRv6_det_medium.onnx")
        assert built[-1][1].endswith("PP-OCRv6_rec_medium.onnx")

    def test_unknown_tier_falls_back_to_the_probed_models(self, qapp, monkeypatch):
        from ocr.engines import PpOcrRustEngine

        built = []

        class _FakeEngine:
            def __init__(self, det, rec):
                built.append((det, rec))

            def close(self):
                built.append(("closed", "closed"))

        import types

        monkeypatch.setitem(__import__("sys").modules, "ppocr_rust",
                            types.SimpleNamespace(Engine=_FakeEngine))
        engine = PpOcrRustEngine()
        monkeypatch.setattr(engine, "is_available", lambda: True)
        monkeypatch.setattr(engine, "_probe_once", lambda: (True, "det_default", "rec_default"))

        assert engine.initialize(tier="v9_huge") is True
        assert built[-1] == ("det_default", "rec_default")


# ── 视觉模型 ──────────────────────────────────────────

class _StubHandler(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容服务：回一段固定内容，或按脚本回错误。"""

    response_text = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    status = 200
    body_override = None
    seen = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 的接口名
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": payload,
        })
        body = self.body_override if self.body_override is not None else json.dumps(
            {"choices": [{"message": {"content": self.response_text}}]}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):        # 别把测试日志刷满
        pass


@pytest.fixture
def stub_server():
    _StubHandler.seen = []
    _StubHandler.status = 200
    _StubHandler.body_override = None
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()


def image(width=64, height=32, color="#3366CC") -> QImage:
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor(color))
    return picture


class TestVisionModels:
    def test_models_round_trip_through_the_config(self, config):
        model = vision_models.VisionModel(name="local", base_url="http://127.0.0.1:1/v1",
                                          model_id="m1", api_key="k")
        vision_models.upsert_model(config, model)

        loaded = vision_models.load_models(config)
        assert len(loaded) == 1
        assert loaded[0].name == "local" and loaded[0].model_id == "m1"

        vision_models.delete_model(config, "local")
        assert vision_models.load_models(config) == []

    def test_corrupt_config_falls_back_to_an_empty_list(self, config):
        config.set_app_setting("ocr_vision_models", "{not json")
        assert vision_models.load_models(config) == []

    def test_endpoint_completion(self):
        assert vision_models.VisionModel(name="a", base_url="http://h/v1",
                                         model_id="m").endpoint() == "http://h/v1/chat/completions"
        assert vision_models.VisionModel(
            name="a", base_url="http://h/v1/chat/completions",
            model_id="m").endpoint() == "http://h/v1/chat/completions"

    def test_conversion_sends_the_image_and_gets_the_markdown(self, config, stub_server):
        model = vision_models.VisionModel(name="stub", base_url=stub_server,
                                          model_id="vision-1", api_key="secret")

        result = vision_models.convert_image(image(), model, target="markdown")

        assert result.ok is True
        assert result.text.startswith("| A | B |")
        request = _StubHandler.seen[-1]
        assert request["path"] == "/v1/chat/completions"
        assert request["auth"] == "Bearer secret"
        assert request["body"]["model"] == "vision-1"
        content = request["body"]["messages"][0]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "Markdown" in content[0]["text"]

    def test_html_target_changes_the_instruction(self, config, stub_server):
        model = vision_models.VisionModel(name="stub", base_url=stub_server, model_id="vision-1")
        vision_models.convert_image(image(), model, target="html")
        instruction = _StubHandler.seen[-1]["body"]["messages"][0]["content"][0]["text"]
        assert "HTML" in instruction

    def test_http_error_is_reported_with_the_status(self, config, stub_server):
        _StubHandler.status = 500
        _StubHandler.body_override = b'{"error": "boom"}'
        model = vision_models.VisionModel(name="stub", base_url=stub_server, model_id="v")

        result = vision_models.convert_image(image(), model)
        assert result.ok is False
        assert "500" in result.error and "boom" in result.error

    def test_a_response_without_choices_is_reported(self, config, stub_server):
        _StubHandler.body_override = json.dumps({"id": "x"}).encode()
        model = vision_models.VisionModel(name="stub", base_url=stub_server, model_id="v")

        result = vision_models.convert_image(image(), model)
        assert result.ok is False
        assert "choices" in result.error

    def test_no_model_configured_is_reported(self):
        result = vision_models.convert_image(image(), None)
        assert result.ok is False and "视觉模型" in result.error

    def test_a_model_without_url_or_id_is_rejected(self):
        model = vision_models.VisionModel(name="broken", base_url="", model_id="")
        result = vision_models.convert_image(image(), model)
        assert result.ok is False

    def test_selected_model_prefers_the_configured_one(self, config):
        vision_models.upsert_model(config, vision_models.VisionModel(
            name="first", base_url="http://a/v1", model_id="m"))
        vision_models.upsert_model(config, vision_models.VisionModel(
            name="second", base_url="http://b/v1", model_id="m"))
        config.set_app_setting("ocr_vision_model", "second")

        assert vision_models.selected_model(config).name == "second"

    def test_the_target_setting_is_whitelisted(self, config):
        config.set_ocr_vision_target("nonsense")
        assert config.get_ocr_vision_target() == "markdown"
        config.set_ocr_vision_target("html")
        assert config.get_ocr_vision_target() == "html"


class TestVisionAction:
    def test_the_action_converts_and_copies(self, qapp, config, stub_server, monkeypatch):
        """走动作入口：静默截图 → 视觉模型 → 剪贴板 + 结果对话框。"""
        from types import SimpleNamespace

        import core.actions as actions_module

        vision_models.upsert_model(config, vision_models.VisionModel(
            name="stub", base_url=stub_server, model_id="vision-1"))
        config.set_app_setting("ocr_vision_model", "stub")
        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(80, 40), QRectF(0, 0, 80, 40)))
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)
        monkeypatch.setattr("ocr.vision_result_window.show_vision_result",
                            lambda *a, **k: None)

        app = SimpleNamespace(config_manager=config)
        assert actions_module.run_action("convert_image_markdown", app) is True
        assert QGuiApplication.clipboard().text().startswith("| A | B |")

    def test_without_a_model_the_action_reports_and_does_nothing(self, qapp, config, monkeypatch):
        from types import SimpleNamespace

        import core.actions as actions_module

        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(), QRectF(0, 0, 10, 10)))
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)
        QGuiApplication.clipboard().setText("untouched")

        assert actions_module.run_action(
            "convert_image_markdown", SimpleNamespace(config_manager=config)) is False
        assert QGuiApplication.clipboard().text() == "untouched"


class TestTableAction:
    def test_the_action_opens_the_editor_for_a_table_result(self, qapp, config, monkeypatch):
        from types import SimpleNamespace

        import core.actions as actions_module
        import ocr.table_editor as table_editor_module

        result = ocr_result([
            ("Name", 0, 0, 30, 10), ("Age", 60, 0, 20, 10),
            ("Ann", 0, 30, 30, 10), ("31", 60, 30, 20, 10),
        ])
        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(80, 40), QRectF(0, 0, 80, 40)))
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)
        monkeypatch.setattr(actions_module, "_OcrRawThread", lambda _image: _InlineThread(result))
        opened = []
        monkeypatch.setattr(table_editor_module, "open_table_editor",
                            lambda document: opened.append(document))

        app = SimpleNamespace(config_manager=config)
        assert actions_module.run_action("recognize_table", app) is True
        assert opened and opened[0].rows == 2
        assert opened[0].get(0, 0) == "Name"

    def test_a_non_table_result_does_not_open_the_editor(self, qapp, config, monkeypatch):
        from types import SimpleNamespace

        import core.actions as actions_module
        import ocr.table_editor as table_editor_module

        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(80, 40), QRectF(0, 0, 80, 40)))
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)
        monkeypatch.setattr(actions_module, "_OcrRawThread",
                            lambda _image: _InlineThread(ocr_result([("single", 0, 0, 40, 10)])))
        opened = []
        monkeypatch.setattr(table_editor_module, "open_table_editor",
                            lambda document: opened.append(document))

        assert actions_module.run_action("recognize_table",
                                         SimpleNamespace(config_manager=config)) is True
        assert opened == []


class TestSolveAction:
    def test_the_task_asks_the_model_to_solve_the_problem(self):
        assert vision_models.TASKS_BY_ID["solve"].id == "solve"
        assert "Solve the problem" in vision_models.instruction_for("solve")

    def test_the_action_sends_the_solve_instruction(self, qapp, config, stub_server, monkeypatch):
        """解题动作固定用 solve 模板，不受设置里选的任务模板影响。"""
        from types import SimpleNamespace

        import core.actions as actions_module

        vision_models.upsert_model(config, vision_models.VisionModel(
            name="stub", base_url=stub_server, model_id="vision-1"))
        config.set_app_setting("ocr_vision_model", "stub")
        config.set_ocr_vision_task("table")          # 设置里选的是表格，动作仍走解题
        monkeypatch.setattr(actions_module, "capture_at_cursor",
                            lambda _app: (image(80, 40), QRectF(0, 0, 80, 40)))
        monkeypatch.setattr(actions_module, "_flash_capture_mask", lambda *a, **k: None)
        monkeypatch.setattr("ocr.vision_result_window.show_vision_result",
                            lambda *a, **k: None)

        app = SimpleNamespace(config_manager=config)
        assert actions_module.run_action("solve_problem", app) is True

        instruction = _StubHandler.seen[-1]["body"]["messages"][0]["content"][0]["text"]
        assert "Solve the problem" in instruction
        assert QGuiApplication.clipboard().text() == _StubHandler.response_text

    def test_the_action_is_registered_as_a_silent_capture(self):
        from core import actions

        action = actions.ACTIONS_BY_ID["solve_problem"]
        assert action.silent_capture is True
        assert "solve_problem" in actions.GESTURE_ACTION_IDS


class _InlineThread:
    """同步版的 OCR 线程替身：start() 里直接把结果发出去（只替线程边界）。"""

    def __init__(self, result):
        self._result = result
        self.recognized = _InlineSignal()
        self.finished = _InlineSignal()

    def start(self):
        self.recognized.emit(self._result)
        self.finished.emit()


_MISSING = object()


class _InlineSignal:
    """最小的信号替身：``emit()`` 不带参数时按无参槽调用（``finished`` 就是这种）。"""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, value=_MISSING):
        for slot in list(self._slots):
            slot() if value is _MISSING else slot(value)


def test_a_leaky_file_fixture_nulls_the_shared_history_store():
    """模拟曾经的缺陷：某个文件级夹具在 teardown 里用 ``reset_store(None)`` 把共享存储置空。

    单独的置空本身无害——危害在下一条用例：``get_store()`` 会惰性新建一个指向**真机**
    ``~/Library/Application Support/Jietuba/history`` 的存储，同一次会话里后面所有入史
    用例就都写进去（跑一次测试多出几十张测试图）。这两条用例必须相邻、按定义顺序执行
    ——本仓库没装随机排序插件，pytest 按定义顺序跑。
    """
    from history import reset_store

    reset_store(None)


def test_the_guard_re_isolation_runs_before_every_test():
    """conftest 的用例级 ``history_store_guard`` 必须在每条用例开跑前把存储指回临时目录。

    上一条用例刚把共享存储置空；如果守卫被删掉，这里 ``get_store()`` 就会惰性建成真机
    目录的存储——断言随即失败，而不是安静地把后面的用例写进开发机的历史目录。
    """
    from core.platform.paths import app_data_dir
    from history import HISTORY_DIR_NAME, get_store

    real = app_data_dir() / HISTORY_DIR_NAME
    store = get_store()
    assert store.root != real, f"共享历史存储指向了真机目录: {real}"
