# -*- coding: utf-8 -*-
"""钉图交互：点击穿透、分组、焦点模式、关闭其它、加载新内容。

用例建的是**真贴图窗口**（走 `PinManager.create_pin`，与用户从截图里钉图完全同一条路径），
平台调用只在点击穿透那一条上有替身（那是 NSWindow/WS_EX_TRANSPARENT 的平台能力，
真机验证见 {SCRATCH}/pin_probe.log）。
"""

import sys

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent

from pin import pin_actions
from pin.pin_manager import PinManager


def image(width=60, height=40, color="#3366CC") -> QImage:
    picture = QImage(width, height, QImage.Format.Format_ARGB32)
    picture.fill(QColor(color))
    return picture


@pytest.fixture
def manager(qapp, isolated_tool_settings):

    instance = PinManager.instance()
    yield instance
    instance.close_all()
    instance.restore_visibility()
    qapp.processEvents()


@pytest.fixture
def config(isolated_tool_settings):
    from settings import get_tool_settings_manager

    manager = get_tool_settings_manager()
    # 这些用例不测 OCR：真识别会在窗口销毁时留下跑着的线程（teardown 期崩溃的来源）
    manager.set_app_setting("ocr_enabled", False)
    return manager


@pytest.fixture
def pin(manager, config, qapp):
    window = manager.create_pin(image(), QPoint(120, 120), config)
    assert window is not None
    qapp.processEvents()
    yield window
    if window in manager.get_all_pins():
        manager.remove_pin(window)
    qapp.processEvents()


# ── 点击穿透 ──────────────────────────────────────────

class TestClickThrough:
    def test_toggle_calls_the_platform_layer_and_flags_the_window(self, pin, monkeypatch):
        calls = []
        monkeypatch.setattr("core.platform.window_ops.set_click_through",
                            lambda window, enabled, **kwargs: calls.append((window, enabled)) or True)

        assert pin.is_click_through() is False
        assert pin.toggle_click_through() is True
        assert pin.is_click_through() is True
        assert calls[-1][0] is pin and calls[-1][1] is True

        assert pin.toggle_click_through() is True
        assert pin.is_click_through() is False
        assert calls[-1][1] is False

    def test_platform_refusal_is_reported_instead_of_claimed(self, pin, monkeypatch):
        monkeypatch.setattr("core.platform.window_ops.set_click_through",
                            lambda window, enabled, **kwargs: False)
        assert pin.set_click_through(True) is False
        assert pin.is_click_through() is False

    def test_real_platform_call_reaches_the_native_window(self, pin, qapp):
        """真机（macOS）：开启之后底层 NSWindow 真的忽略鼠标事件。"""
        if not pin.set_click_through(True):
            pytest.skip("当前平台不支持点击穿透")
        try:
            from core.platform import detection

            if not detection.IS_MACOS:
                pytest.skip("只有 macOS 能读到 NSWindow.ignoresMouseEvents")
            import objc

            native = objc.objc_object(c_void_p=int(pin.winId())).window()
            assert bool(native.ignoresMouseEvents()) is True
        finally:
            pin.set_click_through(False)
            qapp.processEvents()

    def test_the_action_table_exposes_it(self, pin, monkeypatch):
        monkeypatch.setattr(pin, "toggle_click_through", lambda: True)
        assert pin_actions.run_pin_action("toggle_click_through", pin) is True


# ── 分组 ──────────────────────────────────────────────

class TestGroups:
    def test_join_and_query(self, manager, config, qapp):
        first = manager.create_pin(image(), QPoint(100, 100), config)
        second = manager.create_pin(image(), QPoint(200, 200), config)
        try:
            assert manager.add_to_group(first, "素材") is True
            assert manager.add_to_group(second, "素材") is True
            groups = manager.groups()
            assert list(groups) == ["素材"]
            assert len(groups["素材"]) == 2

            assert manager.remove_from_group(first) is True
            assert len(manager.groups()["素材"]) == 1
        finally:
            manager.close_all()
            qapp.processEvents()

    def test_blank_group_name_is_refused(self, pin):
        assert pin is not None
        assert PinManager.instance().add_to_group(pin, "   ") is False

    def test_deleting_empty_groups_keeps_the_non_empty_ones(self, manager, config, qapp):
        member = manager.create_pin(image(), QPoint(100, 100), config)
        ghost = manager.create_pin(image(), QPoint(220, 220), config)
        try:
            manager.remember_group("素材")
            manager.remember_group("草稿")
            manager.add_to_group(member, "素材")
            manager.add_to_group(ghost, "草稿")
            manager.remove_pin(ghost)              # 草稿组没人了

            removed = manager.delete_empty_groups()

            assert removed == ["草稿"]
            assert "素材" in manager.groups()
            assert len(manager.groups()["素材"]) == 1
            assert manager.delete_empty_groups() == []       # 幂等
        finally:
            manager.close_all()
            qapp.processEvents()

    def test_close_group_closes_only_its_members(self, manager, config, qapp):
        first = manager.create_pin(image(), QPoint(100, 100), config)
        second = manager.create_pin(image(), QPoint(200, 200), config)
        keeper = manager.create_pin(image(), QPoint(300, 300), config)
        manager.add_to_group(first, "素材")
        manager.add_to_group(second, "素材")

        closed = manager.close_group("素材")

        assert closed == 2
        assert manager.count() == 1
        assert keeper in manager.get_all_pins()
        qapp.processEvents()


# ── 焦点模式 / 关闭其它 ───────────────────────────────

class TestFocusAndCloseOthers:
    def test_focus_mode_hides_the_others_and_restores(self, manager, config, qapp):
        first = manager.create_pin(image(), QPoint(100, 100), config)
        second = manager.create_pin(image(), QPoint(200, 200), config)
        third = manager.create_pin(image(), QPoint(300, 300), config)

        assert manager.toggle_focus_mode(first) is True
        qapp.processEvents()
        assert manager.is_focus_mode(first) is True
        assert first.isVisible() is True
        assert second.isVisible() is False
        assert third.isVisible() is False

        assert manager.toggle_focus_mode(first) is True
        qapp.processEvents()
        assert manager.is_focus_mode(first) is False
        assert second.isVisible() is True and third.isVisible() is True

    def test_close_others_keeps_only_the_target(self, manager, config, qapp):
        keeper = manager.create_pin(image(), QPoint(100, 100), config)
        manager.create_pin(image(), QPoint(200, 200), config)
        manager.create_pin(image(), QPoint(300, 300), config)

        closed = keeper.close_other_pins()
        qapp.processEvents()

        assert closed == 2
        assert manager.count() == 1
        assert keeper in manager.get_all_pins()
        assert keeper.isVisible() is True

    def test_closing_leaves_no_dangling_references(self, manager, config, qapp):
        keeper = manager.create_pin(image(), QPoint(100, 100), config)
        doomed = manager.create_pin(image(), QPoint(200, 200), config)
        keeper.close_other_pins()
        qapp.processEvents()

        assert doomed not in manager.get_all_pins()
        assert manager.count() == 1
        # 选中集合里也不该留它（否则下一次整组拖动会去动已销毁的窗口）
        assert doomed not in manager.selected_pins()


# ── 加载新内容 ────────────────────────────────────────

class TestLoadContent:
    def test_loading_replaces_the_image_and_resets_zoom(self, pin, qapp):
        pin.scale_factor = 2.0
        replacement = image(120, 90, "#FF8800")

        assert pin.load_image(replacement) is True
        qapp.processEvents()

        assert (pin._orig_size.width(), pin._orig_size.height()) == (120, 90)
        assert pin.scale_factor == 1.0
        background = pin.canvas.scene.background.image()
        assert (background.width(), background.height()) == (120, 90)
        assert background.pixelColor(60, 45).name().lower() == "#ff8800"
        assert pin.canvas.scene.sceneRect() == QRectF(0, 0, 120, 90)

    def test_loading_clears_the_previous_recognition(self, pin, qapp, monkeypatch):
        """换了图，旧的识别结果必须清掉（它是上一张图的）。"""
        from PySide6.QtWidgets import QWidget

        # 假装上一轮识别留下了一层文字 + 一个「有结果」标记
        stale_layer = QWidget(pin)
        pin._ocr_mgr.ocr_text_layer = stale_layer
        pin._ocr_mgr._ocr_has_result = True
        monkeypatch.setattr(type(pin._ocr_mgr), "init_now", lambda self, force=False: True)

        assert pin.load_image(image(50, 50, "#00FF00")) is True
        qapp.processEvents()

        assert pin._ocr_mgr.ocr_text_layer is None
        assert pin._ocr_mgr.has_result is False

    def test_an_empty_image_is_refused(self, pin):
        assert pin.load_image(QImage()) is False
        assert pin.load_image(None) is False

    def test_recognize_text_now_goes_through_the_ocr_manager(self, pin, monkeypatch):
        calls = []
        monkeypatch.setattr(type(pin._ocr_mgr), "init_now",
                            lambda self, force=False: calls.append(force) or True)
        assert pin.recognize_text_now() is True
        assert calls == [True]

    def test_the_action_table_exposes_both_entries(self, pin, monkeypatch):
        seen = []
        monkeypatch.setattr(pin, "recognize_text_now", lambda: seen.append("ocr") or True)
        monkeypatch.setattr(pin, "load_image_from_file", lambda: seen.append("load") or True)
        assert pin_actions.run_pin_action("recognize_text", pin) is True
        assert pin_actions.run_pin_action("load_content", pin) is True
        assert seen == ["ocr", "load"]


class TestActionRegistry:
    def test_the_new_actions_are_in_the_table(self):
        for action_id in ("toggle_click_through", "toggle_focus_mode", "close_others",
                          "load_content", "recognize_text"):
            assert action_id in pin_actions.PIN_ACTIONS_BY_ID

    def test_every_action_id_is_unique(self):
        ids = [action.id for action in pin_actions.PIN_ACTIONS]
        assert len(ids) == len(set(ids))

    def test_focus_and_close_others_run_through_the_manager(self, manager, config, qapp):
        pin = manager.create_pin(image(), QPoint(150, 150), config)
        other = manager.create_pin(image(), QPoint(250, 250), config)

        assert pin_actions.run_pin_action("toggle_focus_mode", pin) is True
        qapp.processEvents()
        assert other.isVisible() is False

        assert pin_actions.run_pin_action("close_others", pin) is True
        qapp.processEvents()
        assert manager.count() == 1


# ── 拖动（系统接管 / 自己搬窗口 + 工具栏跟随） ──────────────

class TestWindowDragOnRealWindows:
    """拖动在真窗口上跑：窗口真的动了，工具栏真的跟着。

    「交给系统拖动」这条边界（窗口管理器）用替身，其余全是真的：真贴图窗口、真画布视图、
    真工具栏、真 Qt 鼠标事件。贴图挑了个够大的尺寸并放在屏幕中间，免得工具栏被屏幕边缘
    夹紧，掩盖「相对位置有没有保持住」。
    """

    @pytest.fixture
    def pinned(self, manager, config, qapp, monkeypatch):
        # 先钉住「跟随窗口」这条平台能力，测的是我们自己搬窗口的那条路
        monkeypatch.setattr("core.platform.window_ops.attach_follow_window", lambda *_: False)
        window = manager.create_pin(image(300, 200), QPoint(400, 300), config)
        qapp.processEvents()
        window.show_toolbar()
        qapp.processEvents()
        yield window
        if window in manager.get_all_pins():
            manager.remove_pin(window)
        qapp.processEvents()

    @staticmethod
    def _mouse(kind, local, global_pos, *, buttons=Qt.MouseButton.NoButton,
               button=Qt.MouseButton.LeftButton):
        return QMouseEvent(kind, QPointF(local), QPointF(global_pos), button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    @staticmethod
    def _offset(pin):
        return (pin.toolbar.x() - pin.x(), pin.toolbar.y() - pin.y())

    def test_the_toolbar_is_anchored_to_the_pin(self, pinned):
        """工具栏挨着贴图放（下方或上方，间距 2），位置规则真的跑了。"""
        toolbar, pin = pinned.toolbar, pinned
        assert toolbar.isVisible()
        below = toolbar.y() - (pin.y() + pin.height())
        above = pin.y() - (toolbar.y() + toolbar.height())
        assert min(abs(below), abs(above)) == 2
        assert toolbar.width() > 0 and toolbar.height() > 0

    def test_the_pin_really_moves_and_the_toolbar_stays_glued(self, pinned, monkeypatch):
        """平台不接受系统拖动时（Windows/Linux 的常路，或合成器拒绝），自己搬窗口。"""
        pin = pinned
        monkeypatch.setattr("core.platform.window_ops.start_system_move", lambda _w: False)
        before = self._offset(pin)
        start = pin.pos()

        pin.view.mousePressEvent(self._mouse(
            QEvent.Type.MouseButtonPress, QPointF(10, 10),
            QPointF(start.x() + 10, start.y() + 10)))
        assert pin._is_dragging is True

        pin.view.mouseMoveEvent(self._mouse(
            QEvent.Type.MouseMove, QPointF(70, 50),
            QPointF(start.x() + 70, start.y() + 50), buttons=Qt.MouseButton.LeftButton))
        assert pin.pos() == start + QPoint(60, 40)
        assert self._offset(pin) == before          # 工具栏一直贴着

        pin.view.mouseReleaseEvent(self._mouse(
            QEvent.Type.MouseButtonRelease, QPointF(70, 50),
            QPointF(start.x() + 70, start.y() + 50)))
        assert pin._is_dragging is False
        assert pin.cursor().shape() is Qt.CursorShape.ArrowCursor

    def test_a_platform_that_accepts_hands_the_drag_over(self, pinned, monkeypatch):
        """系统接受拖动时，按下就交出去（拖动期间我们收不到鼠标事件），由系统搬窗口。"""
        pin = pinned
        asked = []
        monkeypatch.setattr("core.platform.window_ops.start_system_move",
                            lambda window: asked.append(window) or True)
        start = pin.pos()
        pin.view.mousePressEvent(self._mouse(
            QEvent.Type.MouseButtonPress, QPointF(10, 10),
            QPointF(start.x() + 10, start.y() + 10)))
        assert asked == [pin]
        assert pin._is_dragging is False            # start_window_drag 当场收尾
        assert pin.pos() == start

    def test_the_toolbar_follows_a_move_that_comes_from_somewhere_else(self, pinned):
        """贴图位置被别处改掉（缩放、图像变换、系统接管拖动）时工具栏也要跟上：
        同步挂在 moveEvent 上，而不是只在鼠标拖动里。"""
        pin = pinned
        before = self._offset(pin)
        pin.move(pin.x() + 120, pin.y() + 60)
        assert self._offset(pin) == before

    def test_the_toolbar_becomes_a_native_follow_window_on_macos(self, manager, config, qapp):
        """真机：工具栏真的成了贴图的 AppKit 子窗口——拖动时由系统带着它走。

        这一条是「拖动要跟手」的关键：系统原生拖动期间我们收不到鼠标事件，只有这条
        父子窗口关系能让工具栏不跟丢。
        """
        if sys.platform != "darwin":
            pytest.skip("跟随窗口是 macOS 的能力")

        import objc

        window = manager.create_pin(image(300, 200), QPoint(400, 300), config)
        try:
            window.show_toolbar()
            qapp.processEvents()
            assert window._toolbar_follows_natively is True

            parent_ns = objc.objc_object(c_void_p=int(window.winId())).window()
            toolbar_ns = objc.objc_object(c_void_p=int(window.toolbar.winId())).window()
            assert toolbar_ns in list(parent_ns.childWindows())
        finally:
            if window in manager.get_all_pins():
                manager.remove_pin(window)
            qapp.processEvents()

    def test_a_screen_sized_pin_keeps_free_positioning(self, manager, config, qapp, monkeypatch):
        """贴着屏幕大小的贴图不走系统拖动：系统的窗口拖动会把窗口夹在屏幕可用区里，
        这种贴图交给系统就几乎动不了（实测可拖动范围 = 可用区 − 窗口尺寸）。
        """
        screen = qapp.primaryScreen()
        assert screen is not None
        size = screen.availableGeometry().size()
        window = manager.create_pin(image(size.width(), size.height()), QPoint(0, 30), config)
        try:
            asked = []
            monkeypatch.setattr("core.platform.window_ops.start_system_move",
                                lambda target: asked.append(target) or True)
            qapp.processEvents()
            start = window.pos()

            window.view.mousePressEvent(self._mouse(
                QEvent.Type.MouseButtonPress, QPointF(10, 10),
                QPointF(start.x() + 10, start.y() + 10)))
            assert asked == []                      # 尺寸太大，连问都不问
            assert window._is_dragging is True      # 自己搬窗口
            window.view.mouseMoveEvent(self._mouse(
                QEvent.Type.MouseMove, QPointF(210, 110),
                QPointF(start.x() + 210, start.y() + 110), buttons=Qt.MouseButton.LeftButton))
            assert window.pos() == start + QPoint(200, 100)
        finally:
            if window in manager.get_all_pins():
                manager.remove_pin(window)
            qapp.processEvents()
