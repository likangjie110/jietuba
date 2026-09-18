# -*- coding: utf-8 -*-
"""内建公式引擎：Rust 绑定的探测 + 外部 HTTP 服务的协议层。

只替换传输这一层（模块级 ``_post_json``）：本机没有可用的公式服务，协议层（请求体、
请求头、响应形状）只能在假传输上验证；探测与识别逻辑本身都跑真实实现。
"""

import base64
import importlib.util
import os
import sys

import pytest
from PySide6.QtGui import QColor, QImage

from ocr import formula, formula_engines
from ocr.formula_engines import (
    DEFAULT_SERVICE_TIMEOUT,
    ExternalFormulaServiceEngine,
    PPFormulaNetEngine,
)

SERVICE_URL = "https://formula.example.test/api/recognize"


@pytest.fixture(autouse=True)
def _qt_app(qapp):
    """QImage 与 PNG 编码都要 Qt 应用先起来（conftest 的 qapp 是会话级的）。"""
    return qapp


@pytest.fixture
def service_settings(isolated_tool_settings):
    """外部服务配置落在临时 QSettings 上；用例结束把三个键还原。"""
    from settings import get_tool_settings_manager

    manager = get_tool_settings_manager()
    manager.set_app_setting("formula_service_url", "")
    manager.set_app_setting("formula_service_api_key", "")
    manager.set_app_setting("formula_service_timeout", DEFAULT_SERVICE_TIMEOUT)
    yield manager
    manager.set_app_setting("formula_service_url", "")
    manager.set_app_setting("formula_service_api_key", "")
    manager.set_app_setting("formula_service_timeout", DEFAULT_SERVICE_TIMEOUT)


class _FakeTransport:
    """假的传输层：记下每次调用的参数，按脚本返回响应或抛异常。"""

    def __init__(self, response=None, raises=None):
        self.calls = []
        self._response = {} if response is None else response
        self._raises = raises

    def __call__(self, url, payload, headers, timeout):
        self.calls.append(
            {"url": url, "payload": payload, "headers": dict(headers), "timeout": timeout}
        )
        if self._raises is not None:
            raise self._raises
        return self._response

    @property
    def last(self):
        return self.calls[-1]


def _install_transport(monkeypatch, response=None, raises=None) -> _FakeTransport:
    transport = _FakeTransport(response, raises)
    monkeypatch.setattr(formula_engines, "_post_json", transport)
    return transport


def _image(width: int = 48, height: int = 24) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    return image


def _decoded_image(payload) -> bytes:
    return base64.b64decode(payload["image_base64"])


@pytest.fixture
def no_model(monkeypatch):
    """让探测认为模型文件缺失。

    ``models/formula/`` 里有模型时 ``ppocr_formula`` 就是可用的（本机下过模型），
    不注入的话「不可用」这套判据会随机器漂移。注入的只是「文件在不在」这一个事实，
    原因怎么拼、不可用时怎么降级仍然是真实代码。
    """
    monkeypatch.setattr(
        formula_engines,
        "_missing_model_files",
        lambda: [formula_engines.FORMULA_MODEL_REL_PATH],
    )


# ══════════════════════════════════════════════════════════
# ppocr_formula（Rust 侧绑定）
# ══════════════════════════════════════════════════════════

class TestPPFormulaNetEngine:
    def test_unavailable_reason_names_the_missing_piece(self, no_model):
        """缺模型（或扩展）时原因必须说清缺哪个。"""
        engine = PPFormulaNetEngine()

        assert engine.is_available() is False
        reason = engine.last_error or ""
        assert "ppocr_rust" in reason or "模型" in reason
        # 一句光秃秃的「不可用」不算说清
        assert len(reason) > len("不可用")

    def test_probe_caches_result(self, no_model):
        engine = PPFormulaNetEngine()

        assert engine.is_available() is False
        first = engine._probe
        assert first is not None
        assert engine.is_available() is False
        assert engine._probe is first

    def test_recognize_returns_empty_string_when_unavailable(self, no_model):
        engine = PPFormulaNetEngine()
        assert engine.recognize_formula(_image()) == ""
        assert engine.last_error

    def test_initialize_fails_without_model(self, no_model):
        engine = PPFormulaNetEngine()
        assert engine.initialize() is False
        assert engine.last_error
        assert engine._engine is None

    def test_release_is_safe_without_session(self):
        engine = PPFormulaNetEngine()
        engine.release()
        engine.release()
        assert engine._engine is None

    def test_model_paths_resolve_under_models_formula(self):
        model, tokenizer = formula_engines.formula_model_paths()

        assert model.endswith(os.path.join("models", "formula", "PP-FormulaNet_plus-M.onnx"))
        assert tokenizer.endswith(os.path.join("models", "formula", "tokenizer.json"))

    def test_frozen_build_finds_models_next_to_the_executable(self, monkeypatch, tmp_path):
        """打包后模型在「可执行文件同级目录的 models/」下（build_macos_app.py 放的位置），
        而 ResourceManager 走的是 _MEIPASS —— 两条路都要能找到，否则发行版里本地公式
        识别永远报「缺模型」。这条把「打包后的布局」造出来验一遍。
        """
        exe_dir = tmp_path / "Jietuba.app" / "Contents" / "MacOS"
        formula_dir = exe_dir / "models" / "formula"
        formula_dir.mkdir(parents=True)
        (formula_dir / "PP-FormulaNet_plus-M.onnx").write_bytes(b"stub")
        (formula_dir / "tokenizer.json").write_bytes(b"{}")

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "Jietuba"))

        model, tokenizer = formula_engines.formula_model_paths()

        assert model == str(formula_dir / "PP-FormulaNet_plus-M.onnx")
        assert tokenizer == str(formula_dir / "tokenizer.json")

    def test_frozen_build_falls_back_to_meipass(self, monkeypatch, tmp_path):
        """模型被打进包内时（_MEIPASS/models）同样要能找到。"""
        exe_dir = tmp_path / "MacOS"
        exe_dir.mkdir()
        meipass = tmp_path / "_MEI123"
        formula_dir = meipass / "models" / "formula"
        formula_dir.mkdir(parents=True)
        (formula_dir / "PP-FormulaNet_plus-M.onnx").write_bytes(b"stub")
        (formula_dir / "tokenizer.json").write_bytes(b"{}")

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "Jietuba"))
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

        model, _ = formula_engines.formula_model_paths()

        assert model == str(formula_dir / "PP-FormulaNet_plus-M.onnx")

    def test_frozen_build_without_models_reports_the_missing_files(self, monkeypatch, tmp_path):
        """打包后没有模型：路径要指向 exe 同级（告诉用户该往哪放），缺失清单要如实报。"""
        exe_dir = tmp_path / "MacOS"
        exe_dir.mkdir()
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "Jietuba"))
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)

        model, tokenizer = formula_engines.formula_model_paths()

        assert model.startswith(str(exe_dir))
        assert formula_engines._missing_model_files()

    def test_available_when_model_and_binding_are_present(self):
        """模型与扩展都就位时必须报可用（本机下过模型、也重建过扩展）。

        发行版**不带**公式模型，所以缺件时这条是 skip：它验的是「模型就位后探测别说
        不可用」，不是「任何机器上都得有模型」。
        """
        missing = formula_engines._missing_model_files()
        if missing:
            pytest.skip("本机没有公式模型: " + ", ".join(missing))
        if importlib.util.find_spec("ppocr_rust") is None:
            pytest.skip("本机没装 ppocr_rust 扩展")
        engine = PPFormulaNetEngine()

        assert engine.is_available() is True
        assert engine.last_error is None


# ══════════════════════════════════════════════════════════
# external_service（HTTP 公式服务）
# ══════════════════════════════════════════════════════════

class TestExternalFormulaServiceEngine:
    def test_unavailable_without_url(self, service_settings):
        engine = ExternalFormulaServiceEngine()

        assert engine.is_available() is False
        assert "URL" in engine.last_error

    def test_availability_follows_config_without_restart(self, service_settings):
        engine = ExternalFormulaServiceEngine()
        assert engine.is_available() is False

        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        assert engine.is_available() is True

        service_settings.set_app_setting("formula_service_url", "   ")
        assert engine.is_available() is False

    def test_recognize_without_url_returns_empty(self, service_settings, monkeypatch):
        transport = _install_transport(monkeypatch)
        engine = ExternalFormulaServiceEngine()

        assert engine.recognize_formula(_image()) == ""
        assert transport.calls == []

    @pytest.mark.parametrize(
        "response",
        [
            {"latex": "\\frac{a}{b}"},
            {"result": "\\frac{a}{b}"},
            {"data": {"latex": "\\frac{a}{b}"}},
        ],
    )
    def test_recognize_parses_known_response_shapes(
        self, service_settings, monkeypatch, response
    ):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        transport = _install_transport(monkeypatch, response=response)

        assert ExternalFormulaServiceEngine().recognize_formula(_image()) == "\\frac{a}{b}"
        assert transport.last["url"] == SERVICE_URL

    def test_recognize_strips_whitespace(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        _install_transport(monkeypatch, response={"latex": "  x^2 \n"})

        assert ExternalFormulaServiceEngine().recognize_formula(_image()) == "x^2"

    def test_recognize_returns_empty_without_latex(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        _install_transport(monkeypatch, response={"message": "no formula found"})
        engine = ExternalFormulaServiceEngine()

        assert engine.recognize_formula(_image()) == ""
        assert engine.last_error

    def test_transport_error_is_swallowed(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        _install_transport(monkeypatch, raises=RuntimeError("connection refused"))
        engine = ExternalFormulaServiceEngine()

        assert engine.recognize_formula(_image()) == ""
        assert "connection refused" in (engine.last_error or "")

    def test_request_carries_base64_png_and_bearer_token(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        service_settings.set_app_setting("formula_service_api_key", "sk-test-123")
        transport = _install_transport(monkeypatch, response={"latex": "E=mc^2"})

        assert ExternalFormulaServiceEngine().recognize_formula(_image()) == "E=mc^2"

        payload = transport.last["payload"]
        assert set(payload) == {"image_base64"}
        assert _decoded_image(payload).startswith(b"\x89PNG\r\n\x1a\n")
        assert transport.last["headers"]["Authorization"] == "Bearer sk-test-123"

    def test_request_has_no_authorization_header_without_key(
        self, service_settings, monkeypatch
    ):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        transport = _install_transport(monkeypatch, response={"latex": "E=mc^2"})

        ExternalFormulaServiceEngine().recognize_formula(_image())

        assert "Authorization" not in transport.last["headers"]

    def test_timeout_comes_from_settings(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        service_settings.set_app_setting("formula_service_timeout", 30)
        transport = _install_transport(monkeypatch, response={"latex": "E=mc^2"})

        ExternalFormulaServiceEngine().recognize_formula(_image())

        assert transport.last["timeout"] == 30

    def test_blank_pixmap_does_not_hit_the_service(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        transport = _install_transport(monkeypatch, response={"latex": "E=mc^2"})

        assert ExternalFormulaServiceEngine().recognize_formula(QImage()) == ""
        assert transport.calls == []


class TestExternalFormulaServiceVerify:
    def test_verify_ok_with_configured_service(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        transport = _install_transport(monkeypatch, response={"latex": ""})

        assert ExternalFormulaServiceEngine().verify() == (True, "")
        # 探测用的是一张 1×1 白图，不是截图
        assert _decoded_image(transport.last["payload"]) == formula_engines._white_pixel_png()

    def test_verify_reports_transport_failure(self, service_settings, monkeypatch):
        service_settings.set_app_setting("formula_service_url", SERVICE_URL)
        _install_transport(monkeypatch, raises=RuntimeError("timed out"))
        engine = ExternalFormulaServiceEngine()

        ok, reason = engine.verify()

        assert ok is False
        assert "timed out" in reason
        assert "timed out" in engine.last_error

    def test_verify_without_url(self, service_settings, monkeypatch):
        transport = _install_transport(monkeypatch)

        ok, reason = ExternalFormulaServiceEngine().verify()

        assert ok is False
        assert reason
        assert transport.calls == []

    def test_verify_uses_args_before_saving(self, service_settings, monkeypatch):
        """设置页允许「先验证、后保存」：传参优先于已存的配置。"""
        transport = _install_transport(monkeypatch, response={})

        ok, reason = ExternalFormulaServiceEngine().verify(
            url="https://draft.example.test/formula", api_key="draft-key"
        )

        assert (ok, reason) == (True, "")
        assert transport.last["url"] == "https://draft.example.test/formula"
        assert transport.last["headers"]["Authorization"] == "Bearer draft-key"


class TestBuiltinFormulaEngines:
    def test_order_and_labels(self, service_settings):
        engines = formula_engines.builtin_formula_engines()

        assert [e.name for e in engines] == ["ppocr_formula", "external_service"]
        assert [e.label for e in engines] == [
            "PP-FormulaNet (Rust)",
            "External Formula Service",
        ]
        assert all(isinstance(e, formula.FormulaEngine) for e in engines)

    def test_fresh_instances_are_independent(self, service_settings):
        first, second = (
            formula_engines.builtin_formula_engines(),
            formula_engines.builtin_formula_engines(),
        )

        assert first[0] is not second[0]
        assert isinstance(second[0], PPFormulaNetEngine)
        assert isinstance(second[1], ExternalFormulaServiceEngine)
