# -*- coding: utf-8 -*-
"""主窗口：左侧边栏 + 四个页面（截图历史 / 翻译 / 设置 / 关于）。

为什么要有它：以前「历史」「翻译」「设置」「关于」是四个各自独立的窗口/对话框，用户想
从历史跳到设置得先关掉一个再打开另一个。主窗口把它们收进同一个壳里，托盘/热键里给一个
总入口。

页面不重写：历史页直接用 ``history.window.HistoryWindow``，设置页直接用 ``SettingsDialog``
（去掉它自己的标题栏当作子部件塞进来），关于页复用 ``settings_ui.page_about.create_about_page``。
翻译页是唯一新写的——原来的翻译窗口是独立顶层窗口（``FramelessWindow``），塞不进布局，
所以这里用同一个 ``TranslationService`` 做了个薄输入输出面板。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget,
)

from core.i18n import make_tr
from core.logger import T, log_debug, log_exception, log_warning
from ui.fluent_lite import ComboBox, PushButton, TextEdit

_tr = make_tr("MainWindow")

#: 侧边栏条目：(key, 图标文本, 标题)
PAGES = (
    ("history", "🕘", "Screenshot History"),
    ("translation", "🌐", "Translation"),
    ("settings", "⚙", "Settings"),
    ("about", "ℹ", "About"),
)


class _TranslateWorker(QThread):
    """翻译线程：翻译要走网络，放到主线程会把界面卡住（与翻译窗口同一套做法）。"""

    finished_with = Signal(bool, str, str)   # 成功?, 文本, 错误

    def __init__(self, service, text: str, target: str, source: str = ""):
        super().__init__()
        self._service = service
        self._text = text
        self._target = target
        self._source = source or None

    def run(self):
        from translation.models import TranslationRequest

        try:
            request = TranslationRequest(text=self._text, target_lang=self._target,
                                         source_lang=self._source)
            result = self._service.translate(request)
        except Exception as e:                      # 服务层不该抛，兜底也要给用户一句话
            log_exception(e, T("执行翻译"))
            self.finished_with.emit(False, "", str(e))
            return
        if getattr(result, "success", False):
            self.finished_with.emit(True, str(getattr(result, "text", "") or ""), "")
        else:
            self.finished_with.emit(False, "",
                                   str(getattr(result, "error_message", "") or ""))


class TranslationPage(QWidget):
    """主窗口里的翻译页：源文本 + 目标语言 + 译文的薄面板（复用 TranslationService）。"""

    def __init__(self, config_manager=None, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel(_tr("Language"), self))
        self.language_combo = ComboBox(self)
        self.language_combo.setFixedWidth(180)
        for code, label in _target_languages():
            self.language_combo.addItem(label, userData=code)
        bar.addWidget(self.language_combo)
        self.translate_button = PushButton(_tr("Translate"), self)
        self.translate_button.clicked.connect(self.on_translate)
        bar.addWidget(self.translate_button)
        self.copy_button = PushButton(_tr("Copy Result"), self)
        self.copy_button.clicked.connect(self.on_copy)
        bar.addWidget(self.copy_button)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.source_edit = TextEdit(self)
        self.source_edit.setPlaceholderText(_tr("Text to translate"))
        outer.addWidget(self.source_edit, 1)

        self.result_edit = TextEdit(self)
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText(_tr("Translation appears here"))
        outer.addWidget(self.result_edit, 1)

        self.status = QLabel("", self)
        outer.addWidget(self.status)

    # ── 动作 ──

    def target_language(self) -> str:
        return str(self.language_combo.currentData() or "en")

    def on_translate(self) -> bool:
        """翻译源文本框里的内容（走真实的 TranslationService）。"""
        text = self.source_edit.toPlainText().strip()
        if not text:
            self.status.setText(_tr("Nothing to translate."))
            return False
        service = self._service()
        if service is None:
            self.status.setText(_tr("Translation service is unavailable."))
            return False
        if not service.is_configured():
            self.status.setText(_tr("Translation service is not configured."))
            return False

        self.status.setText(_tr("Translating…"))
        self.translate_button.setEnabled(False)
        self._worker = _TranslateWorker(service, text, self.target_language())
        self._worker.finished_with.connect(self._on_translated)
        self._worker.start()
        return True

    def _on_translated(self, ok: bool, text: str, error: str) -> None:
        self.translate_button.setEnabled(True)
        if ok:
            self.result_edit.setPlainText(text)
            self.status.setText(_tr("Done."))
        else:
            self.status.setText(error or _tr("Translation failed."))
            log_warning(T("主窗口翻译失败: {error}", error=error), "MainWindow")

    def on_copy(self) -> bool:
        text = self.result_edit.toPlainText()
        if not text:
            return False
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        return True

    def _service(self):
        try:
            from translation.service import create_default_translation_service

            return create_default_translation_service(self._config)
        except Exception as e:
            log_exception(e, T("创建翻译服务"))
            return None


def _target_languages() -> list:
    """目标语言清单（复用翻译模块的语言表；拿不到时给英文兜底）。"""
    try:
        from translation.languages import SUPPORTED_LANGUAGES

        if isinstance(SUPPORTED_LANGUAGES, dict):
            return [(code, name) for code, name in SUPPORTED_LANGUAGES.items()]
        return [(item.get("code", ""), item.get("name", "")) for item in SUPPORTED_LANGUAGES]
    except Exception:
        return [("zh", "Chinese"), ("en", "English"), ("ja", "Japanese"), ("ko", "Korean")]


class MainWindow(QWidget):
    """带侧边栏的主窗口：历史 / 翻译 / 设置 / 关于。"""

    def __init__(self, config_manager=None, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._pages = {}
        self.setWindowTitle(_tr("Jietuba"))
        self.setMinimumSize(1000, 680)
        self._build_ui()
        self.switch_to("history")

    # ── 界面 ──

    def _build_ui(self):
        from core.resource_manager import ResourceManager

        icon = ResourceManager.get_app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = QListWidget(self)
        self.sidebar.setFixedWidth(180)
        self.sidebar.setObjectName("MainWindowSidebar")
        for key, glyph, title in PAGES:
            item = QListWidgetItem(f"{glyph}  {_tr(title)}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.sidebar.addItem(item)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_changed)
        outer.addWidget(self.sidebar)

        self.stack = QStackedWidget(self)
        outer.addWidget(self.stack, 1)

        for key, _glyph, _title in PAGES:
            page = self._create_page(key)
            self._pages[key] = page
            self.stack.addWidget(page)
        self.setStyleSheet(_stylesheet())

    def _create_page(self, key: str) -> QWidget:
        """按 key 建页；某一页建不起来时给一个说明页而不是让整个窗口炸掉。"""
        try:
            if key == "history":
                from history.window import HistoryWindow

                return HistoryWindow(config_manager=self._config)
            if key == "translation":
                return TranslationPage(self._config)
            if key == "settings":
                return self._create_settings_page()
            if key == "about":
                return self._create_about_page()
        except Exception as e:
            log_exception(e, T("创建主窗口页面: {key}", key=key))
        return _ErrorPage(_tr("This page could not be created."))

    def _create_settings_page(self) -> QWidget:
        """把设置对话框当子部件嵌进来（去掉它自己的标题栏）。"""
        from settings import get_tool_settings_manager
        from ui.settings_ui.dialog import SettingsDialog

        dialog = SettingsDialog(config_manager=self._config or get_tool_settings_manager(),
                               parent=self)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        title_bar = getattr(dialog, "titleBar", None)
        if title_bar is not None:
            title_bar.hide()
        self.settings_dialog = dialog
        return dialog

    def _create_about_page(self) -> QWidget:
        from settings.tool_settings import ToolSettingsManager
        from ui.settings_ui.page_about import create_about_page

        host = _PageHost(self._config or ToolSettingsManager())
        return create_about_page(host)

    # ── 切换 ──

    def page_keys(self) -> list:
        return [key for key, _glyph, _title in PAGES]

    def current_page_key(self) -> str:
        return self.page_keys()[max(0, self.stack.currentIndex())] \
            if self.stack.count() else ""

    def page(self, key: str) -> QWidget | None:
        return self._pages.get(key)

    def switch_to(self, key: str) -> bool:
        """切到某一页（侧边栏与内容一起变）。"""
        if key not in self._pages:
            return False
        index = self.page_keys().index(key)
        self.stack.setCurrentIndex(index)
        self.sidebar.setCurrentRow(index)
        log_debug(T("主窗口切页: {key}", key=key), "MainWindow")
        return True

    def _on_sidebar_changed(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)


class _PageHost:
    """给「设置页工厂」用的最小宿主：只提供它们真正用到的那几个接口。

    ``create_about_page`` 要 ``tr`` 与 ``config_manager``；把它单独跑起来时需要的东西
    就这么点，所以不必为了复用这一页去构造整个 SettingsDialog。
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def tr(self, text: str) -> str:
        return _tr(text)

    def _get_input_style(self) -> str:
        return ""


class _ErrorPage(QWidget):
    """建页失败时的占位页：说清楚发生了什么，而不是留一块空白。"""

    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(message, self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)


def _stylesheet() -> str:
    """侧边栏样式：配色取主题 token（皮肤/明暗跟着走）。

    QSS 的 item 选择器要写成 ``QListWidget#对象名::item``——只写 ``#对象名::item``
    Qt 解析不了整份样式表（会整段丢掉并打一条 "Could not parse stylesheet"）。
    """
    from core.ui_theme import get_ui_theme

    try:
        tokens = get_ui_theme().tokens
        surface = tokens.surface
        text = tokens.text
        accent_soft = tokens.accent_soft
    except Exception:
        surface, text, accent_soft = "#F5F6F7", "#202020", "rgba(0, 120, 215, 0.18)"
    return (f"QListWidget#MainWindowSidebar {{ background: {surface}; color: {text};"
            " border: none; font-size: 14px; padding: 6px; }"
            "QListWidget#MainWindowSidebar::item { padding: 8px 10px; border-radius: 6px; }"
            f"QListWidget#MainWindowSidebar::item:selected {{ background: {accent_soft}; }}")


def open_main_window(config_manager=None) -> MainWindow:
    """打开（或复用）主窗口。"""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    window = getattr(application, "_main_window", None)
    if window is None:
        window = MainWindow(config_manager)
        if application is not None:
            application._main_window = window
            window.destroyed.connect(lambda: setattr(application, "_main_window", None))
    window.show()
    window.raise_()
    window.activateWindow()
    return window
