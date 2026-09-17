# -*- coding: utf-8 -*-
"""结构护栏助手：判断某个模块的**代码**里是否还在直接碰平台 API。

为什么要用 AST 而不是在源码文本里搜关键字：代码的注释与文档字符串里经常会提到
``ctypes.windll``、``win32gui`` 这些名字（恰恰是为了说明为什么不再用它），按文本搜
会把解释当成违规。这里只看真正会执行的部分——import 语句与属性访问链。

这个模块不叫 ``test_*``，pytest 不会收集它，只作为测试的公共工具。
"""

import ast
import inspect
import pathlib
import textwrap

# 只有 Windows 才有的第三方模块：出现它们的 import 就意味着这段代码在别的平台上
# 要么导入失败，要么必须靠 try/except 兜住。
WINDOWS_ONLY_MODULES = (
    "win32api",
    "win32con",
    "win32gui",
    "win32clipboard",
    "win32event",
    "win32process",
    "winreg",
    "pythoncom",
    "pywintypes",
    "windows_media_ocr",
)

# macOS 专有（pyobjc）
MACOS_ONLY_MODULES = ("Quartz", "AppKit", "ApplicationServices", "objc")

# 平台专有的属性链前缀：``ctypes.windll`` 在非 Windows 上根本不存在
PLATFORM_ATTRIBUTE_CHAINS = (
    ("ctypes", "windll"),
    ("ctypes", "wintypes"),
)

# 平台分派常量与探针：业务模块拿它们做 if，等于把「是不是 Windows」写进业务逻辑。
# 正确做法是问能力（``core/platform/capabilities.py``）或直接拿平台层给的后端/实现。
PLATFORM_DISPATCH_NAMES = (
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
    "PLATFORM_NAME",
    "qt_platform_name",
    "is_native_qt_platform",
)

# 上面这些名字的来源：平台层门面，以及唯一的探测模块
PLATFORM_SOURCE_MODULES = ("core.platform", "core.platform.detection")


def module_ast(module) -> ast.Module:
    """取模块源码的 AST（去掉公共缩进与 BOM 后解析）。

    必须处理 BOM：本仓库不少文件（shortcut_manager、frame_recorder、resource_manager…）
    带 UTF-8 BOM，``ast.parse`` 遇到它直接抛 SyntaxError。早先的扫描脚本把
    SyntaxError 当成「解析不了，跳过」，于是这些文件被静默漏掉——护栏看起来在跑，
    实际上没覆盖它们。
    """
    return ast.parse(_clean_source(inspect.getsource(module)))


def source_ast(path) -> ast.Module:
    """按文件路径取 AST，同样处理 BOM。"""
    return ast.parse(_clean_source(pathlib.Path(path).read_text(encoding="utf-8-sig")))


def _clean_source(text: str) -> str:
    return textwrap.dedent(text.lstrip("\ufeff"))


def _dotted_name(node: ast.AST) -> str | None:
    """``a.b.c`` 形式的属性链文本；不是纯名字/属性链时返回 None。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def platform_dispatch_usages(tree: ast.AST) -> set[str]:
    """模块里直接使用平台分派常量/探针的地方（三种写法都算）。

    - ``from core.platform import IS_WINDOWS``
    - ``from core.platform.detection import IS_MACOS``
    - 先导入模块再取属性：``from core.platform import detection`` → ``detection.PLATFORM_NAME``
      （``import core.platform as platform`` → ``platform.qt_platform_name()``、
      ``core.platform.detection.IS_LINUX`` 同样算）

    只对「模块形式」要求真的取了值（``detection.IS_WINDOWS``）：直接把常量 import 进来
    本身就是违规——业务模块没有别的理由导这个名字。
    """
    found: set[str] = set()
    # 本地名 -> 真实模块名；真实模块名本身也登记，好覆盖 ``core.platform.IS_WINDOWS``
    aliases: dict[str, str] = {name: name for name in PLATFORM_SOURCE_MODULES}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module not in PLATFORM_SOURCE_MODULES:
                continue
            for alias in node.names:
                if alias.name in PLATFORM_DISPATCH_NAMES:
                    found.add(f"from {module} import {alias.name}")
                elif module == "core.platform" and alias.name == "detection":
                    aliases[alias.asname or alias.name] = "core.platform.detection"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in PLATFORM_SOURCE_MODULES:
                    aliases[alias.asname or alias.name] = alias.name

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in PLATFORM_DISPATCH_NAMES:
            continue
        path = _dotted_name(node.value)
        if path is not None and aliases.get(path, path) in PLATFORM_SOURCE_MODULES:
            found.add(f"{path}.{node.attr}")
    return found


def is_importable_module_name(stem: str) -> bool:
    """``.py`` 文件名是否是合法的 Python 模块名。

    名字带空格（例如同步工具/编辑器留下的 ``page_misc 2.py`` 冲突副本）的文件根本
    不能被 import，也就不属于应用的一部分。它们会污染结构扫描并可能被 ``git add -A``
    误提交，所以要单独报出来，而不是混进「依赖了单一平台」那一类。
    """
    if not stem or stem[0].isdigit():
        return False
    return all(ch.isalnum() or ch == "_" for ch in stem)


def imported_module_names(module) -> set[str]:
    """模块里 import 过的顶层模块名（不含函数内的延迟导入——那些本就是允许的）。"""
    return {
        alias.name.split(".")[0]
        for node in ast.walk(module_ast(module))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(module_ast(module))
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }


def local_imports_inside_functions(module) -> set[str]:
    """函数体内的延迟导入（这些是允许的降级点，例如 ``import win32api`` 放在 try 里）。"""
    found = set()
    for node in ast.walk(module_ast(module)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                found.update(a.name.split(".")[0] for a in sub.names)
            elif isinstance(sub, ast.ImportFrom) and sub.level == 0 and sub.module:
                found.add(sub.module.split(".")[0])
    return found


def all_imported_names(module) -> set[str]:
    """模块里出现过的全部顶层模块名，含函数体内的延迟导入。"""
    found = set()
    for node in ast.walk(module_ast(module)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def uses_attribute_chain(module, chain: tuple[str, str]) -> bool:
    """模块里是否出现 ``a.b`` 形式的属性访问（仅代码，不含注释/文档）。"""
    for node in ast.walk(module_ast(module)):
        if not isinstance(node, ast.Attribute) or node.attr != chain[1]:
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id == chain[0]:
            return True
        if isinstance(value, ast.Attribute) and value.attr == chain[0]:
            return True
    return False


def platform_violations(module) -> list[str]:
    """返回模块在「不直接依赖单一平台」上的违规项描述；空列表表示合规。"""
    violations = []
    top_level = imported_module_names(module)
    for name in WINDOWS_ONLY_MODULES + MACOS_ONLY_MODULES:
        if name in top_level:
            violations.append(f"模块级 import {name}")
    for chain in PLATFORM_ATTRIBUTE_CHAINS:
        if uses_attribute_chain(module, chain):
            violations.append(f"直接访问 {'.'.join(chain)}")
    return violations
