"""
钉图右键菜单

负责创建和显示钉图窗口的右键上下文菜单
"""

from PySide6.QtWidgets import QMenu, QWidget
from PySide6.QtGui import QAction, QFont
from PySide6.QtCore import QPoint
from core.constants import CSS_FONT_FAMILY_UI
from core.platform.fonts import ui_font_family


def _get_shortcut_display(cfg_key: str) -> str:
    """从配置读取快捷键并返回大写显示文本（如 "CTRL+C"、"R"、"SPACE"）"""
    from settings import get_tool_settings_manager
    cfg = get_tool_settings_manager()
    text = cfg.get_inapp_shortcut(cfg_key)
    if not text:
        text = cfg.APP_DEFAULT_SETTINGS.get(cfg_key, "")
    return text.upper() if text else ""


class PinContextMenu:
    """
    钉图右键菜单管理器
    
    创建和显示钉图窗口的上下文菜单
    """
    
    def __init__(self, parent: QWidget):
        """
        初始化右键菜单管理器
        
        Args:
            parent: 父窗口（PinWindow）
        """
        self.parent = parent
    
    def _create_group(self, manager) -> bool:
        """新建一个分组并把当前贴图放进去（名字由用户输入）。"""
        from PySide6.QtWidgets import QInputDialog

        name, accepted = QInputDialog.getText(
            self.parent, self.parent.tr("New group"), self.parent.tr("Group name"))
        if not accepted or not str(name).strip():
            return False
        manager.remember_group(name)
        return bool(manager.add_to_group(self.parent, name))

    def show(self, global_pos: QPoint, state: dict):
        """
        显示右键菜单
        
        Args:
            global_pos: 全局坐标位置
            state: 当前状态字典，包含：
                - toolbar_visible: 工具栏是否可见
                - stay_on_top: 是否置顶
                - shadow_enabled: 阴影是否启用
                - text_selection_enabled: 文字选择是否启用
        """
        menu = QMenu(self.parent)
        
        # 设置字体
        self._setup_font(menu)
        
        # 设置样式
        menu.setStyleSheet(self._get_menu_style())
        
        # 添加菜单项
        self._add_menu_items(menu, state)
        
        # 显示菜单
        menu.exec(global_pos)
    
    def _setup_font(self, menu: QMenu):
        """设置菜单字体"""
        menu.setFont(QFont(ui_font_family("zh"), 9))
    
    def _get_menu_style(self) -> str:
        """获取菜单样式（悬停颜色跟随主题色）"""
        from core.theme import get_theme
        tc = get_theme().theme_color_hex
        return f"""
            QMenu {{
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 4px;
                font-family: {CSS_FONT_FAMILY_UI};
                font-size: 9pt;
                color: #000000;
            }}
            QMenu::item {{
                padding: 6px 12px;
                border-radius: 3px;
                color: #000000;
                background-color: transparent;
            }}
            QMenu::item:selected {{
                background-color: {tc};
                color: #ffffff;
            }}
            QMenu::item:disabled {{
                color: #9e9e9e;
            }}
            QMenu::separator {{
                height: 1px;
                background: #ddd;
                margin: 4px 6px;
            }}
        """
    
    def _add_menu_items(self, menu: QMenu, state: dict):
        """添加菜单项"""
        def _toggle_text(label: str, enabled: bool) -> str:
            return f"{label}\t{'●' if enabled else '○'}"

        is_thumbnail = state.get('thumbnail_mode', False)

        # 复制内容
        copy_action = QAction(self.parent.tr("Copy"), self.parent)
        copy_action.triggered.connect(self.parent.copy_to_clipboard)
        menu.addAction(copy_action)
        
        # 保存图片
        save_action = QAction(self.parent.tr("Save as"), self.parent)
        save_action.triggered.connect(self.parent.save_image)
        menu.addAction(save_action)
        
        # --- 以下项目在缩略图模式下隐藏 ---
        if not is_thumbnail:
            # 翻译入口始终可用；没有现成结果时由 PinOCRManager 按需识别。
            translate_action = QAction(
                self.parent.tr("Translate (Text Recognition)"), self.parent
            )
            translate_action.triggered.connect(self.parent.request_translation)
            menu.addAction(translate_action)
            
            # 恢复原始大小
            reset_size_action = QAction(self.parent.tr("Reset size"), self.parent)
            reset_size_action.triggered.connect(self.parent.reset_to_original_size)
            menu.addAction(reset_size_action)
            
            # 图像变换子菜单
            transform_menu = QMenu(self.parent.tr("Image transform"), self.parent)
            transform_menu.setStyleSheet(self._get_menu_style())
            self._setup_font(transform_menu)

            act_cw = QAction(self.parent.tr("Rotate right"), self.parent)
            act_cw.triggered.connect(self.parent.rotate_image_cw)
            transform_menu.addAction(act_cw)

            act_ccw = QAction(self.parent.tr("Rotate left"), self.parent)
            act_ccw.triggered.connect(self.parent.rotate_image_ccw)
            transform_menu.addAction(act_ccw)

            act_fh = QAction(self.parent.tr("Flip horizontal"), self.parent)
            act_fh.triggered.connect(self.parent.flip_image_horizontal)
            transform_menu.addAction(act_fh)

            act_fv = QAction(self.parent.tr("Flip vertical"), self.parent)
            act_fv.triggered.connect(self.parent.flip_image_vertical)
            transform_menu.addAction(act_fv)

            transform_menu.addSeparator()

            act_reset = QAction(self.parent.tr("Reset transform"), self.parent)
            act_reset.triggered.connect(self.parent.reset_image_transform)
            transform_menu.addAction(act_reset)

            menu.addMenu(transform_menu)

            # 裁剪（进裁剪模式，在内容区拖一个矩形）
            from .pin_actions import run_pin_action

            crop_action = QAction(self.parent.tr("Crop"), self.parent)
            crop_action.triggered.connect(
                lambda: run_pin_action("crop", self.parent))
            menu.addAction(crop_action)

            # 滤镜子菜单
            filter_menu = QMenu(self.parent.tr("Filter"), self.parent)
            filter_menu.setStyleSheet(self._get_menu_style())
            self._setup_font(filter_menu)
            for action_id, label in (("filter_grayscale", "Grayscale"),
                                     ("filter_invert", "Invert Colours"),
                                     ("filter_blur", "Blur"),
                                     ("filter_emboss", "Emboss")):
                entry = QAction(self.parent.tr(label), self.parent)
                entry.triggered.connect(
                    lambda _checked=False, value=action_id: run_pin_action(value, self.parent))
                filter_menu.addAction(entry)
            menu.addMenu(filter_menu)

        menu.addSeparator()
        
        # --- 以下项目在缩略图模式下也隐藏（工具栏、阴影） ---
        if not is_thumbnail:
            # 显示/隐藏工具栏
            toolbar_visible = state.get('toolbar_visible', False)
            toolbar_key = _get_shortcut_display("inapp_toggle_toolbar")
            toolbar_label = self.parent.tr("Toolbar")
            if toolbar_key:
                toolbar_label += f" ({toolbar_key})"
            toolbar_action = QAction(_toggle_text(toolbar_label, toolbar_visible), self.parent)
            toolbar_action.triggered.connect(self.parent.toggle_toolbar)
            menu.addAction(toolbar_action)
        
        # 切换置顶（任何模式都可用）
        stay_on_top = state.get('stay_on_top', False)
        toggle_top_action = QAction(_toggle_text(self.parent.tr("Always on top"), stay_on_top), self.parent)
        toggle_top_action.triggered.connect(self.parent.toggle_stay_on_top)
        menu.addAction(toggle_top_action)
        
        if not is_thumbnail:
            # 锁定位置与大小
            locked = state.get('locked', False)
            lock_action = QAction(_toggle_text(self.parent.tr("Lock"), locked), self.parent)
            lock_action.triggered.connect(self.parent.toggle_lock)
            menu.addAction(lock_action)

            # 切换阴影效果
            shadow_enabled = state.get('shadow_enabled', True)
            shadow_action = QAction(_toggle_text(self.parent.tr("Shadow effect"), shadow_enabled), self.parent)
            shadow_action.triggered.connect(self.parent.toggle_border_effect)
            menu.addAction(shadow_action)

            # 切换文字选择
            text_selection_enabled = state.get('text_selection_enabled', True)
            text_selection_action = QAction(
                _toggle_text(self.parent.tr("Text selection"), text_selection_enabled),
                self.parent,
            )
            text_selection_action.triggered.connect(self.parent.toggle_text_selection)
            menu.addAction(text_selection_action)
        
        if not is_thumbnail:
            # 点击穿透：开启后窗口不再接收鼠标，所以它同时是一个可绑定的贴图动作
            click_through = state.get('click_through', False)
            through_action = QAction(
                _toggle_text(self.parent.tr("Click-through"), click_through), self.parent)
            through_action.triggered.connect(self.parent.toggle_click_through)
            menu.addAction(through_action)

            # 焦点模式：只留这一张（其余隐藏，不关闭）
            focus_action = QAction(
                _toggle_text(self.parent.tr("Focus mode"), state.get("focus_mode", False)),
                self.parent)
            focus_action.triggered.connect(self.parent.toggle_focus_mode)
            menu.addAction(focus_action)

            # 关闭其它贴图
            others_action = QAction(self.parent.tr("Close other pins"), self.parent)
            others_action.triggered.connect(self.parent.close_other_pins)
            menu.addAction(others_action)

            # 加载新内容 / 重新识别
            load_action = QAction(self.parent.tr("Load new content"), self.parent)
            load_action.triggered.connect(self.parent.load_image_from_file)
            menu.addAction(load_action)
            recognize_action = QAction(self.parent.tr("Recognize text again"), self.parent)
            recognize_action.triggered.connect(self.parent.recognize_text_now)
            menu.addAction(recognize_action)

            # 分组：加进已有组 / 移出 / 新建
            from .pin_manager import PinManager

            manager = PinManager.instance()
            group_menu = menu.addMenu(self.parent.tr("Group"))
            member_groups = manager.group_names()
            for name in member_groups:
                action = QAction(name, self.parent)
                action.triggered.connect(
                    lambda _checked=False, group=name: manager.add_to_group(self.parent, group))
                group_menu.addAction(action)
            if member_groups:
                group_menu.addSeparator()
            leave_action = QAction(self.parent.tr("Remove from group"), self.parent)
            leave_action.triggered.connect(lambda: manager.remove_from_group(self.parent))
            group_menu.addAction(leave_action)
            new_group_action = QAction(self.parent.tr("New group…"), self.parent)
            new_group_action.triggered.connect(
                lambda: self._create_group(manager))
            group_menu.addAction(new_group_action)

            joined = bool(getattr(self.parent, "group_name", ""))
            if not joined:
                leave_action.setEnabled(False)

        # 关闭选中的贴图（多选时才出现）
        selected_count = state.get('selected_count', 0)
        if selected_count:
            selected_close = QAction(
                self.parent.tr("Close Selected Pins") + f" ({selected_count})", self.parent)
            selected_close.triggered.connect(self.parent.close_selected_pins)
            menu.addAction(selected_close)
            menu.addSeparator()

        # 缩略图模式（任何模式都可用）
        thumbnail_mode = state.get('thumbnail_mode', False)
        thumbnail_key = _get_shortcut_display("inapp_thumbnail")
        thumbnail_label = self.parent.tr("Thumbnail mode")
        if thumbnail_key:
            thumbnail_label += f" ({thumbnail_key})"
        thumbnail_action = QAction(
            _toggle_text(thumbnail_label, thumbnail_mode),
            self.parent
        )
        thumbnail_action.triggered.connect(self.parent.toggle_thumbnail_mode)
        menu.addAction(thumbnail_action)
        
        menu.addSeparator()
        
        # 关闭钉图（任何模式都可用）
        close_action = QAction(self.parent.tr("Close"), self.parent)
        close_action.triggered.connect(self.parent.close_window)
        menu.addAction(close_action)
 
