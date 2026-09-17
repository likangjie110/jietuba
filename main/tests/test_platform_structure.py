# -*- coding: utf-8 -*-
"""仓库级结构护栏：业务模块不得再直接依赖单一平台。

这是「项目天然跨平台」的可执行定义。原来判断平台的方式有 6 种且分散在 11 个模块里，
没有任何机制阻止新代码继续这么写。这里有两道扫描，外加一份**账本**：

- 扫描一（老写法）：账本之外的模块一旦出现 ``ctypes.windll`` / ``sys.platform`` /
  模块级 Win32 或 pyobjc 导入 / ``os.startfile``，测试直接失败；
- 扫描二（平台分派）：业务模块不许用 ``IS_WINDOWS`` / ``PLATFORM_NAME`` /
  ``qt_platform_name()`` 这类常量做分派——这类代码三平台都能跑，只有结构约定拦得住；
- 账本（``REMAINING_PLATFORM_DEPENDENCIES``）：扫描一里尚未清掉的模块，一旦清理干净
  （迁移完成），测试同样失败，提示把该行删掉——否则账本会烂成一句空话，没有人知道
  还剩多少。

也就是说，账本只能变短。四项迁移（窗口枚举、全局热键、剪贴板写图与粘贴注入、启动
预热）都已完成后，它只剩一条：OCR 的 Windows 构建变体引擎，见下面的说明。
"""

import ast
import pathlib

import pytest

from platform_guard import (
    MACOS_ONLY_MODULES,
    WINDOWS_ONLY_MODULES,
    is_importable_module_name,
    platform_dispatch_usages,
    source_ast,
)

# tests/ 与 main/ 的上一级，即仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCAN_ROOT = REPO_ROOT / "main"

# 不参与扫描的目录：平台层自身就是唯一允许碰平台 API 的地方；测试与构建脚本不算业务
# 代码；translations 里没有 .py。
EXCLUDED_DIRS = {"platform", "tests", "translations", "scripts"}

PLATFORM_DIR = SCAN_ROOT / "core" / "platform"

# 平台层的模块清单（含 Windows/macOS 后端），按目录现算：
# 新加一个后端就自动进入「能不能导入」的检查，不必回来补名字。
PLATFORM_MODULES = sorted(
    f"core.platform.{path.stem}"
    for path in PLATFORM_DIR.glob("*.py")
    if path.name != "__init__.py"
)

# 扫描一里剩下的条目 -> 为什么留着。清理干净后必须删掉对应行，否则 test_ledger 会失败。
REMAINING_PLATFORM_DEPENDENCIES = {
    # 全局热键、窗口枚举、剪贴板、启动预热原先都在这里，已分别迁到 core/platform
    # 的 hotkey / window / clipboard 与 process。
    "main/ocr/engines.py":
        "windows_media_ocr 是构建变体（OCR_VARIANT == \"win\"）的专有引擎：import 延迟到"
        "函数内且包在 try 里，可用性由 WindowsMediaOcrEngine.is_available() 自报、"
        "OCRManager 按它过滤（get_available_engines）——是按能力分派，不是按平台分派；"
        "非 win 变体的构建根本不注册这个引擎",
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


def scan_platform_dispatch() -> dict[str, list[str]]:
    """扫描 main/ 下的业务模块，返回 {相对路径: [平台分派用法...]}。

    和上面那条扫描互补：前者拦「直接调平台 API」，这里拦「拿平台判断做 if」
    （``IS_WINDOWS`` / ``PLATFORM_NAME`` / ``qt_platform_name()``）。这类代码不报
    import 错误、也能跨平台跑，所以只有结构约定能拦住它。

    平台层自己排除在外：按平台分派正是它的职责（``_is_excluded`` 会漏掉它自己的
    ``__init__.py``，因为导出文件按名字被特殊对待）。
    """
    result = {}
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if PLATFORM_DIR in path.parents:
            continue
        if _is_excluded(path.relative_to(SCAN_ROOT)):
            continue
        issues = platform_dispatch_usages(source_ast(path))
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


class TestNoPlatformDispatchInBusinessCode:
    """业务模块不该问「是不是 Windows」——该问能力，或直接拿平台层给的后端。

    这条比「别调平台 API」更严：``if IS_WINDOWS:`` 不报 import 错误、三平台都能跑，
    但它把平台差异写进了业务逻辑，加一个新平台时还是得回来改这里。
    """

    @pytest.mark.parametrize("source, expected", [
        ("from core.platform import IS_WINDOWS\n", "from core.platform import IS_WINDOWS"),
        ("from core.platform.detection import IS_MACOS\n",
         "from core.platform.detection import IS_MACOS"),
        ("from core.platform import detection\nx = detection.PLATFORM_NAME\n",
         "detection.PLATFORM_NAME"),
        ("import core.platform as platform\nx = platform.qt_platform_name()\n",
         "platform.qt_platform_name"),
        ("import core.platform.detection as det\nx = det.IS_LINUX\n", "det.IS_LINUX"),
        ("x = core.platform.IS_WINDOWS\n", "core.platform.IS_WINDOWS"),
    ])
    def test_dispatch_forms_are_caught(self, source, expected):
        """六种写法都要认出来，否则护栏只是看着在跑。"""
        assert expected in platform_dispatch_usages(ast.parse(source))

    @pytest.mark.parametrize("source", [
        "from core.platform import Capability, available\n",
        "from core.platform.window import WindowFinder\n",
        "from core.platform import capabilities\nx = capabilities.available\n",
        "from core.platform.capture import create_session\n",
    ])
    def test_capability_queries_are_allowed(self, source):
        """问能力、拿实现是允许的：这才是业务模块该走的路。"""
        assert platform_dispatch_usages(ast.parse(source)) == set()

    def test_business_modules_do_not_dispatch_on_platform_constants(self):
        found = scan_platform_dispatch()
        assert not found, (
            "这些模块在用平台判断做分派，请改走 core/platform 的能力查询或后端：\n"
            + "\n".join(f"  {p}: {found[p]}" for p in sorted(found))
        )


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

    @pytest.mark.parametrize("module_name", PLATFORM_MODULES)
    def test_every_platform_module_imports_cleanly(self, module_name):
        """任何平台模块都要能在当前平台导入——这是「缺失依赖不炸在 import 上」的底线。

        模块清单按目录现算（含 Windows/macOS 后端），免得新加一个后端要记得回来补名字。
        """
        import importlib

        assert importlib.import_module(module_name) is not None
