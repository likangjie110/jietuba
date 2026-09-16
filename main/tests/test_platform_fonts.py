# -*- coding: utf-8 -*-
"""平台字体（core/platform/fonts）单元测试。

这组用例的第一职责是**冻结 Windows 的取值**：迁移前 Windows 有三份互不相同的字体栈
（Fluent 组件库用 Segoe UI Variable、系统级 UI 用微软雅黑 UI、正文用微软雅黑），
合并或"顺手统一"都会改变 Windows 上的实际渲染，而本仓库的发行版只有 Windows。
下面把历史值写成字面量逐字比对，改坏立刻红。

第二职责是确认另外两个平台不再声称使用 Windows 字体——迁移前 macOS/Linux 上这些
字体全都不存在，Qt 会静默替代，字体选择器里列的是一串选了没反应的名字。
"""

import pytest

from core.platform import fonts

# 迁移前 Windows 上的实际取值，不允许漂移
WINDOWS_CSS_FONT_FAMILY = '"Microsoft YaHei", "SimSun", Arial, sans-serif'
WINDOWS_CSS_FONT_FAMILY_UI = '"Microsoft YaHei UI", "Segoe UI", sans-serif'
WINDOWS_FLUENT_FONT_FAMILY = '"Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif'
WINDOWS_DEFAULT_FONT_FAMILY = "Microsoft YaHei"
WINDOWS_TEXT_FONTS = [
    "Microsoft YaHei UI",
    "SimSun",
    "Segoe UI",
    "Arial",
    "Yu Gothic UI",
    "Meiryo",
    "Microsoft JhengHei UI",
    "PMingLiU",
]
WINDOWS_TEXT_FONT_BY_LANGUAGE = {
    "zh": "Microsoft YaHei UI",
    "zh_CN": "Microsoft YaHei UI",
    "zh_TW": "Microsoft JhengHei UI",
    "en": "Segoe UI",
    "ja": "Yu Gothic UI",
}

# 只存在于 Windows 的字体名：非 Windows 的取值里不该出现任何一个。
# Arial 刻意不在列表里——macOS 自带、Linux 常有（或由 Liberation Sans 度量兼容替代），
# 它是三个平台共用的兜底项。
WINDOWS_ONLY_FONTS = (
    "Microsoft YaHei",
    "Microsoft JhengHei UI",
    "SimSun",
    "PMingLiU",
    "Segoe UI",
    "Yu Gothic UI",
    "Meiryo",
)


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(fonts, "IS_WINDOWS", True)
    monkeypatch.setattr(fonts, "IS_MACOS", False)
    return fonts


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(fonts, "IS_WINDOWS", False)
    monkeypatch.setattr(fonts, "IS_MACOS", True)
    return fonts


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(fonts, "IS_WINDOWS", False)
    monkeypatch.setattr(fonts, "IS_MACOS", False)
    return fonts


class TestWindowsValuesAreUnchanged:
    """发行版只有 Windows，这组断言是"没有改坏 Windows"的凭据。"""

    def test_css_stacks(self, as_windows):
        assert fonts.css_font_family() == WINDOWS_CSS_FONT_FAMILY
        assert fonts.css_font_family_ui() == WINDOWS_CSS_FONT_FAMILY_UI
        assert fonts.css_font_family_fluent() == WINDOWS_FLUENT_FONT_FAMILY

    def test_three_stacks_stay_distinct(self, as_windows):
        """三份栈本来就不同，迁移时若被合并成一份，Windows 渲染就变了。"""
        stacks = {
            fonts.css_font_family(),
            fonts.css_font_family_ui(),
            fonts.css_font_family_fluent(),
        }
        assert len(stacks) == 3

    def test_family_names(self, as_windows):
        assert fonts.default_font_family() == WINDOWS_DEFAULT_FONT_FAMILY
        assert fonts.ui_font_family("zh") == "Microsoft YaHei UI"
        assert fonts.ui_font_family("en") == "Segoe UI"
        assert fonts.ui_font_family("ja") == "Yu Gothic UI"
        # 语言未知时用通用界面字体，不是正文那一个
        assert fonts.ui_font_family() == "Segoe UI"
        assert fonts.ui_font_family("ko") == "Segoe UI"

    def test_text_font_list(self, as_windows):
        assert fonts.common_text_fonts() == WINDOWS_TEXT_FONTS

    def test_text_font_by_language(self, as_windows):
        assert fonts.default_text_font_by_language() == WINDOWS_TEXT_FONT_BY_LANGUAGE


class TestOtherPlatforms:
    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_no_windows_only_font_names(self, platform, monkeypatch):
        monkeypatch.setattr(fonts, "IS_WINDOWS", False)
        monkeypatch.setattr(fonts, "IS_MACOS", platform == "macos")

        texts = [
            fonts.css_font_family(),
            fonts.css_font_family_ui(),
            fonts.css_font_family_fluent(),
            fonts.default_font_family(),
            fonts.default_ui_font_family(),
            *fonts.common_text_fonts(),
            *fonts.default_text_font_by_language().values(),
            *[fonts.ui_font_family(code) for code in ("zh", "zh_CN", "zh_TW", "en", "ja")],
        ]
        for text in texts:
            for name in WINDOWS_ONLY_FONTS:
                assert name not in text, f"{platform} 的取值里出现了 Windows 字体 {name!r}"

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_every_css_stack_has_a_generic_fallback(self, platform, monkeypatch):
        """字体栈必须以通用族收尾，否则最后一项缺失时没有任何兜底。"""
        monkeypatch.setattr(fonts, "IS_WINDOWS", platform == "windows")
        monkeypatch.setattr(fonts, "IS_MACOS", platform == "macos")
        for stack in (fonts.css_font_family(), fonts.css_font_family_ui(),
                      fonts.css_font_family_fluent()):
            assert stack.endswith("sans-serif")

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_quoted_stack_entries_are_balanced(self, platform, monkeypatch):
        monkeypatch.setattr(fonts, "IS_WINDOWS", platform == "windows")
        monkeypatch.setattr(fonts, "IS_MACOS", platform == "macos")
        for stack in (fonts.css_font_family(), fonts.css_font_family_ui(),
                      fonts.css_font_family_fluent()):
            assert stack.count('"') % 2 == 0

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_text_font_by_language_covers_the_same_languages(self, platform, monkeypatch):
        monkeypatch.setattr(fonts, "IS_WINDOWS", platform == "windows")
        monkeypatch.setattr(fonts, "IS_MACOS", platform == "macos")
        assert set(fonts.default_text_font_by_language()) == set(WINDOWS_TEXT_FONT_BY_LANGUAGE)

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_language_table_agrees_with_ui_font_family(self, platform, monkeypatch):
        """两者都是「某语言的界面用什么字体」，取自同一张表就不能漂移。"""
        monkeypatch.setattr(fonts, "IS_WINDOWS", platform == "windows")
        monkeypatch.setattr(fonts, "IS_MACOS", platform == "macos")
        table = fonts.default_text_font_by_language()
        for code, family in table.items():
            assert fonts.ui_font_family(code) == family

    @pytest.mark.parametrize("platform", ["macos", "linux"])
    def test_default_family_is_offered_in_the_text_font_list(self, platform, monkeypatch):
        """默认字体必须在候选列表里，否则文字工具打开时选中的是列表外的项。"""
        monkeypatch.setattr(fonts, "IS_WINDOWS", False)
        monkeypatch.setattr(fonts, "IS_MACOS", platform == "macos")
        assert fonts.default_font_family() in fonts.common_text_fonts()
