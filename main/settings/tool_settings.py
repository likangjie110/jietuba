"""
统一设置管理器 - 集中管理所有默认配置

本文件是整个应用的配置中心，包含：

1. 工具设置（绘图工具的默认参数）
   - DEFAULT_SETTINGS 字典定义了所有工具的默认值
   - 包括：画笔、荧光笔、矩形、椭圆、箭头、文字、序号等
   - 每个工具都有：颜色、宽度、透明度等参数

2. 应用级别设置（设置界面中的各项配置）
   - APP_DEFAULT_SETTINGS 字典定义了所有应用级别的默认值
   - 包括：智能选区、任务栏按钮、日志、截图保存、长截图、OCR、钉图等

每个工具都有独立的设置，并且会记忆最后使用的状态
"""

import os
from typing import Dict, Any, Optional
from PySide6.QtCore import QSettings, Signal, QObject
from PySide6.QtGui import QColor

from core.platform.paths import default_screenshot_dir, log_dir
from core.platform.window import (
    MAX_ELEMENT_MARGIN,
    UI_DETECTION_ELEMENT,
    normalize_ui_detection,
)


# ── 贴图的可调范围 ────────────────────────────────────
# 设置页的控件范围与贴图窗口的夹取都读这里，避免两边各写一组数字。
PIN_ZOOM_STEP_RANGE = (1.01, 1.30)      # 滚轮缩放步长（倍率）
PIN_OPACITY_STEP_RANGE = (0.01, 0.25)   # Ctrl+滚轮透明度步长
PIN_OPACITY_RANGE = (0.15, 1.0)         # 窗口透明度下限是 0.15，再低就看不见了
PIN_NEW_POSITIONS = ("selection", "cursor", "center")
PIN_ORDER_MODES = ("top", "bottom")
PIN_HISTORY_RANGE = (1, 50)             # 「贴图历史数量」：最多记住多少张贴图


ANNOTATION_TOOL_SHORTCUTS = (
    ("inapp_tool_cursor", "cursor", "Select / Cursor", "s"),
    ("inapp_tool_pen", "pen", "Pen", "p"),
    ("inapp_tool_highlighter", "highlighter", "Highlighter", "m"),
    ("inapp_tool_mosaic", "mosaic", "Mosaic", "x"),
    ("inapp_tool_arrow", "arrow", "Arrow", "a"),
    ("inapp_tool_number", "number", "Number", "n"),
    ("inapp_tool_rect", "rect", "Rectangle", "r"),
    ("inapp_tool_ellipse", "ellipse", "Ellipse", "o"),
    ("inapp_tool_text", "text", "Text", "t"),
    ("inapp_tool_eraser", "eraser", "Eraser", "e"),
)


class ToolSettings:
    """单个工具的设置数据类"""
    
    def __init__(self, tool_id: str, defaults: Dict[str, Any]):
        """
        Args:
            tool_id: 工具ID（pen, rect, ellipse等）
            defaults: 默认设置字典
        """
        self.tool_id = tool_id
        self.defaults = defaults
        self._current = defaults.copy()
    
    def get(self, key: str, default=None) -> Any:
        """获取设置值"""
        return self._current.get(key, default if default is not None else self.defaults.get(key))
    
    def set(self, key: str, value: Any):
        """设置值"""
        self._current[key] = value
    
    def update(self, **kwargs):
        """批量更新设置"""
        self._current.update(kwargs)
    
    def reset_to_defaults(self):
        """重置为默认值"""
        self._current = self.defaults.copy()
    
    def to_dict(self) -> Dict[str, Any]:
        """导出为字典"""
        return self._current.copy()
    
    def from_dict(self, data: Dict[str, Any]):
        """从字典导入"""
        self._current.update(data)


class ToolSettingsManager(QObject):
    """
    工具设置管理器
    
    功能：
    1. 为每个工具维护独立的设置（颜色、尺寸、透明度等）
    2. 自动保存和加载每个工具的最后使用状态
    3. 提供默认设置和重置功能
    """
    
    # 信号：当工具设置改变时发出
    settings_changed = Signal(str, dict)  # (tool_id, settings_dict)
    
    # 各工具的默认设置
    DEFAULT_SETTINGS = {
        "pen": {
            "color": "#FF0000",  # 红色
            "stroke_width": 12,
            "opacity": 1.0,
            "line_style": "solid",  # 线条样式（工具栏一直在存它，缺了默认值就读不回来）
        },
        "highlighter": {
            "color": "#FFFF00",  # 黄色
            "stroke_width": 15,
            "opacity": 1.0,
            "draw_mode": "freehand",
            "line_style": "solid",  # 荧光笔不露出线型选择，但它和画笔共用面板
        },
        "mosaic": {
            "color": "#808080",
            "stroke_width": 30,
            "opacity": 1.0,
            "block_size": 10,  # 必须是 MosaicTool.BLOCK_SIZE_LEVELS 里的档位之一
            "draw_mode": "freehand",  # freehand / rect
            "style": "pixelate",  # pixelate / blur
        },
        "spotlight": {
            "opacity": 0.7,  # 幕布暗度（整个场景一张黑色幕布，所有聚光灯共用）
        },
        "rect": {
            "color": "#FF0000",  # 红色
            "stroke_width": 9,
            "opacity": 1.0,
            "line_style": "solid",  # 线条样式
            "corner_radius": 0,  # 圆角大小（0=直角）
        },
        "ellipse": {
            "color": "#FF0000",  # 红色
            "stroke_width": 9,
            "opacity": 1.0,
            "line_style": "solid",  # 线条样式
        },
        "arrow": {
            "color": "#FF0000",  # 红色
            "stroke_width": 9,
            "opacity": 1.0,
            "arrow_size": 9,  # 箭头大小
            "arrow_style": "single",  # 箭头样式，取值见 ArrowItem.STYLES
            "path_style": "straight",  # 路径样式：straight / curve / elbow
            "head_start": "inherit",   # 起点端点：inherit 跟随 arrow_style，或 none/triangle/...
            "head_end": "inherit",     # 终点端点，取值同上
        },
        "text": {
            "color": "#000000",  # 黑色
            "font_size": 14,
            "opacity": 1.0,
            "font_family": "",
            "background_enabled": False,
            "background_color": "#FFFFFF",  # 文字背景默认白色
            "background_opacity": 255,
            # 描边/阴影：与 TextItem 的 DEFAULT_* 一致（这里不能 import canvas）
            "outline_enabled": False,
            "outline_color": "#FFFFFF",
            "outline_width": 0.07,  # 必须是 TextItem.OUTLINE_WIDTH_LEVELS 里的档位之一（字号的比例）
            "shadow_enabled": False,
            "shadow_color": "#66000000",  # #AARRGGBB，alpha 即阴影不透明度
        },
        "number": {
            "color": "#FF0000",  # 红色
            "style": "solid",    # solid / hollow_bg / hollow_all
            "font_size": 10,
            "opacity": 1.0,
            "stroke_width": 12,
        },
        "eraser": {
            "stroke_width": 25,  # 橡皮擦大小（宽度）
            "opacity": 1.0,      # 占位参数（橡皮擦不需要透明度）
        },
        # ---- 第二批标注工具（工具在按下的那一刻读这些键，所以改完立即生效）----
        "line": {
            "color": "#FF0000",
            "stroke_width": 6,
            "opacity": 1.0,
            "line_style": "solid",  # solid / dashed / dashed_dense
        },
        "watermark": {
            "text": "jietuba",
            "font_size": 28,
            "angle": -30.0,       # 度数，负数=逆时针
            "gap": 120,           # 相邻水印的间距（px）
            "opacity": 0.35,      # 水印自身的淡度
            "color": "#FFFFFF",
        },
        "filter": {
            "kind": "grayscale",  # grayscale / invert / blur / emboss
            "radius": 6,          # 高斯模糊半径
            "strength": 1.0,      # 浮雕强度
        },
        "smart_erase": {
            "brush_width": 40,    # 涂抹宽度
            "sample_margin": 6,   # 从笔画外侧取样估计背景色的圈宽
        },
    }
    
    # 应用级别的默认设置（按照设置界面的页面顺序排列）
    APP_DEFAULT_SETTINGS = {
        # ==================== 1. 快捷键 ====================
        "hotkey": "ctrl+1",                        # 截图热键
        "hotkey_2": "",                            # 截图备用热键
        "clipboard_hotkey": "ctrl+2",              # 剪贴板管理器的快捷键
        "clipboard_hotkey_2": "",                  # 剪贴板管理器的备用快捷键
        "translation_hotkey": "",                  # 翻译主热键
        "translation_hotkey_2": "",                # 翻译备用热键
        "global_hotkeys_disabled": False,           # 是否禁用全局热键

        # 应用内快捷键
        "inapp_confirm": "ctrl+c",             # 确认截图（复制到剪贴板）
        "inapp_pin": "ctrl+d",                 # 钉图
        "inapp_undo": "ctrl+z",                # 撤销
        "inapp_redo": "ctrl+y",                # 重做
        "inapp_delete": "delete",              # 删除选中图元
        "inapp_copy_pin": "ctrl+c",            # 复制钉图内容
        "inapp_thumbnail": "r",                # 切换缩略图模式
        "inapp_toggle_toolbar": "space",       # 切换工具栏
        # 贴图窗口的其它内置快捷键（键位映射到 pin/pin_actions.py 的动作 id）
        "inapp_pin_save": "ctrl+s",            # 保存贴图
        "inapp_pin_rotate": "ctrl+r",          # 顺时针旋转
        "inapp_pin_lock": "ctrl+l",            # 锁定/解锁位置与大小
        "inapp_pin_on_top": "ctrl+t",          # 置顶开关
        "inapp_pin_shadow": "ctrl+h",          # 阴影/描边开关
        "inapp_pin_opacity_up": "ctrl+up",     # 提高不透明度
        "inapp_pin_opacity_down": "ctrl+down",  # 降低不透明度
        "inapp_pin_copy_all_text": "",         # 复制识别到的文字（默认不绑）
        "inapp_pin_copy_and_close": "",        # 复制并关闭（默认不绑）
        "inapp_zoom_in": "pageup",             # 放大镜放大
        "inapp_zoom_out": "pagedown",          # 放大镜缩小
        "inapp_translate": "shift+c",          # 截图翻译
        "inapp_text_recognize": "shift+t",     # 文字识别
        "inapp_cursor_move_mode": "both",      # 鼠标微移模式: both / arrows / wasd
        # 标注元素的层级与对齐（画布内快捷键，定义表见 page_hotkey.LAYER_KEYS）
        "inapp_bring_to_front": "ctrl+shift+]",
        "inapp_send_to_back": "ctrl+shift+[",
        "inapp_bring_forward": "",
        "inapp_send_backward": "",
        "inapp_align_left": "",
        "inapp_align_hcenter": "",
        "inapp_align_right": "",
        "inapp_align_top": "",
        "inapp_align_vcenter": "",
        "inapp_align_bottom": "",
        **{key: default for key, _tool, _label, default in ANNOTATION_TOOL_SHORTCUTS},
        # ==================== 2. 截图 ====================
        # 截图交互
        "double_click_copy_close": True,      # 双击选区复制到剪贴板并关闭
        "cross_tool_selection": True,         # Ctrl 临时跨工具选择标注
        "text_always_on_top": True,           # 文字标注始终高于其他绘制标注
        "screenshot_toolbar_layout": "",      # 截图工具栏按钮排布（JSON，空 = 默认排布，见 ui/toolbar_layout.py）

        # 全局鼠标动作：修饰键 + 鼠标手势 → 动作 id（空表 = 全部未绑定）
        "mouse_gestures": {},
        # 全局鼠标动作截图时显示遮罩（抓完再闪，见 core/actions.py 的说明）
        "mouse_capture_overlay": False,
        # 忽略程序列表：前台程序名命中时不触发全局鼠标动作
        "mouse_ignored_apps": [],

        # 全局动作的快捷键：{动作 id: [主热键, 备用热键]}（空串 = 不绑）。
        # 这是「快捷键/动作页」的唯一出处；旧键 app/hotkey、app/hotkey_2、
        # clipboard/hotkey(_2)、app/translation_hotkey(_2) 由 _LEGACY_HOTKEY_KEYS
        # 一次性迁移过来，之后的读写都走这里。
        "action_hotkeys": {
            "screenshot": ["ctrl+1", ""],
            "clipboard": ["ctrl+2", ""],
            "open_translation": ["", ""],
        },
        # 托盘菜单里显示哪些动作：{动作 id: bool}（缺省看 core.actions 里各动作的默认值）
        "action_tray": {
            "screenshot": True,
            "screenshot_copy": True,
            "screenshot_quick_save": True,
            "long_screenshot": True,
            "gif_capture": True,
            "video_capture": True,
            "open_history": True,
            "open_image_viewer": True,
            "open_main_window": True,
            "recognize_table": True,
            "convert_image_markdown": True,
            "read_image_ai": True,
            "clipboard": True,
            "open_translation": True,
            "open_save_folder": True,
            "pin_clipboard_text": True,
            "translate_clipboard_image": True,
        },

        # UI 检测：截图时鼠标悬停要不要自动框出窗口/控件
        "ui_detection": UI_DETECTION_ELEMENT,  # none 不检测 / window 仅窗口 / element 检测元素
        "ui_detection_margin": 0,              # 元素检测边距（px，仅 element 档生效）

        # 截图保存
        "screenshot_save_enabled": True,       # 自动保存截图
        "screenshot_save_path": default_screenshot_dir(),  # 默认保存路径（按平台选目录）
        # 多保存路径：[{"path": ..., "format": "PNG", "quality": 85}, ...]
        # 空表时按「单路径 + screenshot_format/screenshot_quality」工作（老配置无缝沿用）
        "screenshot_save_paths": [],
        "screenshot_format": "PNG",            # 保存格式: PNG / JPG / BMP / WEBP / PDF
        "screenshot_quality": 85,              # 有损格式质量 (1-100, PNG/BMP忽略)
        "pdf_page_size": "original",           # PDF 页尺寸: original / a4_portrait / a4_landscape

        # 截图圆角
        "screenshot_rounded_enabled": False,   # 圆角截图开关
        "screenshot_rounded_radius": 16,       # 圆角半径（0-100）

        # 截图描边/阴影
        "screenshot_border_enabled": False,          # 描边/阴影开关
        "screenshot_border_mode": "shadow",          # 模式: "shadow" 或 "border"
        "screenshot_border_size": 21,                # 描边宽度 / 阴影大小（1-50）
        "screenshot_shadow_color": "#0078FF",        # 阴影颜色（独立）
        "screenshot_border_color": "#FF0000",        # 描边颜色（独立）
        "screenshot_border_persist": False,          # 每次截图都保持开启
        # GIF 录制
        "gif_fps": 10,                         # GIF默认帧率
        "gif_fps_options": [5, 10, 16, 24],  # 帧率可选项（可在此调整选项）

        # 视频录制（录屏 → MP4/MKV 等容器；编码走 Qt 多媒体的 FFmpeg 后端）
        "video_container": "mp4",              # 容器: mp4 / mkv / mov / avi
        "video_codec": "h264",                 # 视频编码: h264 / h265
        "video_fps": 30,                       # 录制帧率
        "video_quality": "source",             # 清晰度: source 跟随区域 / 1080p / 720p / 480p
        "video_bitrate_mbps": 0,               # 码率上限（Mbps，0 = 交给编码器按清晰度决定）
        "video_audio": "none",                 # 音频: none 不录声音 / microphone 录麦克风
        "video_audio_device": "",              # 麦克风设备名（空 = 系统默认）
        "video_max_duration_s": 0,             # 最长录制时长（秒，0 = 不限制）
        "video_save_path": "",                 # 视频保存目录（空 = 跟随截图保存目录）

        # OCR
        "ocr_enabled": True,                   # 钉图后自动 OCR（保留旧键名以兼容已有配置）
        "ocr_engine": "ppocr_rust",            # OCR引擎类型 (ppocr_rust 推荐, windows_media_ocr 备用)
        "ocr_grayscale": False,                # OCR灰度转换（Windows OCR 不需要）
        "ocr_upscale": True,                   # OCR图像放大（提升小字识别率）
        "ocr_upscale_factor": 2.0,             # OCR放大倍数（1.0-3.0）
        # 识别结果的文本布局：auto 智能分行（默认）/ lines 每个文字块一行 / single 全部并成一行
        "ocr_text_layout": "auto",
        # 标点处理：none 原样 / strip_trailing 去掉行尾标点 / to_halfwidth 全角转半角
        "ocr_punctuation": "none",
        # 识别语言：follow_app 跟随界面语言 / zh / en / ja / ko
        "ocr_language": "follow_app",
        # 什么时候额外弹出识别结果对话框（JSON 列表，见 get_ocr_dialog_triggers）：
        # capture 截图后 / copy_all 复制所有文本 / copy_selection 复制选定文本。
        # 默认空表：沿用「只有复制、不弹窗」的老行为
        "ocr_dialog_triggers": [],
        # 模型档位（见 ocr/model_tiers.py；不可用的档位不会出现在设置页里）
        "ocr_model_tier": "v6_small",
        # 视觉模型（OpenAI 兼容；把图片转 Markdown/HTML，见 ocr/vision_models.py）
        "ocr_vision_models": [],
        "ocr_vision_model": "",
        "ocr_vision_target": "markdown",
        # 任务模板（id 见 ocr/vision_models.py 的 TASKS）：决定发给视觉模型的指令
        "ocr_vision_task": "table",
        # 本地 OCR 置信度低于它就提示「可改用视觉模型」；提示不发任何网络请求
        "ocr_low_confidence_threshold": 0.6,
        # 公式识别（第 9 项）：ppocr_formula 走 Rust 侧 PP-FormulaNet 绑定；
        # external_service 走自建/第三方 HTTP 服务（要填 URL 与可选密钥）
        "formula_engine": "ppocr_formula",
        "formula_service_url": "",
        "formula_service_api_key": "",
        "formula_service_timeout": 15,         # 外部公式服务的超时（秒）
        
        # ==================== 3. 剪贴板 ====================
        "clipboard_enabled": True,             # 剪贴板监听启用
        "clipboard_auto_paste": True,          # 选择后自动粘贴（发送 Ctrl+V）
        "clipboard_history_limit": 1000,        # 历史记录数量限制（0 为不限制）
        "clipboard_auto_cleanup": True,        # 自动清理超出限制的记录
        "clipboard_window_width": 450,         # 剪贴板窗口默认宽度
        "clipboard_window_height": 750,        # 剪贴板窗口默认高度
        "clipboard_window_opacity": 20,         # 剪贴板窗口透明度（0=不透明）
        "clipboard_window_opacity_options": [0, 20, 30, 40, 50, 60],  # 透明度可选项（可在此调整选项）
        "clipboard_paste_with_html": True,     # 粘贴时是否带 HTML 格式
        "clipboard_show_metadata": True,       # 显示时间和来源信息
        "clipboard_font_size": 17,            # 剪贴板项字体大小（像素）
        "clipboard_font_size_options": [15, 16, 17, 18, 19, 20],  # 字体大小可选项
        "clipboard_line_height_padding": 8,   # 多行显示时的额外行高边距（像素，用于确保完整显示）
        "clipboard_display_lines": 1,          # 剪贴板项最大显示行数
        "clipboard_theme": "light",            # 剪贴板窗口主题（light/dark/blue/green/pink/purple/orange）
        # 复制图片项时写进系统剪贴板的形态：auto=图片与 PNG 文件都给（谁认哪个用哪个）
        # / image_only=只给图片 / file_only=只给 PNG 文件（给只认文件的程序用）
        "clipboard_image_copy_mode": "auto",
        # 忽略本程序自己写入剪贴板的内容：不做历史记录，也不触发"新内容"日志，
        # 避免程序自己的复制动作又被打回来形成重复记录/循环
        "clipboard_ignore_own_copy": True,
        "clipboard_group_bar_position": "top", # 分组栏位置（right/left/top）
        "clipboard_preserve_search": False,    # 关闭时保留搜索栏内容
        "clipboard_db_path": "",               # 剪贴板数据库自定义路径（空=默认位置）

        # ==================== 3.5 截图历史 ====================
        # 用户确认过的截图结果存一份到应用数据目录；三个上限都是 0 = 不限制
        "history_enabled": True,               # 是否记录截图历史
        "history_retention_days": 0,           # 保留天数（0 = 永久保留）
        "history_max_entries": 0,              # 最多保留多少条（0 = 不限制）
        "history_max_disk_mb": 0,              # 磁盘占用上限 MiB（0 = 不限制）

        # ==================== 4. 外观 ====================
        "ui_theme_mode": "system",             # 界面主题（system/light/dark）
        "theme_color": "#40E0D0",              # 主题色（青绿色 Turquoise）
        "mask_color_r": 0,                     # 遮罩色 R（0-255）
        "mask_color_g": 0,                     # 遮罩色 G（0-255）
        "mask_color_b": 0,                     # 遮罩色 B（0-255）
        # 遮罩色 Alpha 固定为 120，不提供前端设置
        # 皮肤覆盖层：空串表示该项跟随内置主题（见 core/ui_theme.py 的 SkinOverrides）
        "skin_accent_color": "",               # 皮肤强调色
        "skin_window_color": "",               # 皮肤窗口背景色
        "skin_text_color": "",                 # 皮肤文字色
        "custom_logo_path": "",                # 自定义 logo 图片（空=内置品牌图标）

        # ==================== 5. 翻译 ====================
        "translation_provider": "google",      # 当前翻译引擎
        "deepl_api_key": "",                    # DeepL API 密钥
        "deepl_use_pro": False,                # 是否使用 Pro 版 API
        "amazon_translate_region": "us-west-2",
        "amazon_translate_access_key_id": "",
        "amazon_translate_secret_access_key": "",
        "amazon_translate_session_token": "",
        "google_translate_api_key": "",
        "azure_translate_api_key": "",
        "azure_translate_region": "",
        "azure_translate_endpoint": "",
        "translation_target_lang": "",         # 翻译目标语言（空为跟随系统语言）
        "local_model_id": "",                  # 离线引擎用的模型（空为第一个已安装的）
        # 截图翻译的呈现：text 只给文本（老行为）/ replace 原图替换 / bilingual 下方双语
        "translation_image_mode": "replace",
        "translation_split_sentences": True,   # 自动分句
        "translation_preserve_formatting": True,  # 保留格式

        # ==================== 6. 日志 ====================
        "log_enabled": True,                   # 日志启用
        "log_dir": str(log_dir()),             # 与平台层的日志落点保持同一处来源
        "log_level": "INFO",                  # 日志等级: DEBUG, INFO, WARNING, ERROR
        "log_retention_days": 7,               # 日志保留天数（0表示永久保留）

        # ==================== 7. 其他 ====================
        "show_main_window": False,             # 运行后自动弹出窗口显示（默认后台启动）
        "language": "en",                      # 界面语言（ja/en/zh/ko）
        "magnifier_color_copy_format": "rgb_hex",  # 放大镜复制颜色信息格式（rgb_hex/rgb/hex）
        "magnifier_zoom": 4.0,                 # 放大镜默认倍率（1.0 ~ 10.0）
        "magnifier_zoom_min": 2.0,             # 放大镜最小倍率
        "magnifier_zoom_max": 10.0,            # 放大镜最大倍率
        "pin_auto_toolbar": False,             # 钉图自动显示工具栏
        "pin_default_opacity": 1.0,            # 钉图默认透明度（见 PIN_OPACITY_RANGE）
        "pin_zoom_step": 1.05,                 # 滚轮缩放步长（倍率，见 PIN_ZOOM_STEP_RANGE）
        "pin_opacity_step": 0.05,              # Ctrl+滚轮透明度步长（见 PIN_OPACITY_STEP_RANGE）
        "pin_shadow_enabled": True,            # 新贴图默认带阴影/描边
        "pin_new_position": "selection",       # 新贴图位置：selection 跟随选区 / cursor 鼠标 / center 屏幕中央
        "pin_order": "top",                    # 新贴图的叠放顺序：top 在最上 / bottom 放在已有贴图下面
        "pin_close_confirm": False,            # 关闭贴图前二次确认（退出程序时不问）
        # 贴图窗口的鼠标手势 → 动作（空表 = 用 pin/pin_actions.py 里的默认表；
        # 这里不写死一份，否则手势词汇会有两个出处）
        "pin_mouse_actions": {},
        "pin_restore_on_startup": False,       # 退出时保存未关闭的贴图，下次启动恢复
        "pin_history_limit": 10,               # 最多记住多少张贴图（1-50）
        "pin_text_font_size": 14,              # 文字贴图字号（范围见 TEXT_FONT_SIZE_RANGE）
        "pin_text_max_width": 480,             # 文字贴图内容区最大宽度（见 TEXT_MAX_WIDTH_RANGE）

        # ==================== 7.1 系统与集成（2026-09-19 按截图清单补）====================
        "desktop_toolbar_mode": "none",        # 桌面工具栏：none 不显示 / floating_ball 悬浮球
        "desktop_toolbar_position": "",        # 悬浮球位置 "x,y"（空串 = 默认右下角）
        "tray_click_action": "screenshot",
        "tray_scroll_action": "",              # 托盘滚轮（中键）动作 id（空=不做事）     # 托盘单击执行的动作 id（取自 core.actions 注册表）
        # 网络代理：none 直连 / manual 手动（翻译、公式识别等走网络的功能都用它）
        "proxy_mode": "none",
        "proxy_host": "",
        "proxy_port": 0,
        # 更新源：留空则用内置的 GitHub 地址（core/updates.py）
        "update_source_url": "",

        # ==================== 8. 开发者 ====================
        # 长截图
        "long_stitch_engine": "hash_rust",     # 长截图引擎（hash_rust）
        "long_stitch_debug": False,            # 长截图调试模式
        "scroll_cooldown": 0.15,               # 滚动后等待时间（秒，0.05-1.0）
        "long_stitch_ignore_top_pixels": 0,    # 后续截图顶部忽略像素（0-300）

        # 启动预加载（重启生效）
        "preload_screenshot": True,
        "preload_toolbar": True,
        "preload_ocr": True,
        "preload_settings": True,
        "preload_clipboard": True,

        # 截图信息面板
        "screenshot_info_hide_on_drag": False,  # 拖拽选区时隐藏信息面板

        # ==================== 9. 关于 ====================
        # “关于”页当前没有可持久化配置。
    }
    
    def __init__(self, qsettings: Optional[QSettings] = None, secret_store=None):
        super().__init__()
        self.qsettings = qsettings if qsettings is not None else QSettings("Jietuba", "ToolSettings")
        self._secret_store = secret_store
        self._tool_settings: Dict[str, ToolSettings] = {}
        self._initialize_tools()

    @property
    def secret_store(self):
        """凭据存储，默认是平台层的系统密钥库。

        做成可注入的（与 ``qsettings`` 同一个理由）：测试必须能换成内存实现。真实
        Keychain 在退出阶段会留下 pyobjc 对象，测试里碰它既是「污染开发机的钥匙串」，
        也会让 pytest 拆除期偶发 abort——两件事都不该发生在单元测试里。
        """
        if self._secret_store is None:
            from core.platform import secrets as platform_secrets

            self._secret_store = platform_secrets
        return self._secret_store

    @property
    def settings(self):
        """返回 QSettings 实例。"""
        return self.qsettings
    
    def _initialize_tools(self):
        """初始化所有工具的设置"""
        for tool_id, defaults in self.DEFAULT_SETTINGS.items():
            # 创建工具设置对象
            tool_setting = ToolSettings(tool_id, defaults)
            
            # 从持久化存储加载
            self._load_tool_settings(tool_setting)
            
            self._tool_settings[tool_id] = tool_setting
    
    def reload_from_storage(self) -> None:
        """从存储重新读回工具设置。

        导入设置包（``core/settings_archive.import_settings``）写的是 QSettings，而工具
        设置在被读进内存后就不会自己回去看存储了——不调这一步，导入之后「工具设置」还是
        旧值，用户会看到「导入成功了但画笔粗细没变」。
        """
        for tool_id, defaults in self.DEFAULT_SETTINGS.items():
            tool_setting = self._tool_settings.get(tool_id)
            if tool_setting is None:
                self._tool_settings[tool_id] = ToolSettings(tool_id, defaults)
                self._load_tool_settings(self._tool_settings[tool_id])
                continue
            tool_setting.reset_to_defaults()
            self._load_tool_settings(tool_setting)

    def _load_tool_settings(self, tool_setting: ToolSettings):
        """从 QSettings 加载工具设置"""
        tool_id = tool_setting.tool_id

        def _coerce_value(raw_value, default_value):
            if isinstance(default_value, bool):
                try:
                    if isinstance(raw_value, str):
                        return raw_value.lower() in ("1", "true", "yes", "on")
                    return bool(raw_value)
                except Exception:
                    return default_value
            if isinstance(default_value, int):
                try:
                    if isinstance(raw_value, (list, tuple)) and raw_value:
                        raw_value = raw_value[0]
                    return int(float(raw_value))
                except Exception:
                    return default_value
            if isinstance(default_value, float):
                try:
                    if isinstance(raw_value, (list, tuple)) and raw_value:
                        raw_value = raw_value[0]
                    return float(raw_value)
                except Exception:
                    return default_value
            if isinstance(default_value, str):
                try:
                    if isinstance(raw_value, (list, tuple)):
                        return raw_value[0] if raw_value else default_value
                    return str(raw_value)
                except Exception:
                    return default_value
            return raw_value
        
        # 加载每个设置项
        for key, default_value in tool_setting.defaults.items():
            setting_key = f"tools/{tool_id}/{key}"
            
            # 根据类型加载
            if isinstance(default_value, (bool, int, float, str)):
                raw_value = self.qsettings.value(setting_key, default_value)
                value = _coerce_value(raw_value, default_value)
            else:
                value = self.qsettings.value(setting_key, default_value)
            
            tool_setting.set(key, value)
    
    def _save_tool_settings(self, tool_setting: ToolSettings):
        """保存工具设置到 QSettings。

        不手动 sync()：setValue() 之后 QSettings 会在下一轮事件循环自己落盘。拖动滑块、
        Ctrl+滚轮这类连续调整每一步都会走到这里，手动 sync() 只会在自动落盘之外再多刷一次。
        """
        tool_id = tool_setting.tool_id

        for key, value in tool_setting.to_dict().items():
            setting_key = f"tools/{tool_id}/{key}"
            self.qsettings.setValue(setting_key, value)
    
    def get_tool_settings(self, tool_id: str) -> Optional[ToolSettings]:
        """获取工具的设置对象"""
        return self._tool_settings.get(tool_id)
    
    def get_setting(self, tool_id: str, key: str, default=None) -> Any:
        """
        获取指定工具的某个设置值
        
        Args:
            tool_id: 工具ID
            key: 设置键名
            default: 默认值
        
        Returns:
            设置值
        """
        tool_setting = self._tool_settings.get(tool_id)
        if tool_setting:
            return tool_setting.get(key, default)
        return default
    
    def set_setting(self, tool_id: str, key: str, value: Any, save_immediately: bool = True):
        """
        设置指定工具的某个设置值
        
        Args:
            tool_id: 工具ID
            key: 设置键名
            value: 设置值
            save_immediately: 是否立即保存到磁盘
        """
        tool_setting = self._tool_settings.get(tool_id)
        if tool_setting:
            tool_setting.set(key, value)
            
            if save_immediately:
                self._save_tool_settings(tool_setting)
            
            # 发出信号
            self.settings_changed.emit(tool_id, tool_setting.to_dict())
    
    def update_settings(self, tool_id: str, save_immediately: bool = True, **kwargs):
        """
        批量更新工具设置
        
        Args:
            tool_id: 工具ID
            save_immediately: 是否立即保存
            **kwargs: 设置键值对
        """
        tool_setting = self._tool_settings.get(tool_id)
        if tool_setting:
            tool_setting.update(**kwargs)
            
            if save_immediately:
                self._save_tool_settings(tool_setting)
            
            # 发出信号
            self.settings_changed.emit(tool_id, tool_setting.to_dict())
    
    def save_all(self):
        """保存所有工具的设置"""
        for tool_setting in self._tool_settings.values():
            self._save_tool_settings(tool_setting)
    
    def reset_tool(self, tool_id: str, save_immediately: bool = True):
        """
        重置工具设置为默认值
        
        Args:
            tool_id: 工具ID
            save_immediately: 是否立即保存
        """
        tool_setting = self._tool_settings.get(tool_id)
        if tool_setting:
            tool_setting.reset_to_defaults()
            
            if save_immediately:
                self._save_tool_settings(tool_setting)
            
            # 发出信号
            self.settings_changed.emit(tool_id, tool_setting.to_dict())
    
    def reset_all(self):
        """重置所有工具设置为默认值"""
        for tool_id in self._tool_settings.keys():
            self.reset_tool(tool_id, save_immediately=False)
        
        self.save_all()
    
    def reset_app_settings(self):
        """重置所有应用级别设置为默认值"""
        for key, default_value in self.APP_DEFAULT_SETTINGS.items():
            # 构建完整的设置键名
            if key.startswith("inapp_"):
                setting_key = f"inapp/{key}"
            elif key.startswith("pin_"):
                setting_key = f"pin/{key[4:]}"  # pin_auto_toolbar -> pin/auto_toolbar
            else:
                setting_key = f"app/{key}"
            
            self.qsettings.setValue(setting_key, default_value)
        
        from core.logger import log_info, T
        log_info(T("应用设置已重置为默认值"), "Settings")
    
    def get_app_setting(self, key: str, default=None) -> Any:
        """
        获取应用级别设置的通用方法
        
        Args:
            key: 设置键名（不含 app/ 前缀）
            default: 默认值，如果为 None 则使用 APP_DEFAULT_SETTINGS 中的默认值
            
        Returns:
            设置值
        """
        # 确定实际默认值
        if default is None:
            default = self.APP_DEFAULT_SETTINGS.get(key)
        
        # 构建完整的设置键名
        if key.startswith("pin_"):
            setting_key = f"pin/{key[4:]}"
        else:
            setting_key = f"app/{key}"
        
        # 根据默认值的类型确定返回类型
        if default is not None:
            if isinstance(default, bool):
                return self.qsettings.value(setting_key, default, type=bool)
            elif isinstance(default, int):
                return self.qsettings.value(setting_key, default, type=int)
            elif isinstance(default, float):
                return self.qsettings.value(setting_key, default, type=float)
            else:
                return self.qsettings.value(setting_key, default, type=str)
        else:
            return self.qsettings.value(setting_key, default)
    
    def set_app_setting(self, key: str, value: Any):
        """
        设置应用级别设置的通用方法
        
        Args:
            key: 设置键名（不含 app/ 前缀）
            value: 设置值
        """
        # 构建完整的设置键名
        if key.startswith("pin_"):
            setting_key = f"pin/{key[4:]}"
        else:
            setting_key = f"app/{key}"
        
        self.qsettings.setValue(setting_key, value)

    def reset_all_settings(self):
        """重置所有设置（工具设置 + 应用设置）为默认值"""
        self.reset_all()           # 重置工具设置
        self.reset_app_settings()  # 重置应用设置
        from core.logger import log_info, T
        log_info(T("所有设置已重置为默认值"), "Settings")
    
    def get_color(self, tool_id: str) -> QColor:
        """获取工具的颜色（返回 QColor 对象）"""
        color_str = self.get_setting(tool_id, "color", "#FF0000")
        return QColor(color_str)
    
    def set_color(self, tool_id: str, color: QColor, save_immediately: bool = True):
        """设置工具的颜色"""
        color_str = color.name()  # 转为 #RRGGBB 格式
        self.set_setting(tool_id, "color", color_str, save_immediately)
    
    def get_stroke_width(self, tool_id: str) -> int:
        """获取工具的笔触宽度"""
        return self.get_setting(tool_id, "stroke_width", 3)
    
    def set_stroke_width(self, tool_id: str, width: int, save_immediately: bool = True):
        """设置工具的笔触宽度"""
        self.set_setting(tool_id, "stroke_width", width, save_immediately)
    
    def get_opacity(self, tool_id: str) -> float:
        """获取工具的透明度"""
        return self.get_setting(tool_id, "opacity", 1.0)
    
    def set_opacity(self, tool_id: str, opacity: float, save_immediately: bool = True):
        """设置工具的透明度"""
        self.set_setting(tool_id, "opacity", opacity, save_immediately)
    
    def get_font_size(self, tool_id: str) -> int:
        """获取文字工具的字体大小"""
        return self.get_setting(tool_id, "font_size", 14)
    
    def set_font_size(self, tool_id: str, size: int, save_immediately: bool = True):
        """设置文字工具的字体大小"""
        self.set_setting(tool_id, "font_size", size, save_immediately)
    
    def export_settings(self) -> Dict[str, Dict[str, Any]]:
        """导出所有工具设置（用于备份）"""
        return {
            tool_id: tool_setting.to_dict()
            for tool_id, tool_setting in self._tool_settings.items()
        }
    
    def import_settings(self, settings_dict: Dict[str, Dict[str, Any]]):
        """导入工具设置（用于恢复备份）"""
        for tool_id, settings in settings_dict.items():
            tool_setting = self._tool_settings.get(tool_id)
            if tool_setting:
                tool_setting.from_dict(settings)
                self._save_tool_settings(tool_setting)
    
    # ========================================================================
    # 应用程序级别设置（整合自 ConfigManager）
    # ========================================================================
    
    # 热键的读写统一走动作表（app/action_hotkeys）。下面这六个是给欢迎向导等旧入口
    # 用的视图，不再单独存一份——两处各存一份的话，改了一边另一边就静默失效。

    def get_hotkey(self) -> str:
        """获取全局热键（截图动作的主热键）"""
        return self._action_hotkey_pair("screenshot")[0]

    def set_hotkey(self, value: str):
        """设置全局热键"""
        self._set_action_hotkey("screenshot", 0, value)

    def get_hotkey_2(self) -> str:
        """获取全局截图备用热键"""
        return self._action_hotkey_pair("screenshot")[1]

    def set_hotkey_2(self, value: str):
        """设置全局截图备用热键"""
        self._set_action_hotkey("screenshot", 1, value)

    def get_clipboard_hotkey(self) -> str:
        """获取剪贴板管理器快捷键"""
        return self._action_hotkey_pair("clipboard")[0]

    def set_clipboard_hotkey(self, value: str):
        """设置剪贴板管理器快捷键"""
        self._set_action_hotkey("clipboard", 0, value)

    def get_clipboard_hotkey_2(self) -> str:
        """获取剪贴板管理器备用快捷键"""
        return self._action_hotkey_pair("clipboard")[1]

    def set_clipboard_hotkey_2(self, value: str):
        """设置剪贴板管理器备用快捷键"""
        self._set_action_hotkey("clipboard", 1, value)

    def get_translation_hotkey(self) -> str:
        """获取智能翻译全局快捷键。"""
        return self._action_hotkey_pair("open_translation")[0]

    def set_translation_hotkey(self, value: str):
        """保存智能翻译全局快捷键。"""
        self._set_action_hotkey("open_translation", 0, value)

    def get_translation_hotkey_2(self) -> str:
        """获取智能翻译备用全局快捷键。"""
        return self._action_hotkey_pair("open_translation")[1]

    def set_translation_hotkey_2(self, value: str):
        """保存智能翻译备用全局快捷键。"""
        self._set_action_hotkey("open_translation", 1, value)

    # ---------- 应用内快捷键 ----------
    def get_inapp_shortcut(self, key: str) -> str:
        """获取应用内快捷键 (key 示例: 'inapp_confirm')"""
        return self.qsettings.value(
            f"inapp/{key}", self.APP_DEFAULT_SETTINGS.get(key, ""), type=str
        )

    def set_inapp_shortcut(self, key: str, value: str):
        """设置应用内快捷键"""
        self.qsettings.setValue(f"inapp/{key}", value)

    def get_inapp_cursor_move_mode(self) -> str:
        """获取鼠标微移模式 (both / arrows / wasd)"""
        return self.qsettings.value(
            "inapp/inapp_cursor_move_mode",
            self.APP_DEFAULT_SETTINGS["inapp_cursor_move_mode"], type=str
        )

    def set_inapp_cursor_move_mode(self, value: str):
        """设置鼠标微移模式"""
        self.qsettings.setValue("inapp/inapp_cursor_move_mode", value)

    def get_ui_detection(self) -> str:
        """获取「UI 检测」档位：``none`` / ``window`` / ``element``。

        兼容旧的 ``app/smart_selection``（Bool）：没有新键时 True 映射到默认档
        （element，用户要的就是「把这功能做细」），False 映射到 none。
        """
        raw = self.qsettings.value("app/ui_detection", None)
        mode = normalize_ui_detection(raw)
        if mode is not None:
            return mode

        legacy = self._legacy_smart_selection()
        if legacy is not None:
            return normalize_ui_detection(legacy)
        return self.APP_DEFAULT_SETTINGS["ui_detection"]

    def _legacy_smart_selection(self) -> bool | None:
        """旧的 ``smart_selection`` 原始值 → 是否开启；没设置过返回 None。

        不能直接 `value(..., type=bool)`：QSettings 会把「键不存在」也当成 False，
        迁移逻辑就会把全新安装的默认档吃掉。
        """
        raw = self.qsettings.value("app/smart_selection", None)
        if raw is None:
            return None
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    def set_ui_detection(self, value: str):
        """设置「UI 检测」档位（非法值回落到默认档）。"""
        mode = normalize_ui_detection(value) or self.APP_DEFAULT_SETTINGS["ui_detection"]
        self.qsettings.setValue("app/ui_detection", mode)

    def get_ui_detection_margin(self) -> int:
        """获取元素检测边距（px，仅 element 档生效）。"""
        value = self.qsettings.value(
            "app/ui_detection_margin", self.APP_DEFAULT_SETTINGS["ui_detection_margin"], type=int)
        return max(0, min(int(value), MAX_ELEMENT_MARGIN))

    def set_ui_detection_margin(self, value: int):
        """设置元素检测边距（超范围会被夹到 0..MAX_ELEMENT_MARGIN）。"""
        clamped = max(0, min(int(value), MAX_ELEMENT_MARGIN))
        self.qsettings.setValue("app/ui_detection_margin", clamped)

    def get_mouse_gestures(self) -> dict:
        """读取全局鼠标手势绑定：``{action_id: {"modifier": str, "gesture": str}}``。

        存成 JSON 字符串；坏数据（手工改坏、旧版本残留）当空表处理——设置窗口因此照常
        打开，只是显示成「未绑定」，比抛异常把界面挡住好。
        """
        import json

        raw = self.qsettings.value("app/mouse_gestures", "", type=str)
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
        except Exception:
            from core.logger import log_warning, T

            log_warning(T("全局鼠标绑定配置无法解析，按未绑定处理"), "Settings")
            return {}
        if not isinstance(loaded, dict):
            return {}
        bindings = {}
        for action_id, binding in loaded.items():
            if isinstance(action_id, str) and isinstance(binding, dict):
                bindings[action_id] = {
                    "modifier": str(binding.get("modifier", "")),
                    "gesture": str(binding.get("gesture", "")),
                }
        return bindings

    def set_mouse_gestures(self, bindings: dict) -> None:
        """写入全局鼠标手势绑定（整体替换）。"""
        import json

        self.qsettings.setValue(
            "app/mouse_gestures", json.dumps(bindings or {}, ensure_ascii=False)
        )

    def set_mouse_gesture(self, action_id: str, modifier: str, gesture: str) -> None:
        """绑定/解绑单个动作：``gesture`` 为空表示解绑。"""
        bindings = self.get_mouse_gestures()
        if not gesture:
            bindings.pop(action_id, None)
        else:
            bindings[action_id] = {"modifier": modifier or "", "gesture": gesture}
        self.set_mouse_gestures(bindings)

    #: 旧热键键 → 新表里的位置。只用于一次性搬迁，搬完就不再读它们。
    _LEGACY_HOTKEY_KEYS = (
        ("screenshot", "app/hotkey", "app/hotkey_2"),
        ("clipboard", "clipboard/hotkey", "clipboard/hotkey_2"),
        ("open_translation", "app/translation_hotkey", "app/translation_hotkey_2"),
    )

    def get_action_hotkeys(self) -> dict:
        """读取全局动作的快捷键表：``{动作 id: [主热键, 备用热键]}``。

        表不存在时从旧键（app/hotkey 等六个）搬一次并写回；坏数据按未绑定处理——
        和鼠标手势一样，宁可少一个绑定，也不要让设置页打不开。
        """
        import json

        raw = self.qsettings.value("app/action_hotkeys", "", type=str)
        if not raw:
            table = self._migrate_legacy_hotkeys()
            self.set_action_hotkeys(table)
            return table
        try:
            loaded = json.loads(raw)
        except Exception:
            from core.logger import log_warning, T

            log_warning(T("动作快捷键配置无法解析，按未绑定处理"), "Settings")
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {
            str(action_id): self._normalize_hotkey_pair(keys)
            for action_id, keys in loaded.items()
        }

    @staticmethod
    def _normalize_hotkey_pair(keys) -> list:
        """把一对主/备热键收敛成 ``[str, str]``。"""
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, (list, tuple)):
            keys = []
        pair = [str(key) if key else "" for key in list(keys)[:2]]
        while len(pair) < 2:
            pair.append("")
        return pair

    def _migrate_legacy_hotkeys(self) -> dict:
        """用旧的六个热键键拼出动作快捷键表（没设过的条目用默认表）。"""
        defaults = self.APP_DEFAULT_SETTINGS["action_hotkeys"]
        table = {action_id: list(pair) for action_id, pair in defaults.items()}
        for action_id, primary_key, secondary_key in self._LEGACY_HOTKEY_KEYS:
            fallback = defaults.get(action_id, ["", ""])
            table[action_id] = [
                self.qsettings.value(primary_key, fallback[0], type=str) or "",
                self.qsettings.value(secondary_key, fallback[1], type=str) or "",
            ]
        return table

    def set_action_hotkeys(self, table: dict) -> None:
        """整体替换动作快捷键表。"""
        import json

        normalized = {
            str(action_id): self._normalize_hotkey_pair(keys)
            for action_id, keys in (table or {}).items()
        }
        self.qsettings.setValue(
            "app/action_hotkeys", json.dumps(normalized, ensure_ascii=False)
        )

    def get_action_tray_flags(self) -> dict:
        """读取「哪些动作显示在托盘菜单」：``{动作 id: bool}``。"""
        import json

        raw = self.qsettings.value("app/action_tray", "", type=str)
        default = dict(self.APP_DEFAULT_SETTINGS["action_tray"])
        if not raw:
            return default
        try:
            loaded = json.loads(raw)
        except Exception:
            from core.logger import log_warning, T

            log_warning(T("托盘动作配置无法解析，按默认处理"), "Settings")
            return default
        if not isinstance(loaded, dict):
            return default
        return {str(key): bool(value) for key, value in loaded.items()}

    def set_action_tray_flags(self, flags: dict) -> None:
        """整体替换托盘动作开关表。"""
        import json

        self.qsettings.setValue(
            "app/action_tray",
            json.dumps(
                {str(key): bool(value) for key, value in (flags or {}).items()},
                ensure_ascii=False,
            ),
        )

    def _action_hotkey_pair(self, action_id: str) -> list:
        """取某个动作的主/备热键（缺条目时补空串）。"""
        pair = self.get_action_hotkeys().get(action_id)
        return self._normalize_hotkey_pair(pair)

    def _set_action_hotkey(self, action_id: str, index: int, value: str) -> None:
        """改某个动作的第 index 个热键（0 主 / 1 备）。"""
        table = self.get_action_hotkeys()
        pair = self._normalize_hotkey_pair(table.get(action_id))
        pair[index] = str(value or "")
        table[action_id] = pair
        self.set_action_hotkeys(table)

    def get_mouse_capture_overlay_enabled(self) -> bool:
        """全局鼠标动作截图时是否显示遮罩。"""
        return self.qsettings.value(
            "app/mouse_capture_overlay",
            self.APP_DEFAULT_SETTINGS["mouse_capture_overlay"],
            type=bool,
        )

    def set_mouse_capture_overlay_enabled(self, value: bool):
        """设置全局鼠标动作截图时是否显示遮罩。"""
        self.qsettings.setValue("app/mouse_capture_overlay", bool(value))

    def get_mouse_ignored_apps(self) -> list:
        """忽略程序列表（前台程序名）。

        存成 JSON 字符串，坏数据当空表处理：列表内容直接来自用户可以手改的输入框，
        解析失败时「不忽略任何程序」比让鼠标动作整体失效更合理。
        """
        import json

        raw = self.qsettings.value("app/mouse_ignored_apps", "", type=str)
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
        except Exception:
            from core.logger import log_warning, T

            log_warning(T("忽略程序列表无法解析，按空列表处理"), "Settings")
            return []
        if not isinstance(loaded, list):
            return []
        return [str(name) for name in loaded if str(name).strip()]

    def set_mouse_ignored_apps(self, names) -> None:
        """写入忽略程序列表（整体替换，空白项会被丢掉）。"""
        import json

        cleaned = [str(name).strip() for name in (names or []) if str(name).strip()]
        self.qsettings.setValue(
            "app/mouse_ignored_apps", json.dumps(cleaned, ensure_ascii=False)
        )

    def get_double_click_copy_close_enabled(self) -> bool:
        """获取双击选区后复制并关闭的启用状态。"""
        return self.qsettings.value(
            "app/double_click_copy_close",
            self.APP_DEFAULT_SETTINGS["double_click_copy_close"],
            type=bool,
        )

    def set_double_click_copy_close_enabled(self, value: bool):
        """设置是否双击选区后复制并关闭。"""
        self.qsettings.setValue("app/double_click_copy_close", value)

    def get_cross_tool_selection_enabled(self) -> bool:
        """获取 Ctrl 临时跨工具选择的启用状态。"""
        return self.qsettings.value(
            "app/cross_tool_selection",
            self.APP_DEFAULT_SETTINGS["cross_tool_selection"],
            type=bool,
        )

    def set_cross_tool_selection_enabled(self, value: bool):
        """设置是否允许 Ctrl 临时跨工具选择。"""
        self.qsettings.setValue("app/cross_tool_selection", value)

    def get_text_always_on_top_enabled(self) -> bool:
        """获取文字标注始终置顶的启用状态。"""
        return self.qsettings.value(
            "app/text_always_on_top",
            self.APP_DEFAULT_SETTINGS["text_always_on_top"],
            type=bool,
        )

    def set_text_always_on_top_enabled(self, value: bool):
        """设置文字标注是否始终位于其他绘制标注之上。"""
        self.qsettings.setValue("app/text_always_on_top", value)

    
    def get_log_enabled(self) -> bool:
        """获取日志启用状态"""
        return self.qsettings.value("app/log_enabled", self.APP_DEFAULT_SETTINGS["log_enabled"], type=bool)
    
    def set_log_enabled(self, value: bool):
        """设置日志启用状态"""
        self.qsettings.setValue("app/log_enabled", value)
    
    def get_log_dir(self) -> str:
        """获取日志目录"""
        return self.qsettings.value("app/log_dir", self.APP_DEFAULT_SETTINGS["log_dir"], type=str)
    
    def set_log_dir(self, value: str):
        """设置日志目录"""
        self.qsettings.setValue("app/log_dir", value)
    
    def get_log_level(self) -> str:
        """获取日志等级 (DEBUG, INFO, WARNING, ERROR)"""
        return self.qsettings.value("app/log_level", self.APP_DEFAULT_SETTINGS["log_level"], type=str)
    
    def set_log_level(self, value: str):
        """设置日志等级 (DEBUG, INFO, WARNING, ERROR)"""
        self.qsettings.setValue("app/log_level", value)
    
    def get_log_retention_days(self) -> int:
        """获取日志保留天数（0表示永久保留）"""
        return self.qsettings.value("app/log_retention_days", self.APP_DEFAULT_SETTINGS["log_retention_days"], type=int)
    
    def set_log_retention_days(self, value: int):
        """设置日志保留天数（0表示永久保留）"""
        self.qsettings.setValue("app/log_retention_days", value)
    
    def get_long_stitch_engine(self) -> str:
        """获取长截图引擎"""
        return self.qsettings.value("app/long_stitch_engine", self.APP_DEFAULT_SETTINGS["long_stitch_engine"], type=str)
    
    def set_long_stitch_engine(self, value: str):
        """设置长截图引擎"""
        self.qsettings.setValue("app/long_stitch_engine", value)
    
    def get_scroll_cooldown(self) -> float:
        """获取滚动后等待时间（秒）"""
        return self.qsettings.value("screenshot/scroll_cooldown", self.APP_DEFAULT_SETTINGS["scroll_cooldown"], type=float)
    
    def set_scroll_cooldown(self, value: float):
        """设置滚动后等待时间（秒，0.05-1.0）"""
        self.qsettings.setValue("screenshot/scroll_cooldown", value)

    def get_long_stitch_ignore_top_pixels(self) -> int:
        """获取后续截图顶部忽略像素数"""
        return self.qsettings.value(
            "screenshot/long_stitch_ignore_top_pixels",
            self.APP_DEFAULT_SETTINGS["long_stitch_ignore_top_pixels"],
            type=int,
        )

    def set_long_stitch_ignore_top_pixels(self, value: int):
        """设置后续截图顶部忽略像素数"""
        self.qsettings.setValue("screenshot/long_stitch_ignore_top_pixels", value)
    
    def get_screenshot_save_enabled(self) -> bool:
        """获取截图自动保存"""
        return self.qsettings.value("app/screenshot_save_enabled", self.APP_DEFAULT_SETTINGS["screenshot_save_enabled"], type=bool)
    
    def set_screenshot_save_enabled(self, value: bool):
        """设置截图自动保存"""
        self.qsettings.setValue("app/screenshot_save_enabled", value)
    
    def get_screenshot_save_path(self) -> str:
        """获取截图保存路径"""
        default_path = self.APP_DEFAULT_SETTINGS["screenshot_save_path"]
        path = self.qsettings.value("app/screenshot_save_path", default_path, type=str)
        # 防御性校验：路径必须是有效的绝对路径，否则回退到默认值
        if not path or not os.path.isabs(path):
            path = default_path
            self.qsettings.setValue("app/screenshot_save_path", path)
        return path
    
    def set_screenshot_save_path(self, value: str):
        """设置截图保存路径"""
        if not value or not os.path.isabs(value):
            return
        self.qsettings.setValue("app/screenshot_save_path", value)

    #: PDF 页尺寸（见 core/save.py 的 PDF_PAGE_SIZES）
    PDF_PAGE_SIZES = ("original", "a4_portrait", "a4_landscape")

    def get_pdf_page_size(self) -> str:
        """PDF 页尺寸：原图尺寸 / A4 纵 / A4 横。"""
        return self._choice("pdf_page_size", self.PDF_PAGE_SIZES, "original")

    def set_pdf_page_size(self, value: str):
        self.set_app_setting("pdf_page_size", str(value or "original"))

    def get_screenshot_format(self) -> str:
        """获取截图保存格式 (PNG/JPG/BMP/WEBP/PDF)"""
        return self.qsettings.value("app/screenshot_format", self.APP_DEFAULT_SETTINGS["screenshot_format"], type=str)

    def set_screenshot_format(self, value: str):
        """设置截图保存格式 (PNG/JPG/BMP/WEBP/PDF)"""
        self.qsettings.setValue("app/screenshot_format", value.upper())

    def get_screenshot_quality(self) -> int:
        """获取截图保存质量 (1-100, PNG/BMP时忽略)"""
        return self.qsettings.value("app/screenshot_quality", self.APP_DEFAULT_SETTINGS["screenshot_quality"], type=int)

    #: 单条保存路径的字段
    SAVE_PATH_FIELDS = ("path", "format", "quality")

    def get_save_paths(self) -> list:
        """多保存路径列表；没配过时返回「单路径」那一条（兼容老配置）。"""
        raw = self.get_app_setting("screenshot_save_paths", [])
        paths = []
        if isinstance(raw, str) and raw.strip():
            import json

            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                from core.logger import log_warning, T

                log_warning(T("保存路径列表格式不对，按单路径处理"), "Settings")
                raw = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    item = {"path": item}
                if not isinstance(item, dict):
                    continue
                folder = str(item.get("path", "") or "").strip()
                if not folder:
                    continue
                from core.image_formats import preferred_format

                fmt = preferred_format(item.get("format") or self.get_screenshot_format())
                try:
                    quality = max(1, min(100, int(item.get("quality", self.get_screenshot_quality()))))
                except (TypeError, ValueError):
                    quality = self.get_screenshot_quality()
                paths.append({"path": folder, "format": fmt, "quality": quality})
        if not paths:
            folder = self.get_screenshot_save_path()
            if folder:
                paths.append({"path": folder, "format": self.get_screenshot_format(),
                              "quality": self.get_screenshot_quality()})
        return paths

    def set_save_paths(self, paths) -> None:
        """写入多保存路径列表；传空表时就是「没配过」（读回来会退回单路径）。"""
        import json

        cleaned = []
        for item in paths or []:
            if isinstance(item, str):
                item = {"path": item}
            if not isinstance(item, dict):
                continue
            folder = str(item.get("path", "") or "").strip()
            if not folder:
                continue
            cleaned.append({
                "path": folder,
                "format": str(item.get("format") or "PNG").upper(),
                "quality": max(1, min(100, int(item.get("quality", 85) or 85))),
            })
        self.set_app_setting("screenshot_save_paths", json.dumps(cleaned, ensure_ascii=False))

    def set_screenshot_quality(self, value: int):
        """设置截图保存质量 (1-100)"""
        self.qsettings.setValue("app/screenshot_quality", max(1, min(100, int(value))))

    def get_show_main_window(self) -> bool:
        """获取主窗口显示设置"""
        return self.qsettings.value("app/show_main_window", self.APP_DEFAULT_SETTINGS["show_main_window"], type=bool)
    
    def set_show_main_window(self, value: bool):
        """设置主窗口显示"""
        self.qsettings.setValue("app/show_main_window", value)
    
    def get_ocr_enabled(self) -> bool:
        """获取钉图后自动 OCR 状态（方法名为兼容旧版本保留）"""
        return self.qsettings.value("app/ocr_enabled", self.APP_DEFAULT_SETTINGS["ocr_enabled"], type=bool)
    
    def set_ocr_enabled(self, value: bool):
        """设置钉图后自动 OCR 状态（方法名为兼容旧版本保留）"""
        self.qsettings.setValue("app/ocr_enabled", value)
    
    def get_ocr_engine(self) -> str:
        """获取 OCR 引擎类型"""
        return self.qsettings.value("app/ocr_engine", self.APP_DEFAULT_SETTINGS["ocr_engine"], type=str)
    
    def set_ocr_engine(self, value: str):
        """设置 OCR 引擎类型"""
        self.qsettings.setValue("app/ocr_engine", value)
    
    def get_ocr_grayscale_enabled(self) -> bool:
        """获取 OCR 灰度化"""
        return self.qsettings.value("app/ocr_grayscale", self.APP_DEFAULT_SETTINGS["ocr_grayscale"], type=bool)
    
    # ==================== 钉图设置 ====================

    # 值的可调范围只在这里定义：设置页的控件范围与贴图窗口的夹取用的是同一份，
    # 两边各写一组数字迟早会不一致（界面上能选到、窗口里却被夹掉）。

    def get_pin_auto_toolbar(self) -> bool:
        """获取钉图是否自动显示工具栏"""
        return self.qsettings.value("pin/auto_toolbar", self.APP_DEFAULT_SETTINGS["pin_auto_toolbar"], type=bool)
    
    def set_pin_auto_toolbar(self, enabled: bool):
        """设置钉图自动显示工具栏"""
        self.qsettings.setValue("pin/auto_toolbar", enabled)
    
    def get_pin_default_opacity(self) -> float:
        """获取钉图默认透明度（夹到 PIN_OPACITY_RANGE）"""
        value = self.qsettings.value(
            "pin/default_opacity", self.APP_DEFAULT_SETTINGS["pin_default_opacity"], type=float)
        return max(PIN_OPACITY_RANGE[0], min(PIN_OPACITY_RANGE[1], float(value)))

    def set_pin_default_opacity(self, opacity: float):
        """设置钉图默认透明度"""
        clamped = max(PIN_OPACITY_RANGE[0], min(PIN_OPACITY_RANGE[1], float(opacity)))
        self.qsettings.setValue("pin/default_opacity", clamped)

    def get_pin_zoom_step(self) -> float:
        """滚轮缩放步长（倍率，夹到 PIN_ZOOM_STEP_RANGE）。"""
        value = self.qsettings.value(
            "pin/zoom_step", self.APP_DEFAULT_SETTINGS["pin_zoom_step"], type=float)
        return max(PIN_ZOOM_STEP_RANGE[0], min(PIN_ZOOM_STEP_RANGE[1], float(value)))

    def set_pin_zoom_step(self, step: float):
        clamped = max(PIN_ZOOM_STEP_RANGE[0], min(PIN_ZOOM_STEP_RANGE[1], float(step)))
        self.qsettings.setValue("pin/zoom_step", clamped)

    def get_pin_opacity_step(self) -> float:
        """Ctrl+滚轮调整透明度的步长（夹到 PIN_OPACITY_STEP_RANGE）。"""
        value = self.qsettings.value(
            "pin/opacity_step", self.APP_DEFAULT_SETTINGS["pin_opacity_step"], type=float)
        return max(PIN_OPACITY_STEP_RANGE[0], min(PIN_OPACITY_STEP_RANGE[1], float(value)))

    def set_pin_opacity_step(self, step: float):
        clamped = max(PIN_OPACITY_STEP_RANGE[0], min(PIN_OPACITY_STEP_RANGE[1], float(step)))
        self.qsettings.setValue("pin/opacity_step", clamped)

    def get_pin_shadow_enabled(self) -> bool:
        """新贴图是否默认带阴影/描边。"""
        return self.qsettings.value(
            "pin/shadow_enabled", self.APP_DEFAULT_SETTINGS["pin_shadow_enabled"], type=bool)

    def set_pin_shadow_enabled(self, enabled: bool):
        self.qsettings.setValue("pin/shadow_enabled", bool(enabled))

    def get_pin_new_position(self) -> str:
        """新贴图的位置策略：selection / cursor / center（非法值回落默认）。"""
        raw = str(self.qsettings.value(
            "pin/new_position", self.APP_DEFAULT_SETTINGS["pin_new_position"], type=str) or "")
        return raw if raw in PIN_NEW_POSITIONS else self.APP_DEFAULT_SETTINGS["pin_new_position"]

    def set_pin_new_position(self, mode: str):
        self.qsettings.setValue(
            "pin/new_position",
            mode if mode in PIN_NEW_POSITIONS else self.APP_DEFAULT_SETTINGS["pin_new_position"],
        )

    def get_pin_order(self) -> str:
        """新贴图的叠放顺序：top / bottom（非法值回落默认）。"""
        raw = str(self.qsettings.value(
            "pin/order", self.APP_DEFAULT_SETTINGS["pin_order"], type=str) or "")
        return raw if raw in PIN_ORDER_MODES else self.APP_DEFAULT_SETTINGS["pin_order"]

    def set_pin_order(self, mode: str):
        self.qsettings.setValue(
            "pin/order", mode if mode in PIN_ORDER_MODES else self.APP_DEFAULT_SETTINGS["pin_order"])

    def get_pin_mouse_actions(self) -> dict:
        """读取贴图窗口的手势绑定：``{手势: 动作 id}``。

        这里只做「读」：手势词汇与默认表在 ``pin/pin_actions.py``（那边是唯一出处，
        在这边再抄一份迟早会不一致）。坏数据同样交给那边收敛。
        """
        import json

        raw = self.qsettings.value("pin/mouse_actions", "", type=str)
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
        except Exception:
            from core.logger import log_warning, T

            log_warning(T("贴图手势配置无法解析，按默认处理"), "Settings")
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def set_pin_mouse_actions(self, bindings: dict) -> None:
        """整体替换贴图手势绑定表。"""
        import json

        self.qsettings.setValue(
            "pin/mouse_actions",
            json.dumps({str(k): str(v) for k, v in (bindings or {}).items()},
                       ensure_ascii=False),
        )

    def get_pin_restore_on_startup(self) -> bool:
        """退出时是否保存未关闭的贴图，并在下次启动恢复。"""
        return self.qsettings.value(
            "pin/restore_on_startup", self.APP_DEFAULT_SETTINGS["pin_restore_on_startup"],
            type=bool)

    def set_pin_restore_on_startup(self, enabled: bool):
        self.qsettings.setValue("pin/restore_on_startup", bool(enabled))

    def get_pin_history_limit(self) -> int:
        """最多记住多少张贴图（夹到 PIN_HISTORY_RANGE）。"""
        value = self.qsettings.value(
            "pin/history_limit", self.APP_DEFAULT_SETTINGS["pin_history_limit"], type=int)
        return max(PIN_HISTORY_RANGE[0], min(PIN_HISTORY_RANGE[1], int(value)))

    def set_pin_history_limit(self, limit: int):
        clamped = max(PIN_HISTORY_RANGE[0], min(PIN_HISTORY_RANGE[1], int(limit)))
        self.qsettings.setValue("pin/history_limit", clamped)

    def get_pin_text_font_size(self) -> int:
        """文字贴图的字号（夹到 TEXT_FONT_SIZE_RANGE）。"""
        from pin.pin_text_pin import TEXT_FONT_SIZE_RANGE

        value = self.qsettings.value(
            "pin/text_font_size", self.APP_DEFAULT_SETTINGS["pin_text_font_size"], type=int)
        return max(TEXT_FONT_SIZE_RANGE[0], min(TEXT_FONT_SIZE_RANGE[1], int(value)))

    def set_pin_text_font_size(self, size: int):
        from pin.pin_text_pin import TEXT_FONT_SIZE_RANGE

        clamped = max(TEXT_FONT_SIZE_RANGE[0], min(TEXT_FONT_SIZE_RANGE[1], int(size)))
        self.qsettings.setValue("pin/text_font_size", clamped)

    def get_pin_text_max_width(self) -> int:
        """文字贴图内容区最大宽度（夹到 TEXT_MAX_WIDTH_RANGE）。"""
        from pin.pin_text_pin import TEXT_MAX_WIDTH_RANGE

        value = self.qsettings.value(
            "pin/text_max_width", self.APP_DEFAULT_SETTINGS["pin_text_max_width"], type=int)
        return max(TEXT_MAX_WIDTH_RANGE[0], min(TEXT_MAX_WIDTH_RANGE[1], int(value)))

    def set_pin_text_max_width(self, width: int):
        from pin.pin_text_pin import TEXT_MAX_WIDTH_RANGE

        clamped = max(TEXT_MAX_WIDTH_RANGE[0], min(TEXT_MAX_WIDTH_RANGE[1], int(width)))
        self.qsettings.setValue("pin/text_max_width", clamped)

    def get_pin_close_confirm(self) -> bool:
        """关闭贴图前是否二次确认（退出程序时不问）。"""
        return self.qsettings.value(
            "pin/close_confirm", self.APP_DEFAULT_SETTINGS["pin_close_confirm"], type=bool)

    def set_pin_close_confirm(self, enabled: bool):
        self.qsettings.setValue("pin/close_confirm", bool(enabled))
    
    def set_ocr_grayscale_enabled(self, value: bool):
        """设置 OCR 灰度化"""
        self.qsettings.setValue("app/ocr_grayscale", value)
    
    def get_ocr_upscale_enabled(self) -> bool:
        """获取 OCR 放大"""
        return self.qsettings.value("app/ocr_upscale", self.APP_DEFAULT_SETTINGS["ocr_upscale"], type=bool)
    
    def set_ocr_upscale_enabled(self, value: bool):
        """设置 OCR 放大"""
        self.qsettings.setValue("app/ocr_upscale", value)
    
    def get_ocr_upscale_factor(self) -> float:
        """获取 OCR 放大倍数"""
        return self.qsettings.value("app/ocr_upscale_factor", self.APP_DEFAULT_SETTINGS["ocr_upscale_factor"], type=float)
    
    def set_ocr_upscale_factor(self, value: float):
        """设置 OCR 放大倍数"""
        self.qsettings.setValue("app/ocr_upscale_factor", value)

    # ==================== 识别结果 / 系统集成（2026-09-19 按参考清单补）====================
    #
    # 这一组都做「白名单 + 兜底」：取值只认列出的几个，读到别的（旧版本、手改配置、
    # 损坏的值）一律退回默认。设置页读它们来填控件，行为侧读它们来做判断，两边不会
    # 因为一个坏值各行其是。

    #: 识别结果的文本布局
    OCR_TEXT_LAYOUTS = ("auto", "lines", "single")
    #: 标点处理
    OCR_PUNCTUATIONS = ("none", "strip_trailing", "to_halfwidth")
    #: 识别语言：跟随界面语言 / 具体语言
    OCR_LANGUAGES = ("follow_app", "zh", "en", "ja", "ko")
    #: 什么时候弹识别结果对话框
    OCR_DIALOG_TRIGGERS = ("capture", "copy_all", "copy_selection")

    def _choice(self, key: str, allowed: tuple, default: str) -> str:
        """读一个「只能是这几个值」的设置；读到别的退回默认。"""
        value = str(self.get_app_setting(key, default) or "").strip().lower()
        return value if value in allowed else default

    #: 视觉模型的输出格式
    OCR_VISION_TARGETS = ("markdown", "html")

    def get_ocr_model_tier(self) -> str:
        """识别模型档位；配置里的档位在当前机器上不可用时退回可用档位。"""
        from ocr.model_tiers import DEFAULT_TIER, selected_tier

        return selected_tier(self, fallback=DEFAULT_TIER)

    def set_ocr_model_tier(self, tier: str):
        self.set_app_setting("ocr_model_tier", str(tier or "").strip())

    def get_ocr_vision_target(self) -> str:
        """视觉模型输出 Markdown 还是 HTML。"""
        return self._choice("ocr_vision_target", self.OCR_VISION_TARGETS, "markdown")

    def set_ocr_vision_target(self, target: str):
        self.set_app_setting("ocr_vision_target", str(target or "markdown"))

    def get_ocr_vision_task(self) -> str:
        """视觉模型的任务模板 id。合法清单来自 ocr.vision_models（单一出处）。"""
        from ocr.vision_models import TASKS_BY_ID

        return self._choice("ocr_vision_task", tuple(TASKS_BY_ID), "table")

    def set_ocr_vision_task(self, task: str):
        self.set_app_setting("ocr_vision_task", str(task or "").strip())

    def get_ocr_low_confidence_threshold(self) -> float:
        """置信度提示的阈值；范围外的值夹回 [0.05, 1.0]。"""
        raw = self.get_app_setting("ocr_low_confidence_threshold", 0.6)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.6
        return max(0.05, min(1.0, value))

    def set_ocr_low_confidence_threshold(self, value) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.6
        self.set_app_setting("ocr_low_confidence_threshold", max(0.05, min(1.0, number)))

    def get_ocr_text_layout(self) -> str:
        """识别结果的文本布局（见 OCR_TEXT_LAYOUTS）。"""
        return self._choice("ocr_text_layout", self.OCR_TEXT_LAYOUTS, "auto")

    def set_ocr_text_layout(self, value: str):
        self.set_app_setting("ocr_text_layout", value)

    def get_ocr_punctuation(self) -> str:
        """识别结果的标点处理（见 OCR_PUNCTUATIONS）。"""
        return self._choice("ocr_punctuation", self.OCR_PUNCTUATIONS, "none")

    def set_ocr_punctuation(self, value: str):
        self.set_app_setting("ocr_punctuation", value)

    def get_ocr_language(self) -> str:
        """识别语言（见 OCR_LANGUAGES）。"""
        return self._choice("ocr_language", self.OCR_LANGUAGES, "follow_app")

    def set_ocr_language(self, value: str):
        self.set_app_setting("ocr_language", value)

    def get_ocr_dialog_triggers(self) -> list:
        """什么时候额外弹出识别结果对话框；返回 OCR_DIALOG_TRIGGERS 的子集。"""
        import json

        raw = self.qsettings.value("app/ocr_dialog_triggers", "", type=str)
        if not raw:
            default = self.APP_DEFAULT_SETTINGS["ocr_dialog_triggers"]
            return [item for item in default if item in self.OCR_DIALOG_TRIGGERS]
        try:
            loaded = json.loads(raw)
        except Exception:
            from core.logger import log_warning, T

            log_warning(T("识别结果对话框配置无法解析，按不弹窗处理"), "Settings")
            return []
        if not isinstance(loaded, (list, tuple)):
            return []
        return [str(item) for item in loaded if str(item) in self.OCR_DIALOG_TRIGGERS]

    def set_ocr_dialog_triggers(self, triggers) -> None:
        """整体替换触发时机表；非白名单值直接丢掉。"""
        import json

        values = [str(item) for item in (triggers or [])
                  if str(item) in self.OCR_DIALOG_TRIGGERS]
        self.qsettings.setValue("app/ocr_dialog_triggers", json.dumps(values))

    #: 复制图片项时的形态
    CLIPBOARD_IMAGE_COPY_MODES = ("auto", "image_only", "file_only")

    def get_clipboard_image_copy_mode(self) -> str:
        """复制图片项时写进系统剪贴板的形态（见 CLIPBOARD_IMAGE_COPY_MODES）。"""
        return self._choice("clipboard_image_copy_mode", self.CLIPBOARD_IMAGE_COPY_MODES, "auto")

    def set_clipboard_image_copy_mode(self, value: str):
        self.set_app_setting("clipboard_image_copy_mode", value)

    def get_clipboard_ignore_own_copy(self) -> bool:
        """是否忽略「本程序自己写入剪贴板」的内容（不记历史、不自动粘贴）。"""
        return self.qsettings.value(
            "app/clipboard_ignore_own_copy",
            self.APP_DEFAULT_SETTINGS["clipboard_ignore_own_copy"],
            type=bool,
        )

    def set_clipboard_ignore_own_copy(self, value: bool):
        self.set_app_setting("clipboard_ignore_own_copy", bool(value))

    #: 桌面工具栏形态
    DESKTOP_TOOLBAR_MODES = ("none", "floating_ball")

    def get_desktop_toolbar_mode(self) -> str:
        """桌面工具栏：不显示 / 悬浮球（见 DESKTOP_TOOLBAR_MODES）。"""
        return self._choice("desktop_toolbar_mode", self.DESKTOP_TOOLBAR_MODES, "none")

    def set_desktop_toolbar_mode(self, value: str):
        self.set_app_setting("desktop_toolbar_mode", value)

    def get_desktop_toolbar_position(self) -> tuple | None:
        """悬浮球的位置 ``(x, y)``；没设过或值坏了返回 None（调用方用默认位置）。"""
        raw = str(self.get_app_setting("desktop_toolbar_position", "") or "").strip()
        if not raw:
            return None
        parts = raw.split(",")
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    def set_desktop_toolbar_position(self, x: int, y: int) -> None:
        self.set_app_setting("desktop_toolbar_position", f"{int(x)},{int(y)}")

    def get_tray_click_action(self) -> str:
        """托盘单击执行的动作 id；id 不在注册表里时退回截图。"""
        from core import actions

        action_id = str(self.get_app_setting("tray_click_action", "screenshot") or "").strip()
        return action_id if action_id in actions.ACTIONS_BY_ID else "screenshot"

    def set_tray_click_action(self, action_id: str):
        self.set_app_setting("tray_click_action", action_id)

    def get_tray_scroll_action(self) -> str:
        """托盘滚轮（中键）执行的动作 id；空串表示不做事。"""
        from core import actions

        action_id = str(self.get_app_setting("tray_scroll_action", "") or "").strip()
        return action_id if action_id in actions.ACTIONS_BY_ID else ""

    def set_tray_scroll_action(self, action_id: str):
        self.set_app_setting("tray_scroll_action", str(action_id or ""))

    #: 网络代理
    PROXY_MODES = ("none", "manual")

    def get_proxy_config(self) -> dict:
        """网络代理配置：``{"mode", "host", "port"}``。

        manual 但主机为空 / 端口非法时按「直连」处理——一个填了一半的代理比直连更糟，
        用户会看到所有网络功能都超时却说不出原因。
        """
        mode = self._choice("proxy_mode", self.PROXY_MODES, "none")
        host = str(self.get_app_setting("proxy_host", "") or "").strip()
        try:
            port = int(self.get_app_setting("proxy_port", 0) or 0)
        except (TypeError, ValueError):
            port = 0
        usable = mode == "manual" and bool(host) and 0 < port < 65536
        return {"mode": "manual" if usable else "none", "host": host, "port": port}

    def set_proxy_config(self, mode: str, host: str, port: int) -> None:
        self.set_app_setting("proxy_mode", mode)
        self.set_app_setting("proxy_host", host or "")
        self.set_app_setting("proxy_port", int(port or 0))

    #: 公式识别引擎
    FORMULA_ENGINES = ("ppocr_formula", "external_service")

    def get_formula_engine(self) -> str:
        """公式识别用哪个引擎（见 FORMULA_ENGINES）。"""
        return self._choice("formula_engine", self.FORMULA_ENGINES, "ppocr_formula")

    def set_formula_engine(self, value: str):
        self.set_app_setting("formula_engine", value)

    def get_formula_service_config(self) -> dict:
        """外部公式服务的配置：``{"url", "api_key", "timeout"}``。"""
        try:
            timeout = int(self.get_app_setting("formula_service_timeout", 15) or 15)
        except (TypeError, ValueError):
            timeout = 15
        return {
            "url": str(self.get_app_setting("formula_service_url", "") or "").strip(),
            "api_key": self._get_credential(
                "formula_service_api_key", "app/formula_service_api_key",
            ),
            "timeout": max(1, min(120, timeout)),
        }

    def set_formula_service_config(self, url: str, api_key: str, timeout: int = 15) -> None:
        self.set_app_setting("formula_service_url", url or "")
        self._set_credential("formula_service_api_key", "app/formula_service_api_key", api_key)
        self.set_app_setting("formula_service_timeout", int(timeout or 15))

    def get_update_source_url(self) -> str:
        """更新源地址；留空则用内置的 GitHub 地址（core/updates.py）。"""
        from core.updates import DEFAULT_UPDATE_SOURCE

        return str(self.get_app_setting("update_source_url", "") or "").strip() or DEFAULT_UPDATE_SOURCE

    def set_update_source_url(self, url: str):
        self.set_app_setting("update_source_url", url or "")


    # ==================== 翻译设置 ====================

    def get_translation_provider(self) -> str:
        """获取当前翻译引擎 ID。"""
        return self.qsettings.value(
            "translation/active_provider",
            self.APP_DEFAULT_SETTINGS["translation_provider"],
            type=str,
        )

    def set_translation_provider(self, provider_id: str):
        """设置当前翻译引擎 ID。"""
        self.qsettings.setValue(
            "translation/active_provider",
            (provider_id or "deepl").strip().lower(),
        )

    def get_translation_provider_config(self, provider_id: str) -> dict:
        """返回指定 Provider 的配置；新增引擎只需在这里接入其持久化字段。"""
        provider_id = (provider_id or "").strip().lower()
        if provider_id == "deepl":
            return {
                "api_key": self.get_deepl_api_key() or "",
                "use_pro": self.get_deepl_use_pro(),
            }
        if provider_id == "amazon":
            return {
                "region": self.get_amazon_translate_region(),
                "access_key_id": self.get_amazon_translate_access_key_id(),
                "secret_access_key": (
                    self.get_amazon_translate_secret_access_key()
                ),
                "session_token": self.get_amazon_translate_session_token(),
            }
        if provider_id == "google":
            return {"api_key": self.get_google_translate_api_key()}
        if provider_id == "azure":
            return {
                "api_key": self.get_azure_translate_api_key(),
                "region": self.get_azure_translate_region(),
                "endpoint": self.get_azure_translate_endpoint(),
            }
        if provider_id == "local":
            # 本地离线引擎没有密钥，只有"用哪个模型"（空 = 用第一个装好的）
            return {"model_id": self.get_local_model_id()}
        return {}

    # ==================== 本地离线引擎 ====================

    def get_local_model_id(self) -> str:
        """离线引擎使用的模型 id；空表示用第一个已安装的模型。"""
        return self.qsettings.value(
            "translation/local_model_id",
            self.APP_DEFAULT_SETTINGS["local_model_id"],
            type=str,
        )

    def set_local_model_id(self, value: str):
        self.qsettings.setValue("translation/local_model_id", value or "")
    
    def _get_credential(self, name: str, legacy_key: str) -> str:
        """读一个凭据：系统密钥库优先，其次旧的明文键（读到就顺手迁移）。

        迁移是**读时**做的：用户升级后第一次打开设置页/第一次翻译就会把明文挪走，不需要
        另写一段一次性升级脚本，也不会因为用户从不打开设置页而永远留着明文。
        密钥库不可用（Linux）时退回明文键，行为与改动前一致。
        """
        secret_store = self.secret_store
        if not secret_store.is_available():
            return str(self.qsettings.value(legacy_key, "", type=str) or "")

        stored = secret_store.get_secret(name)
        legacy = str(self.qsettings.value(legacy_key, "", type=str) or "").strip()

        if legacy:
            # 明文和密钥库同时有值且不一样时**以明文为准**：升级前设置页显示给用户的就是
            # 这个值，迁移后应保持用户看到的那一份，而不是被一份来路不明的旧条目顶掉。
            # 两份都在说明中间出过岔子（换了机器、并行装过一版），所以留一条日志。
            from core.logger import T, log_info, log_warning

            if stored and stored != legacy:
                log_warning(
                    T("密钥库里已有一份 {name}，以设置里的值为准", name=name), "Settings",
                )
            if secret_store.set_secret(name, legacy):
                self.qsettings.remove(legacy_key)
                log_info(T("已把凭据 {name} 迁移到系统密钥库", name=name), "Settings")
            return legacy

        return stored

    def _set_credential(self, name: str, legacy_key: str, value: str) -> None:
        """写一个凭据：进系统密钥库，并清掉可能残留的明文副本。空值 = 删除。"""
        secret_store = self.secret_store
        cleaned = (value or "").strip()
        if not secret_store.is_available():
            self.qsettings.setValue(legacy_key, cleaned)
            return

        if not cleaned:
            secret_store.delete_secret(name)
        elif not secret_store.set_secret(name, cleaned):
            # 写不进密钥库时退回明文：用户刚填的 key 不能静默丢掉，但要留一条日志说明
            from core.logger import T, log_warning

            log_warning(
                T("系统密钥库不可写，凭据 {name} 暂存为明文", name=name), "Settings",
            )
            self.qsettings.setValue(legacy_key, cleaned)
            return
        self.qsettings.remove(legacy_key)

    def get_deepl_api_key(self) -> str:
        """获取 DeepL API 密钥"""
        return self._get_credential("deepl_api_key", "app/deepl_api_key")

    def set_deepl_api_key(self, value: str):
        """设置 DeepL API 密钥"""
        self._set_credential("deepl_api_key", "app/deepl_api_key", value)
    
    def get_deepl_use_pro(self) -> bool:
        """获取是否使用 DeepL Pro API"""
        return self.qsettings.value("app/deepl_use_pro", self.APP_DEFAULT_SETTINGS["deepl_use_pro"], type=bool)
    
    def set_deepl_use_pro(self, value: bool):
        """设置是否使用 DeepL Pro API"""
        self.qsettings.setValue("app/deepl_use_pro", value)

    def get_amazon_translate_region(self) -> str:
        return self.qsettings.value(
            "translation/providers/amazon/region",
            self.APP_DEFAULT_SETTINGS["amazon_translate_region"],
            type=str,
        )

    def set_amazon_translate_region(self, value: str):
        self.qsettings.setValue(
            "translation/providers/amazon/region",
            (value or "us-west-2").strip(),
        )

    def get_amazon_translate_access_key_id(self) -> str:
        return self._get_credential(
            "amazon_translate_access_key_id",
            "translation/providers/amazon/access_key_id",
        )

    def set_amazon_translate_access_key_id(self, value: str):
        self._set_credential(
            "amazon_translate_access_key_id",
            "translation/providers/amazon/access_key_id",
            value,
        )

    def get_amazon_translate_secret_access_key(self) -> str:
        return self._get_credential(
            "amazon_translate_secret_access_key",
            "translation/providers/amazon/secret_access_key",
        )

    def set_amazon_translate_secret_access_key(self, value: str):
        self._set_credential(
            "amazon_translate_secret_access_key",
            "translation/providers/amazon/secret_access_key",
            value,
        )

    def get_amazon_translate_session_token(self) -> str:
        return self._get_credential(
            "amazon_translate_session_token",
            "translation/providers/amazon/session_token",
        )

    def set_amazon_translate_session_token(self, value: str):
        self._set_credential(
            "amazon_translate_session_token",
            "translation/providers/amazon/session_token",
            value,
        )

    def get_google_translate_api_key(self) -> str:
        return self._get_credential(
            "google_translate_api_key", "translation/providers/google/api_key",
        )

    def set_google_translate_api_key(self, value: str):
        self._set_credential(
            "google_translate_api_key", "translation/providers/google/api_key", value,
        )

    def get_azure_translate_api_key(self) -> str:
        return self._get_credential(
            "azure_translate_api_key", "translation/providers/azure/api_key",
        )

    def set_azure_translate_api_key(self, value: str):
        self._set_credential(
            "azure_translate_api_key", "translation/providers/azure/api_key", value,
        )

    def get_azure_translate_region(self) -> str:
        return self.qsettings.value(
            "translation/providers/azure/region",
            self.APP_DEFAULT_SETTINGS["azure_translate_region"],
            type=str,
        )

    def set_azure_translate_region(self, value: str):
        self.qsettings.setValue(
            "translation/providers/azure/region",
            (value or "").strip(),
        )

    def get_azure_translate_endpoint(self) -> str:
        return self.qsettings.value(
            "translation/providers/azure/endpoint",
            self.APP_DEFAULT_SETTINGS["azure_translate_endpoint"],
            type=str,
        )

    def set_azure_translate_endpoint(self, value: str):
        self.qsettings.setValue(
            "translation/providers/azure/endpoint",
            (value or "").strip(),
        )
    
    def get_translation_target_lang(self) -> str:
        """
        获取翻译目标语言
        
        如果未设置或为空，则跟随系统语言
        """
        saved = self.qsettings.value("app/translation_target_lang", "", type=str)
        if not saved:
            # 跟随系统语言
            from core.i18n import I18nManager
            sys_lang = I18nManager.get_system_language()
            # 映射到 DeepL 语言代码
            lang_map = {
                "zh": "ZH",
                "ja": "JA", 
                "en": "EN",
            }
            return lang_map.get(sys_lang, "EN")
        return saved
    
    def set_translation_target_lang(self, value: str):
        """设置翻译目标语言"""
        self.qsettings.setValue("app/translation_target_lang", value)
    
    def get_translation_split_sentences(self) -> bool:
        """获取是否启用自动分句"""
        return self.qsettings.value("app/translation_split_sentences", self.APP_DEFAULT_SETTINGS["translation_split_sentences"], type=bool)
    
    def set_translation_split_sentences(self, value: bool):
        """设置是否启用自动分句"""
        self.qsettings.setValue("app/translation_split_sentences", value)
    
    def get_translation_preserve_formatting(self) -> bool:
        """获取是否保留格式"""
        return self.qsettings.value("app/translation_preserve_formatting", self.APP_DEFAULT_SETTINGS["translation_preserve_formatting"], type=bool)
    
    def set_translation_preserve_formatting(self, value: bool):
        """设置是否保留格式"""
        self.qsettings.setValue("app/translation_preserve_formatting", value)
    
    def get_translation_params(self) -> dict:
        """
        获取翻译所需的全部参数（统一入口，避免多处重复获取逻辑）
        
        Returns:
            dict: 包含以下键值:
                - api_key (str)
                - target_lang (str): DeepL 语言代码，如 "ZH", "EN", "JA"
                - use_pro (bool)
                - split_sentences (str): "nonewlines" 或 "0"
                - preserve_formatting (bool)
        """
        api_key = self.get_deepl_api_key() or ""
        
        # 优先读取用户手动保存的目标语言
        saved_target_lang = self.get_app_setting("translation_target_lang", "")
        if saved_target_lang:
            target_lang = saved_target_lang
        else:
            target_lang = self.get_translation_target_lang()
        
        use_pro = self.get_deepl_use_pro()
        split_sentences_enabled = self.get_translation_split_sentences()
        preserve_formatting = self.get_translation_preserve_formatting()
        
        # 转换为 DeepL API 参数: 开启时用 nonewlines（忽略换行），关闭时用 0（不分句）
        split_sentences = "nonewlines" if split_sentences_enabled else "0"
        
        return {
            "api_key": api_key,
            "target_lang": target_lang,
            "use_pro": use_pro,
            "split_sentences": split_sentences,
            "preserve_formatting": preserve_formatting,
        }

    #: 截图翻译的三种呈现
    TRANSLATION_IMAGE_MODES = ("text", "replace", "bilingual")

    def get_translation_image_mode(self) -> str:
        """截图翻译怎么呈现：只给文本 / 原图替换 / 双语对照。"""
        return self._choice("translation_image_mode", self.TRANSLATION_IMAGE_MODES, "replace")

    def set_translation_image_mode(self, mode: str):
        self.set_app_setting("translation_image_mode", str(mode or "replace"))

    def get_translation_request_params(self) -> dict:
        """获取与具体翻译厂商无关的调用参数。"""
        saved_target_lang = self.get_app_setting("translation_target_lang", "")
        return {
            "target_lang": saved_target_lang or self.get_translation_target_lang(),
            "split_sentences": (
                "nonewlines"
                if self.get_translation_split_sentences()
                else "0"
            ),
            "preserve_formatting": self.get_translation_preserve_formatting(),
        }
    
    # ==================== 剪贴板设置 ====================
    
    # 注意：get/set_clipboard_hotkey 和 get/set_clipboard_hotkey_2
    # 已在上方（约 L548）定义，此处不再重复
    
    def get_clipboard_enabled(self) -> bool:
        """获取剪贴板监听是否启用"""
        return self.qsettings.value("clipboard/enabled", self.APP_DEFAULT_SETTINGS["clipboard_enabled"], type=bool)
    
    def set_clipboard_enabled(self, value: bool):
        """设置剪贴板监听是否启用"""
        self.qsettings.setValue("clipboard/enabled", value)
    
    def get_clipboard_auto_paste(self) -> bool:
        """获取是否自动粘贴"""
        return self.qsettings.value("clipboard/auto_paste", self.APP_DEFAULT_SETTINGS["clipboard_auto_paste"], type=bool)
    
    def set_clipboard_auto_paste(self, value: bool):
        """设置是否自动粘贴"""
        self.qsettings.setValue("clipboard/auto_paste", value)
    
    def get_clipboard_history_limit(self) -> int:
        """获取历史记录数量限制"""
        return self.qsettings.value("clipboard/history_limit", self.APP_DEFAULT_SETTINGS["clipboard_history_limit"], type=int)
    
    def set_clipboard_history_limit(self, value: int):
        """设置历史记录数量限制"""
        self.qsettings.setValue("clipboard/history_limit", max(0, value))

    def get_clipboard_db_path(self) -> str:
        """获取剪贴板数据库自定义路径（空字符串表示使用后端默认位置）"""
        return self.qsettings.value("clipboard/db_path", self.APP_DEFAULT_SETTINGS["clipboard_db_path"], type=str)

    def set_clipboard_db_path(self, value: str):
        """设置剪贴板数据库自定义路径"""
        self.qsettings.setValue("clipboard/db_path", value or "")

    # ==================== 截图历史 ====================

    #: 保留天数上限（设置页的下拉选项）
    HISTORY_RETENTION_DAY_OPTIONS = (0, 1, 7, 30, 90, 365)
    #: 条数上限（0 = 不限制）
    HISTORY_MAX_ENTRIES_RANGE = (0, 100000)
    #: 磁盘上限 MiB（0 = 不限制）
    HISTORY_MAX_DISK_RANGE_MB = (0, 100000)

    def get_history_enabled(self) -> bool:
        """是否记录截图历史。"""
        return self.qsettings.value(
            "app/history_enabled", self.APP_DEFAULT_SETTINGS["history_enabled"], type=bool)

    def set_history_enabled(self, value: bool):
        self.set_app_setting("history_enabled", bool(value))

    def get_history_retention_days(self) -> int:
        """保留天数（0 = 永久保留）；不认识的档位退回 0。"""
        value = self.get_app_setting("history_retention_days", 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
        return value if value in self.HISTORY_RETENTION_DAY_OPTIONS else 0

    def set_history_retention_days(self, value: int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        self.set_app_setting("history_retention_days",
                             value if value in self.HISTORY_RETENTION_DAY_OPTIONS else 0)

    def get_history_max_entries(self) -> int:
        """最多保留多少条（0 = 不限制）。"""
        low, high = self.HISTORY_MAX_ENTRIES_RANGE
        try:
            value = int(self.get_app_setting("history_max_entries", 0))
        except (TypeError, ValueError):
            return 0
        return max(low, min(high, value))

    def set_history_max_entries(self, value: int):
        low, high = self.HISTORY_MAX_ENTRIES_RANGE
        self.set_app_setting("history_max_entries", max(low, min(high, self._int_setting(value, "history_max_entries"))))

    def get_history_max_disk_mb(self) -> int:
        """磁盘占用上限 MiB（0 = 不限制）。"""
        low, high = self.HISTORY_MAX_DISK_RANGE_MB
        try:
            value = int(self.get_app_setting("history_max_disk_mb", 0))
        except (TypeError, ValueError):
            return 0
        return max(low, min(high, value))

    def set_history_max_disk_mb(self, value: int):
        low, high = self.HISTORY_MAX_DISK_RANGE_MB
        self.set_app_setting("history_max_disk_mb",
                             max(low, min(high, self._int_setting(value, "history_max_disk_mb"))))

    
    def get_clipboard_auto_cleanup(self) -> bool:
        """获取是否自动清理超出限制的记录"""
        return self.qsettings.value("clipboard/auto_cleanup", self.APP_DEFAULT_SETTINGS["clipboard_auto_cleanup"], type=bool)
    
    def set_clipboard_auto_cleanup(self, value: bool):
        """设置是否自动清理超出限制的记录"""
        self.qsettings.setValue("clipboard/auto_cleanup", value)
    
    def get_clipboard_display_lines(self) -> int:
        """
        [已废弃 2026-01-17] 获取剪贴板项显示行数（1/2）
        
        此方法已废弃，请使用 get_clipboard_font_size() 代替
        为了向后兼容，此方法返回默认值 1
        """
        return 1  # 向后兼容：返回默认值
    
    def set_clipboard_display_lines(self, value: int):
        """
        [已废弃 2026-01-17] 设置剪贴板项显示行数（1/2）
        
        此方法已废弃，请使用 set_clipboard_font_size() 代替
        此方法现在不执行任何操作
        """
        pass  # 向后兼容：不执行任何操作
    
    def get_clipboard_window_opacity(self) -> int:
        """获取剪贴板窗口透明度（0=不透明，数值越大越透明）"""
        return self.qsettings.value("clipboard/window_opacity", self.APP_DEFAULT_SETTINGS["clipboard_window_opacity"], type=int)
    
    def set_clipboard_window_opacity(self, value: int):
        """设置剪贴板窗口透明度"""
        self.qsettings.setValue("clipboard/window_opacity", value)
    
    def get_clipboard_font_size(self) -> int:
        """获取剪贴板项字体大小"""
        return self.qsettings.value("clipboard/font_size", self.APP_DEFAULT_SETTINGS["clipboard_font_size"], type=int)
    
    def set_clipboard_font_size(self, value: int):
        """设置剪贴板项字体大小"""
        self.qsettings.setValue("clipboard/font_size", value)
    
    def get_clipboard_font_size_options(self) -> list:
        """获取剪贴板字体大小选项"""
        return self.APP_DEFAULT_SETTINGS["clipboard_font_size_options"]
    
    def get_clipboard_show_metadata(self) -> bool:
        """获取是否显示时间和来源信息"""
        return self.qsettings.value("clipboard/show_metadata", 
                                    self.APP_DEFAULT_SETTINGS["clipboard_show_metadata"], 
                                    type=bool)
    
    def set_clipboard_show_metadata(self, value: bool):
        """设置是否显示时间和来源信息"""
        self.qsettings.setValue("clipboard/show_metadata", value)
    
    def get_clipboard_preserve_search(self) -> bool:
        """获取是否在关闭时保留搜索栏内容"""
        return self.qsettings.value("clipboard/preserve_search",
                                    self.APP_DEFAULT_SETTINGS["clipboard_preserve_search"],
                                    type=bool)

    def set_clipboard_preserve_search(self, value: bool):
        """设置是否在关闭时保留搜索栏内容"""
        self.qsettings.setValue("clipboard/preserve_search", value)

    def get_clipboard_window_opacity_options(self) -> list:
        """获取剪贴板窗口透明度可选项列表"""
        return self.APP_DEFAULT_SETTINGS["clipboard_window_opacity_options"]
    
    def get_clipboard_line_height_padding(self) -> int:
        """获取多行显示时的额外行高边距（像素）"""
        return self.qsettings.value("clipboard/line_height_padding", 
                                    self.APP_DEFAULT_SETTINGS["clipboard_line_height_padding"], 
                                    type=int)
    
    def set_clipboard_line_height_padding(self, value: int):
        """设置多行显示时的额外行高边距（像素）"""
        self.qsettings.setValue("clipboard/line_height_padding", max(0, value))
    
    def get_clipboard_move_to_top_on_paste(self) -> bool:
        """获取粘贴后是否将内容移到最前（默认 True）"""
        return self.qsettings.value("clipboard/move_to_top_on_paste", True, type=bool)
    
    def set_clipboard_move_to_top_on_paste(self, value: bool):
        """设置粘贴后是否将内容移到最前"""
        self.qsettings.setValue("clipboard/move_to_top_on_paste", value)
    
    def get_clipboard_theme(self) -> str:
        """获取剪贴板窗口主题"""
        return self.qsettings.value("clipboard/theme", self.APP_DEFAULT_SETTINGS["clipboard_theme"], type=str)
    
    def set_clipboard_theme(self, value: str):
        """设置剪贴板窗口主题"""
        self.qsettings.setValue("clipboard/theme", value)

    def get_clipboard_group_bar_position(self) -> str:
        """获取分组栏位置（right/left/top）"""
        v = self.qsettings.value("clipboard/group_bar_position",
                                 self.APP_DEFAULT_SETTINGS["clipboard_group_bar_position"], type=str)
        return v if v in ("right", "left", "top") else "right"

    def set_clipboard_group_bar_position(self, value: str):
        """设置分组栏位置"""
        if value in ("right", "left", "top"):
            self.qsettings.setValue("clipboard/group_bar_position", value)

    # ==================== GIF录制设置 ====================

    def get_gif_fps(self) -> int:
        """获取GIF录制默认帧率"""
        return self.qsettings.value("gif/fps", self.APP_DEFAULT_SETTINGS["gif_fps"], type=int)

    def set_gif_fps(self, value: int):
        """设置GIF录制默认帧率"""
        self.qsettings.setValue("gif/fps", value)

    def get_gif_fps_options(self) -> list:
        """获取GIF帧率可选项列表"""
        return self.APP_DEFAULT_SETTINGS["gif_fps_options"]

    # ==================== 视频录制 ====================

    #: 视频容器（顺序即设置页里的选项顺序）
    VIDEO_CONTAINERS = ("mp4", "mkv", "mov", "avi")
    #: 视频编码
    VIDEO_CODECS = ("h264", "h265")
    #: 清晰度档位：跟随录制区域 / 按高度上限缩放
    VIDEO_QUALITIES = ("source", "1080p", "720p", "480p")
    #: 录制帧率可选项（可在此调整选项）
    VIDEO_FPS_OPTIONS = (15, 24, 30, 60)
    #: 音频来源
    VIDEO_AUDIO_SOURCES = ("none", "microphone")
    #: 码率上限（Mbps）
    VIDEO_BITRATE_RANGE = (0, 200)
    #: 最长录制时长（秒，0 = 不限制）
    VIDEO_MAX_DURATION_RANGE = (0, 24 * 3600)

    def _int_setting(self, value, key: str) -> int:
        """把写进来的值转成整数；转不了就落这个键的默认值（不该让界面传一次脏值就炸）。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(self.APP_DEFAULT_SETTINGS[key])

    def get_video_container(self) -> str:
        """视频容器（见 VIDEO_CONTAINERS）。"""
        return self._choice("video_container", self.VIDEO_CONTAINERS, "mp4")
    def set_video_container(self, value: str):
        self.set_app_setting("video_container", value)

    def get_video_codec(self) -> str:
        """视频编码（见 VIDEO_CODECS）。"""
        return self._choice("video_codec", self.VIDEO_CODECS, "h264")

    def set_video_codec(self, value: str):
        self.set_app_setting("video_codec", value)

    def get_video_fps(self) -> int:
        """录制帧率；不在可选项里就退回默认值（30）。"""
        default = int(self.APP_DEFAULT_SETTINGS["video_fps"])
        value = self.get_app_setting("video_fps", default)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
        return value if value in self.VIDEO_FPS_OPTIONS else default

    def set_video_fps(self, value: int):
        """写帧率；给进来一个不能当数字用的值时落默认值（读的时候本来也会被规范化）。"""
        self.set_app_setting("video_fps", self._int_setting(value, "video_fps"))

    def get_video_fps_options(self) -> list:
        """录制帧率可选项列表。"""
        return list(self.VIDEO_FPS_OPTIONS)

    def get_video_quality(self) -> str:
        """清晰度档位（见 VIDEO_QUALITIES）。"""
        return self._choice("video_quality", self.VIDEO_QUALITIES, "source")

    def set_video_quality(self, value: str):
        self.set_app_setting("video_quality", value)

    def get_video_bitrate_mbps(self) -> int:
        """码率上限（Mbps，0 = 交给编码器决定）。"""
        low, high = self.VIDEO_BITRATE_RANGE
        value = self.get_app_setting("video_bitrate_mbps", 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
        return max(low, min(high, value))

    def set_video_bitrate_mbps(self, value: int):
        low, high = self.VIDEO_BITRATE_RANGE
        value = self._int_setting(value, "video_bitrate_mbps")
        self.set_app_setting("video_bitrate_mbps", max(low, min(high, value)))

    def get_video_audio(self) -> str:
        """音频来源（见 VIDEO_AUDIO_SOURCES）。"""
        return self._choice("video_audio", self.VIDEO_AUDIO_SOURCES, "none")

    def set_video_audio(self, value: str):
        self.set_app_setting("video_audio", value)

    def get_video_audio_device(self) -> str:
        """麦克风设备名；空串表示系统默认设备。"""
        return str(self.get_app_setting("video_audio_device", "") or "").strip()

    def set_video_audio_device(self, value: str):
        self.set_app_setting("video_audio_device", str(value or "").strip())

    def get_video_max_duration_s(self) -> int:
        """最长录制时长（秒，0 = 不限制）。"""
        low, high = self.VIDEO_MAX_DURATION_RANGE
        value = self.get_app_setting("video_max_duration_s", 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
        return max(low, min(high, value))

    def set_video_max_duration_s(self, value: int):
        low, high = self.VIDEO_MAX_DURATION_RANGE
        value = self._int_setting(value, "video_max_duration_s")
        self.set_app_setting("video_max_duration_s", max(low, min(high, value)))

    def get_video_save_path(self) -> str:
        """视频保存目录；空串表示跟随截图保存目录（见 VideoRecordWindow._save_directory）。"""
        return str(self.get_app_setting("video_save_path", "") or "").strip()

    def set_video_save_path(self, value: str):
        self.set_app_setting("video_save_path", str(value or "").strip())


    def is_first_run(self) -> bool:
        """
        检测是否首次运行
        
        逻辑：如果注册表中没有任何配置记录，说明是首次运行
        通过检查一个标记键 "app/has_run_before" 来判断
        """
        return not self.qsettings.value("app/has_run_before", False, type=bool)
    
    def mark_as_run(self):
        """
        标记程序已经运行过一次
        """
        self.qsettings.setValue("app/has_run_before", True)
        self.qsettings.sync()
    
    def should_show_main_window_on_start(self) -> bool:
        """
        判断启动时是否应该显示主窗口
        
        规则：
        1. 如果是首次运行 -> 显示（引导用户）
        2. 如果不是首次运行 -> 根据用户设置决定
        
        Returns:
            bool: True=显示设置窗口，False=不自动打开设置窗口
        """
        # 首次运行：强制显示
        if self.is_first_run():
            from core.logger import log_debug, T
            log_debug(T("检测到首次运行，将自动打开设置窗口"), "Startup")
            return True

        # 非首次运行：读取用户设置
        show = self.get_show_main_window()
        from core.logger import log_debug, T
        if show:
            log_debug(T("根据用户设置：显示设置窗口"), "Startup")
        else:
            log_debug(T("根据用户设置：不自动打开设置窗口"), "Startup")
        return show


# 全局单例
_tool_settings_manager = None


def get_tool_settings_manager(qsettings: Optional[QSettings] = None,
                              secret_store=None) -> ToolSettingsManager:
    """
    获取全局工具设置管理器单例
    
    注意：这个管理器现在同时管理工具设置和应用设置
    虽然名字是 tool_settings_manager，但实际上是统一的配置管理器
    
    Args:
        qsettings: 可选的 QSettings 实例，用于测试时注入隔离存储。
                   仅在首次创建单例时生效。
        secret_store: 可选的凭据存储（默认平台层的系统密钥库），同样只在首次创建时生效。
    """
    global _tool_settings_manager
    if _tool_settings_manager is None:
        _tool_settings_manager = ToolSettingsManager(qsettings=qsettings, secret_store=secret_store)
    return _tool_settings_manager

