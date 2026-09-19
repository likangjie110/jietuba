# -*- coding: utf-8 -*-
"""
设置对话框主类 — Fluent 风格

负责：导航栏、内容堆栈、底部按钮、accept/refresh/reset 逻辑。
各个页面分别位于 page_*.py 模块中。
"""
import os

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget, QWidget, QDialogButtonBox,
    QFileDialog, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import QSize, Qt, Signal
from ui.dialogs import show_info_dialog, show_warning_dialog
from PySide6.QtGui import QColor, QFont, QIcon

from ui.fluent_lite import (
    LineEdit,
    NavigationInterface, NavigationItemPosition,
    FluentIcon, BodyLabel,
    PushButton as FluentPushButton,
    PrimaryPushButton, TransparentPushButton,
    FrostedFramelessDialog,
)
from ui.fluent_lite import FluentTitleBar, scrollbar_qss
from ui.fluent_lite.theme import ACCENT, ACCENT_HOVER, ACCENT_PRESSED

from core import log_info, safe_event
from core.logger import log_debug, log_exception, T
from core.constants import CSS_FONT_FAMILY, DEFAULT_FONT_FAMILY
from core.platform import permissions, shell, window_ops

# 页面创建函数
from .page_hotkey import (
    collect_action_hotkeys, collect_action_tray_flags, create_hotkey_page,
    refresh_action_rows, reset_action_rows, validate_global_hotkey_edits,
)
from .page_mouse import (
    collect_mouse_gestures, create_mouse_page, read_ignored_apps, reset_mouse_page,
)
from .page_appearance import _refresh_logo_preview, _refresh_skin_buttons, _update_color_btn
from .page_capture import create_capture_page, refresh_capture_appearance
from .page_pin import collect_pin_settings, create_pin_page, refresh_pin_page, reset_pin_page
from .page_annotation import (
    apply_annotation_settings, create_annotation_page, refresh_annotation_page,
    reset_annotation_page,
)
from .page_clipboard import create_clipboard_page
from .page_translation import create_translation_page
from .page_log import create_log_page, refresh_latest_log_label
from .page_misc import create_misc_page
from .page_appearance import create_appearance_page
from .page_developer import create_developer_page
from .page_about import create_about_page
from .page_permission import create_permission_page
from .components import (
    theme_surface_color, theme_sidebar_color,
    theme_input_background, theme_popup_background,
    theme_popup_hover_background, theme_text_style, theme_menu_style, refresh_theme_widget_styles,
    apply_theme_text_style,
)

# 权限页在内容栈里的下标。导航项先于内容栈构建，两处都要用它，所以写成常量。
_PERMISSION_PAGE_INDEX = 12
# 开发者选项页没有导航项，只能由 logo 右键菜单进入
_DEVELOPER_PAGE_INDEX = 10


class _FooterPrimaryButton(PrimaryPushButton):
    """Large dialog action button with a subtle trailing sparkle."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._sparkle = QLabel(self)
        self._sparkle.setFixedSize(18, 22)
        self._sparkle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sparkle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._sparkle.setPixmap(FluentIcon.SPARKLE.icon().pixmap(16, 16))
        self._sparkle.setStyleSheet("background: transparent; border: none;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sparkle.move(self.width() - 24, (self.height() - self._sparkle.height()) // 2)


def save_inapp_shortcut_edits(config_manager, edits):
    """Persist in-app editors while preserving empty-as-unbound semantics."""
    from core.shortcut_manager import is_reserved_inapp_shortcut

    for cfg_key, edit in edits.items():
        value = edit.text().strip()
        if value.endswith("+"):
            continue
        config_manager.set_inapp_shortcut(
            cfg_key, "" if is_reserved_inapp_shortcut(value) else value
        )


class SettingsDialog(FrostedFramelessDialog):
    """现代化设置对话框 - Fluent 风格（无系统标题栏）"""

    wizard_requested = Signal()

    def __init__(self, config_manager=None, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.main_window = parent
        self._skip_unsaved_close_prompt = False
        if self.config_manager is None:
            from .mock_config import MockConfig
            self.config_manager = MockConfig()

        # 自定义 Fluent 标题栏（在 setWindowTitle 之前，以接收信号）
        self._setup_titlebar()

        # 平台声明了权限需求（macOS）才有权限页与对应导航项；导航先于内容栈构建，先算出来
        self._permission_page_index = (
            _PERMISSION_PAGE_INDEX if permissions.requirements() else None
        )

        self.setWindowTitle("jietuba")
        self.resize(900, 670)
        self.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        self.setObjectName("SettingsDialog")

        self._setup_ui()
        from core.ui_theme import get_ui_theme
        get_ui_theme().theme_changed.connect(self._on_ui_theme_changed)

    def _setup_titlebar(self):
        """用 FluentTitleBar 替换默认标题栏"""
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        title_bar.iconLabel.hide()
        title_bar.titleLabel.hide()
        title_bar.setFixedHeight(title_bar.buttonLayout.sizeHint().height())
        title_bar.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        title_bar.maxBtn.hide()
        title_bar.setDoubleClickEnabled(False)

        # 设置窗口图标
        try:
            from core.resource_manager import ResourceManager
            self.setWindowIcon(ResourceManager.get_app_icon())
        except Exception as e:
            log_exception(e, T("设置窗口图标"))

    # ================================================================
    # UI 构建
    # ================================================================

    def _setup_ui(self):
        sidebar_width = 212
        title_bar_height = self.titleBar.height() if getattr(self, 'titleBar', None) else 32

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, title_bar_height + 6, 10, 10)
        main_layout.setSpacing(10)

        # 1. 左侧面板
        left_panel = QWidget()
        left_panel.setObjectName("SettingsLeftPanel")
        left_panel.setFixedWidth(sidebar_width)
        left_v = QVBoxLayout(left_panel)
        left_v.setContentsMargins(10, 6, 10, 10)
        left_v.setSpacing(8)

        # logo 区
        logo_area = QWidget()
        logo_area.setFixedHeight(78)
        logo_layout = QHBoxLayout(logo_area)
        logo_layout.setContentsMargins(12, 10, 12, 10)
        logo_layout.setSpacing(10)
        logo_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        logo_icon_lbl = QLabel()
        logo_icon_lbl.setFixedSize(36, 36)
        logo_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        try:
            from core.resource_manager import ResourceManager
            icon_path = ResourceManager.get_resource_path("svg/托盘.svg")
            if os.path.exists(icon_path):
                pm = QIcon(icon_path).pixmap(36, 36)
                logo_icon_lbl.setPixmap(pm)
        except Exception as e:
            log_exception(e, T("加载 Logo 图标"))
        logo_layout.addWidget(logo_icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        app_name_lbl = BodyLabel(self.tr("jietuba"))
        apply_theme_text_style(app_name_lbl, 15, bold=True)
        app_desc_lbl = QLabel(self.tr("Settings"))
        apply_theme_text_style(app_desc_lbl, 12, caption=True)
        text_box.addWidget(app_name_lbl)
        text_box.addWidget(app_desc_lbl)
        logo_layout.addLayout(text_box, 1)
        left_v.addWidget(logo_area)

        # logo 右键菜单 → 开发者选项
        logo_area.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        logo_area.customContextMenuRequested.connect(self._show_logo_context_menu)

        self.nav_list = self._create_navigation(left_panel)
        left_v.addWidget(self.nav_list, 1)
        main_layout.addWidget(left_panel)

        # 2. 右侧内容区
        right_area = QWidget()
        self.right_area = right_area
        right_area.setObjectName("SettingsRightArea")
        right_layout = QVBoxLayout(right_area)
        right_layout.setContentsMargins(24, 16, 24, 18)
        right_layout.setSpacing(14)

        self.content_title = QLabel(self.tr("Shortcut Settings"))
        apply_theme_text_style(
            self.content_title, 20, bold=True,
            extra="padding: 0 6px 2px 6px;"
        )

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(create_hotkey_page(self))           # 0
        self.content_stack.addWidget(create_mouse_page(self))            # 1
        self.content_stack.addWidget(create_capture_page(self))          # 2
        self.content_stack.addWidget(create_pin_page(self))              # 3
        self.content_stack.addWidget(create_annotation_page(self))       # 4
        self.content_stack.addWidget(create_clipboard_page(self))        # 5
        self.content_stack.addWidget(create_appearance_page(self))       # 6
        self.content_stack.addWidget(create_translation_page(self))      # 7
        self.content_stack.addWidget(create_log_page(self))              # 8
        self.content_stack.addWidget(create_misc_page(self))             # 9
        self.content_stack.addWidget(create_developer_page(self))        # 10
        self.content_stack.addWidget(create_about_page(self))            # 11
        if self._permission_page_index is not None:
            self.content_stack.addWidget(create_permission_page(self))   # 12

        right_layout.addWidget(self._create_search_box())
        right_layout.addWidget(self.content_title)
        right_layout.addWidget(self.content_stack)
        right_layout.setStretchFactor(self.content_stack, 1)
        right_layout.addLayout(self._create_button_area())
        main_layout.addWidget(right_area, 1)

        self._apply_dialog_stylesheet()

        self._set_current_nav("shortcuts")

    def _create_search_box(self):
        """顶部的设置搜索：输入关键字 → 列出命中的设置项 → 点一下跳到那一页。

        索引从真实的页面部件现算（见 ui/settings_ui/search.py），不手抄清单；
        空关键字返回空表，因此清空输入框不会弹出一整屏结果。
        """
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        self.search_input = LineEdit(box)
        self.search_input.setPlaceholderText(self.tr("Search settings and functions"))
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        self.search_results = QListWidget(box)
        self.search_results.setMaximumHeight(160)
        self.search_results.itemClicked.connect(self._on_search_result_clicked)
        self.search_results.hide()
        layout.addWidget(self.search_results)
        self._search_entries = []
        return box

    def _ensure_search_index(self):
        """懒建索引：页面已经建好了，第一次搜索时才去遍历（启动不为此多花时间）。"""
        if self._search_entries:
            return self._search_entries
        from .search import index_from_widgets

        names = {index: text for _key, _icon, text, index, _pos in self._nav_items}
        pages = [self.content_stack.widget(index)
                 for index in range(self.content_stack.count())]
        self._search_entries = index_from_widgets(names, pages)
        return self._search_entries

    def _on_search_changed(self, text: str):
        from .search import search

        hits = search(self._ensure_search_index(), text)
        self.search_results.clear()
        for hit in hits:
            item = QListWidgetItem(f"{hit.entry.title}  ·  {hit.entry.page_name}")
            item.setData(Qt.ItemDataRole.UserRole, hit.entry.page_index)
            self.search_results.addItem(item)
        self.search_results.setVisible(bool(hits))

    def _on_search_result_clicked(self, item):
        if item is None:
            return
        index = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        self.content_stack.setCurrentIndex(index)
        for route_key, _icon, _text, stack_index, _pos in self._nav_items:
            if stack_index == index:
                self._set_current_nav(route_key)
                break
        log_debug(T("设置搜索跳转: 第 {index} 页", index=index), "SettingsDialog")

    def _create_navigation(self, parent=None):
        """创建左侧导航栏"""
        nav = NavigationInterface(parent=parent, showMenuButton=False, showReturnButton=False, collapsible=False)
        nav.setObjectName("SettingsNavigation")
        nav.setExpandWidth(188)
        nav.setMinimumExpandWidth(0)
        nav.expand(useAni=False)
        nav.setMinimumWidth(188)
        nav.setMaximumWidth(196)

        self._nav_items = [
            ("shortcuts", FluentIcon.COMMAND_PROMPT, self.tr("Shortcuts"), 0, NavigationItemPosition.TOP),
            ("mouse", FluentIcon.SETTING, self.tr("Global Mouse"), 1, NavigationItemPosition.TOP),
            ("capture", FluentIcon.CAMERA, self.tr("Capture Settings"), 2, NavigationItemPosition.TOP),
            ("pin", FluentIcon.PIN, self.tr("Pin Settings"), 3, NavigationItemPosition.TOP),
            ("annotation", FluentIcon.BRUSH, self.tr("Annotation"), 4, NavigationItemPosition.TOP),
            ("clipboard", FluentIcon.PASTE, self.tr("Clipboard"), 5, NavigationItemPosition.TOP),
            ("appearance", FluentIcon.BRUSH, self.tr("Appearance"), 6, NavigationItemPosition.TOP),
            ("translation", FluentIcon.LANGUAGE, self.tr("Translation"), 7, NavigationItemPosition.TOP),
            ("log", FluentIcon.HISTORY, self.tr("Log Settings"), 8, NavigationItemPosition.TOP),
            ("other", FluentIcon.APPLICATION, self.tr("Other"), 9, NavigationItemPosition.TOP),
            ("about", FluentIcon.INFO, self.tr("About"), 11, NavigationItemPosition.BOTTOM),
        ]

        if self._permission_page_index is not None:
            self._nav_items.insert(
                5,
                ("permissions", FluentIcon.CERTIFICATE, self.tr("Permissions"),
                 self._permission_page_index, NavigationItemPosition.TOP),
            )

        for route_key, icon, text, stack_index, position in self._nav_items:
            nav.addItem(
                routeKey=route_key,
                icon=icon,
                text=text,
                onClick=lambda checked=False, idx=stack_index, rk=route_key: self._on_nav_changed(idx, rk),
                position=position,
            )

        return nav

    def _set_current_nav(self, route_key: str):
        if hasattr(self, 'nav_list') and self.nav_list is not None:
            self.nav_list.setCurrentItem(route_key)

    # ================================================================
    # 辅助方法
    # ================================================================

    def _create_toggle_row(self, title, desc, checked_state, toggle_obj):
        """创建一个标准的一行设置：左字右开关"""
        row = QHBoxLayout()
        text_layout = QVBoxLayout()
        lbl_title = QLabel(title)
        apply_theme_text_style(lbl_title, 13)
        text_layout.addWidget(lbl_title)
        if desc:
            lbl_desc = QLabel(desc)
            apply_theme_text_style(lbl_desc, 12, caption=True)
            text_layout.addWidget(lbl_desc)
        row.addLayout(text_layout)
        row.addStretch()
        toggle_obj.setChecked(checked_state)
        row.addWidget(toggle_obj)
        return row

    def _get_input_style(self):
        from core.ui_theme import get_ui_theme

        tokens = get_ui_theme().tokens
        input_bg = theme_input_background()
        popup_bg = theme_popup_background()
        popup_hover = theme_popup_hover_background()
        # 输入框的文字/边框/按下底色都跟皮肤走：写死的话，皮肤换了底色，
        # 输入框里的字还是老配色（深底深字）
        text_color = tokens.text
        border_color = tokens.border
        focus_bg = tokens.surface_strong
        pressed_bg = tokens.accent_soft
        arrow_color = tokens.text_muted
        return f"""
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
                border: 1px solid {border_color}; border-radius: 4px;
                padding: 4px 8px; background-color: {input_bg};
                color: {text_color}; font-family: {CSS_FONT_FAMILY};
                font-size: 12px;
            }}
            QLineEdit:focus, QSpinBox:focus {{
                border: 1px solid {ACCENT}; background-color: {focus_bg};
            }}
            QSpinBox, QDoubleSpinBox {{ padding-right: 24px; }}
            QSpinBox::up-button, QDoubleSpinBox::up-button {{
                subcontrol-origin: border; subcontrol-position: top right;
                width: 20px; border-left: 1px solid {border_color};
                border-bottom: 1px solid {border_color}; border-top-right-radius: 4px;
                background: {input_bg};
            }}
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{ background: {popup_hover}; }}
            QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed {{ background: {pressed_bg}; }}
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-bottom: 6px solid {arrow_color};
                width: 0; height: 0;
            }}
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                subcontrol-origin: border; subcontrol-position: bottom right;
                width: 20px; border-left: 1px solid {border_color};
                border-bottom-right-radius: 4px; background: {input_bg};
            }}
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {popup_hover}; }}
            QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{ background: {pressed_bg}; }}
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 6px solid {arrow_color};
                width: 0; height: 0;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 20px; border-left: 1px solid {border_color};
                border-top-right-radius: 4px; border-bottom-right-radius: 4px;
                background: {input_bg};
            }}
            QComboBox::down-arrow {{
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 6px solid {arrow_color};
                width: 0; height: 0; margin-right: 6px;
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid {border_color}; background: {popup_bg};
                selection-background-color: {ACCENT}; selection-color: white;
                font-family: {CSS_FONT_FAMILY};
                font-size: 12px; color: {text_color}; outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 8px; min-height: 24px; color: {text_color}; background: {popup_bg};
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {popup_hover}; color: {text_color};
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: {ACCENT}; color: white;
            }}
        """

    # ================================================================
    # 导航 & 开发者入口
    # ================================================================

    def _on_nav_changed(self, stack_index, route_key=None):
        title_map = {
            0: self.tr("Shortcut Settings"),
            1: self.tr("Global Mouse Settings"),
            2: self.tr("Capture Settings"),
            3: self.tr("Pin Settings"),
            4: self.tr("Annotation Settings"),
            5: self.tr("Clipboard Settings"),
            6: self.tr("Appearance Settings"),
            7: self.tr("Translation Settings"),
            8: self.tr("Log Settings"),
            9: self.tr("Other Settings"),
            11: self.tr("Software Information"),
            _PERMISSION_PAGE_INDEX: self.tr("Permissions"),
        }

        if stack_index in title_map:
            self.content_title.setText(title_map[stack_index])
            self.content_stack.setCurrentIndex(stack_index)
            if route_key:
                self._set_current_nav(route_key)
            # 权限页没有可重置的配置项，按钮留着只会给人「点了没反应」的错觉
            if hasattr(self, "_footer_reset_btn"):
                self._footer_reset_btn.setEnabled(stack_index != _PERMISSION_PAGE_INDEX)
            self._refresh_after_page_change()

    def _refresh_clipboard_size(self, delay_ms: int = 0):
        if not hasattr(self, '_clipboard_size_label') or not hasattr(self, '_calc_clipboard_storage_size'):
            return
        if delay_ms > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(delay_ms, self._refresh_clipboard_size)
            return
        size_str = self._calc_clipboard_storage_size()
        self._clipboard_size_label.setText(size_str if size_str else "—")

    def _show_logo_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(theme_menu_style())
        action_dev = menu.addAction(self.tr("Developer Options"))
        action = menu.exec(self.mapToGlobal(pos) if pos else self.cursor().pos())
        if action == action_dev:
            self._open_developer_page()

    def _open_developer_page(self):
        self.content_stack.setCurrentIndex(_DEVELOPER_PAGE_INDEX)
        self.content_title.setText(self.tr("Developer Options"))
        self.nav_list.clearCurrentItem()
        self._refresh_after_page_change()

    def show_permission_page(self) -> bool:
        """直接跳到权限页；平台没有权限需求时返回 False。

        抓屏/热键因为缺权限失败时，弹窗要把用户送到这里（``ui.permission_prompt``），
        而不是让人自己去翻设置。
        """
        if self._permission_page_index is None:
            return False
        self._set_current_nav("permissions")
        self._on_nav_changed(self._permission_page_index, "permissions")
        return True

    def _refresh_after_page_change(self):
        """Clear the translucent backing store before painting a new page."""
        self.update()
        self.right_area.update()
        self.content_stack.update()
        current = self.content_stack.currentWidget()
        if current is not None:
            current.update()

    def _open_welcome_wizard(self):
        self.wizard_requested.emit()

    # ================================================================
    # 文件/目录操作
    # ================================================================

    def _change_save_dir(self):
        new_dir = QFileDialog.getExistingDirectory(self, self.tr("Select Screenshot Save Folder"), self.config_manager.get_screenshot_save_path())
        if new_dir:
            self.save_path_lbl.setText(new_dir)

    def _open_save_dir(self):
        path = self.config_manager.get_screenshot_save_path()
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        shell.open_path(path)

    def _change_log_dir(self):
        new_dir = QFileDialog.getExistingDirectory(self, self.tr("Select Log Save Folder"), self.config_manager.get_log_dir())
        if new_dir:
            self.path_lbl.setText(new_dir)

    def _open_log_dir(self):
        path = self.config_manager.get_log_dir()
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        shell.open_path(path)

    # ================================================================
    # 底部按钮
    # ================================================================

    def _create_button_area(self):
        layout = QHBoxLayout()
        layout.setSpacing(12)

        reset_btn = TransparentPushButton(self.tr("Reset This Page"))
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setIcon(FluentIcon.DELETE)
        reset_btn.clicked.connect(self._reset_current_page)
        self._footer_reset_btn = reset_btn

        cancel_btn = FluentPushButton(self.tr("Cancel"))
        self._footer_cancel_btn = cancel_btn
        cancel_btn.setFixedHeight(46)
        cancel_btn.setMinimumWidth(150)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setIcon(FluentIcon.CANCEL)
        cancel_btn.setIconSize(QSize(22, 22))
        cancel_btn.setStyleSheet("""
            QPushButton {
                min-height: 44px;
                padding: 0 24px;
                color: #20262D;
                background: rgba(255, 255, 255, 0.18);
                border: 1px solid rgba(77, 88, 101, 0.38);
                border-radius: 12px;
                font-size: 16px;
                font-weight: 500;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.46);
                border-color: rgba(55, 68, 82, 0.55);
            }
            QPushButton:pressed {
                background: rgba(220, 228, 236, 0.60);
                border-color: rgba(55, 68, 82, 0.66);
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = _FooterPrimaryButton(self.tr("Apply"))
        self._footer_ok_btn = ok_btn
        ok_btn.setFixedHeight(46)
        ok_btn.setMinimumWidth(150)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setIcon(FluentIcon.CHECK)
        ok_btn.setIconSize(QSize(23, 23))
        ok_btn.setStyleSheet("""
            QPushButton {
                min-height: 44px;
                padding: 0 25px;
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #91AABD, stop:0.52 %s, stop:1 #607F9A);
                border: 1px solid rgba(255, 255, 255, 0.34);
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                outline: none;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #9AAFC0, stop:0.52 %s, stop:1 #58748D);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 %s, stop:1 #465E73);
                padding-top: 2px;
            }
            QPushButton:disabled {
                color: rgba(255, 255, 255, 0.72);
                background: rgba(151, 170, 186, 0.68);
            }
        """ % (ACCENT, ACCENT_HOVER, ACCENT_PRESSED))
        ok_btn.clicked.connect(self.accept)
        self._apply_footer_styles()

        layout.addWidget(reset_btn)
        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(ok_btn)
        return layout

    def _apply_footer_styles(self):
        from core.ui_theme import get_ui_theme
        t = get_ui_theme().tokens
        cancel_btn = getattr(self, "_footer_cancel_btn", None)
        if cancel_btn is not None:
            cancel_btn.setStyleSheet(f"""
                QPushButton {{
                    min-height: 44px;
                    padding: 0 24px;
                    color: {t.text};
                    background: {t.surface};
                    border: 1px solid {t.border_hover};
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 500;
                    outline: none;
                }}
                QPushButton:hover {{
                    background: {t.surface_hover};
                    border-color: {t.border_hover};
                }}
                QPushButton:pressed {{
                    background: {t.surface_subtle};
                }}
            """)
        ok_btn = getattr(self, "_footer_ok_btn", None)
        if ok_btn is not None:
            ok_btn.setStyleSheet(f"""
                QPushButton {{
                    min-height: 44px;
                    padding: 0 25px;
                    color: #FFFFFF;
                    background: {t.accent};
                    border: 1px solid rgba(255, 255, 255, 0.28);
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 600;
                    outline: none;
                }}
                QPushButton:hover {{
                    background: {t.accent_hover};
                }}
                QPushButton:pressed {{
                    background: {t.accent_pressed};
                    padding-top: 2px;
                }}
                QPushButton:disabled {{
                    color: rgba(255, 255, 255, 0.72);
                    background: #687D8F;
                }}
            """)

    # ================================================================
    # 重置页面
    # ================================================================

    def _reset_current_page(self):
        current_index = self.content_stack.currentIndex()
        if current_index == 0:
            self._reset_hotkey_page()
        elif current_index == 1:
            self._reset_mouse_page()
        elif current_index == 2:
            self._reset_screenshot_settings_page()
        elif current_index == 3:
            self._reset_pin_page()
        elif current_index == 4:
            self._reset_annotation_page()
        elif current_index == 5:
            self._reset_clipboard_page()
        elif current_index == 6:
            self._reset_appearance_page()
        elif current_index == 7:
            self._reset_translation_page()
        elif current_index == 8:
            self._reset_log_page()
        elif current_index == 9:
            self._reset_misc_page()
        elif current_index == 10:
            self._reset_long_screenshot_page()
        elif current_index == 11:
            pass

    def _reset_hotkey_page(self):
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        # 全局动作：恢复成默认动作表（动作清单、热键、托盘开关一起回默认）
        reset_action_rows(self)
        # 应用内快捷键
        if hasattr(self, '_inapp_edits'):
            for cfg_key, edit in self._inapp_edits.items():
                edit.setText(defaults.get(cfg_key, ""))
        if hasattr(self, 'cursor_move_combo'):
            idx = self.cursor_move_combo.findData(defaults["inapp_cursor_move_mode"])
            if idx >= 0:
                self.cursor_move_combo.setCurrentIndex(idx)

    def _reset_mouse_page(self):
        """重置全局鼠标页。"""
        reset_mouse_page(self)

    def _reset_pin_page(self):
        """重置贴图页。"""
        reset_pin_page(self)

    def _reset_annotation_page(self):
        """重置标注页（各工具回到出厂样式）。"""
        reset_annotation_page(self)

    def _reset_long_screenshot_page(self):
        """重置开发者选项页。"""
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        if hasattr(self, 'engine_combo'):
            index = self.engine_combo.findData(defaults["long_stitch_engine"])
            if index >= 0:
                self.engine_combo.setCurrentIndex(index)
        if hasattr(self, 'cooldown_spinbox'):
            self.cooldown_spinbox.setValue(defaults["scroll_cooldown"])
        if hasattr(self, 'ignore_top_pixels_spinbox'):
            self.ignore_top_pixels_spinbox.setValue(defaults["long_stitch_ignore_top_pixels"])
        for key in (
            "preload_screenshot", "preload_toolbar", "preload_ocr",
            "preload_settings", "preload_clipboard",
        ):
            toggle = getattr(self, f"{key}_toggle", None)
            if toggle is not None:
                toggle.setChecked(defaults[key])
        if hasattr(self, 'info_hide_on_drag_toggle'):
            self.info_hide_on_drag_toggle.setChecked(
                defaults["screenshot_info_hide_on_drag"]
            )

    def _reset_appearance_page(self):
        """重置外观设置页面"""
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        if hasattr(self, '_ui_theme_combo'):
            index = self._ui_theme_combo.findData(
                defaults.get("ui_theme_mode", "system")
            )
            if index >= 0:
                self._ui_theme_combo.setCurrentIndex(index)
        if hasattr(self, '_appearance_theme_color'):
            self._appearance_theme_color = QColor(defaults["theme_color"])
            _update_color_btn(self._theme_color_btn, self._appearance_theme_color)
        if hasattr(self, '_appearance_mask_color'):
            self._appearance_mask_color = QColor(
                defaults["mask_color_r"], defaults["mask_color_g"],
                defaults["mask_color_b"]
            )
            _update_color_btn(self._mask_color_btn, self._appearance_mask_color)
        if hasattr(self, '_skin_overrides'):
            from core.ui_theme import SkinOverrides

            self._skin_overrides = SkinOverrides.from_values(
                accent=defaults.get("skin_accent_color"),
                window=defaults.get("skin_window_color"),
                text=defaults.get("skin_text_color"),
            )
            if hasattr(self, '_skin_buttons'):
                _refresh_skin_buttons(self)
        if hasattr(self, '_custom_logo_path'):
            self._custom_logo_path = defaults.get("custom_logo_path", "") or ""
            if hasattr(self, '_logo_preview'):
                _refresh_logo_preview(self)

    def _reset_screenshot_settings_page(self):
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        _reset_integration_controls(self)
        if hasattr(self, 'double_click_copy_close_toggle'):
            self.double_click_copy_close_toggle.setChecked(
                defaults["double_click_copy_close"]
            )
        if hasattr(self, 'cross_tool_selection_toggle'):
            self.cross_tool_selection_toggle.setChecked(
                defaults["cross_tool_selection"]
            )
        if hasattr(self, 'text_always_on_top_toggle'):
            self.text_always_on_top_toggle.setChecked(
                defaults["text_always_on_top"]
            )
        if hasattr(self, 'ui_detection_combo'):
            index = self.ui_detection_combo.findData(defaults["ui_detection"])
            self.ui_detection_combo.setCurrentIndex(max(0, index))
        if hasattr(self, 'ui_detection_margin_spin'):
            self.ui_detection_margin_spin.setValue(defaults["ui_detection_margin"])
        if hasattr(self, 'save_toggle'):
            self.save_toggle.setChecked(defaults["screenshot_save_enabled"])
        if hasattr(self, 'save_path_lbl'):
            self.save_path_lbl.setText(defaults["screenshot_save_path"])
        if hasattr(self, 'screenshot_format_combo'):
            idx = {"PNG": 0, "JPG": 1, "BMP": 2, "WEBP": 3, "PDF": 4}.get(defaults["screenshot_format"].upper(), 0)
            self.screenshot_format_combo.setCurrentIndex(idx)
        if hasattr(self, 'ocr_enable_toggle'):
            self.ocr_enable_toggle.setChecked(defaults["ocr_enabled"])
        if hasattr(self, 'ocr_engine_combo'):
            index = self.ocr_engine_combo.findData(defaults["ocr_engine"])
            if index >= 0:
                self.ocr_engine_combo.setCurrentIndex(index)

    def _reset_log_page(self):
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        if hasattr(self, 'log_toggle'):
            self.log_toggle.setChecked(defaults["log_enabled"])
        if hasattr(self, 'log_level_combo'):
            self.log_level_combo.setCurrentText(defaults["log_level"])
        if hasattr(self, 'log_retention_combo'):
            index = self.log_retention_combo.findData(defaults["log_retention_days"])
            if index < 0:
                index = self.log_retention_combo.findData(7)
            if index >= 0:
                self.log_retention_combo.setCurrentIndex(index)
        if hasattr(self, 'path_lbl'):
            self.path_lbl.setText(defaults["log_dir"])

    def _reset_misc_page(self):
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        _reset_integration_controls(self)
        if hasattr(self, 'autostart_toggle'):
            self.autostart_toggle.setChecked(False)
        if hasattr(self, 'show_main_window_toggle'):
            self.show_main_window_toggle.setChecked(defaults["show_main_window"])
        if hasattr(self, 'pin_auto_toolbar_toggle'):
            self.pin_auto_toolbar_toggle.setChecked(defaults["pin_auto_toolbar"])
        if hasattr(self, 'magnifier_color_format_combo'):
            index = self.magnifier_color_format_combo.findData(defaults.get("magnifier_color_copy_format", "rgb_hex"))
            if index >= 0:
                self.magnifier_color_format_combo.setCurrentIndex(index)

    def _reset_translation_page(self):
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        if hasattr(self, 'translation_provider_combo'):
            index = self.translation_provider_combo.findData(
                defaults["translation_provider"]
            )
            if index >= 0:
                self.translation_provider_combo.setCurrentIndex(index)
        if hasattr(self, 'deepl_api_key_input'):
            self.deepl_api_key_input.setText(defaults["deepl_api_key"])
        if hasattr(self, 'deepl_pro_toggle'):
            self.deepl_pro_toggle.setChecked(defaults["deepl_use_pro"])
        if hasattr(self, 'amazon_translate_region_input'):
            self.amazon_translate_region_input.setText(
                defaults["amazon_translate_region"]
            )
        if hasattr(self, 'amazon_translate_access_key_input'):
            self.amazon_translate_access_key_input.setText(
                defaults["amazon_translate_access_key_id"]
            )
        if hasattr(self, 'amazon_translate_secret_key_input'):
            self.amazon_translate_secret_key_input.setText(
                defaults["amazon_translate_secret_access_key"]
            )
        if hasattr(self, 'amazon_translate_session_token_input'):
            self.amazon_translate_session_token_input.setText(
                defaults["amazon_translate_session_token"]
            )
        if hasattr(self, 'google_translate_api_key_input'):
            self.google_translate_api_key_input.setText(
                defaults["google_translate_api_key"]
            )
        if hasattr(self, 'azure_translate_api_key_input'):
            self.azure_translate_api_key_input.setText(
                defaults["azure_translate_api_key"]
            )
        if hasattr(self, 'azure_translate_region_input'):
            self.azure_translate_region_input.setText(
                defaults["azure_translate_region"]
            )
        if hasattr(self, 'azure_translate_endpoint_input'):
            self.azure_translate_endpoint_input.setText(
                defaults["azure_translate_endpoint"]
            )
        if hasattr(self, 'translation_target_combo'):
            index = self.translation_target_combo.findData(defaults["translation_target_lang"])
            if index >= 0:
                self.translation_target_combo.setCurrentIndex(index)
        if hasattr(self, 'translation_image_mode_combo'):
            index = self.translation_image_mode_combo.findData(
                defaults["translation_image_mode"])
            if index >= 0:
                self.translation_image_mode_combo.setCurrentIndex(index)
        if hasattr(self, 'split_sentences_toggle'):
            self.split_sentences_toggle.setChecked(defaults["translation_split_sentences"])
        if hasattr(self, 'preserve_formatting_toggle'):
            self.preserve_formatting_toggle.setChecked(defaults["translation_preserve_formatting"])

    def _reset_clipboard_page(self):
        defaults = self.config_manager.APP_DEFAULT_SETTINGS
        _reset_integration_controls(self)
        if hasattr(self, 'clipboard_enabled_toggle'):
            self.clipboard_enabled_toggle.setChecked(defaults["clipboard_enabled"])
        if hasattr(self, 'clipboard_auto_paste_toggle'):
            self.clipboard_auto_paste_toggle.setChecked(defaults["clipboard_auto_paste"])
        if hasattr(self, 'clipboard_history_limit_spin'):
            self.clipboard_history_limit_spin.setValue(defaults["clipboard_history_limit"])

    # ================================================================
    # 保存（accept）
    # ================================================================

    def accept(self):
        """保存所有设置"""
        # 全部动作热键必须先整体通过校验。这里发生在任何 set_* 之前，
        # 因而冲突值不会写入配置，窗口也不会关闭。
        if not validate_global_hotkey_edits(self, check_system=True):
            self.content_stack.setCurrentIndex(0)
            self._set_current_nav("shortcuts")
            show_warning_dialog(
                self,
                self.tr("Shortcut Conflict"),
                self.tr(
                    "Some global hotkeys are duplicated or unavailable. "
                    "Please fix them before applying."
                ),
            )
            return

        # 防止保存过程中（比如语言切换触发的窗口重建）触发未保存确认弹窗
        self._skip_unsaved_close_prompt = True

        # 0. 全局动作的快捷键与托盘开关（动作清单来自 core.actions）
        if hasattr(self, '_action_hotkey_rows'):
            self.config_manager.set_action_hotkeys(collect_action_hotkeys(self))
            self.config_manager.set_action_tray_flags(collect_action_tray_flags(self))

        # 0.7 标注样式（写回同一份工具设置，工具栏会跟着变）
        if hasattr(self, '_annotation_color_readers'):
            apply_annotation_settings(self)

        # 0.6 贴图设置
        if hasattr(self, 'pin_zoom_step_spin'):
            pin_settings = collect_pin_settings(self)
            if "zoom_step" in pin_settings:
                self.config_manager.set_pin_zoom_step(pin_settings["zoom_step"])
            if "opacity_step" in pin_settings:
                self.config_manager.set_pin_opacity_step(pin_settings["opacity_step"])
            if "default_opacity" in pin_settings:
                self.config_manager.set_pin_default_opacity(pin_settings["default_opacity"])
            if "shadow_enabled" in pin_settings:
                self.config_manager.set_pin_shadow_enabled(pin_settings["shadow_enabled"])
            if "auto_toolbar" in pin_settings:
                self.config_manager.set_pin_auto_toolbar(pin_settings["auto_toolbar"])
            if "new_position" in pin_settings:
                self.config_manager.set_pin_new_position(pin_settings["new_position"])
            if "order" in pin_settings:
                self.config_manager.set_pin_order(pin_settings["order"])
            if "close_confirm" in pin_settings:
                self.config_manager.set_pin_close_confirm(pin_settings["close_confirm"])
            if "mouse_actions" in pin_settings:
                self.config_manager.set_pin_mouse_actions(pin_settings["mouse_actions"])
            if "restore_on_startup" in pin_settings:
                self.config_manager.set_pin_restore_on_startup(
                    pin_settings["restore_on_startup"])
            if "history_limit" in pin_settings:
                self.config_manager.set_pin_history_limit(pin_settings["history_limit"])
            if "text_font_size" in pin_settings:
                self.config_manager.set_pin_text_font_size(pin_settings["text_font_size"])
            if "text_max_width" in pin_settings:
                self.config_manager.set_pin_text_max_width(pin_settings["text_max_width"])

        # 0.5 全局鼠标动作
        if hasattr(self, '_mouse_gesture_rows'):
            self.config_manager.set_mouse_gestures(collect_mouse_gestures(self))
        if hasattr(self, 'mouse_overlay_toggle'):
            self.config_manager.set_mouse_capture_overlay_enabled(
                self.mouse_overlay_toggle.isChecked()
            )
        if hasattr(self, '_mouse_ignored_names'):
            self.config_manager.set_mouse_ignored_apps(read_ignored_apps(self))

        # 1. 截图交互（双击确认 + 智能选区）
        if hasattr(self, 'double_click_copy_close_toggle'):
            self.config_manager.set_double_click_copy_close_enabled(
                self.double_click_copy_close_toggle.isChecked()
            )
        if hasattr(self, 'cross_tool_selection_toggle'):
            self.config_manager.set_cross_tool_selection_enabled(
                self.cross_tool_selection_toggle.isChecked()
            )
        if hasattr(self, 'text_always_on_top_toggle'):
            self.config_manager.set_text_always_on_top_enabled(
                self.text_always_on_top_toggle.isChecked()
            )
        if hasattr(self, 'ui_detection_combo'):
            self.config_manager.set_ui_detection(self.ui_detection_combo.currentData())
        if hasattr(self, 'ui_detection_margin_spin'):
            self.config_manager.set_ui_detection_margin(
                self.ui_detection_margin_spin.value()
            )
        # 截图外观（圆角 / 描边阴影）：与截图窗口里的浮层面板读写同一批键
        if hasattr(self, 'screenshot_rounded_toggle'):
            self.config_manager.set_app_setting(
                "screenshot_rounded_enabled", self.screenshot_rounded_toggle.isChecked())
        if hasattr(self, 'screenshot_radius_spin'):
            self.config_manager.set_app_setting(
                "screenshot_rounded_radius", self.screenshot_radius_spin.value())
        if hasattr(self, 'screenshot_border_toggle'):
            self.config_manager.set_app_setting(
                "screenshot_border_enabled", self.screenshot_border_toggle.isChecked())
        if hasattr(self, 'screenshot_border_mode_combo'):
            self.config_manager.set_app_setting(
                "screenshot_border_mode", self.screenshot_border_mode_combo.currentData())
        if hasattr(self, 'screenshot_border_size_spin'):
            self.config_manager.set_app_setting(
                "screenshot_border_size", self.screenshot_border_size_spin.value())
        if hasattr(self, '_screenshot_border_color'):
            self.config_manager.set_app_setting(
                "screenshot_border_color", self._screenshot_border_color.name())
        if hasattr(self, '_screenshot_shadow_color'):
            self.config_manager.set_app_setting(
                "screenshot_shadow_color", self._screenshot_shadow_color.name())
        if hasattr(self, 'screenshot_border_persist_toggle'):
            self.config_manager.set_app_setting(
                "screenshot_border_persist", self.screenshot_border_persist_toggle.isChecked())
        if hasattr(self, 'magnifier_zoom_spin'):
            self.config_manager.set_app_setting(
                "magnifier_zoom", self.magnifier_zoom_spin.value())

        # 2. 日志设置
        if hasattr(self, 'log_toggle'):
            log_enabled = self.log_toggle.isChecked()
            self.config_manager.set_log_enabled(log_enabled)

            if hasattr(self, 'log_level_combo'):
                log_level = self.log_level_combo.currentText()
                self.config_manager.set_log_level(log_level)
                from core.logger import get_logger, LogLevel
                logger = get_logger()
                level_map = {
                    "DEBUG": LogLevel.DEBUG, "INFO": LogLevel.INFO,
                    "WARNING": LogLevel.WARNING, "ERROR": LogLevel.ERROR,
                }
                if log_level in level_map:
                    logger.set_level(level_map[log_level])
                    logger.set_console_level(level_map[log_level])

            if hasattr(self, 'log_retention_combo'):
                retention_days = self.log_retention_combo.currentData()
                old_retention = self.config_manager.get_log_retention_days()
                self.config_manager.set_log_retention_days(retention_days)
                if retention_days > 0 and retention_days < old_retention:
                    from core.logger import cleanup_old_logs
                    log_dir = self.config_manager.get_log_dir()
                    cleanup_old_logs(log_dir, retention_days)

            if hasattr(self, 'path_lbl'):
                old_log_dir = self.config_manager.get_log_dir()
                new_log_dir = self.path_lbl.text()
                self.config_manager.set_log_dir(new_log_dir)
                from core.logger import get_logger
                logger = get_logger()
                logger.set_enabled(log_enabled)
                if new_log_dir != old_log_dir:
                    logger.set_log_dir(new_log_dir)
                    show_info_dialog(
                        self, self.tr("Log"),
                        self.tr("Log save location changed.") + "\n" + self.tr("*Changes will fully take effect after restart.")
                    )
                self._refresh_latest_log_label()

        # 3. 截图保存
        if hasattr(self, 'save_toggle'):
            self.config_manager.set_screenshot_save_enabled(self.save_toggle.isChecked())
        if hasattr(self, 'save_path_lbl'):
            self.config_manager.set_screenshot_save_path(self.save_path_lbl.text())
        if hasattr(self, 'screenshot_format_combo'):
            self.config_manager.set_screenshot_format(self.screenshot_format_combo.currentData())

        # 4. OCR
        if hasattr(self, 'ocr_enable_toggle'):
            self.config_manager.set_ocr_enabled(self.ocr_enable_toggle.isChecked())
        if hasattr(self, 'ocr_engine_combo'):
            self.config_manager.set_ocr_engine(self.ocr_engine_combo.currentData())
        if hasattr(self, 'ocr_grayscale_toggle'):
            self.config_manager.set_ocr_grayscale_enabled(self.ocr_grayscale_toggle.isChecked())
        if hasattr(self, 'ocr_upscale_toggle'):
            self.config_manager.set_ocr_upscale_enabled(self.ocr_upscale_toggle.isChecked())
        if hasattr(self, 'ocr_scale_spinbox'):
            self.config_manager.set_ocr_upscale_factor(self.ocr_scale_spinbox.value())

        # 5. 翻译
        if hasattr(self, 'translation_provider_combo'):
            self.config_manager.set_translation_provider(
                self.translation_provider_combo.currentData()
            )
        if hasattr(self, 'deepl_api_key_input'):
            self.config_manager.set_deepl_api_key(self.deepl_api_key_input.text().strip())
        if hasattr(self, 'deepl_pro_toggle'):
            self.config_manager.set_deepl_use_pro(self.deepl_pro_toggle.isChecked())
        if hasattr(self, 'amazon_translate_region_input'):
            self.config_manager.set_amazon_translate_region(
                self.amazon_translate_region_input.text().strip()
            )
        if hasattr(self, 'amazon_translate_access_key_input'):
            self.config_manager.set_amazon_translate_access_key_id(
                self.amazon_translate_access_key_input.text().strip()
            )
        if hasattr(self, 'amazon_translate_secret_key_input'):
            self.config_manager.set_amazon_translate_secret_access_key(
                self.amazon_translate_secret_key_input.text().strip()
            )
        if hasattr(self, 'amazon_translate_session_token_input'):
            self.config_manager.set_amazon_translate_session_token(
                self.amazon_translate_session_token_input.text().strip()
            )
        if hasattr(self, 'google_translate_api_key_input'):
            self.config_manager.set_google_translate_api_key(
                self.google_translate_api_key_input.text().strip()
            )
        if hasattr(self, 'azure_translate_api_key_input'):
            self.config_manager.set_azure_translate_api_key(
                self.azure_translate_api_key_input.text().strip()
            )
        if hasattr(self, 'azure_translate_region_input'):
            self.config_manager.set_azure_translate_region(
                self.azure_translate_region_input.text().strip()
            )
        if hasattr(self, 'azure_translate_endpoint_input'):
            self.config_manager.set_azure_translate_endpoint(
                self.azure_translate_endpoint_input.text().strip()
            )
        if hasattr(self, 'translation_target_combo'):
            self.config_manager.set_translation_target_lang(self.translation_target_combo.currentData())
        if hasattr(self, 'split_sentences_toggle'):
            self.config_manager.set_translation_split_sentences(self.split_sentences_toggle.isChecked())
        if hasattr(self, 'preserve_formatting_toggle'):
            self.config_manager.set_translation_preserve_formatting(self.preserve_formatting_toggle.isChecked())

        # 6. 杂项
        if hasattr(self, 'autostart_toggle'):
            from ..welcome.page6_finish import FinishPage as _FP
            _FP._set_autostart(self.autostart_toggle.isChecked())
        if hasattr(self, 'show_main_window_toggle'):
            self.config_manager.set_show_main_window(self.show_main_window_toggle.isChecked())
        if hasattr(self, 'pin_auto_toolbar_toggle'):
            self.config_manager.set_pin_auto_toolbar(self.pin_auto_toolbar_toggle.isChecked())
        if hasattr(self, 'magnifier_color_format_combo'):
            self.config_manager.set_app_setting(
                "magnifier_color_copy_format",
                self.magnifier_color_format_combo.currentData()
            )

        # 界面语言
        if hasattr(self, 'language_combo'):
            new_lang = self.language_combo.currentData()
            old_lang = self.config_manager.get_app_setting("language", "ja")
            self.config_manager.qsettings.setValue("app/language", new_lang)
            if new_lang != old_lang:
                from core.i18n import I18nManager
                I18nManager.load_language(new_lang)

        # 7. 剪贴板
        if hasattr(self, 'clipboard_enabled_toggle'):
            self.config_manager.set_clipboard_enabled(self.clipboard_enabled_toggle.isChecked())
        if hasattr(self, 'clipboard_history_limit_spin'):
            self.config_manager.set_clipboard_history_limit(self.clipboard_history_limit_spin.value())
        if hasattr(self, 'clipboard_hotkey_edit'):
            self.config_manager.set_clipboard_hotkey(self.clipboard_hotkey_edit.text().strip())
        if hasattr(self, 'clipboard_hotkey_edit_2'):
            self.config_manager.set_clipboard_hotkey_2(self.clipboard_hotkey_edit_2.text().strip())

        # 7.5 应用内快捷键
        if hasattr(self, '_inapp_edits'):
            save_inapp_shortcut_edits(self.config_manager, self._inapp_edits)
            # 通知钉图快捷键 handler 重新加载绑定
            try:
                from pin.pin_shortcut import PinShortcutController
                pin_ctrl = PinShortcutController._instance
                if pin_ctrl is not None:
                    pin_ctrl._edit_handler.reload_bindings()
                    pin_ctrl._normal_handler.reload_bindings()
            except Exception as e:
                log_exception(e, T("重载 Pin 快捷键绑定"))
        if hasattr(self, 'cursor_move_combo'):
            self.config_manager.set_inapp_cursor_move_mode(
                self.cursor_move_combo.currentData()
            )

        # 8. 长截图/开发者
        if hasattr(self, 'engine_combo'):
            self.config_manager.set_long_stitch_engine(self.engine_combo.currentData())
        if hasattr(self, 'cooldown_spinbox'):
            self.config_manager.set_scroll_cooldown(self.cooldown_spinbox.value())
        if hasattr(self, 'ignore_top_pixels_spinbox'):
            self.config_manager.set_long_stitch_ignore_top_pixels(self.ignore_top_pixels_spinbox.value())

        # 9. 预加载开关（重启生效）
        if hasattr(self, 'preload_screenshot_toggle'):
            self.config_manager.set_app_setting("preload_screenshot", self.preload_screenshot_toggle.isChecked())
        if hasattr(self, 'preload_toolbar_toggle'):
            self.config_manager.set_app_setting("preload_toolbar", self.preload_toolbar_toggle.isChecked())
        if hasattr(self, 'preload_ocr_toggle'):
            self.config_manager.set_app_setting("preload_ocr", self.preload_ocr_toggle.isChecked())
        if hasattr(self, 'preload_settings_toggle'):
            self.config_manager.set_app_setting("preload_settings", self.preload_settings_toggle.isChecked())
        if hasattr(self, 'preload_clipboard_toggle'):
            self.config_manager.set_app_setting("preload_clipboard", self.preload_clipboard_toggle.isChecked())

        # 10. 截图信息面板行为
        if hasattr(self, 'info_hide_on_drag_toggle'):
            self.config_manager.set_app_setting("screenshot_info_hide_on_drag", self.info_hide_on_drag_toggle.isChecked())

        # 11. 外观设置（主题模式、皮肤、自定义 logo、主题色、遮罩色）
        if hasattr(self, '_ui_theme_combo'):
            from core.ui_theme import get_ui_theme
            get_ui_theme().set_mode(self._ui_theme_combo.currentData())

        self._save_appearance_settings()
        self._save_integration_settings()

        from core.theme import get_theme
        theme = get_theme()
        if hasattr(self, '_appearance_theme_color'):
            theme.set_theme_color(self._appearance_theme_color)
        if hasattr(self, '_appearance_mask_color'):
            theme.set_mask_color(self._appearance_mask_color)

        log_info("すべての設定を保存しました", "Settings")
        self._settings_snapshot = self._snapshot_settings()
        self._skip_unsaved_close_prompt = True
        try:
            super().accept()
        finally:
            self._skip_unsaved_close_prompt = False

    # ================================================================
    # showEvent / refresh
    # ================================================================

    def _refresh_latest_log_label(self):
        refresh_latest_log_label(self)

    def _apply_dialog_stylesheet(self):
        """根据当前主题生成并应用对话框样式表"""
        from core.ui_theme import get_ui_theme
        tokens = get_ui_theme().tokens
        self.setStyleSheet(f"""
            #SettingsDialog {{
                background: transparent;
                border: none;
            }}
            QWidget#SettingsLeftPanel {{
                background-color: {theme_sidebar_color()};
                border: none;
                border-radius: 8px;
            }}
            QWidget#SettingsRightArea {{
                background: {theme_surface_color()};
                border: none;
                border-radius: 8px;
            }}
            QLabel {{
                color: {tokens.text};
            }}
            QStackedWidget {{
                background: transparent;
            }}
        """ + scrollbar_qss(self))

    def _on_ui_theme_changed(self, _tokens):
        """Rebuild window-local styles after an OS or user theme change."""
        self._apply_dialog_stylesheet()
        refresh_theme_widget_styles(self)
        self._apply_footer_styles()
        input_style = self._get_input_style()
        for attr in (
            "hotkey_input", "hotkey_input_2",
            "clipboard_hotkey_edit", "clipboard_hotkey_edit_2",
            "translation_hotkey_edit", "translation_hotkey_edit_2",
            "deepl_api_key_input", "amazon_translate_region_input",
            "amazon_translate_access_key_input",
            "amazon_translate_secret_key_input",
            "amazon_translate_session_token_input",
            "google_translate_api_key_input",
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setStyleSheet(input_style)
        for widget in getattr(self, "_inapp_edits", {}).values():
            widget.setStyleSheet(input_style)
        self.content_title.setStyleSheet(
            theme_text_style(
                20, bold=True,
                extra="padding: 0 6px 2px 6px;"
            )
        )
        self.update()

    @safe_event
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'titleBar') and self.titleBar:
            self.titleBar.resize(self.width(), self.titleBar.height())

    @safe_event
    def showEvent(self, event):
        self._skip_unsaved_close_prompt = False
        self._apply_dialog_stylesheet()
        self.refresh_settings()
        self._settings_snapshot = self._snapshot_settings()
        super().showEvent(event)
        self._apply_taskbar_icon()

    @safe_event
    def hideEvent(self, event):
        # 这里在隐藏时强制复位所有标题栏按钮状态。
        if getattr(self, 'titleBar', None):
            try:
                from qframelesswindow.titlebar.title_bar_buttons import (
                    TitleBarButton, TitleBarButtonState,
                )
                for btn in self.titleBar.findChildren(TitleBarButton):
                    btn.setState(TitleBarButtonState.NORMAL)
            except Exception as e:
                log_exception(e, T("重置标题栏按钮状态"))
        super().hideEvent(event)

    # ================================================================
    # 未保存变更检测
    # ================================================================

    def _snapshot_settings(self):
        """捕获所有可编辑控件的当前值，返回 dict"""
        snap = {}
        # 文本类
        for attr in ('save_path_lbl', 'path_lbl',
                      'deepl_api_key_input', 'amazon_translate_region_input',
                      'amazon_translate_access_key_input',
                      'amazon_translate_secret_key_input',
                      'amazon_translate_session_token_input',
                      'google_translate_api_key_input'):
            w = getattr(self, attr, None)
            if w is not None:
                snap[attr] = w.text()
        # 开关类
        for attr in ('double_click_copy_close_toggle',
                      'cross_tool_selection_toggle',
                      'text_always_on_top_toggle',
                      'save_toggle', 'ocr_enable_toggle',
                      'ocr_grayscale_toggle', 'ocr_upscale_toggle',
                      'deepl_pro_toggle', 'split_sentences_toggle',
                      'preserve_formatting_toggle', 'log_toggle',
                      'clipboard_enabled_toggle', 'clipboard_auto_paste_toggle',
                      'autostart_toggle', 'show_main_window_toggle',
                      'pin_auto_toolbar_toggle', 'info_hide_on_drag_toggle',
                      'preload_screenshot_toggle',
                      'preload_toolbar_toggle', 'preload_ocr_toggle',
                      'preload_settings_toggle', 'preload_clipboard_toggle',
                      'pin_shadow_toggle', 'pin_close_confirm_toggle',
                      'pin_restore_toggle', 'screenshot_rounded_toggle',
                      'screenshot_border_toggle', 'screenshot_border_persist_toggle'):
            w = getattr(self, attr, None)
            if w is not None:
                snap[attr] = w.isChecked()
        # 下拉框类
        for attr in ('screenshot_format_combo', 'ocr_engine_combo',
                      'translation_provider_combo', 'translation_target_combo',
                      'translation_image_mode_combo',
                      'log_level_combo', 'ui_detection_combo',
                      'language_combo', 'engine_combo', 'cursor_move_combo',
                      'magnifier_color_format_combo', 'log_retention_combo',
                      'pin_new_position_combo', 'pin_order_combo',
                      'screenshot_border_mode_combo',
                      '_ui_theme_combo'):
            w = getattr(self, attr, None)
            if w is not None:
                snap[attr] = w.currentIndex()
        # 数值类
        for attr in ('clipboard_history_limit_spin', 'ui_detection_margin_spin',
                      'screenshot_radius_spin', 'screenshot_border_size_spin',
                      'cooldown_spinbox', 'ignore_top_pixels_spinbox',
                      'ocr_scale_spinbox', 'pin_zoom_step_spin',
                      'pin_opacity_step_spin', 'pin_default_opacity_spin',
                      'pin_history_limit_spin', 'pin_text_font_size_spin',
                      'pin_text_max_width_spin'):
            w = getattr(self, attr, None)
            if w is not None:
                snap[attr] = w.value()
        # 贴图窗口的手势绑定（每个手势一个下拉）
        if hasattr(self, 'pin_gesture_combos'):
            snap['pin_gesture_actions'] = {
                gesture: combo.currentData()
                for gesture, combo in self.pin_gesture_combos.items()
            }
        # 全局动作行（动作、主/备热键、托盘开关都算未保存变更）
        if hasattr(self, '_action_hotkey_rows'):
            snap['action_hotkeys'] = [
                (row['action'].currentData(), row['primary'].text(),
                 row['secondary'].text(), row['tray'].isChecked())
                for row in self._action_hotkey_rows
            ]
        # 全局鼠标动作行
        if hasattr(self, '_mouse_gesture_rows'):
            snap['mouse_gestures'] = [
                (row['action'].currentData(), row['modifier'].currentData(),
                 row['gesture'].currentData())
                for row in self._mouse_gesture_rows
            ]
        # 应用内快捷键
        if hasattr(self, '_inapp_edits'):
            for cfg_key, edit in self._inapp_edits.items():
                snap[f'inapp_{cfg_key}'] = edit.text()
        # 颜色
        if hasattr(self, '_appearance_theme_color'):
            snap['theme_color'] = self._appearance_theme_color.name()
        if hasattr(self, '_appearance_mask_color'):
            snap['mask_color'] = self._appearance_mask_color.name()
        # 皮肤覆盖与自定义 logo
        if hasattr(self, '_skin_overrides'):
            snap['skin'] = (
                self._skin_overrides.accent,
                self._skin_overrides.window,
                self._skin_overrides.text,
            )
        if hasattr(self, '_custom_logo_path'):
            snap['custom_logo_path'] = self._custom_logo_path
        # 系统集成 / 文字识别 / 公式识别这几组新控件
        _snapshot_integration_controls(self, snap)
        return snap

    def _has_unsaved_changes(self):
        """比较当前状态和快照，判断是否有未保存的变更"""
        if not hasattr(self, '_settings_snapshot'):
            return False
        current = self._snapshot_settings()
        return current != self._settings_snapshot

    def _confirm_close_with_unsaved_changes(self) -> str:
        """显示未保存变更确认框，返回 save/discard/cancel"""
        from ui.dialogs import show_custom_confirm_dialog
        
        buttons_config = [
            {"id": "save", "text": self.tr("Save"), "role": QDialogButtonBox.ButtonRole.AcceptRole},
            {"id": "discard", "text": self.tr("Don't Save"), "role": QDialogButtonBox.ButtonRole.DestructiveRole},
            {"id": "cancel", "text": self.tr("Cancel"), "role": QDialogButtonBox.ButtonRole.RejectRole, "default": True},
        ]
        
        return show_custom_confirm_dialog(
            self,
            self.tr("Unsaved Changes"),
            self.tr("You have unsaved changes. Do you want to save before closing?"),
            buttons_config
        )

    @safe_event
    def closeEvent(self, event):
        """关闭窗口前检查未保存变更"""
        if self._skip_unsaved_close_prompt:
            event.accept()
            return

        if self._has_unsaved_changes():
            action = self._confirm_close_with_unsaved_changes()
            if action == "save":
                self.accept()
                event.accept()
            elif action == "discard":
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def _apply_taskbar_icon(self):
        """把任务栏/Dock 图标设成用户选的自定义 logo（没设时保持原有行为）。

        平台差异全在平台层：Windows 的任务栏那一份要单独设（Qt 的 setWindowIcon 管不到），
        macOS 的 Dock 那一枚走 NSApplication；两者都不可用时平台函数自己返回 False。
        macOS 只在用户设了自定义 logo 时才动 Dock——否则会把系统给的品牌图标换成
        菜单栏小图标，那是退化。
        """
        from core.resource_manager import ResourceManager

        custom = ResourceManager.custom_logo_path()
        icon_path = custom or ResourceManager.get_resource_path("svg/托盘.svg")
        window_ops.set_taskbar_icon(self, icon_path)
        if custom:
            from main_app import apply_configured_app_icon

            apply_configured_app_icon()




    def _save_integration_settings(self) -> None:
        """保存「系统集成 / 文字识别 / 公式识别」这几组 2026-09-19 新增的设置。

        它们分散在三个页面上（其它页、截图页、剪贴板页），但都属于「保存后要立刻生效」
        的一类，所以在这里一次性落盘 + 应用，免得每加一项就在保存流程里插一段。
        """
        config = self.config_manager
        from core.net import apply_proxy_from_settings

        # 桌面工具栏 / 托盘单击动作
        if hasattr(self, "desktop_toolbar_combo"):
            config.set_desktop_toolbar_mode(self.desktop_toolbar_combo.currentData())
        if hasattr(self, "tray_click_combo"):
            config.set_tray_click_action(self.tray_click_combo.currentData())
        if hasattr(self, "tray_scroll_combo"):
            config.set_tray_scroll_action(self.tray_scroll_combo.currentData() or "")
        if hasattr(self, "pdf_page_combo"):
            config.set_pdf_page_size(self.pdf_page_combo.currentData())

        # 网络代理（进程级，不必等重启）
        if hasattr(self, "proxy_mode_combo"):
            config.set_proxy_config(
                self.proxy_mode_combo.currentData(),
                self.proxy_host_input.text().strip(),
                int(self.proxy_port_spin.value()),
            )
            apply_proxy_from_settings()

        # 更新源
        if hasattr(self, "update_source_input"):
            config.set_update_source_url(self.update_source_input.text().strip())

        # 剪贴板：复制图像为文件 / 忽略自己回写
        if hasattr(self, "clipboard_image_mode_combo"):
            config.set_clipboard_image_copy_mode(
                self.clipboard_image_mode_combo.currentData()
            )
        if hasattr(self, "clipboard_ignore_own_toggle"):
            config.set_clipboard_ignore_own_copy(
                self.clipboard_ignore_own_toggle.isChecked()
            )

        # 文字识别：布局 / 标点 / 语言 / 对话框时机
        if hasattr(self, "ocr_layout_combo"):
            config.set_ocr_text_layout(self.ocr_layout_combo.currentData())
        if hasattr(self, "ocr_punctuation_combo"):
            config.set_ocr_punctuation(self.ocr_punctuation_combo.currentData())
        language_changed = False
        if hasattr(self, "ocr_language_combo"):
            new_language = self.ocr_language_combo.currentData()
            language_changed = new_language != config.get_ocr_language()
            config.set_ocr_language(new_language)
        if hasattr(self, "ocr_tier_combo"):
            config.set_ocr_model_tier(self.ocr_tier_combo.currentData() or "")
        if hasattr(self, "translation_image_mode_combo"):
            config.set_translation_image_mode(
                self.translation_image_mode_combo.currentData())
        if hasattr(self, "ocr_vision_target_combo"):
            config.set_ocr_vision_target(self.ocr_vision_target_combo.currentData())
        if hasattr(self, "ocr_vision_task_combo"):
            config.set_ocr_vision_task(self.ocr_vision_task_combo.currentData())
        if hasattr(self, "ocr_confidence_spin"):
            config.set_ocr_low_confidence_threshold(self.ocr_confidence_spin.value())
        if hasattr(self, "ocr_vision_combo"):
            config.set_app_setting("ocr_vision_model",
                                   self.ocr_vision_combo.currentData() or "")
        if hasattr(self, "ocr_dialog_checks"):
            config.set_ocr_dialog_triggers(
                [trigger for trigger, check in self.ocr_dialog_checks.items()
                 if check.isChecked()]
            )

        # 公式识别：引擎与外部服务
        if hasattr(self, "formula_engine_combo"):
            config.set_formula_engine(self.formula_engine_combo.currentData())
        if hasattr(self, "formula_url_input"):
            config.set_formula_service_config(
                self.formula_url_input.text().strip(),
                self.formula_key_input.text().strip(),
            )

        # 视频录制：这些键一次录制只读一次，落盘即可，没有需要立刻应用的活对象
        if hasattr(self, "video_container_combo"):
            config.set_video_container(self.video_container_combo.currentData())
        if hasattr(self, "video_codec_combo"):
            config.set_video_codec(self.video_codec_combo.currentData())
        if hasattr(self, "video_quality_combo"):
            config.set_video_quality(self.video_quality_combo.currentData())
        if hasattr(self, "video_fps_combo"):
            config.set_video_fps(int(self.video_fps_combo.currentData()))
        if hasattr(self, "video_bitrate_spin"):
            config.set_video_bitrate_mbps(int(self.video_bitrate_spin.value()))
        if hasattr(self, "video_audio_combo"):
            config.set_video_audio(self.video_audio_combo.currentData())
        if hasattr(self, "video_audio_device_combo"):
            config.set_video_audio_device(self.video_audio_device_combo.currentData() or "")
        if hasattr(self, "video_duration_spin"):
            config.set_video_max_duration_s(int(self.video_duration_spin.value()))
        if hasattr(self, "video_save_path_input"):
            config.set_video_save_path(self.video_save_path_input.text().strip())

        # 截图历史：开关与三个上限（保存后立刻按新策略收拾一次，用户马上能看到效果）
        if hasattr(self, "history_enabled_toggle"):
            config.set_history_enabled(self.history_enabled_toggle.isChecked())
        if hasattr(self, "history_retention_combo"):
            config.set_history_retention_days(int(self.history_retention_combo.currentData()))
        if hasattr(self, "history_entries_spin"):
            config.set_history_max_entries(int(self.history_entries_spin.value()))
        if hasattr(self, "history_disk_spin"):
            config.set_history_max_disk_mb(int(self.history_disk_spin.value()))
        if hasattr(self, "history_enabled_toggle"):
            try:
                from history import apply_configured_retention

                apply_configured_retention(config)
            except Exception as e:
                log_exception(e, T("按新策略清理截图历史"))

        app = None
        try:
            from main_app import main_app_instance

            app = main_app_instance()
        except Exception as e:
            log_exception(e, T("读取主程序实例"))
        if app is not None:
            app.apply_desktop_toolbar_mode()

        if language_changed:
            # 识别语言是引擎初始化参数，改了要重新初始化一次（否则下次识别还用旧语言）
            try:
                from ocr import initialize_ocr, resolve_ocr_language

                initialize_ocr(resolve_ocr_language(
                    config.get_ocr_language(),
                    config.get_app_setting("language", ""),
                ))
            except Exception as e:
                log_exception(e, T("按新语言重新初始化 OCR"))

    def _save_appearance_settings(self):
        """把皮肤与自定义 logo 写进配置并立即生效（保存按钮走这里）。"""
        from core.resource_manager import ResourceManager
        from core.ui_theme import get_ui_theme

        if hasattr(self, '_skin_overrides'):
            get_ui_theme().set_skin(self._skin_overrides)
            # 同时自己落一份配置：皮肤是「设置页保存」的产物，不该依赖主题管理器当初
            # 是不是带着配置管理器初始化的
            for key, value in self._skin_overrides.as_settings().items():
                self.config_manager.set_app_setting(key, value)

        if not hasattr(self, '_custom_logo_path'):
            return

        self.config_manager.set_app_setting("custom_logo_path", self._custom_logo_path)
        ResourceManager.refresh_app_icons()
        self._apply_taskbar_icon()

        try:
            from main_app import main_app_instance

            app = main_app_instance()
        except Exception as e:                    # 主程序还没起来（例如无头测试）
            log_exception(e, T("刷新托盘图标"))
            app = None
        if app is not None:
            app.refresh_app_icon()

    def refresh_settings(self):
        """从配置管理器重新读取所有设置并更新界面"""
        if hasattr(self, '_action_hotkey_rows'):
            refresh_action_rows(self)
        if hasattr(self, 'pin_zoom_step_spin'):
            refresh_pin_page(self)
        if hasattr(self, '_annotation_color_readers'):
            refresh_annotation_page(self)
        if hasattr(self, 'screenshot_rounded_toggle'):
            refresh_capture_appearance(self)

        # 应用内快捷键
        if hasattr(self, '_inapp_edits'):
            from core.shortcut_manager import is_reserved_inapp_shortcut
            for cfg_key, edit in self._inapp_edits.items():
                val = self.config_manager.get_inapp_shortcut(cfg_key)
                edit.setText("" if is_reserved_inapp_shortcut(val) else val)
        if hasattr(self, 'cursor_move_combo'):
            mode = self.config_manager.get_inapp_cursor_move_mode()
            idx = self.cursor_move_combo.findData(mode)
            if idx >= 0:
                self.cursor_move_combo.setCurrentIndex(idx)

        if hasattr(self, 'engine_combo'):
            engine = self.config_manager.get_long_stitch_engine()
            index = self.engine_combo.findData(engine)
            if index >= 0:
                self.engine_combo.setCurrentIndex(index)
        if hasattr(self, 'cooldown_spinbox'):
            self.cooldown_spinbox.setValue(self.config_manager.get_scroll_cooldown())
        if hasattr(self, 'ignore_top_pixels_spinbox'):
            self.ignore_top_pixels_spinbox.setValue(self.config_manager.get_long_stitch_ignore_top_pixels())

        if hasattr(self, 'ui_detection_combo'):
            index = self.ui_detection_combo.findData(self.config_manager.get_ui_detection())
            self.ui_detection_combo.setCurrentIndex(max(0, index))
        if hasattr(self, 'ui_detection_margin_spin'):
            self.ui_detection_margin_spin.setValue(
                self.config_manager.get_ui_detection_margin()
            )

        if hasattr(self, 'double_click_copy_close_toggle'):
            self.double_click_copy_close_toggle.setChecked(
                self.config_manager.get_double_click_copy_close_enabled()
            )

        if hasattr(self, 'cross_tool_selection_toggle'):
            self.cross_tool_selection_toggle.setChecked(
                self.config_manager.get_cross_tool_selection_enabled()
            )

        if hasattr(self, 'text_always_on_top_toggle'):
            self.text_always_on_top_toggle.setChecked(
                self.config_manager.get_text_always_on_top_enabled()
            )

        if hasattr(self, 'save_toggle'):
            self.save_toggle.setChecked(self.config_manager.get_screenshot_save_enabled())
        if hasattr(self, 'save_path_lbl'):
            self.save_path_lbl.setText(self.config_manager.get_screenshot_save_path())
        if hasattr(self, 'screenshot_format_combo'):
            idx = {"PNG": 0, "JPG": 1, "BMP": 2, "WEBP": 3, "PDF": 4}.get(
                self.config_manager.get_screenshot_format().upper(), 0)
            self.screenshot_format_combo.setCurrentIndex(idx)
        if hasattr(self, 'ocr_enable_toggle'):
            self.ocr_enable_toggle.setChecked(self.config_manager.get_ocr_enabled())
        if hasattr(self, 'ocr_engine_combo'):
            index = self.ocr_engine_combo.findData(self.config_manager.get_ocr_engine())
            if index >= 0:
                self.ocr_engine_combo.setCurrentIndex(index)
        if hasattr(self, 'ocr_grayscale_toggle'):
            self.ocr_grayscale_toggle.setChecked(self.config_manager.get_ocr_grayscale_enabled())
        if hasattr(self, 'ocr_upscale_toggle'):
            self.ocr_upscale_toggle.setChecked(self.config_manager.get_ocr_upscale_enabled())
        if hasattr(self, 'ocr_scale_spinbox'):
            self.ocr_scale_spinbox.setValue(self.config_manager.get_ocr_upscale_factor())

        if hasattr(self, 'translation_provider_combo'):
            index = self.translation_provider_combo.findData(
                self.config_manager.get_translation_provider()
            )
            if index >= 0:
                self.translation_provider_combo.setCurrentIndex(index)
        if hasattr(self, 'deepl_api_key_input'):
            self.deepl_api_key_input.setText(self.config_manager.get_deepl_api_key())
        if hasattr(self, 'deepl_pro_toggle'):
            self.deepl_pro_toggle.setChecked(self.config_manager.get_deepl_use_pro())
        if hasattr(self, 'amazon_translate_region_input'):
            self.amazon_translate_region_input.setText(
                self.config_manager.get_amazon_translate_region()
            )
        if hasattr(self, 'amazon_translate_access_key_input'):
            self.amazon_translate_access_key_input.setText(
                self.config_manager.get_amazon_translate_access_key_id()
            )
        if hasattr(self, 'amazon_translate_secret_key_input'):
            self.amazon_translate_secret_key_input.setText(
                self.config_manager.get_amazon_translate_secret_access_key()
            )
        if hasattr(self, 'amazon_translate_session_token_input'):
            self.amazon_translate_session_token_input.setText(
                self.config_manager.get_amazon_translate_session_token()
            )
        if hasattr(self, 'google_translate_api_key_input'):
            self.google_translate_api_key_input.setText(
                self.config_manager.get_google_translate_api_key()
            )
        if hasattr(self, 'translation_target_combo'):
            index = self.translation_target_combo.findData(self.config_manager.get_app_setting("translation_target_lang", ""))
            if index >= 0:
                self.translation_target_combo.setCurrentIndex(index)
        if hasattr(self, 'translation_image_mode_combo'):
            index = self.translation_image_mode_combo.findData(
                self.config_manager.get_translation_image_mode())
            if index >= 0:
                self.translation_image_mode_combo.setCurrentIndex(index)
        if hasattr(self, 'split_sentences_toggle'):
            self.split_sentences_toggle.setChecked(self.config_manager.get_translation_split_sentences())
        if hasattr(self, 'preserve_formatting_toggle'):
            self.preserve_formatting_toggle.setChecked(self.config_manager.get_translation_preserve_formatting())

        if hasattr(self, 'log_toggle'):
            self.log_toggle.setChecked(self.config_manager.get_log_enabled())
        if hasattr(self, 'log_level_combo'):
            self.log_level_combo.setCurrentText(self.config_manager.get_log_level())
        if hasattr(self, 'log_retention_combo'):
            index = self.log_retention_combo.findData(self.config_manager.get_log_retention_days())
            if index < 0:
                index = self.log_retention_combo.findData(7)
            if index >= 0:
                self.log_retention_combo.setCurrentIndex(index)
        if hasattr(self, 'path_lbl'):
            self.path_lbl.setText(self.config_manager.get_log_dir())

        if hasattr(self, 'clipboard_enabled_toggle'):
            self.clipboard_enabled_toggle.setChecked(self.config_manager.get_clipboard_enabled())
        if hasattr(self, 'clipboard_auto_paste_toggle'):
            self.clipboard_auto_paste_toggle.setChecked(self.config_manager.get_clipboard_auto_paste())
        if hasattr(self, 'clipboard_history_limit_spin'):
            self.clipboard_history_limit_spin.setValue(self.config_manager.get_clipboard_history_limit())

        if hasattr(self, 'autostart_toggle'):
            from ..welcome.page6_finish import FinishPage as _FP
            self.autostart_toggle.setChecked(_FP._get_autostart())
        if hasattr(self, 'show_main_window_toggle'):
            self.show_main_window_toggle.setChecked(self.config_manager.get_show_main_window())
        if hasattr(self, 'pin_auto_toolbar_toggle'):
            self.pin_auto_toolbar_toggle.setChecked(self.config_manager.get_pin_auto_toolbar())
        if hasattr(self, 'language_combo'):
            index = self.language_combo.findData(self.config_manager.get_app_setting("language", "ja"))
            if index >= 0:
                self.language_combo.setCurrentIndex(index)

        # 预加载开关刷新（默认值统一来自 APP_DEFAULT_SETTINGS）
        if hasattr(self, 'preload_screenshot_toggle'):
            self.preload_screenshot_toggle.setChecked(self.config_manager.get_app_setting("preload_screenshot"))
        if hasattr(self, 'preload_toolbar_toggle'):
            self.preload_toolbar_toggle.setChecked(self.config_manager.get_app_setting("preload_toolbar"))
        if hasattr(self, 'preload_ocr_toggle'):
            self.preload_ocr_toggle.setChecked(self.config_manager.get_app_setting("preload_ocr"))
        if hasattr(self, 'preload_settings_toggle'):
            self.preload_settings_toggle.setChecked(self.config_manager.get_app_setting("preload_settings"))
        if hasattr(self, 'preload_clipboard_toggle'):
            self.preload_clipboard_toggle.setChecked(self.config_manager.get_app_setting("preload_clipboard"))

        # 截图信息面板行为
        if hasattr(self, 'info_hide_on_drag_toggle'):
            self.info_hide_on_drag_toggle.setChecked(
                self.config_manager.get_app_setting("screenshot_info_hide_on_drag")
            )

        # 外观设置
        if hasattr(self, '_ui_theme_combo'):
            from core.ui_theme import get_ui_theme
            index = self._ui_theme_combo.findData(get_ui_theme().mode.value)
            if index >= 0:
                self._ui_theme_combo.setCurrentIndex(index)

        if hasattr(self, '_theme_color_btn'):
            from core.theme import get_theme

            theme = get_theme()
            self._appearance_theme_color = QColor(theme.theme_color)
            mc = theme.mask_color
            self._appearance_mask_color = QColor(mc.red(), mc.green(), mc.blue())
            _update_color_btn(self._theme_color_btn, self._appearance_theme_color)
            _update_color_btn(self._mask_color_btn, self._appearance_mask_color)

        _refresh_integration_controls(self)

        # 皮肤与自定义 logo（外部改过配置时刷新到界面上）
        if hasattr(self, '_skin_buttons'):
            from core.ui_theme import get_ui_theme

            self._skin_overrides = get_ui_theme().skin
            _refresh_skin_buttons(self)
        if hasattr(self, '_logo_preview'):
            self._custom_logo_path = (
                self.config_manager.get_app_setting("custom_logo_path", "") or ""
            )
            _refresh_logo_preview(self)

        # 剪切板主题色同步（在别处改了主题色后打开设置，确保显示最新值）
        if hasattr(self, '_clip_theme_btn'):
            from settings import get_tool_settings_manager
            from .page_appearance import _apply_clip_theme_btn_style
            self._clip_theme_name = get_tool_settings_manager().get_clipboard_theme()
            _apply_clip_theme_btn_style(self._clip_theme_btn, self._clip_theme_name)


def _reset_integration_controls(dialog) -> None:
        """把「系统集成 / 文字识别 / 公式识别」这几组控件恢复成默认值。

        只改界面上的值，真正落盘仍走保存按钮——和这个对话框里其它重置换算方式一致。
        """
        from .page_misc import _select_combo

        defaults = dialog.config_manager.APP_DEFAULT_SETTINGS

        if hasattr(dialog, "desktop_toolbar_combo"):
            _select_combo(dialog.desktop_toolbar_combo, defaults["desktop_toolbar_mode"])
        if hasattr(dialog, "tray_click_combo"):
            _select_combo(dialog.tray_click_combo, defaults["tray_click_action"])
        if hasattr(dialog, "tray_scroll_combo"):
            _select_combo(dialog.tray_scroll_combo, defaults["tray_scroll_action"])
        if hasattr(dialog, "pdf_page_combo"):
            _select_combo(dialog.pdf_page_combo, defaults["pdf_page_size"])
        if hasattr(dialog, "proxy_mode_combo"):
            _select_combo(dialog.proxy_mode_combo, defaults["proxy_mode"])
            dialog.proxy_host_input.setText(defaults["proxy_host"])
            dialog.proxy_port_spin.setValue(int(defaults["proxy_port"]))
        if hasattr(dialog, "update_source_input"):
            dialog.update_source_input.setText(defaults["update_source_url"])
        if hasattr(dialog, "clipboard_image_mode_combo"):
            _select_combo(dialog.clipboard_image_mode_combo,
                          defaults["clipboard_image_copy_mode"])
        if hasattr(dialog, "clipboard_ignore_own_toggle"):
            dialog.clipboard_ignore_own_toggle.setChecked(
                defaults["clipboard_ignore_own_copy"]
            )
        if hasattr(dialog, "ocr_layout_combo"):
            _select_combo(dialog.ocr_layout_combo, defaults["ocr_text_layout"])
        if hasattr(dialog, "ocr_punctuation_combo"):
            _select_combo(dialog.ocr_punctuation_combo, defaults["ocr_punctuation"])
        if hasattr(dialog, "ocr_language_combo"):
            _select_combo(dialog.ocr_language_combo, defaults["ocr_language"])
        if hasattr(dialog, "ocr_tier_combo"):
            _select_combo(dialog.ocr_tier_combo, defaults["ocr_model_tier"])
        if hasattr(dialog, "ocr_vision_target_combo"):
            _select_combo(dialog.ocr_vision_target_combo, defaults["ocr_vision_target"])
        if hasattr(dialog, "ocr_vision_task_combo"):
            _select_combo(dialog.ocr_vision_task_combo, defaults["ocr_vision_task"])
        if hasattr(dialog, "ocr_confidence_spin"):
            dialog.ocr_confidence_spin.setValue(defaults["ocr_low_confidence_threshold"])
        if hasattr(dialog, "ocr_dialog_checks"):
            enabled = set(defaults["ocr_dialog_triggers"])
            for trigger, check in dialog.ocr_dialog_checks.items():
                check.setChecked(trigger in enabled)
        if hasattr(dialog, "formula_engine_combo"):
            _select_combo(dialog.formula_engine_combo, defaults["formula_engine"])
        if hasattr(dialog, "formula_url_input"):
            dialog.formula_url_input.setText(defaults["formula_service_url"])
            dialog.formula_key_input.setText(defaults["formula_service_api_key"])
        if hasattr(dialog, "video_container_combo"):
            _select_combo(dialog.video_container_combo, defaults["video_container"])
        if hasattr(dialog, "video_codec_combo"):
            _select_combo(dialog.video_codec_combo, defaults["video_codec"])
        if hasattr(dialog, "video_quality_combo"):
            _select_combo(dialog.video_quality_combo, defaults["video_quality"])
        if hasattr(dialog, "video_fps_combo"):
            _select_combo(dialog.video_fps_combo, defaults["video_fps"])
        if hasattr(dialog, "video_bitrate_spin"):
            dialog.video_bitrate_spin.setValue(int(defaults["video_bitrate_mbps"]))
        if hasattr(dialog, "video_audio_combo"):
            _select_combo(dialog.video_audio_combo, defaults["video_audio"])
        if hasattr(dialog, "video_audio_device_combo"):
            _select_combo(dialog.video_audio_device_combo, defaults["video_audio_device"])
        if hasattr(dialog, "video_duration_spin"):
            dialog.video_duration_spin.setValue(int(defaults["video_max_duration_s"]))
        if hasattr(dialog, "video_save_path_input"):
            dialog.video_save_path_input.setText(defaults["video_save_path"])
        if hasattr(dialog, "history_enabled_toggle"):
            dialog.history_enabled_toggle.setChecked(defaults["history_enabled"])
        if hasattr(dialog, "history_retention_combo"):
            _select_combo(dialog.history_retention_combo, defaults["history_retention_days"])
        if hasattr(dialog, "history_entries_spin"):
            dialog.history_entries_spin.setValue(int(defaults["history_max_entries"]))
        if hasattr(dialog, "history_disk_spin"):
            dialog.history_disk_spin.setValue(int(defaults["history_max_disk_mb"]))

def _refresh_integration_controls(dialog) -> None:
        """外部改过配置时（例如另一处保存过）把新控件刷新成配置里的值。"""
        from .page_misc import _select_combo

        config = dialog.config_manager

        if hasattr(dialog, "desktop_toolbar_combo"):
            _select_combo(dialog.desktop_toolbar_combo, config.get_desktop_toolbar_mode())
        if hasattr(dialog, "tray_click_combo"):
            _select_combo(dialog.tray_click_combo, config.get_tray_click_action())
        if hasattr(dialog, "tray_scroll_combo"):
            _select_combo(dialog.tray_scroll_combo, config.get_tray_scroll_action())
        if hasattr(dialog, "pdf_page_combo"):
            _select_combo(dialog.pdf_page_combo, config.get_pdf_page_size())
        if hasattr(dialog, "proxy_mode_combo"):
            proxy = config.get_proxy_config()
            _select_combo(dialog.proxy_mode_combo, proxy["mode"])
            dialog.proxy_host_input.setText(proxy["host"])
            dialog.proxy_port_spin.setValue(int(proxy["port"]))
        if hasattr(dialog, "update_source_input"):
            dialog.update_source_input.setText(
                config.get_app_setting("update_source_url", "") or ""
            )
        if hasattr(dialog, "clipboard_image_mode_combo"):
            _select_combo(dialog.clipboard_image_mode_combo,
                          config.get_clipboard_image_copy_mode())
        if hasattr(dialog, "clipboard_ignore_own_toggle"):
            dialog.clipboard_ignore_own_toggle.setChecked(
                config.get_clipboard_ignore_own_copy()
            )
        if hasattr(dialog, "ocr_layout_combo"):
            _select_combo(dialog.ocr_layout_combo, config.get_ocr_text_layout())
        if hasattr(dialog, "ocr_punctuation_combo"):
            _select_combo(dialog.ocr_punctuation_combo, config.get_ocr_punctuation())
        if hasattr(dialog, "ocr_language_combo"):
            _select_combo(dialog.ocr_language_combo, config.get_ocr_language())
        if hasattr(dialog, "ocr_tier_combo"):
            _select_combo(dialog.ocr_tier_combo, config.get_ocr_model_tier())
        if hasattr(dialog, "ocr_vision_target_combo"):
            _select_combo(dialog.ocr_vision_target_combo, config.get_ocr_vision_target())
        if hasattr(dialog, "ocr_vision_task_combo"):
            _select_combo(dialog.ocr_vision_task_combo, config.get_ocr_vision_task())
        if hasattr(dialog, "ocr_confidence_spin"):
            dialog.ocr_confidence_spin.setValue(config.get_ocr_low_confidence_threshold())
        if hasattr(dialog, "ocr_dialog_checks"):
            enabled = set(config.get_ocr_dialog_triggers())
            for trigger, check in dialog.ocr_dialog_checks.items():
                check.setChecked(trigger in enabled)
        if hasattr(dialog, "formula_engine_combo"):
            _select_combo(dialog.formula_engine_combo, config.get_formula_engine())
        if hasattr(dialog, "formula_url_input"):
            service = config.get_formula_service_config()
            dialog.formula_url_input.setText(service["url"])
            dialog.formula_key_input.setText(service["api_key"])
        if hasattr(dialog, "video_container_combo"):
            _select_combo(dialog.video_container_combo, config.get_video_container())
        if hasattr(dialog, "video_codec_combo"):
            _select_combo(dialog.video_codec_combo, config.get_video_codec())
        if hasattr(dialog, "video_quality_combo"):
            _select_combo(dialog.video_quality_combo, config.get_video_quality())
        if hasattr(dialog, "video_fps_combo"):
            _select_combo(dialog.video_fps_combo, config.get_video_fps())
        if hasattr(dialog, "video_bitrate_spin"):
            dialog.video_bitrate_spin.setValue(config.get_video_bitrate_mbps())
        if hasattr(dialog, "video_audio_combo"):
            _select_combo(dialog.video_audio_combo, config.get_video_audio())
        if hasattr(dialog, "video_audio_device_combo"):
            _select_combo(dialog.video_audio_device_combo, config.get_video_audio_device())
        if hasattr(dialog, "video_duration_spin"):
            dialog.video_duration_spin.setValue(config.get_video_max_duration_s())
        if hasattr(dialog, "video_save_path_input"):
            dialog.video_save_path_input.setText(config.get_video_save_path())
        if hasattr(dialog, "history_enabled_toggle"):
            dialog.history_enabled_toggle.setChecked(config.get_history_enabled())
        if hasattr(dialog, "history_retention_combo"):
            _select_combo(dialog.history_retention_combo, config.get_history_retention_days())
        if hasattr(dialog, "history_entries_spin"):
            dialog.history_entries_spin.setValue(config.get_history_max_entries())
        if hasattr(dialog, "history_disk_spin"):
            dialog.history_disk_spin.setValue(config.get_history_max_disk_mb())

def _snapshot_integration_controls(dialog, snap: dict) -> None:
        """把新控件也算进「未保存变更」快照。"""
        for attr in ("desktop_toolbar_combo", "tray_click_combo", "proxy_mode_combo",
                     "clipboard_image_mode_combo", "ocr_layout_combo",
                     "ocr_punctuation_combo", "ocr_language_combo",
                     "formula_engine_combo", "ocr_tier_combo", "ocr_vision_combo",
                     "tray_scroll_combo", "pdf_page_combo",
                     "ocr_vision_target_combo", "ocr_vision_task_combo",
                     "ocr_vision_protocol_combo", "video_container_combo", "video_codec_combo",
                     "video_quality_combo", "video_fps_combo", "video_audio_combo",
                     "video_audio_device_combo"):
            widget = getattr(dialog, attr, None)
            if widget is not None:
                snap[attr] = widget.currentIndex()
        for attr in ("proxy_host_input", "update_source_input", "formula_url_input",
                     "formula_key_input", "video_save_path_input",
                     "ocr_vision_url_input", "ocr_vision_model_input", "ocr_vision_key_input",
                     "ocr_vision_version_input"):
            widget = getattr(dialog, attr, None)
            if widget is not None:
                snap[attr] = widget.text()
        if hasattr(dialog, "proxy_port_spin"):
            snap["proxy_port_spin"] = dialog.proxy_port_spin.value()
        for attr in ("video_bitrate_spin", "video_duration_spin",
                     "history_entries_spin", "history_disk_spin"):
            widget = getattr(dialog, attr, None)
            if widget is not None:
                snap[attr] = widget.value()
        if hasattr(dialog, "history_enabled_toggle"):
            snap["history_enabled_toggle"] = dialog.history_enabled_toggle.isChecked()
        if hasattr(dialog, "history_retention_combo"):
            snap["history_retention_combo"] = dialog.history_retention_combo.currentIndex()
        if hasattr(dialog, "clipboard_ignore_own_toggle"):
            snap["clipboard_ignore_own_toggle"] = dialog.clipboard_ignore_own_toggle.isChecked()
        if hasattr(dialog, "ocr_dialog_checks"):
            snap["ocr_dialog_triggers"] = sorted(
                trigger for trigger, check in dialog.ocr_dialog_checks.items()
                if check.isChecked()
            )
