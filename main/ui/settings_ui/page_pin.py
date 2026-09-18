# -*- coding: utf-8 -*-
"""贴图设置页 — Fluent Design

贴图窗口的交互参数（滚轮缩放步长、透明度步长、默认透明度、阴影、新贴图位置与顺序、
关闭前二次确认）。以前这些值写死在 ``pin/pin_window.py`` 里，用户改不了；现在这里改、
贴图窗口读，范围常量（``PIN_*_RANGE``）两边共用。

「贴图窗口鼠标动作」不在这里——它属于贴图窗口自己的手势表，见下面的说明。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from pin import pin_actions
from pin.pin_text_pin import TEXT_FONT_SIZE_RANGE, TEXT_MAX_WIDTH_RANGE
from settings.tool_settings import (
    PIN_HISTORY_RANGE, PIN_OPACITY_RANGE, PIN_OPACITY_STEP_RANGE, PIN_ZOOM_STEP_RANGE,
)
from ui.fluent_lite import (
    CaptionLabel, ComboBox, DoubleSpinBox, FluentIcon, SpinBox,
    SettingCard as FSettingCard, SwitchSettingCard,
)
from .components import SettingCardGroup, apply_theme_text_style


def _number_card(group, icon, title, content, spin, value, limits, step, *, decimals=None):
    """一行数字设置：说明 + 右对齐的 SpinBox（范围与贴图窗口共用常量）。

    ``decimals`` 只给浮点控件传：整数用 SpinBox，它没有 setDecimals。
    """
    card = FSettingCard(icon, title, content, parent=group)
    spin.setRange(limits[0], limits[1])
    spin.setSingleStep(step)
    if decimals is not None:
        spin.setDecimals(decimals)
    spin.setValue(value)
    spin.setFixedWidth(120)
    card.hBoxLayout.addWidget(spin, 0, Qt.AlignmentFlag.AlignRight)
    card.hBoxLayout.addSpacing(16)
    group.addSettingCard(card)
    return card


def _combo_card(group, icon, title, content, items):
    """一行下拉设置：返回 (卡片, 下拉框)。"""
    card = FSettingCard(icon, title, content, parent=group)
    combo = ComboBox(card)
    combo.setFixedWidth(160)
    for value, label in items:
        combo.addItem(label, userData=value)
    card.hBoxLayout.addWidget(combo, 0, Qt.AlignmentFlag.AlignRight)
    card.hBoxLayout.addSpacing(16)
    group.addSettingCard(card)
    return card, combo


def create_pin_page(dialog) -> QWidget:
    """创建「贴图设置」页。"""
    config = dialog.config_manager

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ── 贴图交互 ──────────────────────────────────────
    grp_interaction = SettingCardGroup(dialog.tr("Pin Interaction"), view)

    dialog.pin_zoom_step_spin = DoubleSpinBox()
    _number_card(
        grp_interaction, FluentIcon.SEARCH,
        dialog.tr("Wheel Zoom Step"),
        dialog.tr("How much one wheel notch scales the pinned image (1.05 means +5%)."),
        dialog.pin_zoom_step_spin, config.get_pin_zoom_step(),
        PIN_ZOOM_STEP_RANGE, 0.01, decimals=2,
    )

    dialog.pin_opacity_step_spin = DoubleSpinBox()
    _number_card(
        grp_interaction, FluentIcon.TRANSPARENT,
        dialog.tr("Opacity Step"),
        dialog.tr("How much Ctrl + wheel changes the window opacity."),
        dialog.pin_opacity_step_spin, config.get_pin_opacity_step(),
        PIN_OPACITY_STEP_RANGE, 0.01, decimals=2,
    )

    dialog.pin_default_opacity_spin = DoubleSpinBox()
    _number_card(
        grp_interaction, FluentIcon.TRANSPARENT,
        dialog.tr("Default Opacity"),
        dialog.tr("Opacity of a newly pinned image."),
        dialog.pin_default_opacity_spin, config.get_pin_default_opacity(),
        PIN_OPACITY_RANGE, 0.05, decimals=2,
    )

    shadow_card = SwitchSettingCard(
        FluentIcon.TRANSPARENT,
        dialog.tr("Shadow and Border"),
        dialog.tr("Draw a border (and shadow) around new pinned images."),
        parent=grp_interaction,
    )
    shadow_card.setChecked(config.get_pin_shadow_enabled())
    dialog.pin_shadow_toggle = shadow_card
    grp_interaction.addSettingCard(shadow_card)

    toolbar_card = SwitchSettingCard(
        FluentIcon.LAYOUT,
        dialog.tr("Show Toolbar on Hover"),
        dialog.tr("On: shows the toolbar when the mouse enters a pinned window."),
        parent=grp_interaction,
    )
    toolbar_card.setChecked(config.get_pin_auto_toolbar())
    dialog.pin_auto_toolbar_toggle = toolbar_card
    grp_interaction.addSettingCard(toolbar_card)

    layout.addWidget(grp_interaction)

    # ── 新贴图 ────────────────────────────────────────
    grp_new = SettingCardGroup(dialog.tr("New Pinned Image"), view)

    _, dialog.pin_new_position_combo = _combo_card(
        grp_new, FluentIcon.LAYOUT,
        dialog.tr("Appearance Position"),
        dialog.tr("Where a new pinned image shows up."),
        (
            ("selection", dialog.tr("At the captured area")),
            ("cursor", dialog.tr("At the mouse position")),
            ("center", dialog.tr("Center of the screen")),
        ),
    )
    index = dialog.pin_new_position_combo.findData(config.get_pin_new_position())
    if index >= 0:
        dialog.pin_new_position_combo.setCurrentIndex(index)

    _, dialog.pin_order_combo = _combo_card(
        grp_new, FluentIcon.ALIGNMENT,
        dialog.tr("Stacking Order"),
        dialog.tr("Whether a new pinned image goes above or below the existing ones."),
        (
            ("top", dialog.tr("Above existing pins")),
            ("bottom", dialog.tr("Below existing pins")),
        ),
    )
    index = dialog.pin_order_combo.findData(config.get_pin_order())
    if index >= 0:
        dialog.pin_order_combo.setCurrentIndex(index)

    dialog.pin_history_limit_spin = SpinBox()
    _number_card(
        grp_new, FluentIcon.HISTORY,
        dialog.tr("Saved Pins"),
        dialog.tr(
            "How many pinned images are remembered when restoring on startup."
        ),
        dialog.pin_history_limit_spin, config.get_pin_history_limit(),
        PIN_HISTORY_RANGE, 1,
    )

    restore_card = SwitchSettingCard(
        FluentIcon.HISTORY,
        dialog.tr("Restore Pins on Startup"),
        dialog.tr(
            "Save the pinned images you leave open, and bring them back the next time "
            "jietuba starts."
        ),
        parent=grp_new,
    )
    restore_card.setChecked(config.get_pin_restore_on_startup())
    dialog.pin_restore_toggle = restore_card
    grp_new.addSettingCard(restore_card)

    dialog.pin_text_font_size_spin = SpinBox()
    _number_card(
        grp_new, FluentIcon.FONT,
        dialog.tr("Text Pin Font Size"),
        dialog.tr("Font size used when turning clipboard text into a pinned image."),
        dialog.pin_text_font_size_spin, config.get_pin_text_font_size(),
        TEXT_FONT_SIZE_RANGE, 1,
    )

    dialog.pin_text_max_width_spin = SpinBox()
    _number_card(
        grp_new, FluentIcon.FONT,
        dialog.tr("Text Pin Max Width"),
        dialog.tr("Content width limit; longer text wraps like a note card."),
        dialog.pin_text_max_width_spin, config.get_pin_text_max_width(),
        TEXT_MAX_WIDTH_RANGE, 10,
    )

    confirm_card = SwitchSettingCard(
        FluentIcon.INFO,
        dialog.tr("Confirm Before Closing"),
        dialog.tr(
            "Ask before closing a pinned image. Closing all pins at once and quitting "
            "never ask."
        ),
        parent=grp_new,
    )
    confirm_card.setChecked(config.get_pin_close_confirm())
    dialog.pin_close_confirm_toggle = confirm_card
    grp_new.addSettingCard(confirm_card)

    layout.addWidget(grp_new)

    # ── 贴图窗口鼠标动作 ──────────────────────────────
    # 手势词汇是固定的（贴图窗口能收到的事件就这几种），所以这里不做「加行/删行」，
    # 每个手势一个下拉。动作清单来自 pin/pin_actions.py。
    grp_actions = SettingCardGroup(dialog.tr("Pin Mouse Actions"), view)
    dialog.pin_gesture_combos = {}
    gesture_table = pin_actions.resolve(config)

    for gesture, title, description in (
        (pin_actions.GESTURE_WHEEL_UP, dialog.tr("Wheel Up"),
         dialog.tr("Rolling the wheel up on a pinned image.")),
        (pin_actions.GESTURE_WHEEL_DOWN, dialog.tr("Wheel Down"),
         dialog.tr("Rolling the wheel down on a pinned image.")),
        (pin_actions.GESTURE_CTRL_WHEEL_UP, dialog.tr("Ctrl + Wheel Up"),
         dialog.tr("Ctrl + wheel up; adjusts opacity by default.")),
        (pin_actions.GESTURE_CTRL_WHEEL_DOWN, dialog.tr("Ctrl + Wheel Down"),
         dialog.tr("Ctrl + wheel down; adjusts opacity by default.")),
        (pin_actions.GESTURE_MIDDLE_CLICK, dialog.tr("Middle Click"),
         dialog.tr("Pressing the middle button on a pinned image.")),
        (pin_actions.GESTURE_DOUBLE_CLICK, dialog.tr("Double Click"),
         dialog.tr("Double-clicking a pinned image.")),
        (pin_actions.GESTURE_RIGHT_CLICK, dialog.tr("Right Click"),
         dialog.tr("Right-clicking a pinned image; opens the menu by default.")),
    ):
        card = FSettingCard(FluentIcon.SETTING, title, description, parent=grp_actions)
        combo = ComboBox(card)
        combo.setFixedWidth(200)
        combo.addItem(dialog.tr("Do nothing"), userData=pin_actions.ACTION_NONE)
        for action in pin_actions.PIN_ACTIONS:
            combo.addItem(dialog.tr(action.label), userData=action.id)
        card.hBoxLayout.addWidget(combo, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(16)
        index = combo.findData(gesture_table.get(gesture, pin_actions.ACTION_NONE))
        if index >= 0:
            combo.setCurrentIndex(index)
        grp_actions.addSettingCard(card)
        dialog.pin_gesture_combos[gesture] = combo

    layout.addWidget(grp_actions)

    hint = CaptionLabel(
        dialog.tr(
            "💡 Values apply to pinned windows created from now on; changes to wheel "
            "steps apply immediately."
        ),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    apply_theme_text_style(hint, 12, caption=True)
    layout.addWidget(hint)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll


def collect_pin_settings(dialog) -> dict:
    """把界面上的值读成 ``{访问器后缀: 值}``，供对话框保存时统一写入。"""
    values = {}
    if hasattr(dialog, "pin_zoom_step_spin"):
        values["zoom_step"] = dialog.pin_zoom_step_spin.value()
    if hasattr(dialog, "pin_opacity_step_spin"):
        values["opacity_step"] = dialog.pin_opacity_step_spin.value()
    if hasattr(dialog, "pin_default_opacity_spin"):
        values["default_opacity"] = dialog.pin_default_opacity_spin.value()
    if hasattr(dialog, "pin_shadow_toggle"):
        values["shadow_enabled"] = dialog.pin_shadow_toggle.isChecked()
    if hasattr(dialog, "pin_auto_toolbar_toggle"):
        values["auto_toolbar"] = dialog.pin_auto_toolbar_toggle.isChecked()
    if hasattr(dialog, "pin_new_position_combo"):
        values["new_position"] = dialog.pin_new_position_combo.currentData()
    if hasattr(dialog, "pin_order_combo"):
        values["order"] = dialog.pin_order_combo.currentData()
    if hasattr(dialog, "pin_close_confirm_toggle"):
        values["close_confirm"] = dialog.pin_close_confirm_toggle.isChecked()
    if hasattr(dialog, "pin_restore_toggle"):
        values["restore_on_startup"] = dialog.pin_restore_toggle.isChecked()
    if hasattr(dialog, "pin_history_limit_spin"):
        values["history_limit"] = dialog.pin_history_limit_spin.value()
    if hasattr(dialog, "pin_text_font_size_spin"):
        values["text_font_size"] = dialog.pin_text_font_size_spin.value()
    if hasattr(dialog, "pin_text_max_width_spin"):
        values["text_max_width"] = dialog.pin_text_max_width_spin.value()
    if hasattr(dialog, "pin_gesture_combos"):
        values["mouse_actions"] = {
            gesture: combo.currentData()
            for gesture, combo in dialog.pin_gesture_combos.items()
            if combo.currentData()
        }
    return values


def refresh_pin_page(dialog) -> None:
    """按配置刷新页面上的值（语言切换/外部改动后）。"""
    config = dialog.config_manager
    if hasattr(dialog, "pin_zoom_step_spin"):
        dialog.pin_zoom_step_spin.setValue(config.get_pin_zoom_step())
    if hasattr(dialog, "pin_opacity_step_spin"):
        dialog.pin_opacity_step_spin.setValue(config.get_pin_opacity_step())
    if hasattr(dialog, "pin_default_opacity_spin"):
        dialog.pin_default_opacity_spin.setValue(config.get_pin_default_opacity())
    if hasattr(dialog, "pin_shadow_toggle"):
        dialog.pin_shadow_toggle.setChecked(config.get_pin_shadow_enabled())
    if hasattr(dialog, "pin_auto_toolbar_toggle"):
        dialog.pin_auto_toolbar_toggle.setChecked(config.get_pin_auto_toolbar())
    if hasattr(dialog, "pin_new_position_combo"):
        index = dialog.pin_new_position_combo.findData(config.get_pin_new_position())
        if index >= 0:
            dialog.pin_new_position_combo.setCurrentIndex(index)
    if hasattr(dialog, "pin_order_combo"):
        index = dialog.pin_order_combo.findData(config.get_pin_order())
        if index >= 0:
            dialog.pin_order_combo.setCurrentIndex(index)
    if hasattr(dialog, "pin_close_confirm_toggle"):
        dialog.pin_close_confirm_toggle.setChecked(config.get_pin_close_confirm())
    if hasattr(dialog, "pin_restore_toggle"):
        dialog.pin_restore_toggle.setChecked(config.get_pin_restore_on_startup())
    if hasattr(dialog, "pin_history_limit_spin"):
        dialog.pin_history_limit_spin.setValue(config.get_pin_history_limit())
    if hasattr(dialog, "pin_text_font_size_spin"):
        dialog.pin_text_font_size_spin.setValue(config.get_pin_text_font_size())
    if hasattr(dialog, "pin_text_max_width_spin"):
        dialog.pin_text_max_width_spin.setValue(config.get_pin_text_max_width())
    _apply_gesture_table(dialog, pin_actions.resolve(config))


def _apply_gesture_table(dialog, table) -> None:
    """把 ``{手势: 动作}`` 写回下拉框。"""
    for gesture, combo in getattr(dialog, "pin_gesture_combos", {}).items():
        index = combo.findData(table.get(gesture, pin_actions.ACTION_NONE))
        if index >= 0:
            combo.setCurrentIndex(index)


def reset_pin_page(dialog) -> None:
    """恢复成默认值（数值与开关都回默认）。"""
    defaults = dialog.config_manager.APP_DEFAULT_SETTINGS
    if hasattr(dialog, "pin_zoom_step_spin"):
        dialog.pin_zoom_step_spin.setValue(defaults["pin_zoom_step"])
    if hasattr(dialog, "pin_opacity_step_spin"):
        dialog.pin_opacity_step_spin.setValue(defaults["pin_opacity_step"])
    if hasattr(dialog, "pin_default_opacity_spin"):
        dialog.pin_default_opacity_spin.setValue(defaults["pin_default_opacity"])
    if hasattr(dialog, "pin_shadow_toggle"):
        dialog.pin_shadow_toggle.setChecked(defaults["pin_shadow_enabled"])
    if hasattr(dialog, "pin_auto_toolbar_toggle"):
        dialog.pin_auto_toolbar_toggle.setChecked(defaults["pin_auto_toolbar"])
    if hasattr(dialog, "pin_new_position_combo"):
        index = dialog.pin_new_position_combo.findData(defaults["pin_new_position"])
        if index >= 0:
            dialog.pin_new_position_combo.setCurrentIndex(index)
    if hasattr(dialog, "pin_order_combo"):
        index = dialog.pin_order_combo.findData(defaults["pin_order"])
        if index >= 0:
            dialog.pin_order_combo.setCurrentIndex(index)
    if hasattr(dialog, "pin_close_confirm_toggle"):
        dialog.pin_close_confirm_toggle.setChecked(defaults["pin_close_confirm"])
    if hasattr(dialog, "pin_restore_toggle"):
        dialog.pin_restore_toggle.setChecked(defaults["pin_restore_on_startup"])
    if hasattr(dialog, "pin_history_limit_spin"):
        dialog.pin_history_limit_spin.setValue(defaults["pin_history_limit"])
    if hasattr(dialog, "pin_text_font_size_spin"):
        dialog.pin_text_font_size_spin.setValue(defaults["pin_text_font_size"])
    if hasattr(dialog, "pin_text_max_width_spin"):
        dialog.pin_text_max_width_spin.setValue(defaults["pin_text_max_width"])
    _apply_gesture_table(dialog, pin_actions.DEFAULT_PIN_MOUSE_ACTIONS)
