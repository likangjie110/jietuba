# -*- coding: utf-8 -*-
"""截图设置页 — Fluent Design"""
import importlib.util

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QCheckBox, QLineEdit,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

from core.logger import T, log_warning
from ui.fluent_lite import (
    SwitchSettingCard, SettingCard as FSettingCard,
    FluentIcon, ComboBox, SpinBox, DoubleSpinBox, CaptionLabel,
    PushButton, LineEdit,
)
from .components import SettingCardGroup, WhiteCard, apply_theme_text_style
from .page_appearance import _make_color_btn, _update_color_btn
from .page_misc import _select_combo



def _save_paths_summary(dialog) -> str:
    """保存路径卡片右侧的一行摘要：几条 + 第一条。"""
    try:
        paths = dialog.config_manager.get_save_paths()
    except Exception:
        return ""
    if not paths:
        return ""
    first = str(paths[0].get("path", ""))
    if len(paths) == 1:
        return first
    return f"{first}  (+{len(paths) - 1})"


def _open_save_paths(dialog) -> None:
    """打开保存路径管理器；关掉之后刷新卡片摘要。"""
    from ui.save_paths_dialog import open_save_paths_dialog

    window = open_save_paths_dialog(dialog.config_manager, dialog)
    window.destroyed.connect(lambda *_args: _refresh_save_paths(dialog))


def _refresh_save_paths(dialog) -> None:
    if hasattr(dialog, "save_paths_label"):
        try:
            dialog.save_paths_label.setText(_save_paths_summary(dialog))
        except Exception as e:
            from core.logger import log_exception

            log_exception(e, T("刷新保存路径摘要"))


def _build_ocr_engine_rows(dialog, group) -> None:
    """OCR 引擎组新增两项：模型档位（只列本机真的有的）与视觉模型（转 Markdown/HTML）。

    档位下拉**按文件是否存在现算**：没放的模型不出现，避免用户选到一个点了就报错的档位。
    """
    from ocr.model_tiers import available_tiers
    from ocr.vision_models import TASKS, load_models, selected_model

    tier_card = FSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("OCR Model"),
        dialog.tr("Which PP-OCR model pair to load (only installed tiers are listed)."),
        parent=group,
    )
    dialog.ocr_tier_combo = ComboBox(tier_card)
    dialog.ocr_tier_combo.setFixedWidth(200)
    tiers = available_tiers()
    for tier_id, label in tiers:
        dialog.ocr_tier_combo.addItem(label, userData=tier_id)
    if tiers:
        _select_combo(dialog.ocr_tier_combo, dialog.config_manager.get_ocr_model_tier())
    else:
        dialog.ocr_tier_combo.addItem(dialog.tr("No OCR model found"), userData="")
        dialog.ocr_tier_combo.setEnabled(False)
    tier_card.hBoxLayout.addWidget(dialog.ocr_tier_combo, 0, Qt.AlignmentFlag.AlignRight)
    tier_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(tier_card)

    vision_card = FSettingCard(
        FluentIcon.DOCUMENT,
        dialog.tr("Vision Model"),
        dialog.tr("OpenAI-compatible endpoint that converts an image to Markdown or HTML."),
        parent=group,
    )
    dialog.ocr_vision_combo = ComboBox(vision_card)
    dialog.ocr_vision_combo.setFixedWidth(180)
    for model in load_models(dialog.config_manager):
        dialog.ocr_vision_combo.addItem(model.name, userData=model.name)
    current = selected_model(dialog.config_manager)
    if current is not None:
        index = dialog.ocr_vision_combo.findData(current.name)
        if index >= 0:
            dialog.ocr_vision_combo.setCurrentIndex(index)
    dialog.ocr_vision_url_input = LineEdit(vision_card)
    dialog.ocr_vision_url_input.setFixedWidth(220)
    dialog.ocr_vision_url_input.setPlaceholderText("https://api.example.com/v1")
    dialog.ocr_vision_model_input = LineEdit(vision_card)
    dialog.ocr_vision_model_input.setFixedWidth(160)
    dialog.ocr_vision_model_input.setPlaceholderText("gpt-4o-mini")
    dialog.ocr_vision_key_input = LineEdit(vision_card)
    dialog.ocr_vision_key_input.setFixedWidth(160)
    dialog.ocr_vision_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    dialog.ocr_vision_protocol_combo = ComboBox(vision_card)
    dialog.ocr_vision_protocol_combo.setFixedWidth(140)
    _VISION_PROTOCOLS = (
        ("openai", dialog.tr("OpenAI compatible")),
        ("azure", dialog.tr("Azure OpenAI")),
        ("anthropic", dialog.tr("Anthropic Claude")),
        ("gemini", dialog.tr("Google Gemini")),
    )
    for protocol_id, label in _VISION_PROTOCOLS:
        dialog.ocr_vision_protocol_combo.addItem(label, userData=protocol_id)
    dialog.ocr_vision_version_input = LineEdit(vision_card)
    dialog.ocr_vision_version_input.setFixedWidth(120)
    dialog.ocr_vision_version_input.setPlaceholderText(dialog.tr("API version"))
    if current is not None:
        dialog.ocr_vision_url_input.setText(current.base_url)
        dialog.ocr_vision_model_input.setText(current.model_id)
        dialog.ocr_vision_key_input.setText(current.api_key)
        _select_combo(dialog.ocr_vision_protocol_combo, current.protocol_id)
        dialog.ocr_vision_version_input.setText(current.api_version)
    else:
        _select_combo(dialog.ocr_vision_protocol_combo, "openai")
    save_btn = PushButton(dialog.tr("Save"), vision_card)
    save_btn.clicked.connect(lambda: _save_vision_model(dialog))
    for widget in (dialog.ocr_vision_combo, dialog.ocr_vision_protocol_combo,
                   dialog.ocr_vision_url_input, dialog.ocr_vision_model_input,
                   dialog.ocr_vision_key_input, dialog.ocr_vision_version_input, save_btn):
        vision_card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        vision_card.hBoxLayout.addSpacing(6)
    group.addSettingCard(vision_card)

    target_card = FSettingCard(
        FluentIcon.EDIT,
        dialog.tr("Image Conversion Output"),
        dialog.tr("Convert images to Markdown (tables) or HTML."),
        parent=group,
    )
    dialog.ocr_vision_target_combo = ComboBox(target_card)
    dialog.ocr_vision_target_combo.setFixedWidth(150)
    dialog.ocr_vision_target_combo.addItem(dialog.tr("Markdown"), userData="markdown")
    dialog.ocr_vision_target_combo.addItem(dialog.tr("HTML"), userData="html")
    _select_combo(dialog.ocr_vision_target_combo, dialog.config_manager.get_ocr_vision_target())
    target_card.hBoxLayout.addWidget(
        dialog.ocr_vision_target_combo, 0, Qt.AlignmentFlag.AlignRight)
    target_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(target_card)

    task_card = FSettingCard(
        FluentIcon.EDIT,
        dialog.tr("Task template"),
        dialog.tr("What the vision model is asked to do with the image."),
        parent=group,
    )
    dialog.ocr_vision_task_combo = ComboBox(task_card)
    dialog.ocr_vision_task_combo.setFixedWidth(180)
    for task in TASKS:
        dialog.ocr_vision_task_combo.addItem(dialog.tr(task.label), userData=task.id)
    _select_combo(dialog.ocr_vision_task_combo, dialog.config_manager.get_ocr_vision_task())
    task_card.hBoxLayout.addWidget(
        dialog.ocr_vision_task_combo, 0, Qt.AlignmentFlag.AlignRight)
    task_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(task_card)

    threshold_card = FSettingCard(
        FluentIcon.EDIT,
        dialog.tr("Low confidence hint"),
        dialog.tr("Suggests the vision model when local OCR confidence drops below this."),
        parent=group,
    )
    dialog.ocr_confidence_spin = DoubleSpinBox(threshold_card)
    dialog.ocr_confidence_spin.setRange(0.05, 1.0)
    dialog.ocr_confidence_spin.setSingleStep(0.05)
    dialog.ocr_confidence_spin.setDecimals(2)
    dialog.ocr_confidence_spin.setFixedWidth(120)
    dialog.ocr_confidence_spin.setValue(
        float(dialog.config_manager.get_ocr_low_confidence_threshold()))
    threshold_card.hBoxLayout.addWidget(
        dialog.ocr_confidence_spin, 0, Qt.AlignmentFlag.AlignRight)
    threshold_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(threshold_card)


def _save_vision_model(dialog) -> bool:
    """把当前输入存的视觉模型保存下来（同名覆盖）。"""
    from ocr.vision_models import VisionModel, upsert_model

    name = dialog.ocr_vision_combo.currentData() or dialog.ocr_vision_combo.currentText()
    name = str(name or "").strip()
    url = dialog.ocr_vision_url_input.text().strip()
    model_id = dialog.ocr_vision_model_input.text().strip()
    if not name or not url or not model_id:
        log_warning(T("视觉模型需要名称、地址与模型 ID"), "SettingsDialog")
        return False
    upsert_model(dialog.config_manager, VisionModel(
        name=name, base_url=url, model_id=model_id,
        api_key=dialog.ocr_vision_key_input.text().strip(),
        protocol=str(dialog.ocr_vision_protocol_combo.currentData() or "openai"),
        api_version=dialog.ocr_vision_version_input.text().strip()))
    dialog.config_manager.set_app_setting("ocr_vision_model", name)
    index = dialog.ocr_vision_combo.findData(name)
    if index < 0:
        dialog.ocr_vision_combo.addItem(name, userData=name)
        index = dialog.ocr_vision_combo.findData(name)
    dialog.ocr_vision_combo.setCurrentIndex(max(0, index))
    return True


def create_capture_page(dialog) -> QWidget:
    """截图設定 ─ 交互行为 + 智能选区 + 保存设置 + OCR"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ── 截图交互 ──────────────────────────────────────
    grp_behavior = SettingCardGroup(dialog.tr("Capture Behavior"), view)

    double_click_card = SwitchSettingCard(
        FluentIcon.CAMERA,
        dialog.tr("Double-click to Copy and Close"),
        dialog.tr(
            "Double-click the selected screenshot to copy it to the clipboard and close the capture."
        ),
        parent=grp_behavior,
    )
    double_click_card.setChecked(
        dialog.config_manager.get_double_click_copy_close_enabled()
    )
    dialog.double_click_copy_close_toggle = double_click_card
    grp_behavior.addSettingCard(double_click_card)

    cross_tool_card = SwitchSettingCard(
        FluentIcon.EDIT,
        dialog.tr("Enable Ctrl Cross-Tool Selection"),
        dialog.tr(
            "Hold Ctrl and click any editable annotation to adjust it without switching tools."
        ),
        parent=grp_behavior,
    )
    cross_tool_card.setChecked(
        dialog.config_manager.get_cross_tool_selection_enabled()
    )
    dialog.cross_tool_selection_toggle = cross_tool_card
    grp_behavior.addSettingCard(cross_tool_card)

    text_top_card = SwitchSettingCard(
        FluentIcon.FONT,
        dialog.tr("Keep Text Annotations on Top"),
        dialog.tr(
            "Keep text above other annotations, including ones drawn later."
        ),
        parent=grp_behavior,
    )
    text_top_card.setChecked(
        dialog.config_manager.get_text_always_on_top_enabled()
    )
    dialog.text_always_on_top_toggle = text_top_card
    grp_behavior.addSettingCard(text_top_card)

    layout.addWidget(grp_behavior)

    # ── UI 检测 ──────────────────────────────────────
    from core.platform.window import (
        MAX_ELEMENT_MARGIN, UI_DETECTION_ELEMENT, UI_DETECTION_NONE, UI_DETECTION_WINDOW,
    )

    grp_smart = SettingCardGroup(dialog.tr("UI Detection"), view)

    smart_card = FSettingCard(
        FluentIcon.CAMERA,
        dialog.tr("UI Detection"),
        dialog.tr("Automatically frames the window or control under the mouse cursor."),
        parent=grp_smart,
    )
    dialog.ui_detection_combo = ComboBox(smart_card)
    dialog.ui_detection_combo.setFixedWidth(160)
    dialog.ui_detection_combo.addItem(dialog.tr("None"), userData=UI_DETECTION_NONE)
    dialog.ui_detection_combo.addItem(dialog.tr("Window only"), userData=UI_DETECTION_WINDOW)
    dialog.ui_detection_combo.addItem(dialog.tr("Elements"), userData=UI_DETECTION_ELEMENT)
    current_detection = dialog.config_manager.get_ui_detection()
    current_index = dialog.ui_detection_combo.findData(current_detection)
    if current_index >= 0:
        dialog.ui_detection_combo.setCurrentIndex(current_index)
    smart_card.hBoxLayout.addWidget(
        dialog.ui_detection_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    smart_card.hBoxLayout.addSpacing(16)
    grp_smart.addSettingCard(smart_card)

    # 元素检测边距：只在「检测元素」档有意义，其它档禁用（灰掉比隐藏更容易理解）
    margin_card = FSettingCard(
        FluentIcon.LAYOUT,
        dialog.tr("Element Margin"),
        dialog.tr("Expands the detected element by this many pixels."),
        parent=grp_smart,
    )
    dialog.ui_detection_margin_spin = SpinBox(margin_card)
    dialog.ui_detection_margin_spin.setMinimum(0)
    dialog.ui_detection_margin_spin.setMaximum(MAX_ELEMENT_MARGIN)
    dialog.ui_detection_margin_spin.setFixedWidth(120)
    dialog.ui_detection_margin_spin.setValue(dialog.config_manager.get_ui_detection_margin())
    margin_card.hBoxLayout.addWidget(
        dialog.ui_detection_margin_spin, 0, Qt.AlignmentFlag.AlignRight
    )
    margin_card.hBoxLayout.addSpacing(16)
    grp_smart.addSettingCard(margin_card)

    def _sync_margin_enabled():
        dialog.ui_detection_margin_spin.setEnabled(
            dialog.ui_detection_combo.currentData() == UI_DETECTION_ELEMENT
        )

    dialog.ui_detection_combo.currentIndexChanged.connect(_sync_margin_enabled)
    _sync_margin_enabled()

    layout.addWidget(grp_smart)

    # ── 截图保存 ──────────────────────────────────────
    grp_save = SettingCardGroup(dialog.tr("Save Settings"), view)

    save_card = SwitchSettingCard(
        FluentIcon.SAVE,
        dialog.tr("Auto-save Screenshots"),
        dialog.tr("Automatically saves as file when capturing."),
        parent=grp_save,
    )
    save_card.setChecked(dialog.config_manager.get_screenshot_save_enabled())
    dialog.save_toggle = save_card
    grp_save.addSettingCard(save_card)

    # 保存路径（卡片）
    path_card = WhiteCard(grp_save)
    path_h = QHBoxLayout(path_card)
    path_h.setContentsMargins(20, 12, 20, 12)
    path_h.setSpacing(12)

    path_icon_lbl = QLabel(dialog.tr("Save Folder:"), path_card)
    apply_theme_text_style(path_icon_lbl, 14)
    dialog.save_path_lbl = QLabel(dialog.config_manager.get_screenshot_save_path(), path_card)
    dialog.save_path_lbl.setWordWrap(True)
    dialog.save_path_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    apply_theme_text_style(dialog.save_path_lbl, 12, caption=True)

    btn_change = PushButton(dialog.tr("Change"), path_card)
    btn_change.setFixedHeight(32)
    btn_change.clicked.connect(dialog._change_save_dir)
    btn_open = PushButton(dialog.tr("Open"), path_card)
    btn_open.setFixedHeight(32)
    btn_open.clicked.connect(dialog._open_save_dir)

    path_h.addWidget(path_icon_lbl)
    path_h.addWidget(dialog.save_path_lbl, 1)
    path_h.addWidget(btn_change)
    path_h.addWidget(btn_open)
    path_card.setFixedHeight(58)
    grp_save.addSettingCard(path_card)

    # 保存格式
    fmt_card = FSettingCard(
        FluentIcon.DOCUMENT,
        dialog.tr("Save Format"),
        dialog.tr("File format for auto-saved screenshots."),
        parent=grp_save,
    )
    # 格式清单由运行时能力派生（见 core/image_formats.py）：本构建没有编码器的格式
    # 不会出现在这里——列出来却写不出去，比不给选更糟
    from core.image_formats import available_formats, preferred_format

    dialog.screenshot_format_combo = ComboBox(fmt_card)
    for fmt, _filter in available_formats():
        dialog.screenshot_format_combo.addItem(fmt if fmt != "JPG" else "JPG",
                                               userData=fmt)
    dialog.screenshot_format_combo.setFixedWidth(110)
    _select_combo(dialog.screenshot_format_combo,
                  preferred_format(dialog.config_manager.get_screenshot_format()))
    fmt_card.hBoxLayout.addWidget(
        dialog.screenshot_format_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    fmt_card.hBoxLayout.addSpacing(16)
    grp_save.addSettingCard(fmt_card)

    # 多保存路径：一次截图同时落到几个目录（每条自带格式与质量）
    paths_card = FSettingCard(
        FluentIcon.FOLDER,
        dialog.tr("Save Paths"),
        dialog.tr("Save every screenshot into one or more folders, each with its own format."),
        parent=grp_save,
    )
    dialog.save_paths_label = CaptionLabel(_save_paths_summary(dialog), paths_card)
    manage_btn = PushButton(dialog.tr("Manage…"), paths_card)
    manage_btn.clicked.connect(lambda: _open_save_paths(dialog))
    for widget in (dialog.save_paths_label, manage_btn):
        paths_card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        paths_card.hBoxLayout.addSpacing(8)
    grp_save.addSettingCard(paths_card)

    page_card = FSettingCard(
        FluentIcon.DOCUMENT,
        dialog.tr("PDF Page Size"),
        dialog.tr("Page size used when saving as PDF."),
        parent=grp_save,
    )
    dialog.pdf_page_combo = ComboBox(page_card)
    dialog.pdf_page_combo.setFixedWidth(150)
    dialog.pdf_page_combo.addItem(dialog.tr("Original size"), userData="original")
    dialog.pdf_page_combo.addItem(dialog.tr("A4 portrait"), userData="a4_portrait")
    dialog.pdf_page_combo.addItem(dialog.tr("A4 landscape"), userData="a4_landscape")
    _select_combo(dialog.pdf_page_combo, dialog.config_manager.get_pdf_page_size())
    page_card.hBoxLayout.addWidget(dialog.pdf_page_combo, 0, Qt.AlignmentFlag.AlignRight)
    page_card.hBoxLayout.addSpacing(16)
    grp_save.addSettingCard(page_card)

    layout.addWidget(grp_save)

    # ── OCR ───────────────────────────────────────────
    # ── 截图外观（圆角 / 描边阴影）────────────────────
    # 这些键以前只能在截图窗口里的浮层面板改，这里给一处固定入口；读写的是同一份
    # 应用设置（app/screenshot_*），不会出现两份值。
    grp_look = SettingCardGroup(dialog.tr("Screenshot Appearance"), view)

    rounded_card = SwitchSettingCard(
        FluentIcon.LAYOUT,
        dialog.tr("Rounded Corners"),
        dialog.tr("Rounds the corners of the captured image."),
        parent=grp_look,
    )
    rounded_card.setChecked(bool(dialog.config_manager.get_app_setting(
        "screenshot_rounded_enabled", False)))
    dialog.screenshot_rounded_toggle = rounded_card
    grp_look.addSettingCard(rounded_card)

    radius_card = FSettingCard(
        FluentIcon.ALIGNMENT,
        dialog.tr("Corner Radius"),
        dialog.tr("How round the corners are (0-100)."),
        parent=grp_look,
    )
    dialog.screenshot_radius_spin = SpinBox(radius_card)
    dialog.screenshot_radius_spin.setRange(0, 100)
    dialog.screenshot_radius_spin.setFixedWidth(120)
    dialog.screenshot_radius_spin.setValue(int(dialog.config_manager.get_app_setting(
        "screenshot_rounded_radius", 16)))
    radius_card.hBoxLayout.addWidget(dialog.screenshot_radius_spin, 0,
                                     Qt.AlignmentFlag.AlignRight)
    radius_card.hBoxLayout.addSpacing(16)
    grp_look.addSettingCard(radius_card)

    border_card = SwitchSettingCard(
        FluentIcon.TRANSPARENT,
        dialog.tr("Border or Shadow"),
        dialog.tr("Draws a border or a drop shadow around the captured image."),
        parent=grp_look,
    )
    border_card.setChecked(bool(dialog.config_manager.get_app_setting(
        "screenshot_border_enabled", False)))
    dialog.screenshot_border_toggle = border_card
    grp_look.addSettingCard(border_card)

    mode_card = FSettingCard(
        FluentIcon.ALIGNMENT,
        dialog.tr("Border Style"),
        dialog.tr("Border draws a solid frame; shadow softens the edges."),
        parent=grp_look,
    )
    dialog.screenshot_border_mode_combo = ComboBox(mode_card)
    dialog.screenshot_border_mode_combo.setFixedWidth(140)
    dialog.screenshot_border_mode_combo.addItem(dialog.tr("Shadow"), userData="shadow")
    dialog.screenshot_border_mode_combo.addItem(dialog.tr("Border"), userData="border")
    current_mode = str(dialog.config_manager.get_app_setting(
        "screenshot_border_mode", "shadow") or "shadow")
    index = dialog.screenshot_border_mode_combo.findData(current_mode)
    dialog.screenshot_border_mode_combo.setCurrentIndex(max(0, index))
    mode_card.hBoxLayout.addWidget(dialog.screenshot_border_mode_combo, 0,
                                   Qt.AlignmentFlag.AlignRight)
    mode_card.hBoxLayout.addSpacing(16)
    grp_look.addSettingCard(mode_card)

    border_size_card = FSettingCard(
        FluentIcon.FONT_SIZE,
        dialog.tr("Border or Shadow Size"),
        dialog.tr("Thickness of the border, or how far the shadow spreads (1-50)."),
        parent=grp_look,
    )
    dialog.screenshot_border_size_spin = SpinBox(border_size_card)
    dialog.screenshot_border_size_spin.setRange(1, 50)
    dialog.screenshot_border_size_spin.setFixedWidth(120)
    dialog.screenshot_border_size_spin.setValue(int(dialog.config_manager.get_app_setting(
        "screenshot_border_size", 21)))
    border_size_card.hBoxLayout.addWidget(dialog.screenshot_border_size_spin, 0,
                                          Qt.AlignmentFlag.AlignRight)
    border_size_card.hBoxLayout.addSpacing(16)
    grp_look.addSettingCard(border_size_card)

    dialog.screenshot_border_color_btn = _make_color_btn(border_size_card)
    dialog._screenshot_border_color = QColor(str(dialog.config_manager.get_app_setting(
        "screenshot_border_color", "#FF0000")))
    _update_color_btn(dialog.screenshot_border_color_btn, dialog._screenshot_border_color)
    color_card = FSettingCard(
        FluentIcon.PALETTE,
        dialog.tr("Border Color"),
        dialog.tr("Color used when the style is Border."),
        parent=grp_look,
    )
    color_card.hBoxLayout.addWidget(dialog.screenshot_border_color_btn, 0,
                                    Qt.AlignmentFlag.AlignRight)
    color_card.hBoxLayout.addSpacing(16)

    def _pick_border_color():
        picked = QColorDialog(dialog._screenshot_border_color, None)
        picked.setWindowTitle(dialog.tr("Pick a color"))
        if picked.exec():
            chosen = picked.currentColor()
            dialog._screenshot_border_color.setRgb(chosen.red(), chosen.green(), chosen.blue())
            _update_color_btn(dialog.screenshot_border_color_btn, dialog._screenshot_border_color)

    dialog.screenshot_border_color_btn.clicked.connect(_pick_border_color)
    grp_look.addSettingCard(color_card)

    dialog.screenshot_shadow_color_btn = _make_color_btn(color_card)
    dialog._screenshot_shadow_color = QColor(str(dialog.config_manager.get_app_setting(
        "screenshot_shadow_color", "#0078FF")))
    _update_color_btn(dialog.screenshot_shadow_color_btn, dialog._screenshot_shadow_color)
    shadow_color_card = FSettingCard(
        FluentIcon.PALETTE,
        dialog.tr("Shadow Color"),
        dialog.tr("Color used when the style is Shadow."),
        parent=grp_look,
    )
    shadow_color_card.hBoxLayout.addWidget(dialog.screenshot_shadow_color_btn, 0,
                                           Qt.AlignmentFlag.AlignRight)
    shadow_color_card.hBoxLayout.addSpacing(16)

    def _pick_shadow_color():
        picked = QColorDialog(dialog._screenshot_shadow_color, None)
        picked.setWindowTitle(dialog.tr("Pick a color"))
        if picked.exec():
            chosen = picked.currentColor()
            dialog._screenshot_shadow_color.setRgb(chosen.red(), chosen.green(), chosen.blue())
            _update_color_btn(dialog.screenshot_shadow_color_btn, dialog._screenshot_shadow_color)

    dialog.screenshot_shadow_color_btn.clicked.connect(_pick_shadow_color)
    grp_look.addSettingCard(shadow_color_card)

    persist_card = SwitchSettingCard(
        FluentIcon.SYNC,
        dialog.tr("Keep the Style On"),
        dialog.tr("Keeps border/shadow enabled for every new capture."),
        parent=grp_look,
    )
    persist_card.setChecked(bool(dialog.config_manager.get_app_setting(
        "screenshot_border_persist", False)))
    dialog.screenshot_border_persist_toggle = persist_card
    grp_look.addSettingCard(persist_card)

    layout.addWidget(grp_look)

    # ── 放大镜 ────────────────────────────────────────
    grp_magnifier = SettingCardGroup(dialog.tr("Magnifier"), view)

    zoom_card = FSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Default Zoom"),
        dialog.tr("Starting magnification of the magnifier (1-10)."),
        parent=grp_magnifier,
    )
    dialog.magnifier_zoom_spin = DoubleSpinBox(zoom_card)
    dialog.magnifier_zoom_spin.setRange(1.0, 10.0)
    dialog.magnifier_zoom_spin.setSingleStep(0.5)
    dialog.magnifier_zoom_spin.setDecimals(1)
    dialog.magnifier_zoom_spin.setFixedWidth(120)
    dialog.magnifier_zoom_spin.setValue(float(dialog.config_manager.get_app_setting(
        "magnifier_zoom", 4.0)))
    zoom_card.hBoxLayout.addWidget(dialog.magnifier_zoom_spin, 0,
                                   Qt.AlignmentFlag.AlignRight)
    zoom_card.hBoxLayout.addSpacing(16)
    grp_magnifier.addSettingCard(zoom_card)

    layout.addWidget(grp_magnifier)

    grp_ocr = SettingCardGroup(dialog.tr("OCR"), view)

    # OCR 可用性检测：走 ocr 模块的官方多引擎检测（含 ppocr_rust / windows_media_ocr），
    # 而不是只看 windows_media_ocr —— 否则装了 ppocr_rust 也会误报“无 OCR 版本”。
    try:
        from ocr import is_ocr_available
        ocr_available = bool(is_ocr_available())
    except Exception:
        # 兜底：ocr 模块不可导入时，退回到最基础的探测
        ocr_available = (
            importlib.util.find_spec("ppocr_rust") is not None
            or importlib.util.find_spec("windows_media_ocr") is not None
        )
    ocr_card = SwitchSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Automatically recognize text after pinning"),
        dialog.tr(
            "On very low-end computers, OCR after pinning may cause a brief stutter. "
            "Turn off automatic recognition if needed."
        ),
        parent=grp_ocr,
    )
    ocr_card.setChecked(
        dialog.config_manager.get_ocr_enabled() if ocr_available else False
    )
    if not ocr_available:
        ocr_card.setEnabled(False)
        ocr_card.setChecked(False)
    dialog.ocr_enable_toggle = ocr_card
    grp_ocr.addSettingCard(ocr_card)

    if not ocr_available:
        no_ocr_card = FSettingCard(
            FluentIcon.INFO,
            dialog.tr("No OCR Version / OCR module not found"),
            parent=grp_ocr,
        )
        grp_ocr.addSettingCard(no_ocr_card)

    _build_ocr_options(dialog, grp_ocr)
    _build_ocr_engine_rows(dialog, grp_ocr)
    _build_formula_cards(dialog, grp_ocr)

    layout.addWidget(grp_ocr)
    # 提示
    hint = CaptionLabel(
        dialog.tr("💡 Hint: Even with auto-save off, it will be copied to clipboard."),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    layout.addWidget(hint)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll



def _build_ocr_options(dialog, group: SettingCardGroup) -> None:
    """识别结果的加工选项：文本布局、标点处理、识别语言、对话框时机。

    这几项只影响「识别出来的文字长什么样」，跟引擎选型无关，所以都挂在同一组里。
    """
    from .page_misc import _select_combo

    layout_card = FSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Text Layout"),
        dialog.tr("How recognized lines are put together."),
        parent=group,
    )
    dialog.ocr_layout_combo = ComboBox(layout_card)
    dialog.ocr_layout_combo.setFixedWidth(150)
    dialog.ocr_layout_combo.addItem(dialog.tr("Automatic"), userData="auto")
    dialog.ocr_layout_combo.addItem(dialog.tr("One Line per Block"), userData="lines")
    dialog.ocr_layout_combo.addItem(dialog.tr("Single Line"), userData="single")
    _select_combo(dialog.ocr_layout_combo, dialog.config_manager.get_ocr_text_layout())
    layout_card.hBoxLayout.addWidget(
        dialog.ocr_layout_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    layout_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(layout_card)

    punct_card = FSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Punctuation"),
        dialog.tr("Clean up the punctuation OCR guessed from image noise."),
        parent=group,
    )
    dialog.ocr_punctuation_combo = ComboBox(punct_card)
    dialog.ocr_punctuation_combo.setFixedWidth(150)
    dialog.ocr_punctuation_combo.addItem(dialog.tr("Do Nothing"), userData="none")
    dialog.ocr_punctuation_combo.addItem(dialog.tr("Strip Trailing"), userData="strip_trailing")
    dialog.ocr_punctuation_combo.addItem(dialog.tr("Full-width to Half-width"), userData="to_halfwidth")
    _select_combo(dialog.ocr_punctuation_combo,
                  dialog.config_manager.get_ocr_punctuation())
    punct_card.hBoxLayout.addWidget(
        dialog.ocr_punctuation_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    punct_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(punct_card)

    lang_card = FSettingCard(
        FluentIcon.LANGUAGE,
        dialog.tr("Recognition Language"),
        dialog.tr("Language passed to the OCR engine."),
        parent=group,
    )
    dialog.ocr_language_combo = ComboBox(lang_card)
    dialog.ocr_language_combo.setFixedWidth(150)
    dialog.ocr_language_combo.addItem(dialog.tr("Follow App"), userData="follow_app")
    dialog.ocr_language_combo.addItem(dialog.tr("Simplified Chinese"), userData="zh")
    dialog.ocr_language_combo.addItem(dialog.tr("English"), userData="en")
    dialog.ocr_language_combo.addItem(dialog.tr("Japanese"), userData="ja")
    dialog.ocr_language_combo.addItem(dialog.tr("Korean"), userData="ko")
    _select_combo(dialog.ocr_language_combo, dialog.config_manager.get_ocr_language())
    lang_card.hBoxLayout.addWidget(
        dialog.ocr_language_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    lang_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(lang_card)

    _build_dialog_trigger_row(dialog, group)


def _build_dialog_trigger_row(dialog, group: SettingCardGroup) -> None:
    """显示对话框时机：三个可勾的时机，勾中的才会额外弹出识别结果窗。"""
    card = FSettingCard(
        FluentIcon.INFO,
        dialog.tr("Show Result Dialog"),
        dialog.tr("When to pop up the recognized text (copying still happens either way)."),
        parent=group,
    )
    current = set(dialog.config_manager.get_ocr_dialog_triggers())
    dialog.ocr_dialog_checks = {}
    holder = QWidget(card)
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    for trigger, label in (("capture", dialog.tr("After Capture")),
                           ("copy_selection", dialog.tr("Copy Selection")),
                           ("copy_all", dialog.tr("Copy All Text"))):
        check = QCheckBox(label, holder)
        check.setChecked(trigger in current)
        row.addWidget(check)
        dialog.ocr_dialog_checks[trigger] = check
    card.hBoxLayout.addWidget(holder, 0, Qt.AlignmentFlag.AlignRight)
    card.hBoxLayout.addSpacing(16)
    group.addSettingCard(card)


def _build_formula_cards(dialog, group: SettingCardGroup) -> None:
    """公式识别：引擎选型（Rust 侧 PP-FormulaNet / 外部服务）+ 外部服务配置与验证。"""
    from .page_misc import _select_combo, _verify_formula_service

    engine_card = FSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Formula Recognition Engine"),
        dialog.tr("PP-FormulaNet runs locally through the Rust binding."),
        parent=group,
    )
    dialog.formula_engine_combo = ComboBox(engine_card)
    dialog.formula_engine_combo.setFixedWidth(200)
    dialog.formula_engine_combo.addItem(
        dialog.tr("PP-FormulaNet (Local, Rust)"), userData="ppocr_formula")
    dialog.formula_engine_combo.addItem(
        dialog.tr("External Service"), userData="external_service")
    _select_combo(dialog.formula_engine_combo, dialog.config_manager.get_formula_engine())
    engine_card.hBoxLayout.addWidget(
        dialog.formula_engine_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    engine_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(engine_card)

    service_card = FSettingCard(
        FluentIcon.LANGUAGE,
        dialog.tr("Formula Service URL"),
        dialog.tr("Only used by the external service engine."),
        parent=group,
    )
    config = dialog.config_manager.get_formula_service_config()
    dialog.formula_url_input = LineEdit(service_card)
    dialog.formula_url_input.setFixedWidth(220)
    dialog.formula_url_input.setPlaceholderText("https://example.com/formula")
    dialog.formula_url_input.setText(config["url"])
    dialog.formula_key_input = LineEdit(service_card)
    dialog.formula_key_input.setFixedWidth(140)
    dialog.formula_key_input.setPlaceholderText(dialog.tr("API Key"))
    dialog.formula_key_input.setText(config["api_key"])
    dialog.formula_verify_btn = PushButton(dialog.tr("Verify"), service_card)
    dialog.formula_verify_btn.clicked.connect(lambda: _verify_formula_service(dialog))
    for widget in (dialog.formula_url_input, dialog.formula_key_input,
                   dialog.formula_verify_btn):
        service_card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        service_card.hBoxLayout.addSpacing(8)
    group.addSettingCard(service_card)

    hint = CaptionLabel(
        dialog.tr("💡 Hint: the local engine needs a formula model in models/formula/; "
                  "without it, use an external service."),
        group,
    )
    hint.setStyleSheet("padding: 5px;")
    group.addSettingCard(hint)



def refresh_capture_appearance(dialog, defaults=None) -> None:
    """刷新「截图外观」与「放大镜」两组。

    传 ``defaults`` 时按给定的默认值刷新（「恢复默认」用），否则按当前配置读。
    """
    config = dialog.config_manager

    def value(key, fallback):
        if defaults is not None:
            return defaults.get(key, fallback)
        return config.get_app_setting(key, fallback)
    if hasattr(dialog, "screenshot_rounded_toggle"):
        dialog.screenshot_rounded_toggle.setChecked(
            bool(value("screenshot_rounded_enabled", False)))
    if hasattr(dialog, "screenshot_radius_spin"):
        dialog.screenshot_radius_spin.setValue(
            int(value("screenshot_rounded_radius", 16)))
    if hasattr(dialog, "screenshot_border_toggle"):
        dialog.screenshot_border_toggle.setChecked(
            bool(value("screenshot_border_enabled", False)))
    if hasattr(dialog, "screenshot_border_mode_combo"):
        index = dialog.screenshot_border_mode_combo.findData(
            str(value("screenshot_border_mode", "shadow")))
        dialog.screenshot_border_mode_combo.setCurrentIndex(max(0, index))
    if hasattr(dialog, "screenshot_border_size_spin"):
        dialog.screenshot_border_size_spin.setValue(
            int(value("screenshot_border_size", 21)))
    if hasattr(dialog, "_screenshot_border_color"):
        dialog._screenshot_border_color = QColor(
            str(value("screenshot_border_color", "#FF0000")))
        _update_color_btn(dialog.screenshot_border_color_btn, dialog._screenshot_border_color)
    if hasattr(dialog, "_screenshot_shadow_color"):
        dialog._screenshot_shadow_color = QColor(
            str(value("screenshot_shadow_color", "#0078FF")))
        _update_color_btn(dialog.screenshot_shadow_color_btn, dialog._screenshot_shadow_color)
    if hasattr(dialog, "screenshot_border_persist_toggle"):
        dialog.screenshot_border_persist_toggle.setChecked(
            bool(value("screenshot_border_persist", False)))
    if hasattr(dialog, "magnifier_zoom_spin"):
        dialog.magnifier_zoom_spin.setValue(
            float(value("magnifier_zoom", 4.0)))
