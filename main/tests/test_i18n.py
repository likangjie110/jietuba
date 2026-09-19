# -*- coding: utf-8 -*-
"""
I18n 翻译系统单元测试

测试 XmlTranslator 的 XML 解析和翻译查找逻辑。
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


SAMPLE_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="zh">
<context>
    <name>MainWindow</name>
    <message>
        <source>Screenshot</source>
        <translation>截图</translation>
    </message>
    <message>
        <source>Settings</source>
        <translation>设置</translation>
    </message>
    <message>
        <source>Unfin</source>
        <translation type="unfinished">未完成</translation>
    </message>
</context>
<context>
    <name>Toolbar</name>
    <message>
        <source>Save</source>
        <translation>保存</translation>
    </message>
</context>
</TS>
"""


@pytest.fixture
def xml_file(tmp_path):
    """创建临时 XML 翻译文件"""
    path = tmp_path / "test_zh.xml"
    path.write_text(SAMPLE_XML, encoding="utf-8")
    return str(path)


class TestXmlTranslator:
    """XmlTranslator 测试"""

    def test_load_success(self, qapp, xml_file):
        """成功加载 XML 文件"""
        from core.i18n import XmlTranslator
        translator = XmlTranslator()
        assert translator.load_from_xml(xml_file) is True

    def test_translate_with_context(self, qapp, xml_file):
        """按上下文翻译"""
        from core.i18n import XmlTranslator
        translator = XmlTranslator()
        translator.load_from_xml(xml_file)
        assert translator.translate("MainWindow", "Screenshot") == "截图"
        assert translator.translate("MainWindow", "Settings") == "设置"
        assert translator.translate("Toolbar", "Save") == "保存"

    def test_translate_fallback_context(self, qapp, xml_file):
        """跨上下文回退查找"""
        from core.i18n import XmlTranslator
        translator = XmlTranslator()
        translator.load_from_xml(xml_file)
        # "Save" 在 Toolbar 上下文中，但用其他上下文也能找到
        result = translator.translate("OtherContext", "Save")
        assert result == "保存"

    def test_translate_missing(self, qapp, xml_file):
        """找不到的翻译返回空字符串"""
        from core.i18n import XmlTranslator
        translator = XmlTranslator()
        translator.load_from_xml(xml_file)
        result = translator.translate("MainWindow", "NonExistent")
        assert result == ""

    def test_unfinished_translation(self, qapp, xml_file):
        """标记为 unfinished 的翻译应使用原文"""
        from core.i18n import XmlTranslator
        translator = XmlTranslator()
        translator.load_from_xml(xml_file)
        # type="unfinished" 的翻译应回退到 source
        result = translator.translate("MainWindow", "Unfin")
        assert result == "Unfin"

    def test_load_nonexistent_file(self, qapp, tmp_path):
        """加载不存在的文件应返回 False"""
        from core.i18n import XmlTranslator
        translator = XmlTranslator()
        result = translator.load_from_xml(str(tmp_path / "nonexistent.xml"))
        assert result is False

    def test_load_invalid_xml(self, qapp, tmp_path):
        """加载无效 XML 文件应返回 False"""
        from core.i18n import XmlTranslator
        bad_file = tmp_path / "bad.xml"
        bad_file.write_text("<notvalid>", encoding="utf-8")
        translator = XmlTranslator()
        result = translator.load_from_xml(str(bad_file))
        assert result is False


class TestI18nManager:
    """I18nManager 单例测试"""

    @pytest.fixture(autouse=True)
    def reset_singleton(self, qapp):
        """重置单例"""
        from core.i18n import I18nManager
        I18nManager._instance = None
        yield

    def test_singleton(self, qapp):
        """应为单例"""
        from core.i18n import I18nManager
        a = I18nManager.instance()
        b = I18nManager.instance()
        assert a is b

    def test_supported_languages(self, qapp):
        """支持的语言列表"""
        from core.i18n import I18nManager
        assert "ja" in I18nManager.LANGUAGES
        assert "en" in I18nManager.LANGUAGES
        assert "ko" in I18nManager.LANGUAGES
        assert "zh" in I18nManager.LANGUAGES

    def test_translations_dir_exists(self, qapp):
        """翻译文件目录应该存在"""
        from core.i18n import I18nManager
        translations_dir = I18nManager.get_translations_dir()
        assert translations_dir.exists(), f"翻译目录不存在: {translations_dir}"


@pytest.mark.parametrize(
    "language",
    ["en", "ja", "ko", "zh"],
)
def test_clipboard_delete_confirmation_is_compiled_in_its_calling_context(
    language,
):
    """QM 翻译必须位于 ClipboardWindow 上下文，不能依赖 XML 的跨上下文回退。"""
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    source = "Are you sure you want to delete this item?"
    context = next(
        context
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == "ClipboardWindow"
    )
    expected = next(
        message.findtext("translation")
        for message in context.findall("message")
        if message.findtext("source") == source
    )

    translator = QTranslator()
    qm_path = translations_dir / f"app_{language}.qm"
    assert translator.load(str(qm_path))
    assert translator.translate("ClipboardWindow", source) == expected


@pytest.mark.parametrize(
    "language",
    ["en", "ja", "ko", "zh"],
)
def test_hotkey_registration_error_is_compiled_in_main_app_context(language):
    """热键注册失败弹窗与动作名必须随界面语言显示。

    失败列表里的名字是 ``self.tr(action.label)``（主/备由 "(2)" 后缀区分），所以这里
    按动作注册表逐个核对——注册表加了动作而没补文案，这个用例就会红。
    """
    from core import actions
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    sources = (
        *(action.label for action in actions.ACTIONS),
        "Hotkey Registration Failed",
        "The following hotkeys failed to register:",
        "The hotkey may be occupied by other programs. Please try a different combination.",
    )
    context = next(
        context
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == "MainApp"
    )
    expected = {
        message.findtext("source"): message.findtext("translation")
        for message in context.findall("message")
    }

    translator = QTranslator()
    qm_path = translations_dir / f"app_{language}.qm"
    assert translator.load(str(qm_path))
    for source in sources:
        assert source in expected
        assert translator.translate("MainApp", source) == expected[source]


@pytest.mark.parametrize(
    "language",
    ["en", "ja", "ko", "zh"],
)
@pytest.mark.parametrize(
    ("context_name", "source"),
    [
        # 逐条冲突提示由录入框组件自己发出，设置窗口和欢迎向导共用这一份
        ("HotkeyEdit", "This hotkey is assigned more than once."),
        (
            "SettingsDialog",
            "Some global hotkeys are duplicated or unavailable. "
            "Please fix them before applying.",
        ),
    ],
)
def test_global_hotkey_conflict_is_compiled_in_its_owning_context(
    language, context_name, source
):
    """全局热键校验提示必须随界面语言显示，且落在真正发出它的上下文里。"""
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    # 同名上下文在 xml 里可能分成多块，全部合并后再查
    expected = {
        message.findtext("source"): message.findtext("translation")
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == context_name
        for message in context.findall("message")
    }

    translator = QTranslator()
    qm_path = translations_dir / f"app_{language}.qm"
    assert translator.load(str(qm_path))
    assert source in expected
    assert translator.translate(context_name, source) == expected[source]


#: 「全局鼠标」页（ui/settings_ui/page_mouse.py）与它的导航项/标题
MOUSE_PAGE_SOURCES = (
    "Global Mouse",
    "Global Mouse Settings",
    "Global Mouse Actions",
    "No global mouse actions yet. Add one to trigger actions with the mouse.",
    "Add Action",
    "Bind another modifier + mouse gesture to an action.",
    "Clear All",
    "Remove",
    "Scroll Up",
    "Scroll Down",
    "Middle Click",
    "Back Button",
    "Forward Button",
    "No Modifier",
    "Cmd / Win",
    "Global Mouse Capture",
    "Show Mask While Capturing",
    "Briefly dims the screen and highlights the captured area after a global mouse "
    "screenshot, so you can see what was grabbed.",
    "Ignored Applications",
    "Global mouse actions do nothing while one of these applications is in front. "
    "Names are matched ignoring case, and .app / .exe suffixes.",
    "Application name",
    "Add",
    "No ignored applications.",
    "💡 Modifier keys are read live: a binding without a modifier fires only when no "
    "modifier is held. On Windows the hotkey module and global mouse actions can fight "
    "over side buttons.",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_mouse_page_texts_are_compiled_in_the_settings_dialog_context(language):
    """全局鼠标页的文案必须随界面语言显示，且落在发出它的 SettingsDialog 上下文里。"""
    from core import actions
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    sources = (*MOUSE_PAGE_SOURCES, *(action.label for action in actions.ACTIONS))
    expected = {
        message.findtext("source"): message.findtext("translation")
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == "SettingsDialog"
        for message in context.findall("message")
    }

    translator = QTranslator()
    qm_path = translations_dir / f"app_{language}.qm"
    assert translator.load(str(qm_path))
    for source in sources:
        assert source in expected, (language, source)
        assert translator.translate("SettingsDialog", source) == expected[source]


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_permission_page_texts_are_compiled_in_the_settings_dialog_context(language):
    """权限页文案必须随界面语言显示，且落在发出它的 SettingsDialog 上下文里。"""
    from core.i18n import I18nManager
    from ui.settings_ui.page_permission import _TEXTS

    translations_dir = I18nManager.get_translations_dir()
    sources = (
        "Permissions",
        "System Permissions",
        "jietuba needs these system permissions. Grant them here; the status "
        "refreshes by itself once granted.",
        "Granted",
        "Not Granted",
        "Granted. Restart jietuba for it to take effect.",
        "Authorize",
        "Open System Settings",
        *(text for pair in _TEXTS.values() for text in pair),
    )
    expected = {
        message.findtext("source"): message.findtext("translation")
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == "SettingsDialog"
        for message in context.findall("message")
    }

    translator = QTranslator()
    qm_path = translations_dir / f"app_{language}.qm"
    assert translator.load(str(qm_path))
    for source in sources:
        assert source in expected, (language, source)
        assert translator.translate("SettingsDialog", source) == expected[source]


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
@pytest.mark.parametrize(
    ("context_name", "source"),
    [
        # 抓屏/热键缺权限时的提示（ui/permission_prompt.py）
        ("PermissionPrompt", "Screen Recording Permission Missing"),
        ("PermissionPrompt", "Accessibility Permission Missing"),
        ("PermissionPrompt", "Open Permission Settings"),
        ("PermissionPrompt", "Later"),
        # 欢迎向导的权限步骤（ui/welcome/page_permission.py，中文即源字符串）
        ("WelcomeWizard", "权限"),
        ("WelcomeWizard", "权限只对本机生效，随时可以在设置里复查。"),
        # 权限行上的重启入口，向导与设置页共用同一份清单组件
        ("SettingsDialog", "Restart jietuba"),
        (
            "SettingsDialog",
            "jietuba will close and reopen so the newly granted permission takes effect.",
        ),
    ],
)
def test_permission_step_texts_are_compiled_in_their_owning_context(
    language, context_name, source
):
    """向导权限步骤、权限提示与重启入口的文案必须随界面语言显示。"""
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    expected = {
        message.findtext("source"): message.findtext("translation")
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == context_name
        for message in context.findall("message")
    }

    translator = QTranslator()
    qm_path = translations_dir / f"app_{language}.qm"
    assert translator.load(str(qm_path))
    assert source in expected, (language, context_name, source)
    assert translator.translate(context_name, source) == expected[source]


def _compiled_toolbar_customization_sources():
    from ui.toolbar_layout_dialog import BUTTON_NAMES, MODE_NAMES

    dialog_sources = [
        *BUTTON_NAMES.values(),
        *(text for _mode, text in MODE_NAMES),
        "Customize Toolbar",
        "This button can't be hidden",
        "Drag the handle to reorder. Use the drop-down to change visibility.",
        "Restore defaults",
        "OK",
        "Cancel",
    ]
    return [("Toolbar", "More"), ("Toolbar", "Adjust")] + [
        ("ToolbarLayoutDialog", source) for source in dialog_sources
    ]


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_toolbar_customization_texts_are_compiled_in_their_contexts(language):
    """「…」弹层和排布对话框的文案必须随界面语言显示，且落在各自发出它的上下文里。"""
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    root = ET.parse(translations_dir / f"app_{language}.xml").getroot()
    translator = QTranslator()
    assert translator.load(str(translations_dir / f"app_{language}.qm"))

    for context_name, source in _compiled_toolbar_customization_sources():
        expected = {
            message.findtext("source"): message.findtext("translation")
            for context in root.findall("context")
            if context.findtext("name") == context_name
            for message in context.findall("message")
        }
        assert expected.get(source), (context_name, source)
        assert translator.translate(context_name, source) == expected[source]


#: 「快捷键/动作」页的全局动作区（ui/settings_ui/page_hotkey.py）
HOTKEY_PAGE_SOURCES = (
    "Global Actions",
    "Action",
    "Hotkey",
    "Backup Hotkey",
    "Tray",
    "Bind another action to a global hotkey.",
    "Clear All Hotkeys",
    "No global actions yet. Add one to bind a hotkey.",
    "Show in the tray menu",
)


def _assert_sources_in_context(language, context_name, sources):
    """这些源串在该语言的 xml 里有译文，且编译后的 .qm 拿得到同一份。"""
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    expected = {
        message.findtext("source"): message.findtext("translation")
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") == context_name
        for message in context.findall("message")
    }

    translator = QTranslator()
    assert translator.load(str(translations_dir / f"app_{language}.qm"))
    for source in sources:
        assert source in expected, (language, context_name, source)
        assert translator.translate(context_name, source) == expected[source]


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_hotkey_page_texts_are_compiled_in_the_settings_dialog_context(language):
    """快捷键/动作页的文案必须随界面语言显示（动作名本身在下面的鼠标页用例里核对）。"""
    _assert_sources_in_context(language, "SettingsDialog", HOTKEY_PAGE_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_action_labels_are_compiled_for_the_tray_and_the_main_app(language):
    """托盘菜单与热键失败弹窗都要按界面语言显示动作名。"""
    from core import actions

    labels = tuple(action.label for action in actions.ACTIONS)
    _assert_sources_in_context(language, "SystemTray", labels)
    _assert_sources_in_context(language, "MainApp", labels)


#: 「贴图设置」页（ui/settings_ui/page_pin.py）
PIN_PAGE_SOURCES = (
    "Pin Settings",
    "Pin Interaction",
    "Wheel Zoom Step",
    "How much one wheel notch scales the pinned image (1.05 means +5%).",
    "Opacity Step",
    "How much Ctrl + wheel changes the window opacity.",
    "Default Opacity",
    "Opacity of a newly pinned image.",
    "Shadow and Border",
    "Draw a border (and shadow) around new pinned images.",
    "Show Toolbar on Hover",
    "On: shows the toolbar when the mouse enters a pinned window.",
    "New Pinned Image",
    "Appearance Position",
    "Where a new pinned image shows up.",
    "At the captured area",
    "At the mouse position",
    "Center of the screen",
    "Stacking Order",
    "Whether a new pinned image goes above or below the existing ones.",
    "Above existing pins",
    "Below existing pins",
    "Confirm Before Closing",
    "Ask before closing a pinned image. Closing all pins at once and quitting never ask.",
    "💡 Values apply to pinned windows created from now on; changes to wheel steps "
    "apply immediately.",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_pin_page_texts_are_compiled_in_the_settings_dialog_context(language):
    _assert_sources_in_context(language, "SettingsDialog", PIN_PAGE_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_pin_close_confirmation_is_compiled_in_the_pin_window_context(language):
    """贴图窗口自己弹的关闭确认，用的是 PinWindow 上下文。"""
    _assert_sources_in_context(
        language, "PinWindow", ("Close pin", "Close this pinned image?")
    )


#: 「贴图窗口鼠标动作」区（ui/settings_ui/page_pin.py）
PIN_MOUSE_ACTION_SOURCES = (
    "Pin Mouse Actions",
    "Wheel Up",
    "Wheel Down",
    "Ctrl + Wheel Up",
    "Ctrl + Wheel Down",
    "Middle Click",
    "Double Click",
    "Right Click",
    "Rolling the wheel up on a pinned image.",
    "Rolling the wheel down on a pinned image.",
    "Ctrl + wheel up; adjusts opacity by default.",
    "Ctrl + wheel down; adjusts opacity by default.",
    "Pressing the middle button on a pinned image.",
    "Double-clicking a pinned image.",
    "Right-clicking a pinned image; opens the menu by default.",
    "Do nothing",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_pin_mouse_action_texts_are_compiled_in_the_settings_dialog_context(language):
    """贴图手势区的文案 + 每个动作名都要能在界面语言里显示。"""
    from pin import pin_actions

    sources = (*PIN_MOUSE_ACTION_SOURCES,
               *(action.label for action in pin_actions.PIN_ACTIONS))
    _assert_sources_in_context(language, "SettingsDialog", sources)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_pin_lock_texts_are_compiled_in_the_pin_window_context(language):
    _assert_sources_in_context(language, "PinWindow", ("Lock", "Locked", "Unlocked"))


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_pin_restore_texts_are_compiled_in_the_settings_dialog_context(language):
    """「启动恢复贴图 / 保存的贴图数量」两项要随界面语言显示。"""
    _assert_sources_in_context(language, "SettingsDialog", (
        "Saved Pins",
        "How many pinned images are remembered when restoring on startup.",
        "Restore Pins on Startup",
        "Save the pinned images you leave open, and bring them back the next time "
        "jietuba starts.",
    ))


#: 「标注」设置页（ui/settings_ui/page_annotation.py）
ANNOTATION_PAGE_SOURCES = (
    "Annotation",
    "Annotation Settings",
    "Pen",
    "Highlighter",
    "Rectangle",
    "Ellipse",
    "Arrow",
    "Color",
    "Default color",
    "Default value",
    "Thickness",
    "Opacity",
    "Pick a color",
    "Mosaic",
    "Mosaic Style",
    "Pixelate for hard blocks, blur for soft ones.",
    "Pixelate",
    "Blur",
    "Mosaic Brush",
    "How coarse the mosaic brush is.",
    "Coarse",
    "Medium",
    "Fine",
    "Text",
    "Font Size",
    "Restore Defaults",
    "Reset every tool on this page to its factory style.",
    "Restore",
    "💡 These are the defaults each tool starts with; the toolbar keeps remembering "
    "whatever you last used.",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_annotation_page_texts_are_compiled_in_the_settings_dialog_context(language):
    _assert_sources_in_context(language, "SettingsDialog", ANNOTATION_PAGE_SOURCES)


#: 截图设置页的「截图外观」与「放大镜」（ui/settings_ui/page_capture.py）
CAPTURE_APPEARANCE_SOURCES = (
    "Screenshot Appearance",
    "Rounded Corners",
    "Rounds the corners of the captured image.",
    "Corner Radius",
    "How round the corners are (0-100).",
    "Border or Shadow",
    "Draws a border or a drop shadow around the captured image.",
    "Border Style",
    "Border draws a solid frame; shadow softens the edges.",
    "Shadow",
    "Border",
    "Border or Shadow Size",
    "Thickness of the border, or how far the shadow spreads (1-50).",
    "Border Color",
    "Color used when the style is Border.",
    "Shadow Color",
    "Color used when the style is Shadow.",
    "Keep the Style On",
    "Keeps border/shadow enabled for every new capture.",
    "Magnifier",
    "Default Zoom",
    "Starting magnification of the magnifier (1-10).",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_capture_appearance_texts_are_compiled_in_the_settings_dialog_context(language):
    _assert_sources_in_context(language, "SettingsDialog", CAPTURE_APPEARANCE_SOURCES)


#: 「外观」页的皮肤配色与自定义 logo（ui/settings_ui/page_appearance.py）
SKIN_PAGE_SOURCES = (
    "Skin Colors",
    "Override the window background, text and accent colors.",
    "Window Background",
    "Text Color",
    "Accent Color",
    "Follow Theme",
    "Customized",
    "Following the theme",
    "Select Color",
    "Custom Logo",
    "Use your own image for the tray and window icons.",
    "Choose Image",
    "Clear",
    "Built-in icon",
    "Cannot read this image, using the built-in icon",
    "Choose Logo Image",
    "Images (*.png *.jpg *.jpeg *.bmp *.gif *.svg *.ico)",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_skin_page_texts_are_compiled_in_the_settings_dialog_context(language):
    _assert_sources_in_context(language, "SettingsDialog", SKIN_PAGE_SOURCES)


#: 公式识别结果的对话框（core/actions.py 里的 FormulaResult 上下文）
FORMULA_RESULT_SOURCES = (
    "Formula Result",
    "Formula Recognition Unavailable",
    "No formula engine is available on this machine: jietuba does not ship a formula "
    "model. Install a formula engine plugin and try again.",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_formula_result_texts_are_compiled(language):
    _assert_sources_in_context(language, "FormulaResult", FORMULA_RESULT_SOURCES)


#: 2026-09-19 按参考清单补的设置所在的文件（其它页 / 截图页 / 剪贴板页 + 识别结果对话框）
NEW_SETTINGS_SOURCES = (
    "main/ui/settings_ui/page_misc.py",
    "main/ui/settings_ui/page_capture.py",
    "main/ui/settings_ui/page_clipboard.py",
    "main/ocr/result_dialog.py",
)


def _literal_tr_sources(path: str) -> set:
    """把一份文件里 ``.tr("字面量")`` / ``make_tr(...)("字面量")`` 的源串抠出来。

    手抄清单会漏（新加一条卡片忘了补清单就测不出来），所以直接读源码。
    """
    import ast

    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    sources = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in ("tr", "translate"):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.strip():
            sources.add(arg.value)
    return sources


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_new_settings_pages_have_no_untranslated_literals(language):
    """本轮新增设置所在的页面：每个 tr() 字面量都要在 .xml 里有译文。

    这条是「不留手抄清单」的护栏：往这些页面里加控件而忘了补文案时，它会直接红。
    """
    from core.i18n import I18nManager

    translations_dir = I18nManager.get_translations_dir()
    expected = {
        message.findtext("source")
        for context in ET.parse(translations_dir / f"app_{language}.xml")
        .getroot()
        .findall("context")
        if context.findtext("name") in ("SettingsDialog", "OcrResultDialog")
        for message in context.findall("message")
    }

    missing = set()
    for path in NEW_SETTINGS_SOURCES:
        missing |= {source for source in _literal_tr_sources(path) if source not in expected}

    assert not missing, (language, sorted(missing))


#: 视频录制工具栏 / 录制窗口自己的文案（ui 与 video 两个模块各一份上下文）
VIDEO_TOOLBAR_SOURCES = (
    "开始录制",
    "停止录制",
    "暂停录制",
    "恢复录制",
    "拖动工具栏",
    "关闭",
    "跟随区域",
)

VIDEO_WINDOW_SOURCES = (
    "视频录制",
    "视频已保存到:\n{path}",
    "打开文件夹",
    "复制路径",
    "确定",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_video_toolbar_texts_are_compiled_in_their_own_context(language):
    """录制工具栏不走设置页上下文，文案要落在发出它的 VideoRecordToolbar 里。"""
    _assert_sources_in_context(language, "VideoRecordToolbar", VIDEO_TOOLBAR_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_video_window_result_dialog_is_compiled_in_its_own_context(language):
    """录完弹出的「已保存到…」对话框用的是 VideoRecordWindow 上下文。"""
    _assert_sources_in_context(language, "VideoRecordWindow", VIDEO_WINDOW_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_video_recording_settings_are_compiled_in_the_settings_dialog_context(language):
    """视频录制设置组的卡片文案。"""
    sources = (
        "Video Recording",
        "Video Container",
        "Video Codec",
        "Video Quality",
        "Follow Selection",
        "Video Frame Rate",
        "Bitrate Limit",
        "Record Audio",
        "No Audio",
        "Microphone",
        "Microphone Device",
        "System Default",
        "Maximum Duration",
        " seconds",
        "Video Save Folder",
        "Video recording is unavailable: no encoding backend.",
    )
    _assert_sources_in_context(language, "SettingsDialog", sources)


#: 第二批标注工具的面板与端点文案（各自的上下文）
ANNOTATION_PANEL_SOURCES = (
    "Line style", "Solid", "Dashed", "Dense dashes",
    "Watermark text", "Font size", "Angle", "Spacing", "Opacity %",
    "Filter", "Grayscale", "Invert", "Gaussian blur", "Emboss",
    "Blur radius", "Emboss strength", "Brush size",
    "Style template", "Template name", "Save template", "Apply", "Delete",
)

ARROW_EXTRA_SOURCES = (
    "Straight", "Curve", "Elbow", "Arrow path", "Start arrowhead", "End arrowhead",
    "Follow style", "None", "Triangle", "Triangle outline", "Circle", "Diamond", "Bar",
)

LAYER_SHORTCUT_SOURCES = (
    "Layer & Alignment", "Bring to Front", "Send to Back", "Bring Forward",
    "Send Backward", "Align Left", "Align Horizontal Center", "Align Right",
    "Align Top", "Align Vertical Center", "Align Bottom",
)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_annotation_panel_texts_are_compiled_in_their_own_context(language):
    """第二批标注工具面板的文案（直线/水印/滤镜/擦除/模板行）。"""
    _assert_sources_in_context(language, "AnnotationSettingsPanel", ANNOTATION_PANEL_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_arrow_path_and_head_texts_are_compiled(language):
    """箭头路径样式与起止端点。"""
    _assert_sources_in_context(language, "ArrowSettingsPanel", ARROW_EXTRA_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_layer_and_align_shortcut_labels_are_compiled(language):
    """层级与对齐那一组应用内快捷键的行标签。"""
    _assert_sources_in_context(language, "SettingsDialog", LAYER_SHORTCUT_SOURCES)


@pytest.mark.parametrize("language", ["en", "ja", "ko", "zh"])
def test_insert_image_dialog_title_is_compiled(language):
    _assert_sources_in_context(language, "InsertImageTool", ("Insert Image",))
