# -*- coding: utf-8 -*-
"""自动分流：一块截图该用哪条识别路径，先本地判，判不出来才考虑视觉模型。

用户不总是知道自己截的是文字、表格还是公式——「帮我看看这块是什么」是最自然的用法。
这里是那个判断本身：**纯函数**，输入本地 OCR 的结果与「本机有什么能力」，输出一条路由。

判断顺序（也是本模块的核心约定）：

1. **有表格特征 → 表格**：本地几何启发式能切出行列时，结果表是可编辑的，用户可以当场改，
   比让模型重写一遍更可控。
2. **有文字且置信度够 → 文字**：本地 OCR 已经拿到结果，没有理由把它送去别处。
3. **有文字但置信度低**：配了视觉模型就让它按「精确取字」重读；否则若本机有公式引擎就交给
   公式引擎（公式图在 OCR 眼里正是「认出来一堆符号但都不踏实」这个形态）；再没有就仍然给
   本地结果（调用方会补一句置信度提示），**不是**直接失败。
4. **完全没认到文字**：配了视觉模型就描述；否则如实报「没识别到内容」。

第 4 条刻意**不**走公式引擎：真机验证时对着一张普通窗口截图，公式引擎照样会吐出一串
LaTeX（实测拿到 `\\cdot`）——没有文字不等于有公式，把噪声复制进剪贴板比如实说「没识别到」
更糟。公式图基本都能被 OCR 认出一些符号（落在第 3 条），所以这样反而更容易命中。

**本地优先、联网兜底**是刻意的：视觉模型会花用户自己的额度、并把截图发到用户配置的端点，
只在本地确实取不到结果时才用。
"""

from typing import Any

from core.logger import T, log_debug

#: 路由取值
ROUTE_TEXT = "text"                    # 本地 OCR 的文字（含低置信度但没别的可换的情况）
ROUTE_TABLE = "table"                  # 本地几何启发式，结果进可编辑表格
ROUTE_FORMULA = "formula"              # 公式引擎（低置信度的符号串）
ROUTE_VISION_TEXT = "vision_text"      # 视觉模型按「精确取字」重读
ROUTE_VISION_DESCRIBE = "vision_describe"   # 视觉模型描述图像
ROUTE_NONE = "none"                    # 什么都没识别到，也没有可用的兜底手段

#: 全部路由（界面/测试用）
ROUTES = (ROUTE_TEXT, ROUTE_TABLE, ROUTE_FORMULA, ROUTE_VISION_TEXT,
          ROUTE_VISION_DESCRIBE, ROUTE_NONE)


def ocr_text(result: Any) -> str:
    """结果里的全部文字（只为判空与判置信度，不参与排版）。"""
    from ocr.ocr_manager import format_ocr_result_text

    try:
        return format_ocr_result_text(result).strip()
    except Exception:
        # 结果形态不对时按「没有文字」处理：分流不该因为一个坏结果抛异常
        return ""


def has_table_features(result: Any) -> bool:
    """结果里有没有表格结构；直接复用给表格编辑器的那套几何判定，不另写一份。"""
    from ocr.ocr_manager import table_grid_from_ocr_result

    return bool(table_grid_from_ocr_result(result))


def choose_route(result: Any, *, threshold: float = 0.6, has_formula: bool = False,
                 has_vision: bool = False) -> str:
    """选一条路由（见模块 docstring 的顺序）。

    ``result`` 是本地 OCR 的原始结果（带坐标）；``has_formula`` / ``has_vision`` 是
    「本机有没有可用的公式引擎 / 配好的视觉模型」，由调用方探测后传进来——这里只做判断，
    不探测能力，才能穷举测试。
    """
    from ocr.quality import average_confidence

    if not isinstance(result, dict) or result.get("code") != 100:
        # 结果本身不合法（引擎失败、图片是空的）：当作「没识别到」继续往下走
        return _without_text(has_vision=has_vision)

    if has_table_features(result):
        return ROUTE_TABLE

    text = ocr_text(result)
    if not text:
        return _without_text(has_vision=has_vision)

    confidence = average_confidence(result)
    if confidence >= _threshold(threshold):
        log_debug(T("自动分流: 本地文字识别（置信度 {percent}%）",
                    percent=int(round(confidence * 100))), "AutoRoute")
        return ROUTE_TEXT

    if has_vision:
        log_debug(T("自动分流: 本地置信度偏低，交给视觉模型重读"), "AutoRoute")
        return ROUTE_VISION_TEXT
    if has_formula:
        log_debug(T("自动分流: 本地置信度偏低且没有视觉模型，交给公式引擎"), "AutoRoute")
        return ROUTE_FORMULA

    # 没有模型也没有公式引擎：本地结果照样给出去，调用方会补一句置信度提示
    return ROUTE_TEXT


def _without_text(*, has_vision: bool) -> str:
    return ROUTE_VISION_DESCRIBE if has_vision else ROUTE_NONE


def _threshold(value: Any) -> float:
    """阈值容错：读坏了按 0.6 处理（与 ocr.quality 的默认值一致）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.6
    if number <= 0 or number > 1:
        return 0.6
    return number
