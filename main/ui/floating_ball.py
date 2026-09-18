# -*- coding: utf-8 -*-
"""桌面悬浮球：常驻桌面的一枚圆形按钮，单击截图、双击开剪贴板、右键给托盘菜单。

它是「桌面工具栏 = 悬浮球模式」的控件本体（显示/隐藏由应用侧接线决定）。三个约束
决定了它的形状：

- **点击不能抢走前台焦点**：单击是「截图」，而「UI 检测」要按鼠标下面是谁做判断；
  悬浮球一旦变成前台窗口，检测到的就是它自己。所以窗口标志里带
  ``WindowDoesNotAcceptFocus``。
- **单击与拖动互斥**：拖动是移动悬浮球，不该顺手触发截图；移动超过阈值才算拖动。
- **单击要等一下再执行**：双击的第一次释放与单击在 Qt 里是同一个事件，立即执行会让
  用户双击时先截一张图。单击因此延迟一个系统双击间隔再跑，双击事件到了就取消它。

平台差异不写在这里：置顶用 Qt 窗口标志，macOS「失焦不隐藏」（Tool 窗口默认会随应用
失焦隐藏）交给 ``core/platform/window_ops.keep_visible_when_inactive``。
"""

from PySide6.QtCore import QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from core.logger import T, log_debug, log_exception, log_warning
from core.platform import window_ops

#: 位置配置键。``get_app_setting`` / ``set_app_setting`` 会自己补 ``app/`` 前缀，
#: 这里只写不带前缀的键名（实际的存储键是 ``app/desktop_toolbar_position``）。
POSITION_KEY = "desktop_toolbar_position"


class FloatingBall(QWidget):
    """无边框、置顶的圆形悬浮球。"""

    #: 直径（逻辑像素）
    DIAMETER = 52
    #: 默认位置离屏幕可用区右/下边缘的距离
    EDGE_MARGIN = 24
    #: 移动超过这个距离（逻辑像素）就算拖动，不再触发单击
    DRAG_THRESHOLD = 4
    #: 图标占圆直径的比例
    ICON_RATIO = 0.56

    def __init__(self, parent=None):
        super().__init__(parent)

        self._drag_offset = None     # 按下时鼠标相对窗口左上角的偏移
        self._press_global = None    # 按下时的全局坐标（算拖动距离用）
        self._dragged = False
        self._icon = self._load_icon()

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._run_screenshot_action)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.DIAMETER, self.DIAMETER)
        self.move(self._start_position())

    # ──────────────────────────────────────────────
    # 显示 / 隐藏
    # ──────────────────────────────────────────────

    def show_at_saved_position(self) -> None:
        """按配置里的位置显示；配置缺失或非法时用主屏右下角的默认位置。"""
        self.move(self._start_position())
        self.show()
        self.raise_()
        # macOS 的 Tool 窗口默认随应用失焦隐藏，悬浮球必须一直在，交给平台层处理
        window_ops.keep_visible_when_inactive(self)

    def hide_ball(self) -> None:
        """隐藏悬浮球（保留窗口，下次 show_at_saved_position 复用）。"""
        # 已排队但还没执行的单击要作废：球都没了，用户不会期望它再截一张图
        self._click_timer.stop()
        self.hide()

    # ──────────────────────────────────────────────
    # 位置持久化
    # ──────────────────────────────────────────────

    @staticmethod
    def parse_position(raw) -> QPoint | None:
        """解析 ``"x,y"``；格式不对返回 None，由调用方回落到默认位置。"""
        if raw is None:
            return None
        parts = str(raw).strip().split(",")
        if len(parts) != 2:
            return None
        try:
            return QPoint(int(parts[0]), int(parts[1]))
        except (TypeError, ValueError):
            return None

    @classmethod
    def default_position(cls) -> QPoint:
        """主屏可用区右下角、离边距 ``EDGE_MARGIN`` 的位置。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QPoint(cls.EDGE_MARGIN, cls.EDGE_MARGIN)
        area = screen.availableGeometry()
        return QPoint(
            area.x() + area.width() - cls.DIAMETER - cls.EDGE_MARGIN,
            area.y() + area.height() - cls.DIAMETER - cls.EDGE_MARGIN,
        )

    def _start_position(self) -> QPoint:
        saved = self._read_saved_position()
        return saved if saved is not None else self.default_position()

    def _read_saved_position(self) -> QPoint | None:
        try:
            raw = self._settings().get_app_setting(POSITION_KEY, "")
        except Exception as e:
            log_exception(e, T("读取悬浮球位置"))
            return None
        point = self.parse_position(raw)
        if point is None and str(raw or "").strip():
            log_warning(
                T("悬浮球位置配置无效，回落到默认位置: {value}", value=str(raw).strip()),
                "FloatingBall",
            )
        return point

    def _save_position(self) -> None:
        position = self.pos()
        value = f"{position.x()},{position.y()}"
        try:
            self._settings().set_app_setting(POSITION_KEY, value)
        except Exception as e:
            log_exception(e, T("保存悬浮球位置"))
            return
        log_debug(T("悬浮球位置已保存: {position}", position=value), "FloatingBall")

    @staticmethod
    def _settings():
        from settings import get_tool_settings_manager

        return get_tool_settings_manager()

    # ──────────────────────────────────────────────
    # 鼠标交互
    # ──────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._click_timer.stop()          # 已排队的单击不再执行
        self._dragged = False
        self._press_global = event.globalPosition().toPoint()
        self._drag_offset = event.position().toPoint()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        global_pos = event.globalPosition().toPoint()
        if not self._dragged:
            if (global_pos - self._press_global).manhattanLength() <= self.DRAG_THRESHOLD:
                event.accept()
                return
            self._dragged = True

        self.move(global_pos - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._drag_offset is None:
            super().mouseReleaseEvent(event)
            return

        dragged = self._dragged
        self._dragged = False
        self._drag_offset = None
        self._press_global = None

        if dragged:
            self._save_position()
        else:
            # 延迟一个双击间隔再截图：双击的第一次释放也走到这里（见模块 docstring）
            self._click_timer.start(self._double_click_interval())
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        self._click_timer.stop()
        self._open_clipboard_window()
        event.accept()

    def contextMenuEvent(self, event):
        self._show_tray_menu()
        event.accept()

    @staticmethod
    def _double_click_interval() -> int:
        app = QApplication.instance()
        if app is None:
            return 0
        try:
            return int(app.doubleClickInterval())
        except Exception:
            return 0

    # ──────────────────────────────────────────────
    # 点击行为（应用实例缺失时静默降级，只记一条日志）
    # ──────────────────────────────────────────────

    def _run_screenshot_action(self) -> None:
        app = self._app_instance()
        if app is None:
            log_debug(T("没有应用实例，悬浮球单击不触发动作"), "FloatingBall")
            return
        from core import actions

        actions.run_action("screenshot", app)

    def _open_clipboard_window(self) -> None:
        app = self._app_instance()
        if app is None:
            log_debug(T("没有应用实例，悬浮球双击不打开剪贴板"), "FloatingBall")
            return
        try:
            app.open_clipboard_window()
        except Exception as e:
            log_exception(e, T("悬浮球打开剪贴板窗口"))

    def _show_tray_menu(self) -> None:
        app = self._app_instance()
        if app is None:
            log_debug(T("没有应用实例，悬浮球右键菜单不弹出"), "FloatingBall")
            return
        try:
            menu = app._create_tray_menu()
        except Exception as e:
            log_exception(e, T("悬浮球右键菜单创建失败"))
            return
        if menu is not None:
            menu.exec(QCursor.pos())

    @staticmethod
    def _app_instance():
        """当前 MainApp；未接线或已退出时为 None。"""
        import main_app

        return main_app.main_app_instance()

    # ──────────────────────────────────────────────
    # 绘制
    # ──────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        circle = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
        painter.setBrush(QColor(28, 30, 34, 210))
        painter.drawEllipse(circle)

        if not self._paint_icon(painter, circle):
            self._paint_dot(painter, circle)
        painter.end()

    def _load_icon(self):
        """悬浮球中间的图标：托盘图标优先，其次应用图标；都拿不到返回 None。

        ``ResourceManager.get_tray_icon`` 在当前仓库还不存在，所以这里用 ``getattr``
        探测并按 ``get_app_icon`` 兜底；两者都拿不到（或返回空图标）时返回 None，
        绘制时退回一个圆点——图标坏了不该让悬浮球建不出来。
        """
        from core.resource_manager import ResourceManager

        for getter_name in ("get_tray_icon", "get_app_icon"):
            getter = getattr(ResourceManager, getter_name, None)
            if not callable(getter):
                continue
            try:
                icon = getter()
            except Exception as e:
                log_exception(e, T("加载悬浮球图标"))
                continue
            if icon is not None and not icon.isNull():
                return icon
        return None

    def _paint_icon(self, painter, circle: QRectF) -> bool:
        if self._icon is None or self._icon.isNull():
            return False
        size = int(circle.width() * self.ICON_RATIO)
        target = QRectF(0, 0, size, size)
        target.moveCenter(circle.center())
        self._icon.paint(painter, target.toRect(), Qt.AlignmentFlag.AlignCenter)
        return True

    def _paint_dot(self, painter, circle: QRectF) -> None:
        """图标资源全丢时的兜底：画一个圆点，别让悬浮球变成一块空白。"""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(235, 238, 242, 235))
        radius = circle.width() * 0.16
        painter.drawEllipse(circle.center(), radius, radius)
