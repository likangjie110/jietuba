# -*- coding: utf-8 -*-
"""判断一条截图历史「来自哪里」：窗口 / 窗口里的元素 / 整块显示器 / 手拉选区。

判定靠**几何**：拿选区中心去问平台层「这一点的窗口和元素是什么」，谁和选区对得上就记谁。
这样不需要在截图会话里一路传状态（截图窗口、手势入口、静默截图各有各的路径），
判定逻辑也只有这一份。

纯逻辑部分（``classify``）与取数部分（``probe_under_point``）分开：前者能穷举测，
后者只在真机上跑得到平台 API。
"""

from __future__ import annotations

from core.logger import T, log_debug, log_exception

#: 来源取值（与 store.SOURCES 一致）
SOURCE_WINDOW = "window"
SOURCE_ELEMENT = "element"
SOURCE_MONITOR = "monitor"
SOURCE_REGION = "region"

#: 判定容差（px）：截图时选区常常比窗口少 1~2px（描边/内缩），完全相等是过严的条件
DEFAULT_TOLERANCE = 8


def _as_tuple(rect) -> tuple:
    """把 QRect / 列表 / 元组统一成 ``(x1, y1, x2, y2)``。"""
    if rect is None:
        return ()
    if isinstance(rect, (list, tuple)):
        if len(rect) == 4:
            x1, y1, x2, y2 = (float(v) for v in rect)
            return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if len(rect) == 2:
            return (float(rect[0]), float(rect[1]), float(rect[0]), float(rect[1]))
        return ()
    try:
        return (float(rect.x()), float(rect.y()),
                float(rect.x() + rect.width()), float(rect.y() + rect.height()))
    except AttributeError:
        return ()


def rects_close(a, b, tolerance: int = DEFAULT_TOLERANCE) -> bool:
    """两个矩形是不是「差不多是同一块」（四边都在容差内）。"""
    first, second = _as_tuple(a), _as_tuple(b)
    if not first or not second:
        return False
    return all(abs(left - right) <= tolerance for left, right in zip(first, second))


def classify(selection, *, detected=None, detected_is_element: bool = False,
             screens=(), tolerance: int = DEFAULT_TOLERANCE) -> str:
    """按几何判定来源。

    ``detected`` 是平台层报的「这一点上的元素/窗口矩形」，``detected_is_element``
    说明它来自元素档还是窗口档；``screens`` 是各显示器的矩形（QRect 或四元组）。
    """
    if detected is not None and rects_close(selection, detected, tolerance):
        return SOURCE_ELEMENT if detected_is_element else SOURCE_WINDOW
    for screen in screens or ():
        if rects_close(selection, screen, tolerance):
            return SOURCE_MONITOR
    return SOURCE_REGION


def probe_under_point(x: int, y: int, *, margin: int = 0):
    """问平台层「这一点上的元素/窗口是什么」。

    返回 ``(source, rect, label)``：元素优先、退回窗口；没有窗口枚举后端或查询失败时
    返回 ``(None, None, "")``（调用方按 region 处理）。
    """
    try:
        from core.platform import window as platform_window

        finder = platform_window.WindowFinder()
        finder.find_windows()
        element_rect = platform_window.find_element_at_point(x, y, margin=margin)
        if element_rect:
            return SOURCE_ELEMENT, list(element_rect), _title_at(finder, x, y)
        rect = finder.find_window_at_point(x, y)
        if rect:
            return SOURCE_WINDOW, list(rect), _title_at(finder, x, y)
    except Exception as e:
        log_exception(e, T("判断截图来源"))
    return None, None, ""


def _title_at(finder, x: int, y: int) -> str:
    """该点上最顶层窗口的标题（拿不到就空串）。"""
    try:
        for window in getattr(finder, "windows", []) or []:
            x1, y1, x2, y2 = window.rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                return str(getattr(window, "title", "") or "")
    except Exception as e:
        log_exception(e, T("读取窗口标题"))
    return ""


def describe(selection, *, screens=(), probe=None, margin: int = 0) -> tuple:
    """给一条历史算出来源与标签；``probe`` 可注入（测试用替身，生产走平台层）。

    标签优先用窗口标题，没有标题的显示器/选区就留空——比编一个「未知窗口」有用：
    空标签在界面上就是不显示那一段。
    """
    rect = _as_tuple(selection)
    if not rect:
        return SOURCE_REGION, ""
    center_x = int((rect[0] + rect[2]) / 2)
    center_y = int((rect[1] + rect[3]) / 2)

    detected_source, detected_rect, label = (None, None, "")
    if probe is not None:
        try:
            detected_source, detected_rect, label = probe(center_x, center_y)
        except Exception as e:
            log_exception(e, T("判断截图来源"))
    else:
        detected_source, detected_rect, label = probe_under_point(center_x, center_y,
                                                                 margin=margin)

    source = classify(
        selection,
        detected=detected_rect,
        detected_is_element=(detected_source == SOURCE_ELEMENT),
        screens=screens,
    )
    log_debug(T("截图来源: {source}{label} (选区 {rect})", source=source,
                label=f" '{label}'" if label else "", rect=rect), "History")
    return source, (label if source != SOURCE_REGION else "")
