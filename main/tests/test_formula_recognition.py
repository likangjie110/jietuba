# -*- coding: utf-8 -*-
"""公式识别 → LaTeX：引擎契约 + 入口 + 不可用时的显式降级。

引擎注册表在本模块里是空的（见下面的 ``_clean_registry``）：本机可能装了公式模型，
那样内建引擎就是可用的，判据会跟着机器漂移。这里验的是：
- 有可用引擎（用例里注册一个 stub）时，入口把 LaTeX 展示出来并复制；
- 没有引擎时入口**显式提示 + 记日志**并返回 False，不静默失败、不抛异常；
- 引擎抛异常也被注册表兜住，入口照旧走「没识别到」。
"""

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

from core import actions
from ocr import formula


class _StubEngine(formula.FormulaEngine):
    name = "stub_formula"
    label = "Stub Formula Engine"

    def __init__(self, latex: str = "E = mc^2", available: bool = True, raises: bool = False):
        super().__init__()
        self._latex = latex
        self._available = available
        self._raises = raises
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def recognize_formula(self, pixmap) -> str:
        self.calls += 1
        if self._raises:
            raise RuntimeError("模型没加载起来")
        return self._latex


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """本模块的判据都以「注册表里只有用例自己注册的引擎」为前提。

    ``ocr/__init__`` 会把内建引擎登记进去，而内建引擎可不可用随机器变化（本机下好
    公式模型、扩展也重建过时 ``ppocr_formula`` 就是可用的），不隔离的话「没有引擎」
    这类断言会跟着环境漂移。换掉注册表本身，用例结束由 monkeypatch 还原。
    """
    monkeypatch.setattr(formula, "_ENGINES", {})
    yield


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    return get_tool_settings_manager()


def _image() -> QImage:
    image = QImage(60, 30, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    return image


class TestFormulaRegistry:
    def test_no_engine_means_unavailable(self):
        """注册表里一个引擎都没有时必须报「不可用」，不能假装能用。"""
        assert formula.is_formula_available() is False
        assert formula.available_formula_engines() == []

    def test_unavailable_engine_returns_empty_string(self):
        formula.register_formula_engine(_StubEngine(available=False))

        assert formula.is_formula_available() is False
        assert formula.recognize_formula(_image()) == ""

    def test_available_engine_returns_latex(self):
        engine = _StubEngine("\\frac{a}{b}")
        formula.register_formula_engine(engine)

        assert formula.is_formula_available() is True
        assert formula.available_formula_engines() == ["stub_formula"]
        assert formula.recognize_formula(_image()) == "\\frac{a}{b}"
        assert engine.calls == 1

    def test_engine_by_name(self):
        formula.register_formula_engine(_StubEngine("x^2"))

        assert formula.recognize_formula(_image(), engine_name="stub_formula") == "x^2"
        # 未注册的名字：如实返回空串（并记日志），不是抛异常
        assert formula.recognize_formula(_image(), engine_name="nope") == ""

    def test_engine_exception_is_swallowed(self):
        engine = _StubEngine(raises=True)
        formula.register_formula_engine(engine)

        assert formula.recognize_formula(_image()) == ""
        assert engine.last_error

    def test_empty_result_is_empty_string(self):
        formula.register_formula_engine(_StubEngine("   "))

        assert formula.recognize_formula(_image()) == ""


class _FakeApp:
    config_manager = None


def _patch_capture(monkeypatch):
    monkeypatch.setattr(actions, "capture_at_cursor", lambda app: (
        _image(), QRectF(0, 0, 60, 30)))
    monkeypatch.setattr(actions, "_flash_capture_mask", lambda rect, app: None)


class TestFormulaAction:
    def test_without_an_engine_the_action_prompts_and_logs(self, monkeypatch, qapp, config):
        _patch_capture(monkeypatch)
        warnings, logged = [], []
        monkeypatch.setattr("ui.dialogs.show_warning_dialog",
                            lambda parent, title, message: warnings.append((title, message)))
        # patch 目标是 actions 自己的命名空间：它 import 的是符号，不是模块
        monkeypatch.setattr(actions, "log_warning",
                            lambda message, *a, **k: logged.append(
                                getattr(message, "template", str(message))))

        assert actions.run_action("recognize_formula", _FakeApp()) is False

        assert warnings, "没有向用户提示公式引擎不可用"
        assert any("公式识别不可用" in message for message in logged), logged

    def test_with_an_engine_it_shows_and_copies_the_latex(self, monkeypatch, qapp, config):
        _patch_capture(monkeypatch)
        engine = _StubEngine("\\int_0^1 x^2 dx")
        formula.register_formula_engine(engine)
        shown = []
        monkeypatch.setattr("ui.formula_window.show_formula_result",
                            lambda latex, parent=None: shown.append(latex))
        clipboard = qapp.clipboard()
        previous = clipboard.text()
        try:
            clipboard.clear()
            assert actions.run_action("recognize_formula", _FakeApp()) is True
            assert shown == ["\\int_0^1 x^2 dx"]
            assert clipboard.text() == "\\int_0^1 x^2 dx"
        finally:
            clipboard.setText(previous)

    def test_engine_that_returns_nothing_does_not_show_a_window(self, monkeypatch, qapp, config):
        _patch_capture(monkeypatch)
        formula.register_formula_engine(_StubEngine(""))
        shown = []
        monkeypatch.setattr("ui.dialogs.show_text_dialog",
                            lambda parent, title, content: shown.append(content))
        monkeypatch.setattr(actions, "log_warning", lambda *a, **k: None)

        assert actions.run_action("recognize_formula", _FakeApp()) is False
        assert shown == []

    def test_action_is_registered_and_bindable(self):
        action = actions.ACTIONS_BY_ID["recognize_formula"]
        assert action.silent_capture is True
        assert "recognize_formula" in actions.GESTURE_ACTION_IDS


class TestFormulaWindow:
    def _window(self, latex=r"\frac{a}{b}"):
        from ui.formula_window import FormulaWindow

        return FormulaWindow(latex)

    def test_window_shows_the_latex_in_the_preview_and_the_source(self, qapp):
        window = self._window()
        try:
            assert window.preview.latex() == r"\frac{a}{b}"
            assert window.latex() == r"\frac{a}{b}"
        finally:
            window.deleteLater()

    def test_editing_the_source_refreshes_the_preview(self, qapp):
        window = self._window()
        try:
            window.source_edit.setPlainText(r"\sqrt{x+1}")
            assert window._refresh_preview() is True
            assert window.preview.latex() == r"\sqrt{x+1}"
        finally:
            window.deleteLater()

    def test_copy_latex_puts_the_edited_source_on_the_clipboard(self, qapp):
        window = self._window()
        try:
            window.source_edit.setPlainText(r"E = mc^2")
            assert window.copy_latex() is True
            assert qapp.clipboard().text() == r"E = mc^2"
        finally:
            window.deleteLater()

    def test_copy_as_image_puts_a_non_empty_image_on_the_clipboard(self, qapp):
        window = self._window()
        try:
            assert window.copy_image() is True
            image = qapp.clipboard().image()
            assert image is not None and not image.isNull()
            assert image.width() > 10 and image.height() > 10
        finally:
            window.deleteLater()

    def test_empty_latex_is_not_copied(self, qapp):
        window = self._window("")
        try:
            assert window.copy_latex() is False
            assert window.copy_image() is False
        finally:
            window.deleteLater()

    def test_broken_latex_still_opens_and_copies(self, qapp):
        """渲染器不抛异常；即使解析出怪东西，窗口也要能开、源码也要能复制。"""
        window = self._window(r"\frac{a}{")
        try:
            assert window._refresh_preview() is True
            assert window.copy_latex() is True
        finally:
            window.deleteLater()
