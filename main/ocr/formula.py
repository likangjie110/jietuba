# -*- coding: utf-8 -*-
"""formula.py - 公式识别引擎契约与注册表。

公式识别与普通 OCR 是两件事：文本引擎（``engines.py`` 里的 PP-OCR）只会给「这一行是
什么字」，给不出 LaTeX 结构，所以这里单独定一套契约。外部实现只要实现 FormulaEngine
并 ``register_formula_engine()`` 注册进来就能用，本仓库其它文件不用改——这和 OCR 引擎、
翻译 provider 是同一个套路。

**本仓库不带公式模型**：``models/`` 下只有 PP-OCR 的 det/rec，``ppocr_rust`` 也只导出
文本引擎。因此 ``is_formula_available()`` 在干净环境里就是 False，调用方必须按
「不可用」显式降级（提示 + 日志），而不是静默返回空串。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .engine import ocr_log


def _t(template: str, **kwargs) -> str:
    """可翻译的日志文案。core.logger 在启动预加载阶段未必就绪，取不到就退回原文。"""
    try:
        from core.logger import T

        return T(template, **kwargs)
    except Exception:
        return template.format(**kwargs) if kwargs else template

#: 注册表：引擎 id → 实例。顺序即注册顺序，也是可用引擎列表的顺序。
_ENGINES: Dict[str, "FormulaEngine"] = {}


class FormulaEngine(ABC):
    """公式识别引擎基类：探测可用性 → （可选）预热 → 识别成 LaTeX → 释放。"""

    #: 引擎 id，也是 ``recognize_formula(engine_name=...)`` 的入参
    name: str = ""
    #: 显示名，只用于日志与状态文本
    label: str = ""

    def __init__(self):
        self.last_error: Optional[str] = None

    @abstractmethod
    def is_available(self) -> bool:
        """模型/运行时是否齐备。会被反复调用（探测、入口判定），实现里要自己缓存结果。"""

    @abstractmethod
    def recognize_formula(self, pixmap) -> str:
        """识别一张图（QPixmap 或 QImage）里的公式，返回 LaTeX 文本。

        没识别到公式时返回空串（不是 None）——调用方据此走「没识别到」的分支。
        抛异常也由注册表兜住，引擎实现不必自己吞。
        """

    def initialize(self) -> bool:
        """预热引擎（加载模型、建会话）。默认无操作。"""
        return True

    def release(self) -> None:  # noqa: B027 —— 默认无操作是接口的一部分
        """释放引擎占用的资源（模型动辄几十 MB）。"""


def register_formula_engine(engine: FormulaEngine) -> None:
    """登记一个公式引擎，同名覆盖（后者说了算）。"""
    _ENGINES[engine.name] = engine


def unregister_formula_engine(name: str) -> bool:
    """注销引擎；没登记过返回 False。测试与插件热替换用。"""
    return _ENGINES.pop(name, None) is not None


def available_formula_engines() -> List[str]:
    """当前可用的引擎 id，顺序与注册顺序一致。"""
    return [name for name, engine in _ENGINES.items() if engine.is_available()]


def registered_formula_engines() -> List[str]:
    """登记过的引擎 id（不管可不可用）。"""
    return list(_ENGINES)


def is_formula_available() -> bool:
    """有没有可用的公式引擎。入口据此决定「能不能识别」并显式降级。"""
    return bool(available_formula_engines())


def recognize_formula(pixmap, engine_name: Optional[str] = None) -> str:
    """识别公式，返回 LaTeX 文本。

    没有可用引擎时返回空串并记一条降级日志（**不抛异常**）：入口那边会再提示一次，
    但日志是给「用户没看到弹窗」时留的现场。
    """
    if engine_name:
        engine = _ENGINES.get(engine_name)
        if engine is None:
            ocr_log(_t("公式引擎未注册: {name}", name=engine_name), "WARN")
            return ""
        if not engine.is_available():
            ocr_log(_t("公式引擎不可用: {name}", name=engine_name), "WARN")
            return ""
    else:
        names = available_formula_engines()
        if not names:
            ocr_log(_t("公式识别不可用：没有可用的公式引擎"), "WARN")
            return ""
        engine = _ENGINES[names[0]]

    try:
        engine.initialize()
        latex = engine.recognize_formula(pixmap) or ""
    except Exception as e:                      # 引擎可能是第三方包，别让异常冒到 UI
        engine.last_error = str(e)
        ocr_log(_t("公式识别异常: {e}", e=str(e)), "ERROR")
        return ""

    if not latex.strip():
        ocr_log(_t("公式引擎没识别到内容: {name}", name=engine.name), "INFO")
        return ""
    return latex.strip()
