"""应用主程序 - 系统托盘集成和全局快捷键管理

负责一次性初始化和管理应用的生命周期，包括系统托盘图标、快捷键钩子、
多窗口实例管理和启动流程。
"""

import sys
import os
from functools import partial

from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QFont
from PySide6.QtCore import QObject, Qt, Signal, Slot
from ui.dialogs import show_warning_dialog, show_error_dialog

from core import actions
from core.shortcut_manager import HotkeySystem, ShortcutManager
from settings import get_tool_settings_manager
from ui.screenshot_window import ScreenshotWindow
from ui.tray_menu import create_tray_menu
from core.logger import (
    setup_logger, get_logger, T,
    log_debug, log_info, log_warning, log_exception
)

# ── 全局版本号 ────────────────────────────────────────────
APP_VERSION = "2026.09.14"

# 当前 MainApp 实例。UI 侧需要「打开设置里的某一页」「重开本程序」这类应用级动作时
# 从这里取（见 ui/permission_actions.py）；没有单独的 App 单例，进程内只有一个。
_main_app_instance: "MainApp | None" = None


def main_app_instance() -> "MainApp | None":
    """当前 MainApp 实例；进程还没建起来时为 None。"""
    return _main_app_instance


def create_fallback_app_icon():
    """绘制占位托盘图标，保证图标资源缺失时托盘依然可见、可点。

    刻意不依赖任何资源文件或主题管理器——走到这里说明资源已经出问题了，
    兜底路径本身不能再有失败的可能。配色沿用应用默认主题色。
    """
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#40E0D0")))
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)

    font = QFont()
    font.setPixelSize(38)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#10322E"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "J")
    painter.end()

    return QIcon(pixmap)


def apply_configured_app_icon() -> bool:
    """配置里有自定义 logo 时，把「整个应用」的图标（macOS 的 Dock）也换成它。

    启动与保存后都要走这里：只换托盘和窗口图标的话，Dock 上还是系统给的那一枚，
    用户会以为没生效。平台不支持（Windows/Linux）时平台函数自己返回 False。
    """
    from core.platform import window_ops
    from core.resource_manager import ResourceManager

    custom = ResourceManager.custom_logo_path()
    if not custom:
        return False
    return window_ops.set_application_icon(custom)


def create_app_icon():
    """创建应用程序图标 - 自定义 logo 优先，其次内置托盘图标。

    必须始终返回有效的 QIcon：托盘是本应用唯一的常驻入口，而
    QSystemTrayIcon.setIcon() 不接受 None——返回 None 会让启动直接抛 TypeError，
    用户看到的现象是"双击没有任何反应"。
    """
    from core.resource_manager import ResourceManager

    custom = ResourceManager.custom_logo_icon()
    if custom is not None:
        return custom

    icon_path = ResourceManager.get_resource_path("svg/托盘.svg")

    if os.path.exists(icon_path):
        # 先渲染再判空：文件存在但 SVG 损坏时 pixmap 为空，
        # 直接使用会得到一个完全透明的托盘图标，和缺失一样不可用。
        icon_pixmap = QIcon(icon_path).pixmap(64, 64)
        if not icon_pixmap.isNull():
            # 加载SVG并放大
            pixmap = QPixmap(64, 64)  # 放大到64x64
            pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(0, 0, icon_pixmap)
            painter.end()

            return QIcon(pixmap)

    log_warning(T("托盘图标资源不可用，改用占位图标: {icon_path}", icon_path=icon_path), "Tray")
    return create_fallback_app_icon()

class MainApp(QObject):
    # Rust clipboard watcher may call from a worker thread.  This signal safely
    # marshals clipboard items back to the Qt GUI thread.
    clipboard_item_received = Signal(object)

    def __init__(self):
        super().__init__()

        global _main_app_instance
        _main_app_instance = self

        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        # Config - 使用统一的设置管理器
        self.config_manager = get_tool_settings_manager()

        # 应用界面主题必须在创建任何窗口前初始化。截图强调色由
        # core.theme 单独管理，二者职责互不影响。
        from core.ui_theme import get_ui_theme
        self.ui_theme_manager = get_ui_theme().init(
            self.config_manager, self.app
        )
        self.ui_theme_manager.theme_changed.connect(
            self._on_ui_theme_changed
        )
        
        # Logger - 日志初始化，
        setup_logger(self.config_manager)
        self._logger = get_logger()
        self.app.aboutToQuit.connect(self._on_about_to_quit)
        
        # 初始化翻译系统
        from core.i18n import I18nManager
        # 检查是否有保存的语言设置，如果没有则使用系统语言
        saved_lang = self.config_manager.get_app_setting("language", "__NOT_SET__")
        if saved_lang == "__NOT_SET__":
            # 第一次启动，检测系统语言
            saved_lang = I18nManager.get_system_language()
            self.config_manager.set_app_setting("language", saved_lang)
            log_info(T("首次启动，检测到系统语言: {saved_lang}", saved_lang=saved_lang), "I18n")
        I18nManager.load_language(saved_lang)
        log_info(T("语言设置: {lang_name}", lang_name=I18nManager.get_current_language_name()), "I18n")
        
        # 连接语言切换信号，用于更新托盘菜单等 UI
        I18nManager.instance().language_changed.connect(self._on_language_changed)
        
        # 初始化主题颜色管理器
        from core.theme import get_theme
        get_theme().init(self.config_manager)
        
        # 输出DPI信息用于调试
        try:
            from PySide6.QtGui import QGuiApplication
            primary_screen = QGuiApplication.primaryScreen()
            if primary_screen:
                dpr = primary_screen.devicePixelRatio()
                logical_dpi = primary_screen.logicalDotsPerInch()
                physical_dpi = primary_screen.physicalDotsPerInch()
                log_debug(f"Device Pixel Ratio: {dpr}", "DPI")
                log_debug(f"Logical DPI: {logical_dpi}", "DPI")
                log_debug(f"Physical DPI: {physical_dpi}", "DPI")
        except Exception as e:
            log_warning(T("无法获取DPI信息: {e}", e=e), "DPI")
        
        # 热键系统先创建，实际注册放到启动预加载完成后，避免预加载期间触发卡顿。
        self.hotkey_system = HotkeySystem()
        self.hotkey_system.set_suppressed(
            self.config_manager.get_app_setting("global_hotkeys_disabled", False)
        )
        # 全局鼠标手势命中后由管理器发信号（回调在监听线程里），这里排队回主线程执行
        ShortcutManager.instance().mouse_gesture_triggered.connect(
            self._on_mouse_gesture, Qt.ConnectionType.QueuedConnection
        )
        # 缺「辅助功能」权限时热键静默失效，监听器那边查到就发这个信号，这里给用户提示
        self.hotkey_system.permission_missing.connect(self._on_hotkey_permission_missing)
        
        # 窗口实例
        self.tray_icon = None
        self.settings_window = None
        self.screenshot_window = None
        # 全局鼠标动作要求「打开编辑器并直接进入某模式」：抓屏是异步的，先把请求记下来，
        # 等窗口真正显示后再应用（见 start_screenshot_for_gesture）。
        self._pending_editor_call = None
        # 贴图会话在退出流程最前面存一次（见 save_pin_session_if_enabled）
        self._pin_session_saved = False
        self.clipboard_window = None
        # 剪贴板管理器
        self.clipboard_manager = None

        from translation.smart_translation_controller import SmartTranslationController
        self.smart_translation_controller = SmartTranslationController(self)
        self.clipboard_item_received.connect(self._on_clipboard_item_received)
        self.clipboard_item_received.connect(
            self.smart_translation_controller.on_clipboard_item
        )
        
        # 启动预加载链（截图模块 → 工具栏 → OCR → 设置窗口 → 剪贴板 → 显示主界面）
        from core.bootstrap import PreloadManager
        self._preloader = PreloadManager(self)
        self._preloader.build_and_start()

    def restore_pins_if_enabled(self) -> int:
        """按设置恢复上次退出时还开着的贴图；返回恢复了几张。

        启动时调用（见 core/bootstrap.py）；关掉「启动恢复」时不做事，也不会去读会话文件。
        """
        if not self.config_manager.get_pin_restore_on_startup():
            return 0
        from pin import pin_session

        return pin_session.restore_pins(
            self.config_manager, self.config_manager.get_pin_history_limit()
        )

    def save_pin_session_if_enabled(self) -> int:
        """按设置保存当前未关闭的贴图；返回存下来的张数。

        一次退出只存一次（``_pin_session_saved``）：退出流程里贴图会被关掉，晚一步再存
        就会把刚存好的会话覆盖成空的。

        关掉设置时会把旧会话删掉：不然用户关掉这个功能以后，上次的图还会在下一次
        打开设置时突然回来。
        """
        from pin import pin_session
        from pin.pin_manager import PinManager

        if self._pin_session_saved:
            return 0
        self._pin_session_saved = True

        limit = self.config_manager.get_pin_history_limit()
        if not self.config_manager.get_pin_restore_on_startup():
            pin_session.clear_session()
            return 0
        return pin_session.save_session(PinManager.instance().get_all_pins(), limit)

    def _on_about_to_quit(self):
        """应用退出前收尾"""
        try:
            # 会话要在贴图被销毁之前存：cleanup() 之后管理器里就没有贴图了
            self.save_pin_session_if_enabled()
        except Exception as e:
            log_exception(e, T("保存贴图会话"))
        try:
            from translation import TranslationManager

            TranslationManager.cleanup()
        except Exception as e:
            log_exception(e, T("清理翻译线程"))
        try:
            if hasattr(self, "_logger") and self._logger:
                self._logger.close()
        except Exception as e:
            log_exception(e, T("关闭logger"))

    def _on_wizard_requested(self):
        """设置窗口请求打开向导：隐藏设置窗口、注销热键，再显示向导，完成后恢复"""
        log_info(T("向导请求：隐藏设置窗口并注销热键"), "MainApp")

        # 1. 隐藏设置窗口
        if self.settings_window:
            self.settings_window.hide()

        # 2. 注销所有热键（向导期间不拦截快捷键）
        self.hotkey_system.unregister_all()

        # 3. 显示向导
        try:
            from ui.welcome import WelcomeWizard
            wizard = WelcomeWizard(self.config_manager)
            wizard.exec()
        except Exception as e:
            log_exception(e, T("向导启动失败"))

        # 4. 向导结束后重新注册热键
        self.update_hotkey()
        log_info(T("向导完成，热键已恢复"), "MainApp")

    def _on_language_changed(self, lang_code: str):
        """语言切换时更新所有 UI 元素"""
        log_debug(T("语言已切换到: {lang_code}，更新 UI", lang_code=lang_code), "I18n")
        
        # 更新托盘菜单
        self._update_tray_menu()
        
        # 重新创建设置窗口（因为设置窗口是预加载的，需要重建才能更新翻译）
        if self.settings_window:
            was_visible = self.settings_window.isVisible()
            self.settings_window.close()
            self.settings_window.deleteLater()
            self.settings_window = None
            
            # 重新创建设置窗口
            self._preloader.preload_settings()
            
            # 如果之前是显示状态，重新显示
            if was_visible:
                self.settings_window.show()
                self.settings_window.activateWindow()
        
        # 关闭翻译窗口（下次打开时会用新语言创建）
        from translation import TranslationManager
        TranslationManager.instance().close_dialog()

    def _create_tray_menu(self) -> QMenu:
        """创建托盘菜单"""
        return create_tray_menu(self)

    def _on_ui_theme_changed(self, _tokens):
        """主题变化后刷新由 MainApp 持有的原生界面。"""
        self._update_tray_menu()
        if self.settings_window:
            self.settings_window.update()

    def _setup_pin_tray_updates(self):
        """刷新托盘菜单中的钉图数量。"""
        try:
            from pin.pin_manager import PinManager
            pin_manager = PinManager.instance()
            pin_manager.pin_created.connect(lambda _pin: self._update_tray_menu())
            pin_manager.pin_closed.connect(lambda _pin: self._update_tray_menu())
            pin_manager.all_pins_closed.connect(self._update_tray_menu)
        except Exception as e:
            log_exception(e, T("连接钉图托盘菜单刷新信号"))

    def _update_tray_menu(self):
        """重建托盘菜单（用于语言切换后刷新）"""
        if not hasattr(self, 'tray_icon') or not self.tray_icon:
            return

        # 更新 tooltip
        self.tray_icon.setToolTip(self.tr("jietuba - Click to screenshot"))

        # 重建菜单
        self.tray_icon.setContextMenu(self._create_tray_menu())

    def refresh_app_icon(self):
        """按当前配置重设托盘图标（保存自定义 logo 后调用，不必重启）。"""
        if not getattr(self, "tray_icon", None):
            return
        self.tray_icon.setIcon(create_app_icon())
        apply_configured_app_icon()

    def update_hotkey(self, show_error: bool = False):
        """按动作注册表重建全部全局热键。

        以前这里是三段硬编码（截图主/备、翻译主/备、剪贴板主/备），现在动作和热键都来自
        ``core.actions`` 与 ``app/action_hotkeys``：新增动作不必再改这个函数，用户也能在
        「快捷键/动作」页里给任意动作绑键。

        Args:
            show_error: 是否显示错误提示（设置保存时为 True，启动时为 False）
        """

        # 注销所有已注册的热键
        self.hotkey_system.unregister_all()
        self.hotkey_system.set_suppressed(
            self.config_manager.get_app_setting("global_hotkeys_disabled", False)
        )

        failed_hotkeys = []  # 收集注册失败的热键

        for action_id, keys in self.config_manager.get_action_hotkeys().items():
            action = actions.ACTIONS_BY_ID.get(action_id)
            if action is None:
                log_warning(T("忽略不认识的动作快捷键: {action_id}", action_id=action_id),
                            "Hotkey")
                continue
            # 剪贴板监听关掉时它的热键也不必注册（开了也打不开窗口）
            if action_id == "clipboard" and not self.config_manager.get_clipboard_enabled():
                continue

            for index, hotkey in enumerate(keys):
                if not hotkey:
                    continue
                label = self.tr(action.label) + (" (2)" if index else "")
                if self.hotkey_system.register_hotkey(
                    hotkey, partial(actions.run_action, action_id, self)
                ):
                    log_info(T("动作热键已注册: {label} -> {hotkey}",
                               label=label, hotkey=hotkey), "Hotkey")
                else:
                    log_warning(T("动作热键注册失败: {label} -> {hotkey}",
                                  label=label, hotkey=hotkey), "Hotkey")
                    failed_hotkeys.append((label, hotkey))

        # 全局鼠标手势：绑定表跟着配置走，改一次重设一次（空表会停掉监听）
        self.hotkey_system.set_mouse_gestures(self.config_manager.get_mouse_gestures())

        if show_error and failed_hotkeys:
            self._show_hotkey_error(failed_hotkeys)

    def set_global_hotkeys_disabled(self, disabled: bool):
        """禁用/启用所有全局热键，并立即应用。"""
        self.config_manager.set_app_setting("global_hotkeys_disabled", disabled)
        self.hotkey_system.set_suppressed(disabled)
        if disabled:
            log_info(T("全局热键已临时禁用（保留注册，仅忽略回调）"), "Hotkey")
        else:
            log_info(T("全局热键已启用"), "Hotkey")
            if not self.hotkey_system.has_registered_hotkeys():
                self.update_hotkey(show_error=True)
    
    def _show_hotkey_error(self, failed_hotkeys: list):
        """显示热键注册失败的提示"""
        
        lines = []
        for name, key in failed_hotkeys:
            lines.append(f"• {name}: {key}")
        
        msg = self.tr("The following hotkeys failed to register:") + "\n\n"
        msg += "\n".join(lines)
        msg += "\n\n" + self.tr("The hotkey may be occupied by other programs. Please try a different combination.")
        
        log_debug(T("显示热键错误提示: {failed_hotkeys}", failed_hotkeys=failed_hotkeys), "Hotkey")
        
        show_warning_dialog(
            None,
            self.tr("Hotkey Registration Failed"),
            msg,
        )

    def setup_tray(self):
        if self.tray_icon:
            return

        if not QSystemTrayIcon.isSystemTrayAvailable():
            show_error_dialog(None, "Error", "System tray not available")
        self.tray_icon = QSystemTrayIcon(self)

        # Use custom icon
        icon = create_app_icon()
        self.tray_icon.setIcon(icon)
        apply_configured_app_icon()

        # 桌面工具栏（悬浮球）与网络代理：都按设置来
        self.apply_desktop_toolbar_mode()
        from core.net import apply_proxy_from_settings

        apply_proxy_from_settings()

        self.tray_icon.setToolTip(self.tr("jietuba - Click to screenshot"))

        # Menu
        self.tray_icon.setContextMenu(self._create_tray_menu())
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        """托盘图标被点：按设置执行动作（默认截图）。

        单击执行的动作取自「快捷键/动作」页那份注册表（``app/tray_click_action``），
        所以用户可以把托盘单击换成剪贴板、翻译等任何已登记的动作。
        """
        from core import actions

        if reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            # 托盘滚轮点击：默认不绑（空串=不做事），绑了才跑
            scroll_action = self.config_manager.get_tray_scroll_action()
            if not scroll_action:
                return
            log_debug(T("托盘滚轮触发动作: {action_id}", action_id=scroll_action), "Tray")
            actions.run_action(scroll_action, self)
            return
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return

        action_id = self.config_manager.get_tray_click_action()
        if action_id == "screenshot":
            self.start_screenshot()
            return
        log_debug(T("托盘单击触发动作: {action_id}", action_id=action_id), "Tray")
        actions.run_action(action_id, self)

    # ── 桌面工具栏（悬浮球）────────────────────────────────

    def apply_desktop_toolbar_mode(self) -> bool:
        """按设置显示/隐藏桌面悬浮球；返回当前是否在显示。

        启动时与设置保存后都要走这里：模式是「不显示」时把球收掉（并释放窗口），
        改成「悬浮球」时建一个出来。悬浮球自己负责位置持久化与单击/右键行为。
        """
        mode = self.config_manager.get_desktop_toolbar_mode()
        if mode != "floating_ball":
            if getattr(self, "floating_ball", None) is not None:
                self.floating_ball.hide_ball()
                self.floating_ball.deleteLater()
                self.floating_ball = None
            return False

        if getattr(self, "floating_ball", None) is None:
            from ui.floating_ball import FloatingBall

            self.floating_ball = FloatingBall()
        self.floating_ball.show_at_saved_position()
        return True

    def _activate_blocking_modal(self) -> bool:
        """模态窗口存在时阻止创建无法交互的截图层。"""
        modal = QApplication.activeModalWidget()
        if modal is None or not modal.isVisible():
            return False

        log_debug(
            T("检测到模态窗口 {modal_type}，忽略截图触发", modal_type=type(modal).__name__),
            "MainApp",
        )
        modal.raise_()
        modal.activateWindow()
        return True
            
    def start_screenshot(self):
        """启动截图 - 管理截图窗口生命周期"""
        
        # 已有截图窗口且会话活跃 → 忽略重复触发，并把焦点还给截图窗口
        if self.screenshot_window and getattr(self.screenshot_window, '_session_active', False):
            log_debug(T("截图窗口已存在，忽略重复触发"), "MainApp")
            # 这一次没打开新会话，请求（如果有）不能留到下一次普通截图
            self._pending_editor_call = None
            self.screenshot_window.activateWindow()
            self.screenshot_window.raise_()
            # 如果有颜色选择器正在显示，重新提到截图窗口上方，防止被全屏窗口遮挡
            from PySide6.QtWidgets import QApplication, QColorDialog
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QColorDialog) and w.isVisible():
                    w.raise_()
                    w.activateWindow()
                    return
            self.screenshot_window.setFocus()
            return

        # QDialog.exec() 的嵌套事件循环仍会处理托盘信号，但应用模态会屏蔽
        # 新截图窗口的输入。此时不创建截图层，转而把现有模态窗口提到前面。
        if self._activate_blocking_modal():
            self._pending_editor_call = None
            return
        
        # 后台截图线程正在运行时也忽略重复触发
        if getattr(self, '_capture_thread', None) and self._capture_thread.isRunning():
            log_debug(T("后台截图线程进行中，忽略重复触发"), "MainApp")
            self._pending_editor_call = None
            return

        # 关闭所有已打开的颜色选择器（避免其遮挡截图界面或触发焦点冲突）
        from PySide6.QtWidgets import QApplication, QColorDialog
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QColorDialog) and w.isVisible():
                w.reject()

        log_info(T("启动后台截图线程"), "MainApp")
        
        # 在后台线程执行 mss.grab()，避免主线程被阻塞 100~500ms
        from PySide6.QtCore import QThread, Signal

        class CaptureThread(QThread):
            captured = Signal(object, object)  # (QImage, QRectF)

            def run(self):
                try:
                    from capture.capture_service import CaptureService
                    image, rect = CaptureService().capture_all_screens()
                    self.captured.emit(image, rect)
                except Exception as e:
                    log_exception(e, T("后台截图失败"))

        self._capture_thread = CaptureThread()
        self._capture_thread.captured.connect(self._on_capture_ready)
        self._capture_thread.start()

    def start_screenshot_for_gesture(self, mode: str, rect=None) -> bool:
        """全局鼠标动作：带上检测到的区域和目标模式打开截图编辑器。

        抓屏是异步的，这里拿不到截图窗口，所以先把请求记下来，由 ``_on_capture_ready``
        在窗口显示后应用。
        """
        self._pending_editor_call = (mode, rect)
        self.start_screenshot()
        return True

    def _on_capture_ready(self, image, rect):
        """后台截图完成后，在主线程创建或复用截图窗口"""
        log_debug(T("后台截图完成，准备截图窗口"), "MainApp")

        pending = self._pending_editor_call
        self._pending_editor_call = None

        # 截图采集期间也可能弹出模态窗口，避免在线程结束后创建一个被锁死的界面。
        if self._activate_blocking_modal():
            return

        # 缺「屏幕录制」权限时抓屏照样成功，只是画面里没有窗口。用户此刻正好在场，
        # 提示一次并给一个能跳到权限页的入口（每条权限每进程只弹一次）。
        self._prompt_missing_capture_permission()

        if self.screenshot_window is not None:
            # 复用已有窗口（节省 ~250ms 的 UI 壳创建时间）
            log_debug(T("复用已有截图窗口"), "MainApp")
            self.screenshot_window.prepare_new_session(image, rect)
        else:
            # 首次创建
            log_debug(T("首次创建截图窗口"), "MainApp")
            self.screenshot_window = ScreenshotWindow(
                self.config_manager,
                prefetched_image=image,
                prefetched_rect=rect,
            )

        if pending is not None:
            mode, target_rect = pending
            try:
                self.screenshot_window.apply_editor_mode(mode, target_rect)
            except Exception as e:
                log_exception(e, T("进入截图模式"))
    
    def _prompt_missing_capture_permission(self):
        """缺「屏幕录制」权限时提示一次：截图里只有桌面时至少知道该去哪勾。"""
        from core.platform import capture as platform_capture
        from core.platform import permissions
        from ui.permission_prompt import prompt_missing_permission

        try:
            if platform_capture.screen_capture_trusted():
                return
            prompt_missing_permission(permissions.SCREEN_RECORDING)
        except Exception as e:
            log_exception(e, T("提示屏幕录制权限"))

    def _on_hotkey_permission_missing(self):
        """输入监听权限缺失（ShortcutManager 轮询发现用户还没授权）时提示一次。"""
        from core.platform import permissions
        from ui.permission_prompt import prompt_missing_permission

        try:
            prompt_missing_permission(permissions.ACCESSIBILITY)
        except Exception as e:
            log_exception(e, T("提示辅助功能权限"))

    def open_settings(self):
        """打开设置窗口"""
        if not self.settings_window:
            # Fallback: 如果还没预加载，立即创建
            self._preloader.preload_settings()
        
        # 确保窗口在可见屏幕内
        self._ensure_window_on_screen(self.settings_window)
        
        self.settings_window.setWindowState(self.settings_window.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _ensure_window_on_screen(self, win):
        """检查窗口位置，若在所有屏幕外则居中显示"""
        from PySide6.QtWidgets import QApplication
        pos = win.pos()
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(pos):
                return
        # 窗口不在任何屏幕内，重置到主屏幕中央
        screen = QApplication.primaryScreen()
        if screen:
            screen_rect = screen.availableGeometry()
            x = screen_rect.x() + (screen_rect.width() - win.width()) // 2
            y = screen_rect.y() + (screen_rect.height() - win.height()) // 2
            win.move(x, y)

    def _on_mouse_gesture(self, action_id: str) -> None:
        """全局鼠标手势命中的动作在主线程执行（抓屏、复制、钉图都要求在主线程）。

        「忽略程序列表」也在这里判：查前台程序在 macOS 上要走 AppKit，必须在主线程。
        """
        try:
            ignored = actions.gesture_ignored_app_name(self)
            if ignored:
                log_debug(
                    T("前台程序在忽略列表里，跳过鼠标动作: {name}", name=ignored),
                    "MouseGesture",
                )
                return
            actions.run_action(action_id, self)
        except Exception as e:
            from core.logger import log_exception

            log_exception(e, T("执行鼠标手势动作"))

    def on_settings_accepted(self):
        """设置保存后更新热键和剪贴板设置"""
        self.set_clipboard_monitoring_enabled(
            self.config_manager.get_clipboard_enabled()
        )
        self.update_hotkey(show_error=True)

        # 托盘菜单里的动作项由「快捷键/动作」页的托盘开关决定，改完要立刻重建菜单，
        # 否则要等下一次主题切换/钉图事件才会跟上。
        self._update_tray_menu()

        # 通知剪贴板窗口重新加载设置
        if hasattr(self, 'clipboard_window') and self.clipboard_window:
            self.clipboard_window._load_settings()
            # 同时更新历史限制
            if hasattr(self, 'clipboard_manager') and self.clipboard_manager:
                self.clipboard_manager._apply_history_limit()
    
    def open_translator(self):
        """打开翻译窗口"""
        from translation import TranslationManager
        
        params = self.config_manager.get_translation_request_params()
        
        manager = TranslationManager.instance()
        manager.translate(
            text="",
            **params
        )

    @Slot(object)
    def _on_clipboard_item_received(self, item):
        """Refresh clipboard UI on the GUI thread without owning probe logic."""
        if self.clipboard_window:
            self.clipboard_window.notify_new_content()

    def set_clipboard_monitoring_enabled(self, enabled: bool) -> bool:
        """Synchronize the Rust clipboard watcher with the saved setting.

        The manager stays allocated while disabled so a later enable only needs
        to start its watcher thread.  ``stop_monitoring`` joins that thread,
        making this method safe to call again immediately after disabling it.
        """
        manager = self.clipboard_manager

        if not enabled:
            if not manager:
                log_debug(T("剪贴板监听已禁用，未创建管理器"), "Clipboard")
                return True
            if not manager.is_available:
                log_warning(T("剪贴板管理器不可用，无法停止监听"), "Clipboard")
                return False
            if not manager.is_monitoring():
                return True

            try:
                manager.stop_monitoring()
            except Exception as e:
                log_exception(e, T("停止剪贴板监听"))
                return False

            stopped = not manager.is_monitoring()
            if stopped:
                log_info(T("剪贴板监听已按设置关闭"), "Clipboard")
            return stopped

        try:
            if manager is None:
                from clipboard import ClipboardManager
                manager = ClipboardManager()
                self.clipboard_manager = manager

            if not manager.is_available:
                log_warning(T("剪贴板管理器不可用（pyclipboard 未安装）"), "Clipboard")
                return False
            if manager.is_monitoring():
                return True

            manager.start_monitoring(callback=self.clipboard_item_received.emit)
            started = manager.is_monitoring()
            if started:
                log_info(T("剪贴板监听已按设置启动"), "Clipboard")
            else:
                log_warning(T("剪贴板监听启动失败"), "Clipboard")
            return started
        except ImportError:
            log_debug(T("clipboard 模块不存在"), "Clipboard")
        except Exception as e:
            log_exception(e, T("启动剪贴板监听"))
        return False
    
    def open_history_window(self):
        """打开截图历史窗口（托盘/热键/全局鼠标动作共用这个入口）。"""
        try:
            from history.window import open_history_window

            self.history_window = open_history_window(self.config_manager)
            return self.history_window
        except Exception as e:
            from core.logger import log_exception

            log_exception(e, T("打开截图历史窗口"))
            return None

    def open_image_viewer(self):
        """打开独立图片查看器：优先看剪贴板里的图，其次看历史里最新的一条。"""
        from PySide6.QtGui import QImage

        from core.actions import clipboard_image
        from ui.image_viewer import open_image_viewer

        image = clipboard_image()
        if image is None or image.isNull():
            from history import get_store

            entries = get_store().entries()
            if entries:
                image = get_store().image(entries[0].id)
        if image is None or image.isNull():
            log_warning(T("没有可查看的图片（剪贴板与历史都为空）"), "ImageViewer")
            return None
        return open_image_viewer(image=QImage(image), config_manager=self.config_manager)

    def open_main_window(self):
        """打开主窗口（历史 / 翻译 / 设置 / 关于）。"""
        from ui.main_window import open_main_window

        self.main_window = open_main_window(self.config_manager)
        return self.main_window

    def open_clipboard_window(self):
        """打开剪切板历史窗口"""
        
        try:
            from clipboard import ClipboardWindow
            
            # 如果窗口已存在且可见，则关闭
            if self.clipboard_window and self.clipboard_window.isVisible():
                self.clipboard_window.close()
                return
            
            # 如果窗口不存在，创建新窗口
            if not self.clipboard_window:
                self.clipboard_window = ClipboardWindow()
            
            self.clipboard_window.setWindowState(self.clipboard_window.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
            self.clipboard_window.show()
            self.clipboard_window.raise_()
            self.clipboard_window.activateWindow()
            log_debug(T("剪切板窗口已打开"), "Clipboard")

        except Exception as e:
            log_exception(e, T("打开剪切板窗口失败"))
        
    def quit_app(self):
        # 先把「未关闭的贴图」存下来：退出流程里贴图会被关闭，之后再存就只剩空列表了
        try:
            self.save_pin_session_if_enabled()
        except Exception as e:
            log_exception(e, T("保存贴图会话"))

        # 完全销毁缓存的截图窗口
        if self.screenshot_window:
            try:
                self.screenshot_window.full_destroy()
            except Exception as e:
                log_exception(e, T("销毁截图窗口"))
            self.screenshot_window = None

        # 关闭剪贴板窗口
        if self.clipboard_window:
            try:
                self.clipboard_window.close()
            except Exception as e:
                log_exception(e, T("关闭剪贴板窗口"))
            self.clipboard_window = None

        # 停止剪贴板监听
        if self.clipboard_manager and self.clipboard_manager.is_available:
            try:
                self.clipboard_manager.stop_monitoring()
            except Exception as e:
                log_exception(e, T("停止剪贴板监听"))

        # 关闭设置窗口
        if self.settings_window:
            try:
                self.settings_window.close()
            except Exception as e:
                log_exception(e, T("关闭设置窗口"))
            self.settings_window = None

        # 等待预加载线程结束（最多 2 秒，避免卡退出）
        for attr in ('_screenshot_preload_thread', '_ocr_preload_thread', '_capture_thread'):
            thread = getattr(self, attr, None)
            if thread and thread.isRunning():
                thread.wait(2000)

        self.hotkey_system.unregister_all()
        self.app.quit()
        
    def run(self):
        sys.exit(self.app.exec())

if __name__ == "__main__":
    from core.bootstrap import run
    run()
