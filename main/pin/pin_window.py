"""
钉图窗口 - 核心窗口类

架构说明：
- PinWindow：主窗口，只负责窗口管理和子控件布局
- PinCanvasView：唯一内容渲染者，使用 Qt 的 GPU 加速渲染
- PinCanvas：画布核心，包含工具信号路由
- PinOCRManager：OCR 初始化和线程管理
- PinThumbnailMode：缩略图模式逻辑
- PinControlButtons：控制按钮管理器
- PinContextMenu：右键菜单管理器
- PinTranslationHelper：翻译功能助手
"""

from PySide6.QtWidgets import QRubberBand, QApplication, QLabel, QWidget
from PySide6.QtCore import Qt, QPoint, QRect, QSize, QTimer, Signal, QRectF, QEvent
from PySide6.QtGui import (
    QColor, QPixmap, QImage, QPainter, QMouseEvent, QWheelEvent, QKeyEvent,
    QTransform,
)
from .pin_canvas_view import PinCanvasView
from .pin_controls import PinControlButtons
from .pin_context_menu import PinContextMenu
from .pin_translation import PinTranslationHelper
from .pin_border_overlay import PinBorderOverlay
from .pin_ocr_manager import PinOCRManager
from .pin_thumbnail import PinThumbnailMode
from .pin_image_transform import PinImageTransform
from core import log_debug, log_info, log_warning, log_error, safe_event
from core.theme import get_theme
from core.logger import log_exception, T
from core.clipboard_utils import deliver_image_async
from core.platform import window_ops
from ui.fluent_lite.theme import ACCENT
from settings.tool_settings import PIN_OPACITY_RANGE
from . import pin_actions


def _save_filter() -> str:
    """保存对话框的过滤器：由运行时能力派生（见 core/image_formats.py）。"""
    from core.image_formats import save_dialog_filter

    return save_dialog_filter()


#: 交给系统拖动时，窗口最多占屏幕可用区的多少（两边都算）。
#:
#: 系统的窗口拖动会把窗口夹在屏幕可用区里（实测可拖动范围 = 可用区 − 窗口尺寸），
#: 贴图越大余地越小：整屏截图钉出来的贴图交给系统就几乎动不了。留不到四分之一余地时
#: 「贴图能自由摆放」比「拖动跟手」更重要，那种尺寸继续自己搬窗口。
DRAG_MAX_WINDOW_SHARE = 0.75


def pin_config_value(config_manager, getter_name: str, default):
    """读一项贴图配置。

    配置管理器缺失（窗口在测试里被裸建）或读过一次失败时用默认值：贴图的交互参数
    读不出来不该让窗口建不起来。
    """
    getter = getattr(config_manager, getter_name, None) if config_manager else None
    if getter is None:
        return default
    try:
        return getter()
    except Exception as e:
        log_exception(e, T("读取贴图配置"))
        return default


def pin_config_bool(config_manager, getter_name: str, default: bool) -> bool:
    return bool(pin_config_value(config_manager, getter_name, default))


def pin_config_float(config_manager, getter_name: str, default: float, limits) -> float:
    """读一项浮点配置并夹到 ``limits`` 内。"""
    try:
        value = float(pin_config_value(config_manager, getter_name, default))
    except (TypeError, ValueError):
        value = float(default)
    return max(limits[0], min(limits[1], value))


class PinWindow(QWidget):
    """
    钉图窗口 - 可拖动、缩放、编辑的置顶图像窗口

    核心特性:
    - 无边框置顶窗口 + 描边/阴影效果
    - 拖动移动 / 滚轮缩放
    - 鼠标悬停显示控制按钮
    - ESC 关闭 / R 缩略图模式
    - 支持绘图编辑（委托给 PinCanvas）
    - OCR 文字选择（委托给 PinOCRManager）
    """

    # 信号
    closed = Signal()  # 窗口关闭信号

    def __init__(self, image: QImage, position: QPoint, config_manager,
                 drawing_items=None, selection_offset=None, number_next=None):
        """
        Args:
            image: 选区底图（只包含选区的纯净背景，不含绘制）
            position: 初始位置（全局坐标）
            config_manager: 配置管理器
            drawing_items: 绘制项目列表（从截图窗口继承）
            selection_offset: 选区在原场景中的偏移量
            number_next: 源场景的下一个序号值（用于同步计数器）
        """
        super().__init__()

        self.config_manager = config_manager
        self.drawing_items = drawing_items or []
        self.selection_offset = selection_offset or QPoint(0, 0)

        # ====== 光晕/阴影样式参数 ======
        # 「阴影/描边」与透明度都可以在「贴图设置」页里改默认值（以前是写死的）
        self.halo_enabled = pin_config_bool(config_manager, "get_pin_shadow_enabled", True)
        self.corner = 0
        self.border_width = 2
        tc = get_theme().theme_color
        tc.setAlpha(200)
        self.border_color = tc

        # ====== 窗口状态 ======
        self._is_closed = False
        self._is_dragging = False
        self._is_editing = False
        self._drag_start_pos = QPoint()
        self._drag_start_window_pos = QPoint()
        self._last_hover_state = False
        #: 工具栏是否已挂成本窗口的原生跟随窗口（系统搬贴图时一起带走）
        self._toolbar_follows_natively = False

        # ====== 设置窗口属性 ======
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if self.halo_enabled:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        # ====== 底图 ======
        self._orig_size = image.size()
        self._base_pixmap = QPixmap.fromImage(image)
        self.base_image = None  # 释放 QImage

        # ====== 缩放 ======
        self.scale_factor = 1.0
        self._view_scale_x = 1.0
        self._view_scale_y = 1.0
        self._last_background_scale_size = None

        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(80)
        self._scale_timer.timeout.connect(self._apply_smooth_scaling)
        self._is_scaling = False

        # ====== 窗口透明度 ======
        self._win_opacity = pin_config_float(
            config_manager, "get_pin_default_opacity", 1.0, PIN_OPACITY_RANGE)
        self.setWindowOpacity(self._win_opacity)

        # ====== 缩放百分比提示 ======
        self._zoom_label = QLabel(self)
        self._zoom_label.setStyleSheet(
            "QLabel {"
            "  color: #2EC4B6;"
            "  background: rgba(0, 0, 0, 160);"
            "  border-radius: 4px;"
            "  padding: 2px 6px;"
            "  font-size: 12px;"
            "  font-weight: bold;"
            "}"
        )
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.hide()
        self._zoom_hide_timer = QTimer(self)
        self._zoom_hide_timer.setSingleShot(True)
        self._zoom_hide_timer.setInterval(1000)
        self._zoom_hide_timer.timeout.connect(self._zoom_label.hide)

        self.view = None

        # ====== 初始几何 ======
        self.setGeometry(position.x(), position.y(), image.width(), image.height())

        # ====== UI 组件 ======
        self.setup_ui()

        # ====== 画布 ======
        from .pin_canvas import PinCanvas
        self.canvas = PinCanvas(self, self._orig_size, image)
        if self.drawing_items:
            self.canvas.initialize_from_items(self.drawing_items, self.selection_offset, number_next)

        # ====== CanvasView ======
        self._cross_tool_selection_enabled = bool(
            self.config_manager.get_cross_tool_selection_enabled()
        ) if self.config_manager else False
        self.view = PinCanvasView(
            self.canvas.scene, self, self.canvas,
            cross_tool_select=self._cross_tool_selection_enabled,
        )
        self.view.setParent(self)
        self.view.setGeometry(0, 0, self.width(), self.height())
        self.view.set_corner_radius(self.corner)
        self._update_view_transform()
        self.view.viewport().installEventFilter(self)

        # ====== 工具栏（按需创建） ======
        self.toolbar = None

        # ====== OCR 管理器 ======
        self._ocr_mgr = PinOCRManager(self, config_manager)

        # ====== 缩略图模式 ======
        self._thumbnail = PinThumbnailMode(self)

        # 裁剪模式（在内容区拖一个矩形）
        self._crop_mode = False
        self._crop_origin = None
        self._crop_band = None

        # ====== 图像变换管理器 ======
        self._image_transform = PinImageTransform()

        # ====== 描边 Overlay（单圈主题色，无阴影）======
        self.border_overlay = None
        if self.halo_enabled:
            self.border_overlay = PinBorderOverlay(
                self, corner_radius=self.corner, border_color=self.border_color)
            self.border_overlay.setGeometry(0, 0, self.width(), self.height())
            self.border_overlay.raise_()

        # ====== 显示 ======
        self.show()
        # macOS 上 Qt 的 Tool 窗口（NSPanel）默认「失焦即隐藏」：贴图要长期留在屏幕上，
        # 必须关掉这条，否则用户切到别的应用时贴图会凭空消失。
        window_ops.keep_visible_when_inactive(self)
        self.update_button_positions()

        # ====== 注册到全局快捷键控制器 ======
        from .pin_shortcut import PinShortcutController
        PinShortcutController.instance().register(self)

        # 多选集合变化 → 刷新选中外观（窗口销毁时 Qt 会自动断开）
        self._selection_manager().selection_changed.connect(self.refresh_selection_look)

        # 延迟 300ms 初始化 OCR（等钉图窗口完全显示后再启动，识别在子线程中运行，不阻塞主线程）
        QTimer.singleShot(300, self._ocr_mgr.init_now)

        log_info(
            T(
                "创建成功: {width}x{height}, 位置: ({x}, {y}), 透明度: {opacity:.2f}, 阴影: {shadow}",
                width=image.width(), height=image.height(),
                x=position.x(), y=position.y(),
                opacity=self._win_opacity,
                shadow=self.tr("开") if self.halo_enabled else self.tr("关"),
            ),
            "PinWindow",
        )
        if self.drawing_items:
            log_debug(T("继承了 {count} 个绘制项目（向量数据）", count=len(self.drawing_items)), "PinWindow")

    # ==================================================================
    # 兼容属性：让外部通过 pin_window.ocr_text_layer 访问
    # ==================================================================

    @property
    def ocr_text_layer(self):
        return self._ocr_mgr.ocr_text_layer if hasattr(self, '_ocr_mgr') else None

    @property
    def _ocr_has_result(self):
        return self._ocr_mgr.has_result if hasattr(self, '_ocr_mgr') else False

    @property
    def _text_selection_enabled(self):
        return self._ocr_mgr.text_selection_enabled if hasattr(self, '_ocr_mgr') else False

    @property
    def _thumbnail_mode(self):
        return self._thumbnail.active if hasattr(self, '_thumbnail') else False

    # ==================================================================
    # UI 设置
    # ==================================================================

    def setup_ui(self):
        """设置 UI 布局"""
        self.setup_control_buttons()

    def setup_control_buttons(self):
        """设置控制按钮"""
        self._control_buttons = PinControlButtons(self)
        self.close_button = self._control_buttons.close_button
        self.toolbar_toggle_button = self._control_buttons.toolbar_toggle_button
        self._control_buttons.connect_signals(
            close_handler=self.close_window,
            toggle_toolbar_handler=self.toggle_toolbar,
        )
        self._context_menu = PinContextMenu(self)
        self._translation_helper = PinTranslationHelper(self, self.config_manager)
        self.update_button_positions()

    def update_button_positions(self):
        """更新按钮位置"""
        if hasattr(self, '_control_buttons'):
            self._control_buttons.update_positions(self.width())

    # ==================================================================
    # 悬停 / 控制按钮
    # ==================================================================

    def _auto_toolbar_enabled(self) -> bool:
        return self.config_manager.get_pin_auto_toolbar() if self.config_manager else True

    def _ensure_hover_controls_visible(self):
        if not self.close_button.isVisible():
            self.close_button.show()
        self.close_button.raise_()

        if self._thumbnail_mode:
            return

        if not self._auto_toolbar_enabled() and not self.toolbar_toggle_button.isVisible():
            self.toolbar_toggle_button.show()
        self.toolbar_toggle_button.raise_()

        if self._auto_toolbar_enabled():
            toolbar_hidden = not self.toolbar or not self.toolbar.isVisible()
            if toolbar_hidden and not self._is_closed:
                self.show_toolbar()

    def _set_hover_state(self, hovering: bool):
        if hovering:
            self._ensure_hover_controls_visible()
            if self.toolbar:
                self.toolbar.on_parent_hover(True)
            self._last_hover_state = True
            return
        if self.toolbar:
            self.toolbar.on_parent_hover(False)
        self._last_hover_state = False
        QTimer.singleShot(300, self._delayed_hide_buttons)

    def _delayed_hide_buttons(self):
        if self._is_closed:
            return
        if self._last_hover_state:
            return
        if not self.underMouse():
            self.close_button.hide()
            self.toolbar_toggle_button.hide()

    def _set_control_buttons_visible(self, visible: bool):
        if hasattr(self, 'close_button') and self.close_button:
            self.close_button.setVisible(visible)
        if hasattr(self, 'toolbar_toggle_button') and self.toolbar_toggle_button:
            self.toolbar_toggle_button.setVisible(visible)

    # ==================================================================
    # 窗口拖动
    # ==================================================================

    def start_window_drag(self, global_pos: QPoint):
        self._is_dragging = True
        self._drag_start_pos = global_pos
        self._drag_start_window_pos = self.pos()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        handed = self._let_system_drag()
        log_debug(
            T("贴图拖动开始: 起点 ({x}, {y})，系统接管: {system}",
              x=global_pos.x(), y=global_pos.y(), system=handed),
            "PinWindow")
        if handed:
            # 系统拖动是阻塞的（返回时用户已经松开鼠标），鼠标事件不会再到我们这里，
            # 所以在这里直接收尾。
            self.end_window_drag()

    def _let_system_drag(self) -> bool:
        """把这张贴图的拖动交给系统；返回 True 表示拖动已经结束。

        交给系统的好处是窗口由窗口管理器搬，应用侧每个鼠标事件不做任何窗口操作——
        快速移动鼠标时（高刷新率鼠标一秒能发几百个事件）不会像自己搬那样跟不上。
        代价是拖动期间收不到鼠标事件，所以三条限制：

        - 成组拖动不走这条路：系统一次只搬一个窗口，同组其它贴图得由我们每帧搬；
        - 平台/合成器不接受（``start_system_move`` 返回 False）时退回手动搬窗口；
        - 贴图太大时不走：系统的窗口拖动会把窗口夹在屏幕可用区里（实测可拖动范围 =
          可用区 − 窗口尺寸），贴着屏幕大小的贴图交给系统就等于动不了，那时「能自由
          摆放」比「原生顺滑」更重要。
        """
        if self.is_selected() or not self._system_drag_has_room():
            return False
        if not window_ops.start_system_move(self):
            return False
        log_debug(T("贴图拖动已交给系统"), "PinWindow")
        return True

    def _system_drag_has_room(self) -> bool:
        """窗口在屏幕可用区里是否还留得下足够的移动余地（见 ``DRAG_MAX_WINDOW_SHARE``）。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return False
        available = screen.availableGeometry()
        return (self.width() <= available.width() * DRAG_MAX_WINDOW_SHARE
                and self.height() <= available.height() * DRAG_MAX_WINDOW_SHARE)

    def update_window_drag(self, global_pos: QPoint):
        if not self._is_dragging:
            return
        delta = global_pos - self._drag_start_pos
        moved = (self._drag_start_window_pos + delta) - self.pos()
        self.move(self._drag_start_window_pos + delta)
        # 拖着选中的一张时，整组一起动（多选的用途就在这里）
        if moved and self.is_selected():
            self._selection_manager().move_selected(self, moved)

    def end_window_drag(self):
        if not self._is_dragging:
            return
        self._is_dragging = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        log_debug(
            T("贴图拖动结束: 位移 ({dx}, {dy})",
              dx=self.x() - self._drag_start_window_pos.x(),
              dy=self.y() - self._drag_start_window_pos.y()),
            "PinWindow")
        # 拖动结束后再摆一次工具栏：拖动中贴到屏幕边缘时，位置规则会翻到另一侧
        self._sync_toolbar_with_window()

    def _sync_toolbar_with_window(self):
        """让工具栏跟住贴图。

        macOS 上工具栏是原生跟随窗口（``window_ops.attach_follow_window``）：系统搬贴图
        时同帧带着它走，我们不必再逐个鼠标事件搬它一次。
        """
        if getattr(self, "_toolbar_follows_natively", False):
            return
        toolbar = getattr(self, "toolbar", None)
        if toolbar and toolbar.isVisible():
            toolbar.sync_with_pin_window()

    # ==================================================================
    # 视图 / 缩放
    # ==================================================================

    def update_display(self):
        if hasattr(self, 'view') and self.view:
            self.view.viewport().update()
        else:
            self.update()

    def _update_view_transform(self):
        if not getattr(self, 'view', None) or not getattr(self, 'canvas', None):
            return
        scene_rect = self.canvas.scene.sceneRect()
        if scene_rect.width() == 0 or scene_rect.height() == 0:
            return
        self.view.resetTransform()
        cr = self.content_rect()
        scale_x = cr.width() / scene_rect.width()
        scale_y = cr.height() / scene_rect.height()
        self._view_scale_x = float(scale_x)
        self._view_scale_y = float(scale_y)

        transform = getattr(self, '_image_transform', None)
        if transform and transform.has_transform:
            t = transform.build_view_transform(
                cr.width(), cr.height(),
                scene_rect.width(), scene_rect.height())
            self.view.setTransform(t)
        else:
            self.view.scale(scale_x, scale_y)

        # 通知光标管理器更新缩放（光标大小跟随视觉缩放）
        cursor_mgr = getattr(self.view, 'cursor_manager', None)
        if cursor_mgr:
            cursor_mgr.update_view_scale(float(scale_x))

    def _refresh_background_for_scale(self):
        if not getattr(self, 'canvas', None) or not getattr(self.canvas, 'scene', None):
            return
        background_item = getattr(self.canvas.scene, 'background', None)
        if background_item is None or self._view_scale_x <= 0 or self._view_scale_y <= 0:
            return
        if not getattr(self, '_base_pixmap', None):
            return

        # 计算背景预缩放尺寸
        # 目标：让预缩放后的像素密度匹配实际显示分辨率
        # 旋转 90°/270° 时，场景 x 轴映射到显示 y 轴，反之亦然
        # 所以每个场景轴的有效显示缩放需要交换
        transform = getattr(self, '_image_transform', None)
        cr = self.content_rect()
        scene_rect = self.canvas.scene.sceneRect()

        if transform and transform.is_rotated_90_or_270:
            # 场景 x 轴 → 显示 y 轴，场景 y 轴 → 显示 x 轴
            bg_scale_x = cr.height() / scene_rect.width()
            bg_scale_y = cr.width() / scene_rect.height()
        else:
            bg_scale_x = self._view_scale_x
            bg_scale_y = self._view_scale_y

        tw = max(1, int(round(self._orig_size.width() * bg_scale_x)))
        th = max(1, int(round(self._orig_size.height() * bg_scale_y)))
        target_size = (tw, th)
        if self._last_background_scale_size == target_size:
            return
        scaled = self._base_pixmap.scaled(
            tw, th,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # 只换渲染用的位图，不能走 update_image()：那会把这张按显示分辨率
        # 重采样的位图灌进背景的"内容"缓存，马赛克的缩小图届时就会按显示
        # 分辨率而不是原图分辨率切块，缩放后马赛克内容会错位/错误。
        background_item.set_display_pixmap(
            scaled,
            QTransform.fromScale(1.0 / bg_scale_x, 1.0 / bg_scale_y),
        )
        self._last_background_scale_size = target_size

    def content_rect(self) -> QRectF:
        """内容区域（整个窗口）"""
        return QRectF(self.rect())

    # ==================================================================
    # Qt 事件
    # ==================================================================

    @safe_event
    def resizeEvent(self, event):
        if hasattr(self, 'view') and self.view:
            self.view.setGeometry(0, 0, self.width(), self.height())
            if self._thumbnail_mode:
                self._thumbnail.update_view()
            else:
                self._update_view_transform()
            self.view._update_viewport_mask()

        if not self._thumbnail_mode:
            self.update_button_positions()
            if self.toolbar and self.toolbar.isVisible():
                self.toolbar.sync_with_pin_window()

        # OCR 层
        if not self._thumbnail_mode and hasattr(self, '_ocr_mgr'):
            cr = self.content_rect()
            self._ocr_mgr.update_geometry(cr.toRect())

        # 描边 Overlay
        if hasattr(self, 'border_overlay') and self.border_overlay:
            self.border_overlay.setGeometry(0, 0, self.width(), self.height())
            self.border_overlay.raise_()

        super().resizeEvent(event)

    @safe_event
    def moveEvent(self, event):
        super().moveEvent(event)
        # 工具栏（以及它下面的二级面板）跟着贴图走。以前只在鼠标拖动里同步，所以
        # 系统拖动、贴图位置被别处改掉时就不同步了。
        self._sync_toolbar_with_window()

    @safe_event
    def paintEvent(self, event):
        # View 是唯一的内容渲染者，这里不画任何东西
        pass

    @safe_event
    def mousePressEvent(self, event: QMouseEvent):
        self._set_hover_state(True)
        # Ctrl/Cmd + 左键：加入/移出多选集合（不进拖动，避免选的时候把图挪走）
        if (event.button() == Qt.MouseButton.LeftButton
                and event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                         | Qt.KeyboardModifier.MetaModifier)):
            self.toggle_selection()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and not (self.canvas and self.canvas.is_editing):
            # 普通点击一张没被选中的贴图：先清掉旧的选择（点哪张就是只操作它）
            if not self.is_selected():
                self._selection_manager().clear_selection()
            if self.is_locked():
                # 锁定期间不拖动：位置就是用户要固定的东西
                log_debug(T("贴图已锁定，忽略拖动"), "PinWindow")
                event.accept()
                return
            self.start_window_drag(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self.run_gesture(
            pin_actions.GESTURE_MIDDLE_CLICK
        ):
            event.accept()
            return
        super().mousePressEvent(event)

    @safe_event
    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging:
            # 拖动中不再每次鼠标移动都刷一遍悬停状态（按钮的 show/raise 与工具栏的
            # 悬停回调都在这条路上，每个事件白跑一次只会拖慢跟手）
            self.update_window_drag(event.globalPosition().toPoint())
            event.accept()
            return
        self._set_hover_state(True)
        super().mouseMoveEvent(event)

    @safe_event
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self.end_window_drag()
            event.accept()
            return
        elif event.button() == Qt.MouseButton.RightButton:
            self._context_menu_pos = event.globalPosition().toPoint()
            if self.run_gesture(pin_actions.GESTURE_RIGHT_CLICK):
                event.accept()
                return
            super().mouseReleaseEvent(event)
            return
        super().mouseReleaseEvent(event)

    # ── 多选 ──────────────────────────────────────────

    def _selection_manager(self):
        from pin.pin_manager import PinManager

        return PinManager.instance()

    def is_selected(self) -> bool:
        """这张贴图是否在多选集合里。"""
        return self._selection_manager().is_selected(self)

    def toggle_selection(self) -> bool:
        """加入/移出多选集合；返回切换后的状态。"""
        selected = self._selection_manager().toggle_selection(self)
        log_debug(T("贴图多选: {selected}", selected=selected), "PinWindow")
        return selected

    def close_selected_pins(self) -> int:
        """关闭所有选中的贴图（右键菜单与贴图动作共用）。"""
        return self._selection_manager().close_selected()

    def refresh_selection_look(self) -> None:
        """按选中状态给出可见反馈。

        有描边时把描边换成主题强调色；用户把「阴影与描边」关掉时没有可换色的东西，
        就退回左上角的小提示——不然多选会变成「选了但看不出来」。
        """
        selected = self.is_selected()
        if self.border_overlay:
            color = QColor(self.border_color)
            if selected:
                color = QColor(ACCENT)
                color.setAlpha(255)
            self.border_overlay.set_border_color(color)
            return
        if selected:
            self._show_hint_label(self.tr("Selected"))

    def gesture(self, kind: str) -> str:
        """取某个手势当前绑定的动作 id（未绑定返回 ``none``）。"""
        return pin_actions.resolve(self.config_manager).get(kind, pin_actions.ACTION_NONE)

    def run_gesture(self, kind: str) -> bool:
        """执行一个手势绑定的动作；返回是否真的做了事。

        手势表可以在「贴图设置」页里改（默认等于改造前的写死行为）。
        """
        return pin_actions.run_pin_action(self.gesture(kind), self)

    @safe_event
    def wheelEvent(self, event: QWheelEvent):
        if self._thumbnail_mode:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        up = delta > 0
        with_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if with_ctrl:
            kind = pin_actions.GESTURE_CTRL_WHEEL_UP if up else pin_actions.GESTURE_CTRL_WHEEL_DOWN
        else:
            kind = pin_actions.GESTURE_WHEEL_UP if up else pin_actions.GESTURE_WHEEL_DOWN
        if self.run_gesture(kind):
            event.accept()

    def apply_zoom(self, direction: int, *, fine: bool = False) -> bool:
        """按方向缩放窗口（``direction`` > 0 放大，< 0 缩小）。返回是否真的改了大小。

        缩放倍率来自「贴图设置」页；``fine`` 用精细步长（贴图手势里的「精细缩放」）。
        缩小必须用放大倍率的倒数，否则放大再缩小会累计误差。
        """
        if direction == 0 or self._thumbnail_mode:
            return False
        if getattr(self, "_locked", False):
            log_debug(T("贴图已锁定，忽略缩放"), "PinWindow")
            return False

        step = pin_config_float(self.config_manager, "get_pin_zoom_step", 1.05, (1.01, 4.0))
        if fine:
            step = 1.0 + (step - 1.0) / 5.0 if step > 1.0 else pin_actions.FINE_ZOOM_STEP
        sf = step if direction > 0 else 1.0 / step

        self._is_scaling = True
        if hasattr(self, '_image_transform'):
            base_size = self._image_transform.display_size(self._orig_size)
        else:
            base_size = self._orig_size

        min_scale = max(50.0 / base_size.width(), 50.0 / base_size.height())
        new_scale = max(min_scale, min(self.scale_factor * sf, 4.0))
        # 消除互逆浮点运算在 100% 附近可能留下的极小误差。
        if abs(new_scale - 1.0) < 1e-6:
            new_scale = 1.0
        self.scale_factor = new_scale

        # 始终从原图逻辑尺寸计算，避免按当前整数窗口尺寸反复取整。
        nw = max(1, int(round(base_size.width() * new_scale)))
        nh = max(1, int(round(base_size.height() * new_scale)))

        self.setGeometry(self.x(), self.y(), nw, nh)
        if self.canvas:
            self.canvas.invalidate_cache()
        self.update()
        self._scale_timer.start()
        self._show_zoom_percent()
        return True

    def adjust_opacity(self, direction: int) -> bool:
        """按方向调窗口不透明度（> 0 更不透明）。步长来自设置页。"""
        if direction == 0:
            return False
        step = pin_config_float(self.config_manager, "get_pin_opacity_step", 0.05, (0.0, 1.0))
        step = step if direction > 0 else -step
        self._win_opacity = max(
            PIN_OPACITY_RANGE[0], min(PIN_OPACITY_RANGE[1], self._win_opacity + step)
        )
        self.setWindowOpacity(self._win_opacity)
        self._show_hint_label(f"α {int(self._win_opacity * 100)}%")
        return True

    def _apply_smooth_scaling(self):
        if self._is_closed:
            return
        self._is_scaling = False
        self._refresh_background_for_scale()
        self.update()

    def _show_zoom_percent(self):
        """在左上角显示当前缩放百分比"""
        # 使用逻辑缩放比例，避免窗口像素取整掩盖真实比例。
        percent = int(round(self.scale_factor * 100))
        self._show_hint_label(f"{percent}%")

    def _show_hint_label(self, text: str):
        """在左上角显示提示 label（缩放% 和透明度% 共用）。"""
        self._zoom_label.setText(text)
        self._zoom_label.adjustSize()
        self._zoom_label.move(8, 8)
        self._zoom_label.raise_()
        self._zoom_label.show()
        self._zoom_hide_timer.start()

    @safe_event
    def enterEvent(self, event):
        super().enterEvent(event)
        self._set_hover_state(True)

    @safe_event
    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._set_hover_state(False)

    @safe_event
    def keyPressEvent(self, event: QKeyEvent):
        # 裁剪模式下 Esc 先取消裁剪，不关闭贴图
        if self._crop_mode and event.key() == Qt.Key.Key_Escape:
            self.cancel_crop_mode()
            log_debug(T("已取消贴图裁剪"), "PinWindow")
            return
        # 钉图快捷键已由 ShortcutManager 统一分发给 PinEdit/PinNormal Handler
        # 这里只做兜底，防止焦点偶尔在 PinWindow 上时按键无反应
        super().keyPressEvent(event)

    @safe_event
    def eventFilter(self, obj, event):
        if self.view and obj == self.view.viewport():
            if self._crop_mode and self._handle_crop_event(event):
                return True
            if event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter, QEvent.Type.MouseMove):
                self._set_hover_state(True)
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
                self._set_hover_state(False)
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                # 贴图内容区是子控件（画布视图），双击会被它先吃掉；这里在事件过滤器里
                # 先看一眼，只有用户真的给「双击」绑了动作才吞掉，否则放行给画布
                # （那边要用双击做控制点连点）。
                if self.run_gesture(pin_actions.GESTURE_DOUBLE_CLICK):
                    return True
        return super().eventFilter(obj, event)

    # ==================================================================
    # 缩略图模式（委托给 PinThumbnailMode）
    # ==================================================================

    def toggle_thumbnail_mode(self):
        self._thumbnail.toggle()

    # ==================================================================
    # 工具栏管理
    # ==================================================================

    def show_toolbar(self):
        if not self.toolbar:
            from .pin_toolbar import PinToolbar
            self.toolbar = PinToolbar(parent_pin_window=self, config_manager=self.config_manager)
            if self.canvas:
                self.canvas.connect_toolbar(self.toolbar, self.view)
            log_debug(T("创建工具栏，信号已由 PinCanvas 连接"), "PinWindow")

        auto = self.config_manager.get_pin_auto_toolbar() if self.config_manager else True
        if auto:
            self.toolbar.enable_auto_hide(True)
            self.toolbar.set_auto_hide_delay(2000)
        else:
            self.toolbar.enable_auto_hide(False)
        self.toolbar.show()
        self._attach_toolbar_as_follow_window()

    def _attach_toolbar_as_follow_window(self):
        """把工具栏挂成贴图的原生跟随窗口（挂不上就继续每帧自己同步位置）。"""
        if self._toolbar_follows_natively or not self.toolbar:
            return
        self._toolbar_follows_natively = window_ops.attach_follow_window(self, self.toolbar)
        if self._toolbar_follows_natively:
            log_debug(T("工具栏已挂为跟随窗口，拖动时由系统带动"), "PinWindow")

    def hide_toolbar(self):
        if self.toolbar:
            if hasattr(self.toolbar, '_hide_all_panels'):
                self.toolbar._hide_all_panels()
            if hasattr(self.toolbar, 'current_tool') and self.toolbar.current_tool:
                for btn in self.toolbar.tool_buttons.values():
                    btn.setChecked(False)
                self.toolbar.current_tool = None
                self.toolbar.tool_changed.emit("cursor")
            self.toolbar.hide()

    def toggle_toolbar(self):
        if self.toolbar and self.toolbar.isVisible():
            self.hide_toolbar()
        else:
            self.show_toolbar()

    # ==================================================================
    # 翻译
    # ==================================================================

    def request_translation(self):
        """翻译这张贴图（没有现成的 OCR 结果时先按需识别）。"""
        if not hasattr(self, '_translation_helper'):
            return
        if self._ocr_has_result:
            if self.ocr_text_layer:
                self._translation_helper.translate(self.ocr_text_layer)
        else:
            if not self._translation_helper.begin_ocr_translation():
                return
            # 先把控制权交还事件循环，让翻译窗口真正绘制出来，再做可能需要
            # 初始化模型的 OCR；这与截图翻译“先出面板、后识别”的体感一致。
            QTimer.singleShot(0, self._start_ocr_for_translation)

    def _start_ocr_for_translation(self):
        if self._is_closed:
            return
        if self._ocr_mgr.recognize_for_translation():
            log_info(T("OCR 识别中，翻译将在识别完成后自动执行"), "Translate")
        else:
            log_warning(T("无法启动钉图 OCR 识别"), "Translate")
            self._translation_helper.complete_ocr_translation(
                False, self.tr("OCR recognition could not be started")
            )

    def _on_ocr_translation_finished(self, success: bool, result: str):
        """接收钉图文字层 OCR 结果，交给统一翻译界面。"""
        if hasattr(self, '_translation_helper'):
            self._translation_helper.complete_ocr_translation(success, result)

    # ==================================================================
    # 右键菜单
    # ==================================================================

    def show_context_menu_at_cursor(self) -> bool:
        """在鼠标位置弹出右键菜单（手势表里的「显示菜单」用）。"""
        from PySide6.QtGui import QCursor

        pos = getattr(self, "_context_menu_pos", None) or QCursor.pos()
        self.show_context_menu(pos)
        return True

    def show_context_menu(self, global_pos: QPoint):
        if hasattr(self, '_context_menu'):
            state = {
                'toolbar_visible': self.toolbar and self.toolbar.isVisible(),
                'stay_on_top': bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint),
                'shadow_enabled': self.halo_enabled,
                'text_selection_enabled': self._text_selection_enabled,
                'thumbnail_mode': self._thumbnail_mode,
                'locked': self.is_locked(),
                'click_through': self.is_click_through(),
                'focus_mode': self._selection_manager().is_focus_mode(self),
                'group': getattr(self, 'group_name', ''),
                'selected_count': len(self._selection_manager().selected_pins()),
            }
            self._context_menu.show(global_pos, state)


    # ==================================================================
    # 点击穿透
    # ==================================================================

    def is_click_through(self) -> bool:
        """贴图是否处于点击穿透状态（鼠标事件穿过窗口落到下面的程序）。"""
        return bool(getattr(self, "_click_through", False))

    def set_click_through(self, enabled: bool) -> bool:
        """开关点击穿透；平台不支持时返回 False 并记日志（不假装成功）。

        开启后窗口不再接收鼠标，右键菜单自然也就点不出来——所以它同时是一个可绑定的
        贴图动作（手势/快捷键），用户有一条退路；提示里也会说明怎么取消。
        """
        from core.platform import window_ops

        ok = False
        try:
            ok = bool(window_ops.set_click_through(self, bool(enabled), layered=True))
        except Exception as e:
            log_exception(e, T("设置点击穿透"))
        if not ok:
            log_warning(T("当前平台不支持点击穿透"), "PinWindow")
            return False
        self._click_through = bool(enabled)
        self._show_hint_label(self.tr("Click-through on") if enabled
                              else self.tr("Click-through off"))
        log_debug(T("贴图点击穿透: {enabled}", enabled=enabled), "PinWindow")
        return True

    def toggle_click_through(self) -> bool:
        """切换点击穿透。"""
        return self.set_click_through(not self.is_click_through())

    # ==================================================================
    # 焦点模式 / 关闭其它
    # ==================================================================

    def toggle_focus_mode(self) -> bool:
        """焦点模式：只留这一张可见，再按一次还原（由 PinManager 统一做，见 pin_manager）。"""
        from .pin_manager import PinManager

        return PinManager.instance().toggle_focus_mode(self)

    def close_other_pins(self) -> int:
        """关掉除自己以外的所有贴图，返回关掉的数量。"""
        from .pin_manager import PinManager

        return PinManager.instance().close_other_pins(self)

    # ==================================================================
    # 加载新内容
    # ==================================================================

    def load_image(self, image) -> bool:
        """把一张新图片装进这张贴图（含重跑识别）。

        换底图要成套地换：底图像素、原始尺寸、缩放/旋转状态、画布里的绘制元素、OCR 结果。
        只换其中几项会留下「图变了但识别结果还是旧的」这类错位——所以这里整体重来一遍。
        """
        if image is None or image.isNull():
            return False
        try:
            self._orig_size = image.size()
            self._base_pixmap = QPixmap.fromImage(image)
            if hasattr(self, "_image_transform"):
                self._image_transform.reset()
            self.scale_factor = 1.0
            self._view_scale_x = 1.0
            self._view_scale_y = 1.0
            self._last_background_scale_size = None
            canvas = getattr(self, "canvas", None)
            if canvas is not None:
                scene = canvas.scene
                # 不能 scene.clear()：那会把 BackgroundItem 一起删掉（Python 侧还留着引用，
                # 下一步 update_image 就会撞上已销毁的 C++ 对象）。只清画布上的绘制元素。
                background = getattr(scene, "background", None)
                keep = {item for item in (background, getattr(scene, "selection_item", None))
                        if item is not None}
                for item in list(scene.items()):
                    if item not in keep:
                        scene.removeItem(item)
                scene.setSceneRect(QRectF(0, 0, image.width(), image.height()))
                if background is not None:
                    background.update_image(image)
                    background.setPos(0, 0)
                canvas.base_size = image.size()
                if hasattr(canvas, "_initialize_selection"):
                    canvas._initialize_selection()
            self.setGeometry(self.x(), self.y(), image.width(), image.height())
            if self.view is not None:
                self.view.setGeometry(0, 0, self.width(), self.height())
            self._update_view_transform()

            # 旧的识别结果必须清掉：它对应的是上一张图（管理器继续用，只清结果）
            if hasattr(self, "_ocr_mgr"):
                self._ocr_mgr.clear_result()
                if self.config_manager is None or self.config_manager.get_ocr_enabled():
                    self._ocr_mgr.init_now(force=True)
            self.update()
            log_info(T("贴图已加载新内容: {w}x{h}", w=image.width(), h=image.height()),
                     "PinWindow")
            return True
        except Exception as e:
            log_exception(e, T("加载贴图新内容"))
            return False

    # ==================================================================
    # 裁剪与滤镜（都走 load_image：换底图要成套地换）
    # ==================================================================

    def current_image(self):
        """当前底图（像素形态）。没有底图时返回空 QImage。"""
        pixmap = getattr(self, "_base_pixmap", None)
        if pixmap is None:
            return QImage()
        return pixmap.toImage()

    def start_crop_mode(self) -> bool:
        """进入裁剪模式：在内容区拖一个矩形，松手即裁。Esc 取消。"""
        if self.view is None or self.current_image().isNull():
            return False
        self._crop_mode = True
        self._crop_origin = None
        self._crop_band = QRubberBand(QRubberBand.Shape.Rectangle, self.view.viewport())
        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self._show_hint_label(self.tr("Drag to crop, Esc to cancel"))
        log_debug(T("贴图进入裁剪模式"), "PinWindow")
        return True

    def cancel_crop_mode(self) -> None:
        self._crop_mode = False
        self._crop_origin = None
        if getattr(self, "_crop_band", None) is not None:
            self._crop_band.hide()
        if self.view is not None:
            self.view.viewport().unsetCursor()

    def _handle_crop_event(self, event) -> bool:
        """裁剪模式下的鼠标事件；返回 True 表示已消化掉这个事件。"""
        if event.type() == QEvent.Type.MouseButtonPress:
            self._crop_origin = event.position().toPoint()
            self._crop_band.setGeometry(QRect(self._crop_origin, QSize()))
            self._crop_band.show()
            return True
        if event.type() == QEvent.Type.MouseMove and self._crop_origin is not None:
            self._crop_band.setGeometry(
                QRect(self._crop_origin, event.position().toPoint()).normalized())
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._crop_origin is not None:
            self._crop_band.setGeometry(
                QRect(self._crop_origin, event.position().toPoint()).normalized())
            rect = self._crop_rect_in_image(self._crop_band.geometry())
            self.cancel_crop_mode()
            if rect is not None:
                self.crop_to(rect)
            return True
        return False

    def _crop_rect_in_image(self, viewport_rect):
        """视口矩形 → 图像坐标（视图有缩放，必须走 mapToScene）。"""
        if self.view is None or viewport_rect.isEmpty():
            return None
        top_left = self.view.mapToScene(viewport_rect.topLeft())
        bottom_right = self.view.mapToScene(viewport_rect.bottomRight())
        rect = QRectF(top_left, bottom_right).normalized()
        image = self.current_image()
        if image.isNull():
            return None
        bounds = QRectF(0, 0, image.width(), image.height())
        rect = rect.intersected(bounds)
        if rect.width() < 2 or rect.height() < 2:
            return None
        return rect

    def crop_to(self, rect) -> bool:
        """按图像坐标裁剪当前贴图。"""
        image = self.current_image()
        if image.isNull():
            return False
        target = QRectF(rect).normalized().intersected(
            QRectF(0, 0, image.width(), image.height()))
        if target.width() < 1 or target.height() < 1:
            log_warning(T("裁剪范围无效，未执行"), "PinWindow")
            return False
        cropped = image.copy(target.toRect())
        if cropped.isNull():
            return False
        log_info(T("贴图已裁剪: {w}x{h}", w=cropped.width(), h=cropped.height()), "PinWindow")
        return self.load_image(cropped)

    def apply_filter(self, kind: str) -> bool:
        """把整张贴图按给定滤镜处理（灰度 / 反相 / 模糊 / 浮雕）。"""
        from tools.annotation import FILTER_DEFAULTS, apply_filter

        image = self.current_image()
        if image.isNull():
            return False
        name = str(kind or "").strip() or FILTER_DEFAULTS["kind"]
        if name not in ("grayscale", "invert", "blur", "emboss"):
            log_warning(T("不认识的滤镜: {kind}", kind=name), "PinWindow")
            return False
        processed = apply_filter(image, name, radius=int(FILTER_DEFAULTS["radius"]),
                                 strength=float(FILTER_DEFAULTS["strength"]))
        if processed is None or processed.isNull():
            return False
        log_info(T("贴图已应用滤镜: {kind}", kind=name), "PinWindow")
        return self.load_image(processed)

    def load_image_from_file(self) -> bool:
        """选一张本地图片替换当前贴图（右键菜单入口）。"""
        from PySide6.QtWidgets import QFileDialog

        path, _selected = QFileDialog.getOpenFileName(
            self, self.tr("Load New Content"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;All files (*)")
        if not path:
            return False
        image = QImage(path)
        if image.isNull():
            log_warning(T("读取图片失败: {path}", path=path), "PinWindow")
            return False
        return self.load_image(image)

    def recognize_text_now(self) -> bool:
        """重新识别当前贴图上的文字（换图之后、或第一次自动识别失败时用）。"""
        if not hasattr(self, "_ocr_mgr"):
            return False
        return bool(self._ocr_mgr.init_now(force=True))

    def is_locked(self) -> bool:
        """贴图是否被锁定（锁定期间不能拖动、缩放，避免挪位置时被误改）。"""
        return bool(getattr(self, "_locked", False))

    def toggle_lock(self):
        """锁定/解锁贴图的位置与大小。"""
        self._locked = not self.is_locked()
        log_debug(T("贴图锁定状态: {locked}", locked=self._locked), "PinWindow")
        self._show_hint_label(self.tr("Locked") if self._locked else self.tr("Unlocked"))

    def toggle_stay_on_top(self):
        flags = self.windowFlags()
        if flags & Qt.WindowType.WindowStaysOnTopHint:
            new_flags = flags & ~Qt.WindowType.WindowStaysOnTopHint
        else:
            new_flags = flags | Qt.WindowType.WindowStaysOnTopHint
        geo = self.geometry()
        self.setWindowFlags(new_flags)
        self.setGeometry(geo)
        self.show()

    def toggle_border_effect(self):
        self.halo_enabled = not self.halo_enabled
        self._image_transform._refresh_border(self)
        self.update()

    def toggle_text_selection(self):
        if hasattr(self, '_ocr_mgr'):
            self._ocr_mgr.toggle_text_selection()

    def reset_to_original_size(self):
        """恢复到原图 100% 大小（保留当前旋转/翻转状态，与滚轮缩放一样固定左上角坐标）"""
        # 根据当前变换状态计算 100% 显示尺寸
        if hasattr(self, '_image_transform') and self._image_transform.has_transform:
            sz = self._image_transform.display_size(self._orig_size)
        else:
            sz = self._orig_size
        target_w = sz.width()
        target_h = sz.height()
        # 固定左上角坐标（与滚轮缩放行为一致）
        self.setGeometry(self.x(), self.y(), target_w, target_h)
        self.scale_factor = 1.0
        if self.canvas:
            self.canvas.invalidate_cache()
        self.update_button_positions()
        self._update_view_transform()
        self._refresh_background_for_scale()
        if self.toolbar and self.toolbar.isVisible():
            self.toolbar.sync_with_pin_window()
        self._image_transform._refresh_border(self)
        self.update()
        self._show_zoom_percent()

    # ==================================================================
    # 图像变换（委托给 PinImageTransform）
    # ==================================================================

    def rotate_image_cw(self):
        """顺时针旋转 90°"""
        self._image_transform.rotate_cw()
        self._image_transform.apply_to_window(self)

    def rotate_image_ccw(self):
        """逆时针旋转 90°"""
        self._image_transform.rotate_ccw()
        self._image_transform.apply_to_window(self)

    def flip_image_horizontal(self):
        """水平翻转"""
        self._image_transform.flip_horizontal()
        self._image_transform.apply_to_window(self)

    def flip_image_vertical(self):
        """垂直翻转"""
        self._image_transform.flip_vertical()
        self._image_transform.apply_to_window(self)

    def reset_image_transform(self):
        """重置所有图像变换"""
        self._image_transform.reset()
        self._image_transform.apply_to_window(self)

    # ==================================================================
    # 图像导出
    # ==================================================================

    def get_current_image(self) -> QImage:
        dpr = self.devicePixelRatioF()
        if self.canvas:
            img = self.canvas.get_current_image(dpr)
        else:
            img = QImage(
                int(self.width() * dpr), int(self.height() * dpr),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            img.fill(Qt.GlobalColor.transparent)
            img.setDevicePixelRatio(dpr)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawPixmap(self.rect(), self._base_pixmap)
            p.end()
        # 应用图像变换（旋转/翻转）
        if hasattr(self, '_image_transform'):
            img = self._image_transform.transform_image(img)
        return img

    def _with_edit_paused(self, func):
        """退出编辑模式执行操作，再恢复。"""
        was_editing = self.canvas and self.canvas.is_editing
        active_tool_id = None
        if was_editing and hasattr(self.canvas, 'tool_controller'):
            ct = self.canvas.tool_controller.current_tool
            if ct:
                active_tool_id = ct.id
            self.canvas.deactivate_tool()
        try:
            func()
        finally:
            if was_editing and active_tool_id:
                self.canvas.activate_tool(active_tool_id)

    def save_image(self):
        from datetime import datetime
        from PySide6.QtWidgets import QFileDialog
        from core.save import SaveService
        import os
        import re

        fmt = (self.config_manager.get_screenshot_format() if self.config_manager else "PNG").lower()
        default_name = f"pinned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Pin Image"),
            default_name,
            _save_filter(),
        )
        if not file_path:
            return

        ext = os.path.splitext(file_path)[1].lstrip(".")
        if ext:
            image_format = ext.upper()
        else:
            match = re.search(r'\*\.(\w+)', selected_filter)
            image_format = match.group(1).upper() if match else "PNG"
        if not ext:
            file_path = f"{file_path}.{image_format.lower()}"

        def _do_save():
            image = self.get_current_image()
            save_service = SaveService(config_manager=self.config_manager)
            if save_service.save_qimage_to_path(image, file_path, image_format=image_format):
                log_info(T("保存成功: {file_path}", file_path=file_path), "PinWindow")
            else:
                log_error(T("保存失败: {file_path}", file_path=file_path), "PinWindow")

        self._with_edit_paused(_do_save)

    def copy_to_clipboard(self):
        def _do_copy():
            image = self.get_current_image()
            deliver_image_async(image)
        self._with_edit_paused(_do_copy)

    def copy_and_close(self):
        """复制内容并关闭（贴图手势里最常用的组合）。"""
        self.copy_to_clipboard()
        self.close_window()

    def copy_recognized_text(self) -> bool:
        """把 OCR 识别出的文字复制到剪贴板；还没识别出文字时返回 False。

        OCR 是贴图显示后异步跑的（约 300ms），刚贴出来就触发这个动作时可能还没有结果，
        这时只记一条日志——总比往剪贴板里塞上一次的内容好。
        """
        layer = self.ocr_text_layer
        if layer is None:
            log_warning(T("还没有 OCR 结果，无法复制文字"), "PinWindow")
            return False
        text = ""
        try:
            text = layer.get_all_text(separator="\n") or ""
        except Exception as e:
            log_exception(e, T("读取识别文字"))
            return False
        if not text.strip():
            log_warning(T("没有识别到文字"), "PinWindow")
            return False
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        log_info(T("已复制识别出的文字（{count} 字）", count=len(text)), "PinWindow")
        return True

    # ==================================================================
    # 窗口关闭 / 资源清理
    # ==================================================================

    def close_window(self, *, confirm: bool = True):
        """关闭这张贴图。

        ``confirm=False`` 给批量关闭（「关闭所有钉图窗口」与应用退出）用：那是用户
        一次性的决定，挨个弹确认比不确认更烦。
        """
        if self._is_closed:
            return
        if confirm and not self._confirm_close():
            return
        log_debug(T("开始关闭"), "PinWindow")
        self._is_closed = True
        self.cleanup()
        self.closed.emit()
        self.close()

    def _confirm_close(self) -> bool:
        """按设置询问「确定关闭这张贴图吗」；没开二次确认就直接放行。"""
        if not pin_config_bool(self.config_manager, "get_pin_close_confirm", False):
            return True
        from ui.dialogs import show_confirm_dialog

        log_debug(T("关闭贴图前二次确认"), "PinWindow")
        return show_confirm_dialog(
            self, self.tr("Close pin"), self.tr("Close this pinned image?")
        )

    def cleanup(self):
        log_debug(T("清理资源..."), "PinWindow")
        try:
            # 从快捷键控制器注销
            try:
                from .pin_shortcut import PinShortcutController
                PinShortcutController.instance().unregister(self)
            except Exception as e:
                log_exception(e, T("注销快捷键控制器"))

            # 定时器
            if hasattr(self, '_scale_timer') and self._scale_timer:
                try:
                    self._scale_timer.stop()
                    self._scale_timer.deleteLater()
                    self._scale_timer = None
                except Exception as e:
                    log_exception(e, T("停止缩放定时器"))

            if hasattr(self, '_zoom_hide_timer') and self._zoom_hide_timer:
                try:
                    self._zoom_hide_timer.stop()
                    self._zoom_hide_timer.deleteLater()
                    self._zoom_hide_timer = None
                except Exception as e:
                    log_exception(e, T("停止缩放百分比定时器"))

            # 工具栏
            if hasattr(self, 'toolbar') and self.toolbar:
                try:
                    if self._toolbar_follows_natively:
                        window_ops.detach_follow_window(self, self.toolbar)
                        self._toolbar_follows_natively = False
                    for pn in ('paint_panel', 'shape_panel', 'arrow_panel', 'number_panel', 'text_panel'):
                        panel = getattr(self.toolbar, pn, None)
                        if panel:
                            try:
                                panel.close()
                                panel.deleteLater()
                            except Exception as e:
                                log_exception(e, T("关闭工具栏面板"))
                            setattr(self.toolbar, pn, None)
                    for alias in ('paint_menu', 'text_menu'):
                        if hasattr(self.toolbar, alias):
                            setattr(self.toolbar, alias, None)
                    self.toolbar.close()
                    self.toolbar.deleteLater()
                    self.toolbar = None
                except Exception as e:
                    log_exception(e, T("清理工具栏"))

            # OCR
            if hasattr(self, '_ocr_mgr'):
                self._ocr_mgr.cleanup()

            # 视图
            if hasattr(self, 'view') and self.view:
                try:
                    if hasattr(self.view, 'viewport'):
                        try:
                            self.view.viewport().removeEventFilter(self)
                        except Exception as e:
                            log_exception(e, T("移除视图事件过滤器"))
                    self.view.deleteLater()
                    self.view = None
                except Exception as e:
                    log_exception(e, T("清理视图"))

            # 画布
            if hasattr(self, 'canvas') and self.canvas:
                try:
                    self.canvas.cleanup()
                except Exception as e:
                    log_warning(T("画布清理时出错: {e}", e=e), "PinWindow")
                finally:
                    self.canvas = None

            # 图像数据
            self._base_pixmap = None

            log_info(T("资源清理完成"), "PinWindow")
        except Exception as e:
            log_error(T("cleanup过程中发生错误: {e}", e=e), "PinWindow")
            log_exception(e, "PinWindow.cleanup")

    @safe_event
    def closeEvent(self, event):
        try:
            if not self._is_closed:
                # 自发（来自窗口系统，比如用户点了关闭）还是代码里 close() 的：
                # 排查「贴图为什么自己关了」时这两者要分得清
                log_debug(
                    T("贴图窗口收到关闭事件（自发={spontaneous}）",
                      spontaneous=bool(event.spontaneous())),
                    "PinWindow",
                )
                self._is_closed = True
                try:
                    self.cleanup()
                except Exception as e:
                    log_error(T("cleanup时发生错误: {e}", e=e), "PinWindow")
                    log_exception(e, "PinWindow.cleanup")
                try:
                    self.closed.emit()
                except Exception as e:
                    log_error(T("发送closed信号时发生错误: {e}", e=e), "PinWindow")
            super().closeEvent(event)
        except Exception as e:
            log_error(T("closeEvent发生严重错误: {e}", e=e), "PinWindow")
            try:
                super().closeEvent(event)
            except Exception as e:
                log_exception(e, "PinWindow super closeEvent")
