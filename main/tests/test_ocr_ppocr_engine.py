# -*- coding: utf-8 -*-
"""
ppocr_rust 引擎的生命周期

引擎从 OCRManager 里拆成独立的 PpOcrRustEngine（ocr/engines.py）之后，状态判断
依旧是「对象是否存在」，原先那个与扩展内部重复的 ready 标志仍然不存在。这里钉住
这一层的状态机：

- 初始化幂等，失败时不留下半初始化的对象
- TextLine 的 .points/.text/.score 被摊平成下游要的 [[[x,y]x4], text, score]
- 空文本行被丢弃
- 释放会真的调用 close()（模型有几十 MB，不能只重置标记等进程退出）
- 识别取到局部变量后引擎才被别的线程释放时，走错误分支而不是抛 AttributeError

探测结果是注入的（monkeypatch probe_ppocr_rust），用例不依赖开发机上真的装了扩展
或仓库里有模型文件。管理器那一层的注册表行为在 TestManagerRegistry 里。
"""
import sys

import pytest
from PySide6.QtGui import QImage

from ocr import engines as ocr_engines
from ocr import ocr_manager as om
from ocr.engine import OcrEngine, format_result


class _FakeTextLine:
    """冒充扩展返回的 TextLine：具名字段，不是裸元组。"""

    def __init__(self, points, text, score):
        self.points = points
        self.text = text
        self.score = score


class _FakeEngine:
    def __init__(self, det_path, rec_path):
        self.det_path = det_path
        self.rec_path = rec_path
        self.closed = False
        self.recognize_calls = []
        self.lines = [
            _FakeTextLine([(0.0, 0.0), (9.0, 0.0), (9.0, 5.0), (0.0, 5.0)], "hello", 0.9)
        ]

    def recognize(self, data, w, h, stride):
        self.recognize_calls.append((len(data), w, h, stride))
        return self.lines

    def close(self):
        self.closed = True


class _FakeModule:
    """冒充 ppocr_rust 扩展模块。"""

    def __init__(self):
        self.instances = []
        self.raise_on_new = None

    def Engine(self, det_path, rec_path):  # noqa: N802 —— 冒充扩展里的类名
        if self.raise_on_new is not None:
            raise self.raise_on_new
        eng = _FakeEngine(det_path, rec_path)
        self.instances.append(eng)
        return eng


@pytest.fixture
def fake_ppocr(monkeypatch):
    """假扩展 + 假探测结果：引擎认为扩展和模型都在。"""
    module = _FakeModule()
    monkeypatch.setitem(sys.modules, "ppocr_rust", module)
    monkeypatch.setattr(
        ocr_engines, "probe_ppocr_rust", lambda: (True, "det.onnx", "rec.onnx")
    )
    return module


@pytest.fixture
def pp_engine(fake_ppocr):
    return ocr_engines.PpOcrRustEngine()


def _image(w=12, h=6):
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(0)
    return img


class TestAvailability:

    def test_available_when_extension_and_models_present(self, pp_engine):
        assert pp_engine.is_available() is True

    def test_unavailable_without_models(self, monkeypatch):
        monkeypatch.setattr(ocr_engines, "probe_ppocr_rust", lambda: (False, None, None))
        assert ocr_engines.PpOcrRustEngine().is_available() is False

    def test_probe_result_is_cached(self, monkeypatch):
        """探测要摸磁盘，不能每次调用都重来一遍。"""
        calls = []

        def _counted():
            calls.append(1)
            return True, "det.onnx", "rec.onnx"

        monkeypatch.setattr(ocr_engines, "probe_ppocr_rust", _counted)
        engine = ocr_engines.PpOcrRustEngine()
        engine.is_available()
        engine.is_available()
        assert len(calls) == 1


class TestInitialize:

    def test_creates_engine_with_configured_model_paths(self, pp_engine, fake_ppocr):
        assert pp_engine.initialize() is True
        assert len(fake_ppocr.instances) == 1
        assert (fake_ppocr.instances[0].det_path, fake_ppocr.instances[0].rec_path) == (
            "det.onnx", "rec.onnx")

    def test_is_idempotent(self, pp_engine, fake_ppocr):
        assert pp_engine.initialize() is True
        assert pp_engine.initialize() is True
        # 已有引擎就直接返回，不再构造第二个
        assert len(fake_ppocr.instances) == 1

    def test_failure_leaves_no_half_built_engine(self, pp_engine, fake_ppocr):
        fake_ppocr.raise_on_new = RuntimeError("模型加载失败")
        assert pp_engine.initialize() is False
        assert pp_engine._engine is None
        assert "模型加载失败" in pp_engine.last_error

    def test_unavailable_extension_short_circuits(self, monkeypatch):
        monkeypatch.setattr(ocr_engines, "probe_ppocr_rust", lambda: (False, None, None))
        assert ocr_engines.PpOcrRustEngine().initialize() is False


class TestRecognize:

    def test_textline_fields_are_flattened_for_downstream(self, pp_engine, qapp):
        result = pp_engine.recognize(_image(), "dict")
        assert result["code"] == 100
        box, text, score = result["data"][0]["box"], result["data"][0]["text"], result["data"][0]["score"]
        assert text == "hello"
        assert score == pytest.approx(0.9)
        # points 的元组被摊成 [[x, y], ...]，四个角点
        assert box == [[0.0, 0.0], [9.0, 0.0], [9.0, 5.0], [0.0, 5.0]]

    def test_initializes_on_first_use(self, pp_engine, fake_ppocr, qapp):
        pp_engine.recognize(_image(), "text")
        assert len(fake_ppocr.instances) == 1
        assert fake_ppocr.instances[0].recognize_calls

    def test_blank_lines_are_dropped(self, pp_engine, fake_ppocr, qapp):
        pp_engine.initialize()
        fake_ppocr.instances[0].lines = [_FakeTextLine([(0.0, 0.0)] * 4, "", 0.5)]
        assert pp_engine.recognize(_image(), "text") == "[未识别到文字]"

    def test_no_lines_at_all(self, pp_engine, fake_ppocr, qapp):
        pp_engine.initialize()
        fake_ppocr.instances[0].lines = []
        assert pp_engine.recognize(_image(), "text") == "[未识别到文字]"

    def test_engine_released_concurrently_reports_error(self, pp_engine, fake_ppocr, qapp, monkeypatch):
        """引擎在另一线程被释放时走错误分支，而不是对 None 取属性崩掉。"""
        pp_engine.initialize()
        pp_engine._engine = None
        monkeypatch.setattr(pp_engine, "initialize", lambda *a, **k: True)  # 谎称初始化成功但不真的建引擎

        out = pp_engine.recognize(_image(), "text")
        assert "已释放" in out

    def test_recognize_failure_is_reported(self, pp_engine, fake_ppocr, qapp):
        pp_engine.initialize()

        def _boom(*_a, **_k):
            raise RuntimeError("推理失败")

        fake_ppocr.instances[0].recognize = _boom
        assert "推理失败" in pp_engine.recognize(_image(), "text")

    def test_unavailable_extension_reports_error(self, monkeypatch, qapp):
        monkeypatch.setattr(ocr_engines, "probe_ppocr_rust", lambda: (False, None, None))
        assert "不可用" in ocr_engines.PpOcrRustEngine().recognize(_image(), "text")


class TestReleaseAndStatus:

    def test_release_closes_the_engine(self, pp_engine, fake_ppocr):
        pp_engine.initialize()
        engine = fake_ppocr.instances[0]
        pp_engine.release()
        # 模型有几十 MB，必须当场释放而不是等进程退出
        assert engine.closed is True
        assert pp_engine._engine is None

    def test_release_without_engine_is_harmless(self, pp_engine):
        pp_engine.release()
        assert pp_engine._engine is None

    def test_is_loaded_tracks_the_object(self, pp_engine):
        assert pp_engine.is_loaded() is False
        pp_engine.initialize()
        assert pp_engine.is_loaded() is True
        pp_engine.release()
        assert pp_engine.is_loaded() is False

    def test_status_text_tracks_the_object(self, pp_engine):
        assert pp_engine.status_text() == "未初始化"
        pp_engine.initialize()
        assert "已初始化" in pp_engine.status_text()


class _FakeOcrEngine(OcrEngine):
    """最小可用引擎，用来验收注册表行为（不碰任何真实扩展）。"""

    name = "fake"
    label = "Fake"

    def __init__(self, available=True):
        super().__init__()
        self._available = available
        self.loaded = False
        self.released = False
        self.raises = None

    def is_available(self):
        return self._available

    def initialize(self, language=None):
        self.loaded = True
        return True

    def recognize(self, pixmap, return_format="dict"):
        if self.raises is not None:
            raise self.raises
        rows = [[[[0, 0], [9, 0], [9, 5], [0, 5]], "hello", 0.9]]
        return format_result(rows, return_format, 0.0)

    def release(self):
        self.released = True

    def is_loaded(self):
        return self.loaded


@pytest.fixture
def manager(monkeypatch):
    """单例，每条用例给一个干净的实例。

    注册表清空：内建引擎的可用性取决于本机有没有扩展和模型，留着会让「自动选择」
    这类断言随环境变化。
    """
    monkeypatch.setattr(om.OCRManager, "_instance", None, raising=False)
    monkeypatch.setattr(om.OCRManager, "_initialized", False, raising=False)
    mgr = om.OCRManager()
    mgr._engines.clear()
    return mgr


class TestManagerRegistry:

    def test_registered_engine_can_be_selected_and_recognized(self, manager):
        engine = _FakeOcrEngine()
        manager.register_engine(engine)

        assert manager.get_available_engines() == ["fake"]
        assert manager.set_engine("fake") is True
        assert manager.recognize_pixmap(_image())["data"][0]["text"] == "hello"

    def test_unavailable_engine_is_not_auto_selected(self, manager):
        manager.register_engine(_FakeOcrEngine(available=False))
        assert manager.get_available_engines() == []
        assert manager.initialize() is False
        assert manager.get_last_error() == "没有可用的 OCR 引擎"

    def test_set_engine_rejects_unknown_and_unavailable(self, manager):
        manager.register_engine(_FakeOcrEngine(available=False))
        assert manager.set_engine("nope") is False
        assert manager.set_engine("fake") is False
        assert manager.get_current_engine() is None

    def test_release_engine_releases_every_engine(self, manager):
        first, second = _FakeOcrEngine(), _FakeOcrEngine()
        second.name = "fake2"
        manager.register_engine(first)
        manager.register_engine(second)

        manager.release_engine()

        assert (first.released, second.released) == (True, True)
        assert manager.get_current_engine() is None

    def test_broken_engine_exception_is_contained(self, manager):
        """引擎可能是第三方包，它抛的异常不该冒到 UI 线程。"""
        engine = _FakeOcrEngine()
        engine.raises = RuntimeError("引擎炸了")
        manager.register_engine(engine)
        manager.set_engine("fake")

        result = manager.recognize_pixmap(_image())

        assert result["code"] == -1
        assert "引擎炸了" in result["msg"]

    def test_pp_variant_registers_only_ppocr_rust(self):
        assert [e.name for e in ocr_engines.builtin_engines()] == ["ppocr_rust"]
