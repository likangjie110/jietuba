# -*- coding: utf-8 -*-
"""全局鼠标设置页 — Fluent Design

「修饰键 + 鼠标手势 → 动作」的绑定表，外加两个全局开关：截图时是否显示遮罩、
哪些程序上不触发。动作清单来自 ``core.actions`` 的动作注册表，这一页不自己维护
一份名字表——注册表是「应用里能做哪些事」的唯一出处。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from core import actions
from core.platform import pointer as platform_pointer
from ui.fluent_lite import (
    CaptionLabel, ComboBox, FluentIcon, LineEdit, PushButton,
    SettingCard as FSettingCard, SwitchSettingCard,
)
from .components import (
    CARD_PADDING, EMPTY_HINT_H, ROW_H, ROW_SPACING, SettingCardGroup, WhiteCard,
    apply_theme_text_style, rows_block_height,
)

_ACTION_W = 200
_MODIFIER_W = 110
_GESTURE_W = 150
_REMOVE_W = 72


def _gesture_label(dialog, gesture: str) -> str:
    """手势在界面上的说法（配置里存的是平台层的英文 token）。"""
    return {
        platform_pointer.GESTURE_WHEEL_UP: dialog.tr("Scroll Up"),
        platform_pointer.GESTURE_WHEEL_DOWN: dialog.tr("Scroll Down"),
        platform_pointer.GESTURE_MIDDLE_CLICK: dialog.tr("Middle Click"),
        platform_pointer.GESTURE_BACK_CLICK: dialog.tr("Back Button"),
        platform_pointer.GESTURE_FORWARD_CLICK: dialog.tr("Forward Button"),
    }.get(gesture, gesture)


def _modifier_label(dialog, modifier: str) -> str:
    if not modifier:
        return dialog.tr("No Modifier")
    return {
        "ctrl": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "cmd": "Cmd / Win",
    }.get(modifier, modifier)


def _sync_mouse_rows(dialog) -> None:
    """按当前行列表收敛高度与空表提示（增删之后统一调这里）。"""
    rows = dialog._mouse_gesture_rows
    count = len(rows)
    dialog._mouse_rows_holder.setFixedHeight(rows_block_height(count))
    dialog._mouse_rows_holder.setVisible(count > 0)
    dialog._mouse_empty_hint.setVisible(count == 0)
    dialog._mouse_rows_card.setFixedHeight(
        rows_block_height(count) + CARD_PADDING * 2
    )


def _remove_mouse_row(dialog, row: dict) -> None:
    """移除一行绑定。"""
    rows = dialog._mouse_gesture_rows
    if row in rows:
        rows.remove(row)
    card = row.get("card")
    if card is not None:
        card.setParent(None)
        card.deleteLater()
    _sync_mouse_rows(dialog)


def _add_mouse_row(dialog, action_id: str = "", modifier: str = "", gesture: str = "",
                   rebuild: bool = True) -> dict | None:
    """新增一行绑定；不指定动作时挑第一个还没绑的动作。

    配置里留着已不存在的动作 id 时**不建行**：默默把它显示成第一个动作等于替用户改了
    绑定，下一次手势就会触发一个他没选过的功能。丢弃、记一条日志（同一个动作在
    ``ShortcutManager`` 那边也是这样被丢掉的）。
    """
    if action_id and action_id not in actions.ACTIONS_BY_ID:
        from core.logger import T, log_warning

        log_warning(T("忽略不认识的动作绑定: {action_id}", action_id=action_id), "MouseGesture")
        return None

    bound = {row["action"].currentData() for row in dialog._mouse_gesture_rows}
    if not action_id:
        for action in actions.ACTIONS:
            if action.id not in bound:
                action_id = action.id
                break
        else:
            action_id = actions.ACTIONS[0].id
    if not gesture:
        gesture = platform_pointer.GESTURE_WHEEL_UP

    card = WhiteCard(dialog._mouse_rows_holder)
    card.setFixedHeight(ROW_H)
    row_layout = QHBoxLayout(card)
    row_layout.setContentsMargins(14, 4, 12, 4)
    row_layout.setSpacing(10)

    action_combo = ComboBox(card)
    action_combo.setFixedWidth(_ACTION_W)
    for action in actions.ACTIONS:
        action_combo.addItem(dialog.tr(action.label), userData=action.id)
    index = action_combo.findData(action_id)
    action_combo.setCurrentIndex(max(0, index))

    modifier_combo = ComboBox(card)
    modifier_combo.setFixedWidth(_MODIFIER_W)
    for value in platform_pointer.MOUSE_MODIFIERS:
        modifier_combo.addItem(_modifier_label(dialog, value), userData=value)
    index = modifier_combo.findData(modifier)
    modifier_combo.setCurrentIndex(max(0, index))

    gesture_combo = ComboBox(card)
    gesture_combo.setFixedWidth(_GESTURE_W)
    for value in platform_pointer.MOUSE_GESTURES:
        gesture_combo.addItem(_gesture_label(dialog, value), userData=value)
    index = gesture_combo.findData(gesture)
    gesture_combo.setCurrentIndex(max(0, index))

    row = {
        "card": card,
        "action": action_combo,
        "modifier": modifier_combo,
        "gesture": gesture_combo,
    }

    remove_btn = PushButton(dialog.tr("Remove"), card)
    remove_btn.setFixedWidth(_REMOVE_W)
    remove_btn.clicked.connect(lambda: _remove_mouse_row(dialog, row))

    row_layout.addWidget(action_combo)
    row_layout.addWidget(modifier_combo)
    row_layout.addWidget(gesture_combo)
    row_layout.addStretch(1)
    row_layout.addWidget(remove_btn)

    dialog._mouse_gesture_rows.append(row)
    dialog._mouse_rows_holder.layout().addWidget(card)
    if rebuild:
        _sync_mouse_rows(dialog)
    return row


def collect_mouse_gestures(dialog) -> dict:
    """把界面上的行读成配置表：``{动作 id: {"modifier", "gesture"}}``。

    同一动作被绑了多行时以最后一行为准——界面允许重复（用户中途改主意很正常），
    存的时候收敛成一张表。
    """
    bindings = {}
    for row in getattr(dialog, "_mouse_gesture_rows", []):
        action_id = row["action"].currentData()
        if not action_id:
            continue
        bindings[action_id] = {
            "modifier": row["modifier"].currentData() or "",
            "gesture": row["gesture"].currentData() or "",
        }
    return bindings


# ── 忽略程序列表 ──────────────────────────────────────

def _render_ignored_rows(dialog) -> None:
    """按名字列表重建忽略项行（读出来的是纯字符串，重建最省事）。"""
    holder = dialog._mouse_ignored_holder
    layout = holder.layout()
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()

    names = dialog._mouse_ignored_names
    for name in names:
        card = WhiteCard(holder)
        card.setFixedHeight(ROW_H)
        row_layout = QHBoxLayout(card)
        row_layout.setContentsMargins(14, 4, 12, 4)
        row_layout.setSpacing(10)

        label = QLabel(name, card)
        apply_theme_text_style(label, 13)
        row_layout.addWidget(label, 1)

        remove_btn = PushButton(dialog.tr("Remove"), card)
        remove_btn.setFixedWidth(_REMOVE_W)
        remove_btn.clicked.connect(
            lambda _checked=False, target=name: _remove_ignored_app(dialog, target)
        )
        row_layout.addWidget(remove_btn)
        layout.addWidget(card)

    count = len(names)
    holder.setFixedHeight(rows_block_height(count))
    holder.setVisible(count > 0)
    dialog._mouse_ignored_hint.setVisible(count == 0)
    dialog._mouse_ignored_card.setFixedHeight(
        rows_block_height(count) + CARD_PADDING * 2
    )


def _remove_ignored_app(dialog, name: str) -> None:
    if name in dialog._mouse_ignored_names:
        dialog._mouse_ignored_names.remove(name)
    _render_ignored_rows(dialog)


def _add_ignored_app(dialog) -> None:
    """把输入框里的程序名加进列表（空白、重复都不加）。"""
    name = dialog.mouse_ignored_edit.text().strip()
    if not name:
        return
    if name not in dialog._mouse_ignored_names:
        dialog._mouse_ignored_names.append(name)
    dialog.mouse_ignored_edit.clear()
    _render_ignored_rows(dialog)


def read_ignored_apps(dialog) -> list:
    """读出界面上的忽略程序列表。"""
    return list(getattr(dialog, "_mouse_ignored_names", []))


def reset_mouse_page(dialog) -> None:
    """把页面恢复成默认值：没有绑定、不开遮罩、忽略列表为空。"""
    defaults = dialog.config_manager.APP_DEFAULT_SETTINGS
    for row in list(getattr(dialog, "_mouse_gesture_rows", [])):
        _remove_mouse_row(dialog, row)
    if hasattr(dialog, "mouse_overlay_toggle"):
        dialog.mouse_overlay_toggle.setChecked(defaults["mouse_capture_overlay"])
    if hasattr(dialog, "_mouse_ignored_names"):
        dialog._mouse_ignored_names[:] = list(defaults["mouse_ignored_apps"] or [])
        _render_ignored_rows(dialog)


def create_mouse_page(dialog) -> QWidget:
    """创建「全局鼠标」设置页。"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ── 动作绑定 ──────────────────────────────────────
    grp_actions = SettingCardGroup(dialog.tr("Global Mouse Actions"), view)

    dialog._mouse_rows_card = WhiteCard(grp_actions)
    card_layout = QVBoxLayout(dialog._mouse_rows_card)
    card_layout.setContentsMargins(14, CARD_PADDING - 12, 14, CARD_PADDING - 12)
    card_layout.setSpacing(8)

    dialog._mouse_rows_holder = QWidget(dialog._mouse_rows_card)
    holder_layout = QVBoxLayout(dialog._mouse_rows_holder)
    holder_layout.setContentsMargins(0, 0, 0, 0)
    holder_layout.setSpacing(ROW_SPACING)
    card_layout.addWidget(dialog._mouse_rows_holder)

    dialog._mouse_empty_hint = CaptionLabel(
        dialog.tr("No global mouse actions yet. Add one to trigger actions with the mouse."),
        dialog._mouse_rows_card,
    )
    dialog._mouse_empty_hint.setFixedHeight(EMPTY_HINT_H)
    card_layout.addWidget(dialog._mouse_empty_hint)

    dialog._mouse_gesture_rows = []
    for action_id, binding in dialog.config_manager.get_mouse_gestures().items():
        _add_mouse_row(
            dialog,
            action_id,
            str(binding.get("modifier", "")),
            str(binding.get("gesture", "")),
            rebuild=False,
        )
    _sync_mouse_rows(dialog)
    grp_actions.addSettingCard(dialog._mouse_rows_card)

    buttons_card = FSettingCard(
        FluentIcon.SETTING,
        dialog.tr("Add Action"),
        dialog.tr("Bind another modifier + mouse gesture to an action."),
        parent=grp_actions,
    )
    btn_add = PushButton(dialog.tr("Add Action"), buttons_card)
    btn_add.setFixedHeight(32)
    btn_add.clicked.connect(lambda: _add_mouse_row(dialog))
    btn_clear = PushButton(dialog.tr("Clear All"), buttons_card)
    btn_clear.setFixedHeight(32)

    def _clear_all():
        for row in list(dialog._mouse_gesture_rows):
            _remove_mouse_row(dialog, row)

    btn_clear.clicked.connect(_clear_all)
    buttons_card.hBoxLayout.addWidget(btn_add, 0, Qt.AlignmentFlag.AlignRight)
    buttons_card.hBoxLayout.addSpacing(8)
    buttons_card.hBoxLayout.addWidget(btn_clear, 0, Qt.AlignmentFlag.AlignRight)
    buttons_card.hBoxLayout.addSpacing(16)
    grp_actions.addSettingCard(buttons_card)

    layout.addWidget(grp_actions)

    # ── 截图行为 ──────────────────────────────────────
    grp_behavior = SettingCardGroup(dialog.tr("Global Mouse Capture"), view)

    overlay_card = SwitchSettingCard(
        FluentIcon.TRANSPARENT,
        dialog.tr("Show Mask While Capturing"),
        dialog.tr(
            "Briefly dims the screen and highlights the captured area after a global "
            "mouse screenshot, so you can see what was grabbed."
        ),
        parent=grp_behavior,
    )
    overlay_card.setChecked(dialog.config_manager.get_mouse_capture_overlay_enabled())
    dialog.mouse_overlay_toggle = overlay_card
    grp_behavior.addSettingCard(overlay_card)

    layout.addWidget(grp_behavior)

    # ── 忽略的程序 ────────────────────────────────────
    grp_ignore = SettingCardGroup(dialog.tr("Ignored Applications"), view)

    input_card = FSettingCard(
        FluentIcon.FILTER,
        dialog.tr("Ignored Applications"),
        dialog.tr(
            "Global mouse actions do nothing while one of these applications is in "
            "front. Names are matched ignoring case, and .app / .exe suffixes."
        ),
        parent=grp_ignore,
    )
    dialog.mouse_ignored_edit = LineEdit(input_card, use_default_style=False)
    dialog.mouse_ignored_edit.setPlaceholderText(dialog.tr("Application name"))
    dialog.mouse_ignored_edit.setFixedWidth(200)
    dialog.mouse_ignored_edit.setStyleSheet(dialog._get_input_style())
    btn_add_ignored = PushButton(dialog.tr("Add"), input_card)
    btn_add_ignored.setFixedHeight(32)
    btn_add_ignored.clicked.connect(lambda: _add_ignored_app(dialog))
    dialog.mouse_ignored_edit.returnPressed.connect(lambda: _add_ignored_app(dialog))

    input_card.hBoxLayout.addWidget(dialog.mouse_ignored_edit, 0, Qt.AlignmentFlag.AlignRight)
    input_card.hBoxLayout.addSpacing(8)
    input_card.hBoxLayout.addWidget(btn_add_ignored, 0, Qt.AlignmentFlag.AlignRight)
    input_card.hBoxLayout.addSpacing(16)
    grp_ignore.addSettingCard(input_card)

    dialog._mouse_ignored_card = WhiteCard(grp_ignore)
    ignored_layout = QVBoxLayout(dialog._mouse_ignored_card)
    ignored_layout.setContentsMargins(14, CARD_PADDING - 12, 14, CARD_PADDING - 12)
    ignored_layout.setSpacing(ROW_SPACING)

    dialog._mouse_ignored_holder = QWidget(dialog._mouse_ignored_card)
    ignored_holder_layout = QVBoxLayout(dialog._mouse_ignored_holder)
    ignored_holder_layout.setContentsMargins(0, 0, 0, 0)
    ignored_holder_layout.setSpacing(ROW_SPACING)
    ignored_layout.addWidget(dialog._mouse_ignored_holder)

    dialog._mouse_ignored_hint = CaptionLabel(
        dialog.tr("No ignored applications."),
        dialog._mouse_ignored_card,
    )
    dialog._mouse_ignored_hint.setFixedHeight(EMPTY_HINT_H)
    ignored_layout.addWidget(dialog._mouse_ignored_hint)

    dialog._mouse_ignored_names = list(dialog.config_manager.get_mouse_ignored_apps())
    grp_ignore.addSettingCard(dialog._mouse_ignored_card)
    _render_ignored_rows(dialog)

    layout.addWidget(grp_ignore)

    hint = CaptionLabel(
        dialog.tr(
            "💡 Modifier keys are read live: a binding without a modifier fires only "
            "when no modifier is held. On Windows the hotkey module and global mouse "
            "actions can fight over side buttons."
        ),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    layout.addWidget(hint)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll
