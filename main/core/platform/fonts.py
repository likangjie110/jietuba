# -*- coding: utf-8 -*-
"""平台字体族：QSS 字体栈与 QFont 用的字体名。

迁移前这些名字全是 Windows 字体（微软雅黑、SimSun、Segoe UI、Yu Gothic UI……），
而且同一个用途在不同文件里各写一份——``core/constants.py``、``ui/fluent_lite/theme.py``、
``ui/color_picker_dialog.py`` 各有一份字体栈，散在 UI 里的 ``QFont("Microsoft YaHei", 10)``
还有十几处。

macOS/Linux 上没有这些字体，Qt 会静默换一个替代字体：界面能看，但字体选择器里列出的
是一串装了也不存在的名字，用户选了没有任何效果。

Windows 的取值全部逐字保留——三个字体栈在那个平台上本来就是三个不同的栈
（Fluent 组件库用 Segoe UI Variable，系统级 UI 用微软雅黑 UI，正文用微软雅黑），
这里按用途分开导出，不合并成一份。
"""

from core.platform.detection import IS_MACOS, IS_WINDOWS

# 语言码到「界面字体」的映射；三个平台共用同一组键，值各自不同。
_UI_LANGUAGES = ("zh", "zh_CN", "zh_TW", "en", "ja")

_WINDOWS_UI_FONTS = {
    "zh": "Microsoft YaHei UI",
    "zh_CN": "Microsoft YaHei UI",
    "zh_TW": "Microsoft JhengHei UI",
    "en": "Segoe UI",
    "ja": "Yu Gothic UI",
}
_MACOS_UI_FONTS = {
    "zh": "PingFang SC",
    "zh_CN": "PingFang SC",
    "zh_TW": "PingFang TC",
    "en": "Helvetica Neue",
    "ja": "Hiragino Sans",
}
_LINUX_UI_FONTS = {
    "zh": "Noto Sans CJK SC",
    "zh_CN": "Noto Sans CJK SC",
    "zh_TW": "Noto Sans CJK TC",
    "en": "DejaVu Sans",
    "ja": "Noto Sans CJK JP",
}


def _ui_font_table() -> dict[str, str]:
    if IS_WINDOWS:
        return _WINDOWS_UI_FONTS
    if IS_MACOS:
        return _MACOS_UI_FONTS
    return _LINUX_UI_FONTS


def ui_font_family(language_code: str | None = None) -> str:
    """界面字体族名（QFont 构造用）。

    按语言取，因为中/日/繁各自的系统 UI 字体不同。取不到映射时回落到当前平台的
    默认界面字体——不是 Windows 的微软雅黑。
    """
    table = _ui_font_table()
    return table.get(language_code or "", default_ui_font_family())


def default_ui_font_family() -> str:
    """当前平台的通用界面字体族名（语言未知时的答案）。"""
    if IS_WINDOWS:
        return "Segoe UI"
    if IS_MACOS:
        return "Helvetica Neue"
    return "DejaVu Sans"


def default_font_family() -> str:
    """应用默认字体族名（正文类控件的 QFont 用它）。

    与 ``ui_font_family()`` 的区别是历史上就用两个名字：正文用「微软雅黑」，
    界面控件用「微软雅黑 UI」。Windows 上两者都保留原值。
    """
    if IS_WINDOWS:
        return "Microsoft YaHei"
    if IS_MACOS:
        return "PingFang SC"
    return "Noto Sans CJK SC"


def css_font_family() -> str:
    """QSS ``font-family`` 值，用于大部分界面控件。"""
    if IS_WINDOWS:
        return '"Microsoft YaHei", "SimSun", Arial, sans-serif'
    if IS_MACOS:
        return '"PingFang SC", "Hiragino Sans GB", "Helvetica Neue", Arial, sans-serif'
    return '"Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans", Arial, sans-serif'


def css_font_family_ui() -> str:
    """QSS ``font-family`` 值，用于系统级 UI（托盘菜单、上下文菜单）。"""
    if IS_WINDOWS:
        return '"Microsoft YaHei UI", "Segoe UI", sans-serif'
    if IS_MACOS:
        return '"PingFang SC", "Helvetica Neue", Arial, sans-serif'
    return '"Noto Sans CJK SC", "DejaVu Sans", sans-serif'


def css_font_family_fluent() -> str:
    """QSS ``font-family`` 值，用于自研的 Fluent 风格组件库。

    Windows 上单独一份：它优先用 Windows 11 的 Segoe UI Variable，与上面两个栈
    不是同一个，所以不合并。
    """
    if IS_WINDOWS:
        return '"Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif'
    if IS_MACOS:
        return '"PingFang SC", "Helvetica Neue", Arial, sans-serif'
    return '"Noto Sans CJK SC", "DejaVu Sans", sans-serif'


def common_text_fonts() -> list[str]:
    """文字标注工具提供的候选字体。

    列表刻意保持很短：启动时不做全量字体枚举，只列这几个常见字体 + 系统默认字体
    （系统默认由 ``core.constants.get_available_text_fonts`` 插到最前面）。
    """
    if IS_WINDOWS:
        return [
            "Microsoft YaHei UI",
            "SimSun",
            "Segoe UI",
            "Arial",
            "Yu Gothic UI",
            "Meiryo",
            "Microsoft JhengHei UI",
            "PMingLiU",
        ]
    if IS_MACOS:
        return [
            "PingFang SC",
            "Hiragino Sans",
            "Hiragino Sans GB",
            "Songti SC",
            "STHeiti",
            "Helvetica Neue",
            "Arial",
        ]
    return [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Noto Sans CJK TC",
        "WenQuanYi Micro Hei",
        "DejaVu Sans",
        "Liberation Sans",
        "Arial",
    ]


def default_text_font_by_language() -> dict[str, str]:
    """语言码 → 文字标注的默认字体族名。

    与 ``ui_font_family()`` 取自同一张表：两者都是「某种语言的界面该用什么字体」，
    分开维护只会让它们漂移。
    """
    table = _ui_font_table()
    return {code: table[code] for code in _UI_LANGUAGES}
