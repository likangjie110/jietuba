# -*- coding: utf-8 -*-
"""快捷键设置页 — Fluent Design"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QStackedWidget,
)
from PySide6.QtCore import Qt

from core import actions
from ui.dialogs import show_confirm_dialog
from ui.fluent_lite import (
    CaptionLabel, ComboBox, FluentIcon, PushButton, SegmentedWidget, SwitchButton,
    SettingCard as FSettingCard,
)
from ui.fluent_lite.theme import ACCENT
from .components import (
    CARD_PADDING, EMPTY_HINT_H, ROW_H, ROW_SPACING, SettingCardGroup, WhiteCard,
    apply_theme_text_style, rows_block_height,
)
from ..hotkey_edit import HotkeyEdit, validate_hotkey_group
from ..inapp_key_edit import InAppKeyEdit
from settings import ANNOTATION_TOOL_SHORTCUTS
from core.shortcut_manager import is_reserved_inapp_shortcut


# ── 应用内快捷键定义表（分组）──────────────────────────────
SCREENSHOT_KEYS = [
    ("inapp_confirm",   "Confirm Screenshot",     "ctrl+c"),
    ("inapp_pin",       "Pin Image",              "ctrl+d"),
    ("inapp_undo",      "Undo",                   "ctrl+z"),
    ("inapp_redo",      "Redo",                   "ctrl+y"),
    ("inapp_delete",    "Delete Selected",        "delete"),
    ("inapp_zoom_in",   "Magnifier Zoom In",      "pageup"),
    ("inapp_zoom_out",  "Magnifier Zoom Out",     "pagedown"),
    ("inapp_translate", "Screenshot Translate",    "shift+c"),
    ("inapp_text_recognize", "Recognize Text",   "shift+t"),
]

# 标注元素的层级与对齐（画布内快捷键；默认都带修饰键，避免与单键工具快捷方式撞车）
LAYER_KEYS = [
    ("inapp_bring_to_front", "Bring to Front",      "ctrl+shift+]"),
    ("inapp_send_to_back",   "Send to Back",        "ctrl+shift+["),
    ("inapp_bring_forward",  "Bring Forward",       ""),
    ("inapp_send_backward",  "Send Backward",       ""),
    ("inapp_align_left",     "Align Left",          ""),
    ("inapp_align_hcenter",  "Align Horizontal Center", ""),
    ("inapp_align_right",    "Align Right",         ""),
    ("inapp_align_top",      "Align Top",           ""),
    ("inapp_align_vcenter",  "Align Vertical Center", ""),
    ("inapp_align_bottom",   "Align Bottom",        ""),
]

PIN_KEYS = [
    ("inapp_copy_pin",        "Copy Pinned Image",      "ctrl+c"),
    ("inapp_thumbnail",       "Toggle Thumbnail",       "r"),
    ("inapp_toggle_toolbar",  "Toggle Toolbar",         "space"),
    ("inapp_pin_save",        "Save Pinned Image",      "ctrl+s"),
    ("inapp_pin_rotate",      "Rotate Pinned Image",    "ctrl+r"),
    ("inapp_pin_lock",        "Lock Pinned Image",      "ctrl+l"),
    ("inapp_pin_on_top",      "Toggle Always on Top",   "ctrl+t"),
    ("inapp_pin_shadow",      "Toggle Shadow",          "ctrl+h"),
    ("inapp_pin_opacity_up",  "Increase Pin Opacity",   "ctrl+up"),
    ("inapp_pin_opacity_down", "Decrease Pin Opacity",  "ctrl+down"),
    ("inapp_pin_copy_all_text", "Copy Recognized Text",  ""),
    ("inapp_pin_copy_and_close", "Copy Pin and Close",   ""),
]

TOOL_KEYS = [
    (cfg_key, label, default)
    for cfg_key, _tool_id, label, default in ANNOTATION_TOOL_SHORTCUTS
]

INAPP_KEYS = SCREENSHOT_KEYS + TOOL_KEYS + LAYER_KEYS + PIN_KEYS

_EDIT_W = 140
_EDIT_H = 28
_SEGMENT_HINT_STYLE = "font-size: 12px; background: transparent;"

# ── 全局动作行（动作注册表驱动）──────────────────────────
_ACTION_W = 150
_HOTKEY_W = 132
_TRAY_W = 46
_REMOVE_W = 60


def _iter_global_hotkey_edits(dialog):
    """所有动作行上的热键录入框——它们属于同一个冲突域。

    动作可以增删，冲突域不再是一组固定属性；这里按行收集，判重规则本身仍在
    ui.hotkey_edit.validate_hotkey_group（欢迎向导复用的是同一份）。
    """
    for row in getattr(dialog, "_action_hotkey_rows", []):
        yield row["primary"]
        yield row["secondary"]


def validate_global_hotkey_edits(dialog, *, check_system: bool = False) -> bool:
    """校验「快捷键/动作」页上所有动作的主/备热键。"""
    return validate_hotkey_group(
        _iter_global_hotkey_edits(dialog), check_system=check_system
    )


def _rows(dialog) -> list:
    """动作行列表；页面还没构建时按空表处理并挂上去。"""
    rows = getattr(dialog, "_action_hotkey_rows", None)
    if rows is None:
        rows = []
        dialog._action_hotkey_rows = rows
    return rows


def _sync_action_rows(dialog) -> None:
    """按当前行列表收敛高度与空表提示（增删之后统一调这里）。"""
    if not hasattr(dialog, "_action_rows_holder"):
        return
    count = len(_rows(dialog))
    dialog._action_rows_holder.setFixedHeight(rows_block_height(count))
    dialog._action_rows_holder.setVisible(count > 0)
    dialog._action_empty_hint.setVisible(count == 0)
    dialog._action_rows_card.setFixedHeight(rows_block_height(count) + CARD_PADDING * 2)


def _remove_action_row(dialog, row: dict) -> None:
    rows = _rows(dialog)
    if row in rows:
        rows.remove(row)
    card = row.get("card")
    if card is not None:
        card.setParent(None)
        card.deleteLater()
    _sync_action_rows(dialog)


def _add_action_row(dialog, action_id: str = "", primary: str = "", secondary: str = "",
                    in_tray: bool | None = None, rebuild: bool = True) -> dict | None:
    """新增一行动作绑定；不指定动作时挑第一个还没进列表的动作。

    配置里留着已不存在的动作 id 时不建行（和全局鼠标页同一套判断）：默默把它显示成
    第一个动作，等于替用户改了绑定。托盘开关默认取注册表里该动作的默认值。
    """
    if action_id and action_id not in actions.ACTIONS_BY_ID:
        from core.logger import T, log_warning

        log_warning(T("忽略不认识的动作绑定: {action_id}", action_id=action_id), "Hotkey")
        return None

    listed = {row["action"].currentData() for row in _rows(dialog)}
    if not action_id:
        action_id = next(
            (action.id for action in actions.ACTIONS if action.id not in listed),
            actions.ACTIONS[0].id,
        )

    holder = getattr(dialog, "_action_rows_holder", None)
    card = WhiteCard(holder)
    card.setFixedHeight(ROW_H)
    row_layout = QHBoxLayout(card)
    row_layout.setContentsMargins(14, 4, 12, 4)
    row_layout.setSpacing(10)

    combo = ComboBox(card)
    combo.setFixedWidth(_ACTION_W)
    for action in actions.ACTIONS:
        combo.addItem(dialog.tr(action.label), userData=action.id)
    combo.setCurrentIndex(max(0, combo.findData(action_id)))

    primary_edit = HotkeyEdit()
    primary_edit.setFixedWidth(_HOTKEY_W)
    primary_edit.setPlaceholderText(dialog.tr("e.g.: ctrl+shift+a"))
    primary_edit.setStyleSheet(dialog._get_input_style())
    primary_edit.setText(primary)

    secondary_edit = HotkeyEdit()
    secondary_edit.setFixedWidth(_HOTKEY_W)
    secondary_edit.setPlaceholderText(dialog.tr("e.g.: ctrl+shift+a"))
    secondary_edit.setStyleSheet(dialog._get_input_style())
    secondary_edit.setText(secondary)

    tray_switch = SwitchButton(card)
    tray_switch.setToolTip(dialog.tr("Show in the tray menu"))
    if in_tray is None:
        in_tray = actions.ACTIONS_BY_ID[action_id].tray
    tray_switch.setChecked(bool(in_tray))

    row = {
        "card": card,
        "action": combo,
        "primary": primary_edit,
        "secondary": secondary_edit,
        "tray": tray_switch,
    }

    remove_btn = PushButton(dialog.tr("Remove"), card)
    remove_btn.setFixedWidth(_REMOVE_W)
    remove_btn.clicked.connect(lambda: _remove_action_row(dialog, row))

    row_layout.addWidget(combo)
    row_layout.addWidget(primary_edit)
    row_layout.addWidget(secondary_edit)
    row_layout.addWidget(tray_switch)
    row_layout.addStretch(1)
    row_layout.addWidget(remove_btn)

    _rows(dialog).append(row)
    if holder is not None:
        holder.layout().addWidget(card)
    for edit in (primary_edit, secondary_edit):
        edit.textChanged.connect(
            lambda _text, d=dialog: validate_global_hotkey_edits(d)
        )
    if rebuild:
        _sync_action_rows(dialog)
    return row


def collect_action_hotkeys(dialog) -> dict:
    """把行读成 ``{动作 id: [主热键, 备用热键]}``（同一动作的多行以最后一行为准）。"""
    table = {}
    for row in getattr(dialog, "_action_hotkey_rows", []):
        action_id = row["action"].currentData()
        if not action_id:
            continue
        table[action_id] = [
            row["primary"].text().strip(), row["secondary"].text().strip(),
        ]
    return table


def collect_action_tray_flags(dialog) -> dict:
    """把行读成 ``{动作 id: 是否显示在托盘}``。

    行被移除的动作不在这里面——写出的是全量表，所以「移除动作」同时也把它从托盘拿掉。
    """
    flags = {}
    for row in getattr(dialog, "_action_hotkey_rows", []):
        action_id = row["action"].currentData()
        if action_id:
            flags[action_id] = row["tray"].isChecked()
    return flags


def _rebuild_action_rows(dialog, hotkeys: dict, tray_flags: dict) -> None:
    """按给定的表重建动作行（刷新与「恢复默认」共用）。"""
    for row in list(getattr(dialog, "_action_hotkey_rows", [])):
        _remove_action_row(dialog, row)
    for action_id, pair in (hotkeys or {}).items():
        _add_action_row(
            dialog, action_id, pair[0], pair[1],
            tray_flags.get(action_id, actions.ACTIONS_BY_ID[action_id].tray)
            if action_id in actions.ACTIONS_BY_ID else False,
            rebuild=False,
        )
    _sync_action_rows(dialog)
    validate_global_hotkey_edits(dialog)


def refresh_action_rows(dialog) -> None:
    """按配置重建动作行（界面刷新、语言切换后重建时用）。"""
    _rebuild_action_rows(
        dialog,
        dialog.config_manager.get_action_hotkeys(),
        dialog.config_manager.get_action_tray_flags(),
    )


def reset_action_rows(dialog) -> None:
    """恢复成注册表/默认设置里的动作表。"""
    defaults = dialog.config_manager.APP_DEFAULT_SETTINGS
    _rebuild_action_rows(dialog, defaults["action_hotkeys"], defaults["action_tray"])


def _build_shortcut_row(dialog, parent, title: str, editor: QWidget) -> QWidget:
    row_card = WhiteCard(parent)
    row_card.setFixedHeight(46)

    row_layout = QHBoxLayout(row_card)
    row_layout.setContentsMargins(16, 0, 14, 0)
    row_layout.setSpacing(12)
    row_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

    title_label = QLabel(title, row_card)
    apply_theme_text_style(title_label, 13)
    row_layout.addWidget(title_label, 1)
    row_layout.addWidget(editor, 0, Qt.AlignmentFlag.AlignRight)
    return row_card


def _stack_page_height(row_count: int) -> int:
    return row_count * 46 + max(0, row_count - 1) * 8


def create_hotkey_page(dialog) -> QWidget:
    """创建快捷键设置页面 — Fluent Design"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    input_style = dialog._get_input_style()

    # ════ 全局动作（动作注册表驱动）════
    # 动作清单来自 core.actions，热键存在 app/action_hotkeys；这一页只负责把它们摆出来，
    # 不再自己维护「截图/剪贴板/翻译」三组固定字段——注册表里加了动作，这里自动出现。
    grp_global = SettingCardGroup(dialog.tr("Global Actions"), view)

    dialog._action_rows_card = WhiteCard(grp_global)
    card_layout = QVBoxLayout(dialog._action_rows_card)
    card_layout.setContentsMargins(14, CARD_PADDING - 12, 14, CARD_PADDING - 12)
    card_layout.setSpacing(ROW_SPACING)

    header = QWidget(dialog._action_rows_card)
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(14, 0, 12, 0)
    header_layout.setSpacing(10)
    for text, width in (
        (dialog.tr("Action"), _ACTION_W),
        (dialog.tr("Hotkey"), _HOTKEY_W),
        (dialog.tr("Backup Hotkey"), _HOTKEY_W),
        (dialog.tr("Tray"), _TRAY_W),
    ):
        column = CaptionLabel(text, header)
        column.setFixedWidth(width)
        header_layout.addWidget(column)
    header_layout.addStretch(1)
    header_layout.addSpacing(_REMOVE_W)
    card_layout.addWidget(header)

    dialog._action_rows_holder = QWidget(dialog._action_rows_card)
    holder_layout = QVBoxLayout(dialog._action_rows_holder)
    holder_layout.setContentsMargins(0, 0, 0, 0)
    holder_layout.setSpacing(ROW_SPACING)
    card_layout.addWidget(dialog._action_rows_holder)

    dialog._action_empty_hint = CaptionLabel(
        dialog.tr("No global actions yet. Add one to bind a hotkey."),
        dialog._action_rows_card,
    )
    dialog._action_empty_hint.setFixedHeight(EMPTY_HINT_H)
    card_layout.addWidget(dialog._action_empty_hint)

    dialog._action_hotkey_rows = []
    refresh_action_rows(dialog)
    grp_global.addSettingCard(dialog._action_rows_card)

    buttons_card = FSettingCard(
        FluentIcon.SETTING,
        dialog.tr("Add Action"),
        dialog.tr("Bind another action to a global hotkey."),
        parent=grp_global,
    )
    btn_add = PushButton(dialog.tr("Add Action"), buttons_card)
    btn_add.setFixedHeight(32)
    btn_add.clicked.connect(lambda: _add_action_row(dialog))
    btn_clear = PushButton(dialog.tr("Clear All Hotkeys"), buttons_card)
    btn_clear.setFixedHeight(32)

    def _clear_all_hotkeys():
        """清空所有动作的主/备热键（动作行保留，托盘开关不动）。"""
        for row in dialog._action_hotkey_rows:
            row["primary"].setText("")
            row["secondary"].setText("")

    btn_clear.clicked.connect(_clear_all_hotkeys)
    buttons_card.hBoxLayout.addWidget(btn_add, 0, Qt.AlignmentFlag.AlignRight)
    buttons_card.hBoxLayout.addSpacing(8)
    buttons_card.hBoxLayout.addWidget(btn_clear, 0, Qt.AlignmentFlag.AlignRight)
    buttons_card.hBoxLayout.addSpacing(16)
    grp_global.addSettingCard(buttons_card)

    layout.addWidget(grp_global)

    # ════ 应用内快捷键 ════
    grp_inapp = SettingCardGroup(dialog.tr("In-App Shortcuts"), view)

    dialog._inapp_edits = {}
    dialog._inapp_groups = {}

    tab_card = WhiteCard(grp_inapp)
    tab_layout = QVBoxLayout(tab_card)
    tab_layout.setContentsMargins(16, 16, 16, 16)
    tab_layout.setSpacing(12)

    tab_switch = SegmentedWidget(tab_card)
    tab_switch.setFixedHeight(34)
    tab_switch.setIndicatorColor(ACCENT, ACCENT)

    stack = QStackedWidget(tab_card)
    stack.setObjectName("InAppShortcutStack")
    stack.setStyleSheet("#InAppShortcutStack { background: transparent; border: none; }")

    def _build_tab(keys_list: list, group_name: str, extra_widgets=None) -> QWidget:
        page = QWidget()
        vbox = QVBoxLayout(page)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        for cfg_key, tr_src, default in keys_list:
            edit = InAppKeyEdit()
            edit.setFixedSize(_EDIT_W, _EDIT_H)
            edit.setStyleSheet(input_style)
            value = dialog.config_manager.get_inapp_shortcut(cfg_key)
            edit.setText("" if is_reserved_inapp_shortcut(value) else value)
            dialog._inapp_edits[cfg_key] = edit
            dialog._inapp_groups[cfg_key] = group_name

            vbox.addWidget(_build_shortcut_row(dialog, page, dialog.tr(tr_src), edit))

        if extra_widgets:
            for w in extra_widgets:
                vbox.addWidget(w)

        vbox.addStretch(1)
        return page

    # 鼠标微移模式
    dialog.cursor_move_combo = ComboBox()
    dialog.cursor_move_combo.setFixedSize(_EDIT_W, _EDIT_H)
    dialog.cursor_move_combo.addItem("WASD + ↑↓←→", userData="both")
    dialog.cursor_move_combo.addItem("↑↓←→", userData="arrows")
    dialog.cursor_move_combo.addItem("WASD", userData="wasd")

    cur_mode = dialog.config_manager.get_inapp_cursor_move_mode()
    idx = dialog.cursor_move_combo.findData(cur_mode)
    if idx >= 0:
        dialog.cursor_move_combo.setCurrentIndex(idx)

    move_row = _build_shortcut_row(
        dialog, tab_card, dialog.tr("Cursor Move Keys"), dialog.cursor_move_combo
    )

    screenshot_tab = _build_tab(
        SCREENSHOT_KEYS, "screenshot", extra_widgets=[move_row]
    )
    tools_tab = _build_tab(TOOL_KEYS, "screenshot")
    layer_tab = _build_tab(LAYER_KEYS, "screenshot")
    pin_tab = _build_tab(PIN_KEYS, "pin")

    stack.addWidget(screenshot_tab)
    stack.addWidget(tools_tab)
    stack.addWidget(layer_tab)
    stack.addWidget(pin_tab)

    tab_switch.addItem("screenshot", dialog.tr("Screenshot Shortcuts"), lambda: stack.setCurrentIndex(0))
    tab_switch.addItem("tools", dialog.tr("Annotation Tools"), lambda: stack.setCurrentIndex(1))
    tab_switch.addItem("layer", dialog.tr("Layer & Alignment"),
                       lambda: stack.setCurrentIndex(2))
    tab_switch.addItem("pin", dialog.tr("Pin Shortcuts"), lambda: stack.setCurrentIndex(3))
    tab_switch.setCurrentItem("screenshot")

    tab_layout.addWidget(tab_switch, 0, Qt.AlignmentFlag.AlignLeft)
    tab_layout.addWidget(stack)

    screenshot_h = _stack_page_height(len(SCREENSHOT_KEYS) + 1)
    tools_h = _stack_page_height(len(TOOL_KEYS))
    layer_h = _stack_page_height(len(LAYER_KEYS))
    pin_h = _stack_page_height(len(PIN_KEYS))
    tallest = max(screenshot_h, tools_h, layer_h, pin_h)
    stack.setMinimumHeight(tallest)
    tab_card.setFixedHeight(tallest + 80)
    grp_inapp.addSettingCard(tab_card)

    # 冲突检测
    for cfg_key, edit in dialog._inapp_edits.items():
        edit.textChanged.connect(
            lambda text, k=cfg_key: _on_shortcut_changed(
                dialog, k, text, input_style
            )
        )

    layout.addWidget(grp_inapp)

    # 提示
    hint = CaptionLabel(
        dialog.tr("💡 Configured shortcuts take priority over WASD and C. Arrow keys remain available; Esc is reserved."),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    layout.addWidget(hint)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll


# ── 输入后冲突检测（交互式弹窗）──────────────────────────

def _on_shortcut_changed(dialog, changed_key: str, new_text: str, base_style: str):
    """某个输入框值变化时，检查同组内是否冲突，弹窗询问是否替换"""
    new_text = new_text.strip().lower()
    # 忽略空值、未完成的中间态（如 "ctrl+"）
    if not new_text or new_text.endswith("+"):
        return

    my_group = dialog._inapp_groups.get(changed_key, "")

    # 找同组内与新值相同的其他 edit
    conflict_key = None
    for cfg_key, edit in dialog._inapp_edits.items():
        if cfg_key == changed_key:
            continue
        if dialog._inapp_groups.get(cfg_key, "") != my_group:
            continue
        if edit.text().strip().lower() == new_text:
            conflict_key = cfg_key
            break

    if conflict_key is None:
        return  # 无冲突

    # 找到冲突项的显示名
    conflict_label = conflict_key
    for keys_list in (SCREENSHOT_KEYS, TOOL_KEYS, LAYER_KEYS, PIN_KEYS):
        for cfg, tr_src, _default in keys_list:
            if cfg == conflict_key:
                conflict_label = dialog.tr(tr_src)
                break

    # 弹窗询问
    current_edit = dialog._inapp_edits[changed_key]
    conflict_edit = dialog._inapp_edits[conflict_key]

    # 阻塞信号防止递归
    current_edit.blockSignals(True)
    conflict_edit.blockSignals(True)

    ret = show_confirm_dialog(
        dialog,
        dialog.tr("Shortcut Conflict"),
        dialog.tr('"%1" is already used by "%2".\nReplace it?')
            .replace('%1', new_text.upper())
            .replace('%2', conflict_label),
    )

    if ret is True:
        # 清空旧的，保留新的
        conflict_edit.setText("")
    else:
        # 撤销本次输入，恢复旧值
        old_val = dialog.config_manager.get_inapp_shortcut(changed_key)
        current_edit.setText("" if is_reserved_inapp_shortcut(old_val) else old_val)

    current_edit.blockSignals(False)
    conflict_edit.blockSignals(False)
 
