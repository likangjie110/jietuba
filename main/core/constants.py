# -*- coding: utf-8 -*-
"""
全局常量。
"""
from pathlib import Path

from core.platform.fonts import (
    common_text_fonts,
    css_font_family,
    css_font_family_ui,
    default_font_family,
    default_text_font_by_language,
)
from core.platform.paths import app_data_dir, log_dir


# ── 应用数据目录 ──────────────────────────────────────────
# 实现已迁到平台层，Windows 取值逐字不变；macOS 走 ~/Library/Application Support，
# Linux 走 XDG_DATA_HOME，不再落进伪造的 ~/AppData/Local。这里保留原来的函数名，
# 调用方（logger、crash_handler、离线翻译模型）无需改动。

def get_app_data_dir() -> Path:
    """返回应用数据根目录（日志、崩溃记录等都放在这里）。"""
    return app_data_dir()


def get_log_dir() -> Path:
    """返回日志目录。"""
    return log_dir()


# ── CSS font-family 值 ────────────────────────────────────
# 取值来自平台层（core/platform/fonts.py）：Windows 逐字保留原来的三份字体栈，
# macOS/Linux 换成各自存在的字体。以前这里硬编码 Windows 字体名，在另外两个平台上
# 会由 Qt 静默替代，字体选择器里列出来的是装了也不存在的名字。
#
# 用于 QSS stylesheet 中的 font-family 属性（大部分 UI 控件）
CSS_FONT_FAMILY = css_font_family()

# 用于系统级 UI 元素（托盘菜单、上下文菜单等）
CSS_FONT_FAMILY_UI = css_font_family_ui()

# QFont 构造时使用的默认字体族名
DEFAULT_FONT_FAMILY = default_font_family()

# Fonts exposed by the text annotation tool. Keep this list small so the app
# never needs to scan the full system font database during startup.
COMMON_TEXT_FONTS = common_text_fonts()

DEFAULT_TEXT_FONT_BY_LANGUAGE = default_text_font_by_language()

_SYSTEM_DEFAULT_TEXT_FONT_LOGGED = False


def get_default_text_font_for_language(language_code: str | None = None) -> str:
    """Return the text-tool default font for the current UI language."""
    if language_code is None:
        try:
            from core.i18n import I18nManager
            language_code = I18nManager.get_current_language()
        except Exception:
            language_code = ""

    return DEFAULT_TEXT_FONT_BY_LANGUAGE.get(
        language_code or "",
        get_system_default_text_font_family(),
    )


def get_system_default_text_font_family() -> str:
    """Return Qt's system general font without enumerating all font families."""
    global _SYSTEM_DEFAULT_TEXT_FONT_LOGGED
    try:
        from PySide6.QtGui import QFontDatabase
        family = QFontDatabase.systemFont(
            QFontDatabase.SystemFont.GeneralFont
        ).family()
        family = family or COMMON_TEXT_FONTS[0]
    except Exception:
        family = COMMON_TEXT_FONTS[0]

    if not _SYSTEM_DEFAULT_TEXT_FONT_LOGGED:
        try:
            from core.logger import log_debug, T
            log_debug(T("系统默认文字字体: {family}", family=family), "Font")
        except Exception:
            pass
        _SYSTEM_DEFAULT_TEXT_FONT_LOGGED = True

    return family


def get_available_text_fonts() -> list[str]:
    """Return text-tool fonts plus the system default, deduplicated."""
    fonts = list(COMMON_TEXT_FONTS)
    system_font = get_system_default_text_font_family()
    if system_font and system_font not in fonts:
        fonts.insert(0, system_font)
    return fonts


def normalize_text_font_family(font_family: str | None, language_code: str | None = None) -> str:
    """Clamp text-tool fonts to the approved lightweight whitelist."""
    if font_family in get_available_text_fonts():
        return font_family
    return get_default_text_font_for_language(language_code)

# 项目主页（欢迎页与设置“关于”页共用）
PROJECT_GITHUB_URL = "https://github.com/1003129155/jietuba"
 
