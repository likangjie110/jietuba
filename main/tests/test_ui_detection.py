# -*- coding: utf-8 -*-
"""「UI 检测」档位在画布上的行为：不检测 / 仅窗口 / 检测元素。

平台后端自己的测试在 test_platform_window.py（元素矩形、边距外扩、档位回退），
这里测的是上面那一层：「档位 → 框谁」，以及防抖缓存没有被档位改写。

用真实的 CanvasView（离屏），只把平台门面替换成替身。
"""

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import core.platform.window as window
from core.platform.contracts import WindowInfo


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def view(qapp):
    from canvas.scene import CanvasScene
    from canvas.view import CanvasView

    background = QImage(400, 300, QImage.Format.Format_ARGB32)
    background.fill(0xFFFFFFFF)
    scene = CanvasScene(background, QRectF(0, 0, 400, 300))
    canvas_view = CanvasView(scene)
    yield canvas_view
    canvas_view.cleanup()


def _prepare(view, monkeypatch, mode, *, margin=0, element=(120, 130, 180, 170),
             windows=((0, 0, 400, 300),)):
    """装好档位：窗口枚举给出假窗口，元素命中由 ``element`` 决定（None = 没命中）。"""
    calls = []

    monkeypatch.setattr(window, "is_window_enumeration_available", lambda: True)
    monkeypatch.setattr(window, "is_element_detection_available", lambda: element is not None)
    monkeypatch.setattr(window.WindowFinder, "find_windows", lambda self: None)

    def fake_element_at_point(x, y, margin=0):
        calls.append((x, y, margin))
        return element

    monkeypatch.setattr(window, "find_element_at_point", fake_element_at_point)

    view.enable_ui_detection(mode, margin)
    if view.window_finder is not None:      # none 档不会创建 WindowFinder
        view.window_finder.windows = [
            WindowInfo(index, rect, f"窗口{index}") for index, rect in enumerate(windows)
        ]
    return calls


def test_none_mode_frames_nothing(view, monkeypatch):
    _prepare(view, monkeypatch, "none")

    assert view.smart_selection_enabled is False
    assert view._get_ui_detection_rect(QPointF(150, 150)).isEmpty()


def test_window_mode_frames_the_window(view, monkeypatch):
    calls = _prepare(view, monkeypatch, "window")

    assert view._get_ui_detection_rect(QPointF(150, 150)) == QRectF(0, 0, 400, 300)
    assert calls == []          # 窗口档不该去问元素


def test_element_mode_frames_the_element(view, monkeypatch):
    _prepare(view, monkeypatch, "element")

    assert view._get_ui_detection_rect(QPointF(150, 150)) == QRectF(120, 130, 60, 40)


def test_element_mode_passes_the_configured_margin(view, monkeypatch):
    calls = _prepare(view, monkeypatch, "element", margin=8)

    view._get_ui_detection_rect(QPointF(150, 150))

    assert calls and calls[0][2] == 8


def test_element_mode_falls_back_to_the_window(view, monkeypatch):
    _prepare(view, monkeypatch, "element", element=None)

    assert view._get_ui_detection_rect(QPointF(150, 150)) == QRectF(0, 0, 400, 300)


def test_window_mode_is_used_when_the_platform_has_no_element_backend(view, monkeypatch):
    """默认档是 element，但平台没有元素后端时按窗口级工作（别变成什么都不框）。"""
    calls = _prepare(view, monkeypatch, "element", element=None)

    assert view._get_ui_detection_rect(QPointF(150, 150)) == QRectF(0, 0, 400, 300)
    assert calls                       # 试过一次，回退到窗口


def test_small_moves_reuse_the_cached_rect(view, monkeypatch):
    calls = _prepare(view, monkeypatch, "element")

    first = view._get_ui_detection_rect(QPointF(150, 150))
    second = view._get_ui_detection_rect(QPointF(152, 150))   # 2px < 8px 阈值

    assert first == second
    assert len(calls) == 1
