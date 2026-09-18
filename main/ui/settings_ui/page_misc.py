# -*- coding: utf-8 -*-
"""杂项设置页 — Fluent Design"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QSpinBox
from PySide6.QtCore import Qt
from core.platform import Capability, available
from ui.fluent_lite import (
    SwitchSettingCard, SettingCard as FSettingCard,
    FluentIcon, ComboBox, CaptionLabel, PushButton, LineEdit,
)
from .components import SettingCardGroup


def _select_combo(combo, value: str) -> None:
    """按 userData 选中；值不认识时保持第 0 项（默认档）。"""
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _build_proxy_row(dialog, group: SettingCardGroup) -> None:
    """网络代理：类型 + 主机 + 端口 + 验证。

    验证只试连代理服务器本身（``core.net.verify_proxy``），不代用户发真实请求。
    """
    card = FSettingCard(
        FluentIcon.SETTING,
        dialog.tr("Proxy Type"),
        dialog.tr("Used by translation and other network features."),
        parent=group,
    )
    config = dialog.config_manager.get_proxy_config()

    dialog.proxy_mode_combo = ComboBox(card)
    dialog.proxy_mode_combo.setFixedWidth(110)
    dialog.proxy_mode_combo.addItem(dialog.tr("No Proxy"), userData="none")
    dialog.proxy_mode_combo.addItem(dialog.tr("Manual"), userData="manual")
    _select_combo(dialog.proxy_mode_combo, config["mode"])

    dialog.proxy_host_input = LineEdit(card)
    dialog.proxy_host_input.setFixedWidth(150)
    dialog.proxy_host_input.setPlaceholderText(dialog.tr("Host"))
    dialog.proxy_host_input.setText(config["host"])

    dialog.proxy_port_spin = QSpinBox(card)
    dialog.proxy_port_spin.setRange(0, 65535)
    dialog.proxy_port_spin.setFixedWidth(90)
    dialog.proxy_port_spin.setValue(int(config["port"]))

    dialog.proxy_verify_btn = PushButton(dialog.tr("Verify"), card)
    dialog.proxy_verify_btn.clicked.connect(lambda: _verify_proxy(dialog))

    for widget in (dialog.proxy_mode_combo, dialog.proxy_host_input,
                   dialog.proxy_port_spin, dialog.proxy_verify_btn):
        card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(8)
    group.addSettingCard(card)


def _verify_proxy(dialog) -> None:
    from core.net import verify_proxy
    from ui.dialogs import show_info_dialog, show_warning_dialog

    host = dialog.proxy_host_input.text().strip()
    port = int(dialog.proxy_port_spin.value())
    ok, reason = verify_proxy(host, port)
    if ok:
        show_info_dialog(dialog, dialog.tr("Proxy"), dialog.tr("Connected to the proxy server."))
    else:
        show_warning_dialog(
            dialog,
            dialog.tr("Proxy"),
            dialog.tr("Cannot reach the proxy server: {reason}", reason=reason),
        )


def _verify_formula_service(dialog) -> None:
    """试一次外部公式服务：拿 1×1 白图发一条请求，只看协议是否通。"""
    from ocr.formula_engines import ExternalFormulaServiceEngine
    from ui.dialogs import show_info_dialog, show_warning_dialog

    engine = ExternalFormulaServiceEngine()
    ok, reason = engine.verify(
        url=dialog.formula_url_input.text().strip(),
        api_key=dialog.formula_key_input.text().strip(),
    )
    if ok:
        show_info_dialog(dialog, dialog.tr("Formula Service"),
                         dialog.tr("The service answered."))
    else:
        show_warning_dialog(
            dialog, dialog.tr("Formula Service"),
            dialog.tr("Cannot use this service: {reason}", reason=reason),
        )


def _build_update_row(dialog, group: SettingCardGroup) -> None:
    """更新源（地址可改，留空用内置的 GitHub 发布页）+ 检查更新。"""
    from core.updates import check_for_updates

    card = FSettingCard(
        FluentIcon.INFO,
        dialog.tr("Update Source"),
        dialog.tr("Releases page used by \"Check for Updates\"."),
        parent=group,
    )
    dialog.update_source_input = LineEdit(card)
    dialog.update_source_input.setFixedWidth(280)
    dialog.update_source_input.setPlaceholderText(dialog.tr("Default: GitHub releases page"))
    dialog.update_source_input.setText(
        dialog.config_manager.get_app_setting("update_source_url", "") or ""
    )

    check_btn = PushButton(dialog.tr("Check for Updates"), card)
    check_btn.clicked.connect(lambda: check_for_updates())

    for widget in (dialog.update_source_input, check_btn):
        card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(8)
    group.addSettingCard(card)



def _build_video_row(dialog, group: SettingCardGroup) -> None:
    """视频录制组：容器 / 编码 / 清晰度 / 帧率 / 码率 / 声音 / 最长时长 / 保存目录。

    这些键一次录制只读一次（``VideoRecordWindow._build_options``），所以保存后不需要
    立刻应用到什么活着的对象——与代理、悬浮球那类「保存即生效」的设置不同。
    """
    from core.platform.video import audio_input_device_names, container_formats, video_codecs
    from core.platform.video import is_available as video_available

    config = dialog.config_manager

    container_card = FSettingCard(
        FluentIcon.CAMERA,
        dialog.tr("Video Container"),
        dialog.tr("File format of the recorded video."),
        parent=group,
    )
    dialog.video_container_combo = ComboBox(container_card)
    dialog.video_container_combo.setFixedWidth(140)
    for name in container_formats() or list(config.VIDEO_CONTAINERS):
        dialog.video_container_combo.addItem(name.upper(), userData=name)
    _select_combo(dialog.video_container_combo, config.get_video_container())
    container_card.hBoxLayout.addWidget(
        dialog.video_container_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    container_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(container_card)

    codec_card = FSettingCard(
        FluentIcon.EDIT,
        dialog.tr("Video Codec"),
        dialog.tr("H.264 is the safest choice; H.265 gives smaller files."),
        parent=group,
    )
    dialog.video_codec_combo = ComboBox(codec_card)
    dialog.video_codec_combo.setFixedWidth(140)
    for name in video_codecs() or list(config.VIDEO_CODECS):
        dialog.video_codec_combo.addItem(name.upper(), userData=name)
    _select_combo(dialog.video_codec_combo, config.get_video_codec())
    codec_card.hBoxLayout.addWidget(dialog.video_codec_combo, 0, Qt.AlignmentFlag.AlignRight)
    codec_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(codec_card)

    quality_card = FSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Video Quality"),
        dialog.tr("Follow the selected area, or cap the output at 1080p/720p/480p."),
        parent=group,
    )
    dialog.video_quality_combo = ComboBox(quality_card)
    dialog.video_quality_combo.setFixedWidth(140)
    for value, label in (("source", dialog.tr("Follow Selection")),
                         ("1080p", "1080p"), ("720p", "720p"), ("480p", "480p")):
        dialog.video_quality_combo.addItem(label, userData=value)
    _select_combo(dialog.video_quality_combo, config.get_video_quality())
    quality_card.hBoxLayout.addWidget(
        dialog.video_quality_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    quality_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(quality_card)

    fps_card = FSettingCard(
        FluentIcon.STOP_WATCH,
        dialog.tr("Video Frame Rate"),
        dialog.tr("Higher frame rates make smoother videos and larger files."),
        parent=group,
    )
    dialog.video_fps_combo = ComboBox(fps_card)
    dialog.video_fps_combo.setFixedWidth(140)
    for value in config.get_video_fps_options():
        dialog.video_fps_combo.addItem(f"{value} fps", userData=value)
    _select_combo(dialog.video_fps_combo, config.get_video_fps())
    fps_card.hBoxLayout.addWidget(dialog.video_fps_combo, 0, Qt.AlignmentFlag.AlignRight)
    fps_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(fps_card)

    bitrate_card = FSettingCard(
        FluentIcon.DOWNLOAD,
        dialog.tr("Bitrate Limit"),
        dialog.tr("0 lets the encoder decide from the quality preset."),
        parent=group,
    )
    dialog.video_bitrate_spin = QSpinBox(bitrate_card)
    dialog.video_bitrate_spin.setRange(*config.VIDEO_BITRATE_RANGE)
    dialog.video_bitrate_spin.setSuffix(" Mbps")
    dialog.video_bitrate_spin.setFixedWidth(140)
    dialog.video_bitrate_spin.setValue(config.get_video_bitrate_mbps())
    bitrate_card.hBoxLayout.addWidget(
        dialog.video_bitrate_spin, 0, Qt.AlignmentFlag.AlignRight
    )
    bitrate_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(bitrate_card)

    audio_card = FSettingCard(
        FluentIcon.PEOPLE,
        dialog.tr("Record Audio"),
        dialog.tr("Record the microphone together with the screen."),
        parent=group,
    )
    dialog.video_audio_combo = ComboBox(audio_card)
    dialog.video_audio_combo.setFixedWidth(140)
    dialog.video_audio_combo.addItem(dialog.tr("No Audio"), userData="none")
    dialog.video_audio_combo.addItem(dialog.tr("Microphone"), userData="microphone")
    _select_combo(dialog.video_audio_combo, config.get_video_audio())
    audio_card.hBoxLayout.addWidget(dialog.video_audio_combo, 0, Qt.AlignmentFlag.AlignRight)
    audio_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(audio_card)

    device_card = FSettingCard(
        FluentIcon.PEOPLE,
        dialog.tr("Microphone Device"),
        dialog.tr("Device used when recording audio (empty = system default)."),
        parent=group,
    )
    dialog.video_audio_device_combo = ComboBox(device_card)
    dialog.video_audio_device_combo.setFixedWidth(220)
    dialog.video_audio_device_combo.addItem(dialog.tr("System Default"), userData="")
    for name in audio_input_device_names():
        dialog.video_audio_device_combo.addItem(name, userData=name)
    _select_combo(dialog.video_audio_device_combo, config.get_video_audio_device())
    device_card.hBoxLayout.addWidget(
        dialog.video_audio_device_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    device_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(device_card)

    # 只有选了麦克风，设备这一行才有意义——置灰而不是留着误导
    def sync_audio_device_enabled() -> None:
        enabled = dialog.video_audio_combo.currentData() == "microphone"
        device_card.setEnabled(enabled)
        dialog.video_audio_device_combo.setEnabled(enabled)

    dialog.video_audio_combo.currentIndexChanged.connect(lambda _index: sync_audio_device_enabled())
    sync_audio_device_enabled()

    duration_card = FSettingCard(
        FluentIcon.HISTORY,
        dialog.tr("Maximum Duration"),
        dialog.tr("Automatically stop after this many seconds (0 = no limit)."),
        parent=group,
    )
    dialog.video_duration_spin = QSpinBox(duration_card)
    dialog.video_duration_spin.setRange(*config.VIDEO_MAX_DURATION_RANGE)
    dialog.video_duration_spin.setSuffix(dialog.tr(" seconds"))
    dialog.video_duration_spin.setFixedWidth(160)
    dialog.video_duration_spin.setValue(config.get_video_max_duration_s())
    duration_card.hBoxLayout.addWidget(
        dialog.video_duration_spin, 0, Qt.AlignmentFlag.AlignRight
    )
    duration_card.hBoxLayout.addSpacing(16)
    group.addSettingCard(duration_card)

    path_card = FSettingCard(
        FluentIcon.FOLDER,
        dialog.tr("Video Save Folder"),
        dialog.tr("Empty = follow the screenshot save folder."),
        parent=group,
    )
    dialog.video_save_path_input = LineEdit(path_card)
    dialog.video_save_path_input.setFixedWidth(220)
    dialog.video_save_path_input.setText(config.get_video_save_path())
    browse_btn = PushButton(dialog.tr("Browse"), path_card)
    browse_btn.clicked.connect(lambda: _choose_video_folder(dialog))
    for widget in (dialog.video_save_path_input, browse_btn):
        path_card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        path_card.hBoxLayout.addSpacing(8)
    group.addSettingCard(path_card)

    # 后端不可用时（构建里没有 QtMultimedia）整组置灰并说明原因，不给假开关
    if not video_available():
        group.setEnabled(False)
        group.setToolTip(dialog.tr("Video recording is unavailable: no encoding backend."))


def _choose_video_folder(dialog) -> None:
    """选择视频保存目录（空 = 跟随截图目录）。"""
    from PySide6.QtWidgets import QFileDialog

    folder = QFileDialog.getExistingDirectory(
        dialog, dialog.tr("Video Save Folder"), dialog.video_save_path_input.text()
    )
    if folder:
        dialog.video_save_path_input.setText(folder)


def create_misc_page(dialog) -> QWidget:
    """创建杂项设置页面 — Fluent Design"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    view = QWidget()
    view.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ════ 启动行为 ════
    grp_startup = SettingCardGroup(dialog.tr("Startup"), view)

    # 开机自启：只有平台层声明支持时才显示卡片。不支持的平台上卡片点了没反应，
    # 显示出来只会让人以为功能坏了。
    if available(Capability.AUTOSTART):
        from ..welcome.page6_finish import FinishPage as _FP
        autostart_card = SwitchSettingCard(
            FluentIcon.POWER_BUTTON,
            dialog.tr("Launch on Startup"),
            dialog.tr("Register in Windows startup via registry."),
            parent=grp_startup,
        )
        autostart_card.setChecked(_FP._get_autostart())
        dialog.autostart_toggle = autostart_card
        grp_startup.addSettingCard(autostart_card)

    # 主界面显示
    show_card = SwitchSettingCard(
        FluentIcon.APPLICATION,
        dialog.tr("Show Main Window on Startup"),
        dialog.tr("If off, starts in background."),
        parent=grp_startup,
    )
    show_card.setChecked(dialog.config_manager.get_show_main_window())
    dialog.show_main_window_toggle = show_card
    grp_startup.addSettingCard(show_card)

    layout.addWidget(grp_startup)

    # 钉图相关的开关都搬到了「贴图设置」页（同一个设置只该有一个家在）

    # ════ 操作 ════
    grp_ops = SettingCardGroup(dialog.tr("Operation"), view)

    # 颜色复制格式
    fmt_card = FSettingCard(
        FluentIcon.PALETTE,
        dialog.tr("Color Copy Format"),
        dialog.tr("Used when copying color info in magnifier."),
        parent=grp_ops,
    )
    dialog.magnifier_color_format_combo = ComboBox(fmt_card)
    dialog.magnifier_color_format_combo.setFixedWidth(140)
    dialog.magnifier_color_format_combo.addItem(dialog.tr("RGB+HEX"), userData="rgb_hex")
    dialog.magnifier_color_format_combo.addItem(dialog.tr("RGB only"), userData="rgb")
    dialog.magnifier_color_format_combo.addItem(dialog.tr("HEX only"), userData="hex")

    current_format = dialog.config_manager.get_app_setting(
        "magnifier_color_copy_format", "rgb_hex"
    )
    idx = dialog.magnifier_color_format_combo.findData(current_format)
    if idx >= 0:
        dialog.magnifier_color_format_combo.setCurrentIndex(idx)
    fmt_card.hBoxLayout.addWidget(
        dialog.magnifier_color_format_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    fmt_card.hBoxLayout.addSpacing(16)
    grp_ops.addSettingCard(fmt_card)

    # 界面语言
    lang_card = FSettingCard(
        FluentIcon.LANGUAGE,
        dialog.tr("Language"),
        dialog.tr("Select display language. Restart required after change."),
        parent=grp_ops,
    )
    dialog.language_combo = ComboBox(lang_card)
    dialog.language_combo.setFixedWidth(140)

    from core.i18n import I18nManager
    for code, name in I18nManager.get_available_languages().items():
        dialog.language_combo.addItem(name, userData=code)

    current_lang = dialog.config_manager.get_app_setting("language", "ja")
    index = dialog.language_combo.findData(current_lang)
    if index >= 0:
        dialog.language_combo.setCurrentIndex(index)
    lang_card.hBoxLayout.addWidget(
        dialog.language_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    lang_card.hBoxLayout.addSpacing(16)
    grp_ops.addSettingCard(lang_card)

    layout.addWidget(grp_ops)

    # ════ 视频录制 ════
    grp_video = SettingCardGroup(dialog.tr("Video Recording"), view)
    _build_video_row(dialog, grp_video)
    layout.addWidget(grp_video)

    # ════ 桌面工具栏 / 托盘 ════
    grp_shell = SettingCardGroup(dialog.tr("Desktop & Tray"), view)

    # 桌面工具栏：目前只有「悬浮球」一种形态，另一个选项是彻底不显示
    toolbar_card = FSettingCard(
        FluentIcon.APPLICATION,
        dialog.tr("Desktop Toolbar"),
        dialog.tr("A floating ball on the desktop: click to capture, right-click for the tray menu."),
        parent=grp_shell,
    )
    dialog.desktop_toolbar_combo = ComboBox(toolbar_card)
    dialog.desktop_toolbar_combo.setFixedWidth(160)
    dialog.desktop_toolbar_combo.addItem(dialog.tr("Do Not Show"), userData="none")
    dialog.desktop_toolbar_combo.addItem(dialog.tr("Floating Ball"), userData="floating_ball")
    _select_combo(dialog.desktop_toolbar_combo,
                  dialog.config_manager.get_desktop_toolbar_mode())
    toolbar_card.hBoxLayout.addWidget(
        dialog.desktop_toolbar_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    toolbar_card.hBoxLayout.addSpacing(16)
    grp_shell.addSettingCard(toolbar_card)

    # 托盘单击动作：列出动作注册表里的动作（默认截图）
    tray_card = FSettingCard(
        FluentIcon.COMMAND_PROMPT,
        dialog.tr("Tray Click Action"),
        dialog.tr("What a single click on the tray icon does."),
        parent=grp_shell,
    )
    dialog.tray_click_combo = ComboBox(tray_card)
    dialog.tray_click_combo.setFixedWidth(180)
    from core import actions as _actions
    for action in _actions.ACTIONS:
        dialog.tray_click_combo.addItem(dialog.tr(action.label), userData=action.id)
    _select_combo(dialog.tray_click_combo, dialog.config_manager.get_tray_click_action())
    tray_card.hBoxLayout.addWidget(
        dialog.tray_click_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    tray_card.hBoxLayout.addSpacing(16)
    grp_shell.addSettingCard(tray_card)

    layout.addWidget(grp_shell)

    # ════ 网络代理 ════
    grp_proxy = SettingCardGroup(dialog.tr("Network Proxy"), view)
    _build_proxy_row(dialog, grp_proxy)
    layout.addWidget(grp_proxy)

    # ════ 更新 ════
    grp_update = SettingCardGroup(dialog.tr("Updates"), view)
    _build_update_row(dialog, grp_update)
    layout.addWidget(grp_update)

    # ════ 分享分析数据 ════
    grp_privacy = SettingCardGroup(dialog.tr("Usage Data"), view)
    privacy_card = SwitchSettingCard(
        FluentIcon.SETTING,
        dialog.tr("Send Usage Statistics Automatically"),
        dialog.tr("Reserved: this version collects and uploads nothing."),
        parent=grp_privacy,
    )
    # 当前版本没有任何上报通道：开关留着占位，但置灰并说明——给一个点了有反应的假开关
    # 比明说「没有实现」更糟。
    privacy_card.setChecked(False)
    privacy_card.setEnabled(False)
    dialog.share_analytics_toggle = privacy_card
    grp_privacy.addSettingCard(privacy_card)
    layout.addWidget(grp_privacy)

    # 提示
    hint = CaptionLabel(
        dialog.tr("💡 Hint: Even with background startup, you can operate from system tray."),
        view,
    )
    hint.setStyleSheet("padding: 5px;")
    layout.addWidget(hint)

    layout.addStretch()
    scroll.setWidget(view)
    return scroll
 
