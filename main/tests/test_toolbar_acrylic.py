# -*- coding: utf-8 -*-
"""工具栏的亚克力（毛玻璃）背景接线。

判定的对象是**应用真正在用的那个工具栏控件**（`ui/toolbar.py` 的 `Toolbar`），不是演示
控件：这里构造真工具栏并驱动真实的 `showEvent` → 平台入口 → `_paint_toolbar_frame`，
只把平台入口换成假实现（因为单测里不需要真的往窗口上挂系统视图；真实原生效果由真机
探针负责）。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from ui.toolbar import ACRYLIC_FILL_ALPHA, Toolbar


def _render_center_pixel(toolbar):
    """把真工具栏渲染进一张图，取中心的填充像素（跳过主题色描边）。"""
    image = QImage(toolbar.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    toolbar.render(image)
    return image.pixelColor(toolbar.width() // 2, toolbar.height() // 2)


class TestAcrylicFill:
    """底色策略：亚克力生效→半透明；否则→不透明白（外观不回归）。"""

    def test_without_acrylic_the_frame_is_opaque(self, qapp):
        """顶层工具栏 + 原生不可用 → 保持原来的不透明白底。"""
        toolbar = Toolbar()
        try:
            toolbar._translucent_frame = False

            probe = _render_center_pixel(toolbar)

            # 中心落在按钮区，按钮自己那层极淡底色叠在不透明白底上 → 近白且完全不透明
            assert probe.alpha() == 255, "没有亚克力时工具栏必须是不透明白底（原外观）"
            assert probe.lightness() > 230
        finally:
            toolbar.deleteLater()
            qapp.processEvents()

    def test_with_acrylic_the_fill_is_translucent(self, qapp):
        toolbar = Toolbar()
        try:
            toolbar._translucent_frame = True

            probe = _render_center_pixel(toolbar)

            # 半透明：背后的系统模糊层能透出来（合成取整会有几点的误差）
            assert abs(probe.alpha() - ACRYLIC_FILL_ALPHA) <= 8, probe.alpha()
            assert 0 < probe.alpha() < 255
        finally:
            toolbar.deleteLater()
            qapp.processEvents()


class TestShowAppliesAcrylic:
    """显示时应用一次：能力可用就走亚克力，不可用就保持原外观（且工具栏仍然可见可用）。"""

    @staticmethod
    def _show(toolbar, monkeypatch, *, applied):
        calls = []

        def _apply(window, *args, **kwargs):
            calls.append(window)
            return applied

        monkeypatch.setattr("core.platform.window_ops.apply_acrylic_background", _apply)
        monkeypatch.setattr("core.logger.log_debug", lambda *_a, **_k: None)
        toolbar.show()
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        return calls

    def test_available_platform_turns_acrylic_on(self, qapp, monkeypatch):
        toolbar = Toolbar()
        try:
            calls = self._show(toolbar, monkeypatch, applied=True)

            assert calls and calls[0] is toolbar       # 驱动的是工具栏本体
            assert toolbar._translucent_frame is True
            assert toolbar._acrylic_native is True
        finally:
            toolbar.close()
            toolbar.deleteLater()
            qapp.processEvents()

    def test_unavailable_platform_keeps_the_old_look(self, qapp, monkeypatch):
        toolbar = Toolbar()
        try:
            assert self._show(toolbar, monkeypatch, applied=False)

            # 顶层窗口 + 原生不可用：保持不透明（和改动前一致）
            assert toolbar._translucent_frame is False
            # 降级后仍然可见、尺寸非零、按钮还在
            assert toolbar.isVisible()
            assert toolbar.width() > 0 and toolbar.height() > 0
            assert toolbar._buttons, "降级后工具栏的按钮不能消失"
        finally:
            toolbar.close()
            toolbar.deleteLater()
            qapp.processEvents()

    def test_platform_failure_is_swallowed(self, qapp, monkeypatch):
        """平台调用抛异常时不能把显示流程带崩。"""
        toolbar = Toolbar()
        try:
            def _boom(*_a, **_k):
                raise RuntimeError("原生调用炸了")

            monkeypatch.setattr("core.platform.window_ops.apply_acrylic_background", _boom)
            monkeypatch.setattr("core.logger.log_exception", lambda *_a, **_k: None)
            toolbar.show()
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()

            assert toolbar._translucent_frame is False
            assert toolbar.isVisible()
        finally:
            toolbar.close()
            toolbar.deleteLater()
            qapp.processEvents()

    def test_child_toolbar_degrades_to_translucent(self, qapp, monkeypatch):
        """截图会话里的工具栏是子部件：拿不到窗口级模糊，但要半透明透出冻结画面。"""
        parent = QWidget()
        parent.resize(600, 200)
        parent.show()                       # 父窗口不显示时子部件不会收到 showEvent
        toolbar = Toolbar(parent)
        try:
            assert self._show(toolbar, monkeypatch, applied=False)

            assert toolbar._translucent_frame is True, "子部件必须走半透明降级"
            assert toolbar._acrylic_native is False, "子部件拿不到原生模糊"
            probe = _render_center_pixel(toolbar)
            assert 0 < probe.alpha() < 255, "半透明降级时底色要透出父窗口画面"
        finally:
            toolbar.close()
            toolbar.deleteLater()
            parent.close()
            parent.deleteLater()
            qapp.processEvents()
