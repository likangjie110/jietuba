# -*- coding: utf-8 -*-
"""仓库级结构护栏：业务模块不得再直接依赖单一平台。

这是「项目天然跨平台」的可执行定义。原来判断平台的方式有 6 种且分散在 11 个模块里，
没有任何机制阻止新代码继续这么写；本文件把现状钉成一份**账本**：

- 账本之外的模块一旦出现 ``ctypes.windll`` / ``sys.platform`` / 模块级 Win32 或
  pyobjc 导入 / ``os.startfile``，测试直接失败——新代码不该再走老路；
- 账本里的模块一旦清理干净（迁移完成），测试同样失败，提示把该行删掉——
  否则账本会烂成一句空话，没有人知道还剩多少。

也就是说，这份列表只能变短。它现在对应的正是尚未迁移的能力：窗口枚举、全局热键、
剪贴板写图与粘贴注入。
"""

import ast
import pathlib

import pytest

from platform_guard import (
    MACOS_ONLY_MODULES,
    WINDOWS_ONLY_MODULES,
    is_importable_module_name,
    source_ast,
)

# tests/ 与 main/ 的上一级，即仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCAN_ROOT = REPO_ROOT / "main"

# 不参与扫描的目录：平台层自身就是唯一允许碰平台 API 的地方；测试与构建脚本不算业务
# 代码；translations 里没有 .py。
EXCLUDED_DIRS = {"platform", "tests", "translations", "scripts"}

# 尚未迁移的模块 -> 所属能力。迁移完成后必须删掉对应行，否则 test_ledger 会失败。
REMAINING_PLATFORM_DEPENDENCIES = {
    # 全局热键原先也在这里；键盘与鼠标侧键的机制已迁到 core/platform/hotkey（门面）
    # 与 hotkey_win32（RegisterHotKey、WM_HOTKEY 过滤器、侧键钩子原语）。
    "main/ocr/engines.py":
        "windows_media_ocr 是构建变体（OCR_VARIANT == \"win\"）的专有引擎，"
        "属于按能力而不是按平台分派；保留，但需在 OCR 层显式登记为不可用引擎",
}


def _is_excluded(path: pathlib.Path) -> bool:
    if path.name.startswith("_"):
        return False
    if not is_importable_module_name(path.stem):
        # 名字带空格之类的文件不能被 import，不属于应用的一部分；
        # 它们由 TestNoStrayModules 单独报出来
        return True
    return bool(set(path.parts) & EXCLUDED_DIRS)


def _imported_modules(tree: ast.AST) -> set[str]:
    """模块里 import 过的顶层模块名。

    函数体内的延迟导入也算——它们同样是「这个模块依赖某个平台」的证据，
    只是把失败推迟到了调用时。真正该待在平台层之外的是这类依赖本身。
    """
    names = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names |= {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    return names


def _platform_dependencies(tree: ast.AST) -> set[str]:
    """模块直接依赖单一平台的全部证据。"""
    found = set()

    imported = _imported_modules(tree)
    for name in WINDOWS_ONLY_MODULES + MACOS_ONLY_MODULES:
        if name in imported:
            found.add(f"import {name}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        on_ctypes = (isinstance(value, ast.Name) and value.id == "ctypes") or (
            isinstance(value, ast.Attribute) and value.attr == "ctypes"
        )
        if node.attr in ("windll", "wintypes") and on_ctypes:
            found.add(f"ctypes.{node.attr}")
        if node.attr == "platform" and isinstance(value, ast.Name) and value.id == "sys":
            found.add("sys.platform")
        # os.startfile 只存在于 Windows
        if node.attr == "startfile" and isinstance(value, ast.Name) and value.id == "os":
            found.add("os.startfile")
    return found


def scan_platform_dependencies() -> dict[str, list[str]]:
    """扫描 main/ 下的业务模块，返回 {相对路径: [违规项...]}。"""
    result = {}
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if _is_excluded(path.relative_to(SCAN_ROOT)):
            continue
        issues = _platform_dependencies(source_ast(path))
        if issues:
            result[path.relative_to(REPO_ROOT).as_posix()] = sorted(issues)
    return result


class TestNoNewPlatformDependencies:
    def test_scan_finds_something(self):
        """扫描器本身要能发现问题：全都干净时这条会失败，提示删除整个护栏。

        防止「扫描逻辑写错了所以永远通过」——那比没有护栏更糟。
        """
        assert scan_platform_dependencies(), "扫描没有发现任何平台依赖，先确认 platform_guard 是否失效"

    def test_only_ledgered_modules_touch_platform_apis(self):
        found = scan_platform_dependencies()
        new_offenders = sorted(set(found) - set(REMAINING_PLATFORM_DEPENDENCIES))
        assert not new_offenders, (
            "这些模块新增了对单一平台的直接依赖，请改走 core/platform：\n"
            + "\n".join(f"  {p}: {found[p]}" for p in new_offenders)
        )

    def test_ledger_has_no_stale_entries(self):
        """迁移完成后必须把账本里的对应行删掉，否则这份列表会烂掉。"""
        found = set(scan_platform_dependencies())
        cleaned = sorted(set(REMAINING_PLATFORM_DEPENDENCIES) - found)
        assert not cleaned, (
            "这些模块已经不再直接依赖平台 API，请从 REMAINING_PLATFORM_DEPENDENCIES 删掉：\n"
            + "\n".join(f"  {p}" for p in cleaned)
        )

    def test_ledger_entries_are_described(self):
        """每条待办都要写清属于哪个能力，别人接手时不必重新考古。"""
        for path, reason in REMAINING_PLATFORM_DEPENDENCIES.items():
            assert reason.strip(), f"{path} 缺少说明"
            assert (REPO_ROOT / path).exists(), f"{path} 已不存在"


class TestNoStrayModules:
    """仓库里不该出现不能被 import 的 .py 文件。

    这类文件是同步工具/编辑器留下的「X 2.py」冲突副本：既不会被加载，也容易被
    ``git add -A`` 误提交，还会让结构扫描报出莫名其妙的结果。它们通常与某个 git
    历史版本逐字节相同，确认后用 git 就能取回，可以直接删掉。
    """

    def test_python_filenames_are_importable(self):
        offenders = sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in SCAN_ROOT.rglob("*.py")
            if not is_importable_module_name(path.stem)
        )
        assert offenders == [], (
            "这些 .py 文件名不是合法的模块名（多半是重复副本，可对照 git 历史确认后删除）：\n"
            + "\n".join(f"  {p}" for p in offenders)
        )


class TestPlatformLayerIsTheOnlyHome:
    def test_platform_layer_does_not_import_business_modules(self):
        """平台层是叶子：它不该反过来依赖业务模块，否则会绕成环。"""
        platform_dir = SCAN_ROOT / "core" / "platform"
        forbidden = {
            "capture", "canvas", "tools", "gif", "pin", "stitch", "clipboard",
            "ocr", "translation", "ui", "barcode", "settings",
        }
        offenders = []
        for path in sorted(platform_dir.glob("*.py")):
            imported = _imported_modules(source_ast(path))
            hit = imported & forbidden
            if hit:
                offenders.append((path.name, sorted(hit)))
        assert offenders == [], offenders

    def test_platform_layer_imports_logger_lazily(self):
        """平台层被 core.constants 导入，而 constants 在 logger 的依赖链上。

        模块级 ``from core.logger import ...`` 会让 core.logger -> core.constants ->
        core.platform.paths -> core.logger 成环，因此平台层一律在函数内延迟导入。
        """
        platform_dir = SCAN_ROOT / "core" / "platform"
        offenders = []
        for path in sorted(platform_dir.glob("*.py")):
            tree = source_ast(path)
            for node in tree.body:  # 只看模块顶层，函数体内的导入无需考虑
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("core.logger"):
                    offenders.append(f"{path.name}:{node.lineno}")
                if isinstance(node, ast.Import):
                    if any(a.name.startswith("core.logger") for a in node.names):
                        offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == [], (
            "平台层必须在函数内延迟导入 core.logger，否则与 core.constants 成环："
            + ", ".join(offenders)
        )

    def test_platform_layer_is_importable_without_qt(self):
        """平台探测与能力矩阵要能在 QApplication 之前使用（bootstrap 早期就调它们）。"""
        from core.platform import capabilities, detection

        assert isinstance(detection.IS_WINDOWS, bool)
        assert capabilities.available(capabilities.Capability.WINDOW_ENUMERATION) in (True, False)

    @pytest.mark.parametrize("module_name", [
        "core.platform.capabilities",
        "core.platform.detection",
        "core.platform.fonts",
        "core.platform.paths",
        "core.platform.shell",
        "core.platform.startup",
        "core.platform.process",
        "core.platform.pointer",
        "core.platform.window_ops",
    ])
    def test_every_platform_module_imports_cleanly(self, module_name):
        """任何平台模块都要能在当前平台导入——这是「缺失依赖不炸在 import 上」的底线。"""
        import importlib

        assert importlib.import_module(module_name) is not None
