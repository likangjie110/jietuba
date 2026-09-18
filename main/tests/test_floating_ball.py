# -*- coding: utf-8 -*-
"""桌面悬浮球（``ui/floating_ball.py``）。

这里驱动真实控件与真实事件处理函数：构造、QMouseEvent 序列、配置读写都走实现本身，
只把**边界**换成替身——``main_app.main_app_instance``（应用实例）与
``core.actions.run_action``（动作执行入口）。图标资源、真机窗口层级与拖动手感不在
单测覆盖范围内（见文件末尾的说明）。
"""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QIcon, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main_app
from core import actions
from core.resource_manager import ResourceManager
from settings import get_tool_settings_manager
from ui.floating_ball import POSITION_KEY, FloatingBall

#: 配置里的实际存储键（``get_app_setting`` 自己补的 ``app/`` 前缀）
STORED_KEY = f"app/{POSITION_KEY}"


@pytest.fixture(autouse=True)
def _clean_saved_position():
    """每个用例都从「没有配置」开始。

    ``isolated_tool_settings`` 是会话级的，拖动用例写进去的位置会串到后面的用例里，
    「回落默认位置」这类断言就不再可信。
    """
    manager = get_tool_settings_manager()
    manager.qsettings.remove(STORED_KEY)
    yield
    manager.qsettings.remove(STORED_KEY)


class _FakeApp:
    """应用实例替身：只记录被调到的入口，不真的开窗口。"""

    def __init__(self, menu=None):
        self.menu = menu
        self.calls = []

    def open_clipboard_window(self):
        self.calls.append("open_clipboard_window")

    def _create_tray_menu(self):
        self.calls.append("create_tray_menu")
        return self.menu


def _mouse_event(kind, local, global_pos, button, buttons):
    return QMouseEvent(
        kind, QPointF(local), QPointF(global_pos),
        button, buttons, Qt.KeyboardModifier.NoModifier,
    )


def _click(ball: FloatingBall) -> None:
    """在同一位置按下并释放（未移动 = 单击）。"""
    local = ball.rect().center()
    global_pos = ball.mapToGlobal(local)
    ball.mousePressEvent(_mouse_event(
        QEvent.Type.MouseButtonPress, local, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    ball.mouseReleaseEvent(_mouse_event(
        QEvent.Type.MouseButtonRelease, local, global_pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))


def _drag(ball: FloatingBall, delta: QPoint) -> None:
    """按下、拖过 ``delta``、松开。"""
    local = ball.rect().center()
    start_global = ball.mapToGlobal(local)
    end_global = start_global + delta
    ball.mousePressEvent(_mouse_event(
        QEvent.Type.MouseButtonPress, local, start_global,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    ball.mouseMoveEvent(_mouse_event(
        QEvent.Type.MouseMove, local, end_global,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    ball.mouseReleaseEvent(_mouse_event(
        QEvent.Type.MouseButtonRelease, local, end_global,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))


def _wait_out_the_click_delay() -> None:
    """单击是延迟一个系统双击间隔执行的（为了不和双击打架），这里等它跑完。"""
    QTest.qWait(QApplication.doubleClickInterval() + 100)


class TestStartPosition:

    @pytest.mark.parametrize("raw", [None, "", "abc", "1,2,3", "1", " , ", "10;20"])
    def test_bad_or_missing_config_falls_back_to_the_default(self, qapp, raw):
        manager = get_tool_settings_manager()
        if raw is not None:
            manager.set_app_setting(POSITION_KEY, raw)

        ball = FloatingBall()

        assert ball.pos() == FloatingBall.default_position()

    def test_valid_config_is_used(self, qapp):
        get_tool_settings_manager().set_app_setting(POSITION_KEY, "123,456")

        ball = FloatingBall()

        assert ball.pos() == QPoint(123, 456)

    @pytest.mark.parametrize(("raw", "expected"), [
        ("123,456", QPoint(123, 456)),
        ("  120 , 80 ", QPoint(120, 80)),
        ("-30,-40", QPoint(-30, -40)),
    ])
    def test_parse_position_accepts_xy(self, raw, expected):
        assert FloatingBall.parse_position(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "abc", "1,2,3", "1", "1.5,2", " , "])
    def test_parse_position_rejects_junk(self, raw):
        assert FloatingBall.parse_position(raw) is None


class TestClick:

    def test_single_click_runs_the_screenshot_action(self, qapp, monkeypatch):
        app = _FakeApp()
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        seen = []
        monkeypatch.setattr(
            actions, "run_action",
            lambda action_id, instance: seen.append((action_id, instance)),
        )

        ball = FloatingBall()
        _click(ball)
        assert seen == [], "单击不该立刻执行：双击的第一次释放也要等一下"
        _wait_out_the_click_delay()

        assert seen == [("screenshot", app)]

    def test_single_click_without_an_app_only_logs(self, qapp, monkeypatch):
        monkeypatch.setattr(main_app, "main_app_instance", lambda: None)

        ball = FloatingBall()
        _click(ball)
        _wait_out_the_click_delay()          # 不抛异常即可

    def test_double_click_opens_the_clipboard_window(self, qapp, monkeypatch):
        app = _FakeApp()
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)

        ball = FloatingBall()
        local = ball.rect().center()
        ball.mouseDoubleClickEvent(_mouse_event(
            QEvent.Type.MouseButtonDblClick, local, ball.mapToGlobal(local),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))

        assert app.calls == ["open_clipboard_window"]

    def test_double_click_cancels_the_pending_single_click(self, qapp, monkeypatch):
        """双击前的那次释放不能顺带截一张图。"""
        app = _FakeApp()
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        seen = []
        monkeypatch.setattr(
            actions, "run_action",
            lambda action_id, instance: seen.append(action_id),
        )

        ball = FloatingBall()
        _click(ball)
        local = ball.rect().center()
        ball.mouseDoubleClickEvent(_mouse_event(
            QEvent.Type.MouseButtonDblClick, local, ball.mapToGlobal(local),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        _wait_out_the_click_delay()

        assert seen == []
        assert app.calls == ["open_clipboard_window"]


class TestDrag:

    def test_drag_moves_the_ball_and_saves_the_position(self, qapp, monkeypatch):
        app = _FakeApp()
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        seen = []
        monkeypatch.setattr(
            actions, "run_action",
            lambda action_id, instance: seen.append(action_id),
        )
        ball = FloatingBall()
        start = ball.pos()

        _drag(ball, QPoint(90, 70))

        assert ball.pos() == start + QPoint(90, 70)
        stored = get_tool_settings_manager().get_app_setting(POSITION_KEY, "")
        assert stored == f"{start.x() + 90},{start.y() + 70}"
        # 拖动过就不算单击：延迟窗口过去也不该执行动作
        _wait_out_the_click_delay()
        assert seen == []

    def test_a_new_ball_restores_the_dragged_position(self, qapp, monkeypatch):
        """拖动写回配置，重建（=重开程序）后要落在同一处。"""
        monkeypatch.setattr(main_app, "main_app_instance", lambda: _FakeApp())
        ball = FloatingBall()
        _drag(ball, QPoint(-40, 25))

        assert FloatingBall().pos() == ball.pos()

    def test_a_tiny_move_still_counts_as_a_click(self, qapp, monkeypatch):
        app = _FakeApp()
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        seen = []
        monkeypatch.setattr(
            actions, "run_action",
            lambda action_id, instance: seen.append(action_id),
        )
        ball = FloatingBall()
        start = ball.pos()

        _drag(ball, QPoint(1, 1))
        _wait_out_the_click_delay()

        assert ball.pos() == start, "没超过阈值的移动不该真的挪窗口"
        assert seen == ["screenshot"]


class TestContextMenu:

    def test_context_menu_without_an_app_does_not_crash(self, qapp, monkeypatch):
        monkeypatch.setattr(main_app, "main_app_instance", lambda: None)
        ball = FloatingBall()

        ball.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, QPoint(4, 4), QPoint(40, 40)))

    def test_context_menu_asks_the_app_for_the_tray_menu(self, qapp, monkeypatch):
        """菜单构造走应用的托盘菜单入口；这里让它是 None，避免真的 exec 阻塞测试。"""
        app = _FakeApp(menu=None)
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        ball = FloatingBall()

        ball.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, QPoint(4, 4), QPoint(40, 40)))

        assert app.calls == ["create_tray_menu"]

    def test_tray_menu_failure_does_not_crash(self, qapp, monkeypatch):
        app = SimpleNamespace(
            _create_tray_menu=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(main_app, "main_app_instance", lambda: app)
        ball = FloatingBall()

        ball.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, QPoint(4, 4), QPoint(40, 40)))


class TestPainting:

    def test_renders_a_dot_when_no_icon_is_available(self, qapp, monkeypatch):
        """图标资源全丢时也要画得出东西（圆点兜底），不能抛异常。"""
        monkeypatch.setattr(ResourceManager, "get_tray_icon", staticmethod(lambda: QIcon()),
                            raising=False)
        monkeypatch.setattr(ResourceManager, "get_app_icon", staticmethod(lambda: QIcon()))

        ball = FloatingBall()
        pixmap = QPixmap(ball.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        ball.render(pixmap)

        center = QPoint(ball.width() // 2, ball.height() // 2)
        assert pixmap.toImage().pixelColor(center).alpha() > 0
